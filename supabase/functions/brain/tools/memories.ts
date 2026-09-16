import type { McpServer } from 'mcp-lite'
import { z } from 'zod'
import { supabase } from '../db.ts'
import { embed, embeddingsEnabled } from '../embeddings.ts'
import type { Memory, User } from '../types.ts'
import { canEditMemory, getAccessibleProject, getMemoryWithProject, resolveScope, validateContent } from './access.ts'
import { fail, failInternal, ok, UUID_RE } from './util.ts'

const MAX_TAGS = 20
const MAX_TAG_LENGTH = 64
const MAX_SOURCE_LENGTH = 200
const tagsSchema = z.array(z.string().max(MAX_TAG_LENGTH)).max(MAX_TAGS)

export function registerMemoryTools(mcp: McpServer, user: User): void {
  mcp.tool('remember', {
    description:
      'Save a durable fact worth keeping: a decision, preference or piece of context - one idea per memory, understandable without extra context. In a team project use "private" only for notes teammates must not see. If a known fact changes, use update_memory instead of adding a new one. In semantic mode the response may also include "related" near-duplicates (similarity 0.75-0.92); review them and call update_memory/forget on the old one if it is now superseded.',
    inputSchema: z.object({
      content: z.string().max(600).describe('The fact to remember, max 600 characters.'),
      project_slug: z.string().max(200),
      tags: tagsSchema.optional(),
      pinned: z.boolean().optional(),
      visibility: z.enum(['shared', 'private']).optional().describe(
        'Defaults to private in personal projects, shared in team projects.',
      ),
      source: z.string().max(MAX_SOURCE_LENGTH).optional(),
    }),
    handler: async (args) => {
      try {
        if (!user.grant.canWrite) return fail('This connection is read-only.')

        const contentError = validateContent(args.content)
        if (contentError) return fail(contentError)

        const project = await getAccessibleProject(args.project_slug, user)
        if (!project) return fail('Project not found or not accessible.')

        const visibility = args.visibility ?? (project.owner_id === null ? 'shared' : 'private')

        let embedding: number[] | null = null
        let related: { id: string; content: string; similarity: number }[] = []
        if (embeddingsEnabled) {
          try {
            embedding = await embed(args.content)
          } catch (e) {
            return failInternal('Failed to generate embedding.', e, 'embed')
          }

          const scope = await resolveScope(args.project_slug, user)

          const { data: matches, error: matchError } = await supabase.rpc('match_memories', {
            query_embedding: embedding,
            p_user: user.id,
            p_scope: scope,
            match_count: 1,
            min_similarity: 0.92,
          })
          if (matchError) return fail('Failed to check for duplicates.')
          if (matches && matches.length > 0) {
            const dup = matches[0]
            return ok({
              duplicate_of: dup.id,
              existing_content: dup.content,
              similarity: Math.round(dup.similarity * 100) / 100,
            })
          }

          // No near-duplicate (>=0.92), but there may be weaker matches worth
          // surfacing (0.75-0.92) so the caller can consider superseding them.
          const { data: relatedMatches } = await supabase.rpc('match_memories', {
            query_embedding: embedding,
            p_user: user.id,
            p_scope: scope,
            match_count: 3,
            min_similarity: 0.75,
          })
          if (relatedMatches && relatedMatches.length > 0) {
            related = relatedMatches.map((m: { id: string; content: string; similarity: number }) => ({
              id: m.id,
              content: m.content,
              similarity: Math.round(m.similarity * 100) / 100,
            }))
          }
        }

        const { data, error } = await supabase
          .from('memories')
          .insert({
            project_slug: args.project_slug,
            created_by: user.id,
            visibility,
            content: args.content,
            tags: args.tags ?? [],
            pinned: args.pinned ?? false,
            source: args.source ?? 'claude',
            embedding,
          })
          .select('*')
          .single()

        if (error) {
          console.log(`remember failed: ${error.message}`)
          return fail('Could not save memory.')
        }

        const saved = data as Memory
        const out: Record<string, unknown> = {
          id: saved.id,
          project_slug: saved.project_slug,
          visibility: saved.visibility,
        }
        if (related.length > 0) {
          out.related = related
          out.hint = 'These existing memories are similar. If one of them is now outdated, call update_memory or forget on it.'
        }
        return ok(out)
      } catch (e) {
        return failInternal('Failed to save memory.', e, 'remember')
      }
    },
  })

  mcp.tool('update_memory', {
    description:
      'Update an existing memory in place: content, tags, pinned state or visibility. Use instead of remember when a known fact changes, to avoid duplicates. Requires edit permission on the memory.',
    inputSchema: z.object({
      id: z.string().max(36).describe('Memory id'),
      content: z.string().max(600).optional().describe('New content, max 600 characters.'),
      tags: tagsSchema.optional(),
      pinned: z.boolean().optional(),
      visibility: z.enum(['shared', 'private']).optional(),
    }),
    handler: async (args) => {
      try {
        if (!user.grant.canWrite) return fail('This connection is read-only.')

        if (!UUID_RE.test(args.id)) return fail('Memory not found or not accessible.')
        const found = await getMemoryWithProject(args.id)
        if (!found || !canEditMemory(found.memory, found.project, user)) {
          return fail('Memory not found or not accessible.')
        }
        if (user.grant.projects !== null && !user.grant.projects.includes(found.memory.project_slug)) {
          return fail('Memory not found or not accessible.')
        }

        const updates: Record<string, unknown> = {}
        if (args.content !== undefined) {
          const contentError = validateContent(args.content)
          if (contentError) return fail(contentError)
          updates.content = args.content

          if (embeddingsEnabled) {
            try {
              updates.embedding = await embed(args.content)
            } catch (e) {
              return failInternal('Failed to generate embedding.', e, 'embed')
            }
          }
        }
        if (args.tags !== undefined) updates.tags = args.tags
        if (args.pinned !== undefined) updates.pinned = args.pinned
        if (args.visibility !== undefined) {
          // canEditMemory() above also allows anyone with project access to
          // edit a *shared* memory (e.g. fix a typo), but changing its
          // visibility to private is a different, more consequential action:
          // it would let a non-author unilaterally hide a memory from the
          // rest of the team. Restrict that specifically to the author,
          // matching what RLS's memories_update policy actually allows a
          // non-author to change (visibility must stay 'shared' in that
          // path's with-check).
          if (found.memory.created_by !== user.id) {
            return fail('Only the author of a memory can change its visibility.')
          }
          updates.visibility = args.visibility
        }

        if (Object.keys(updates).length === 0) {
          return ok({ id: found.memory.id, updated: true })
        }

        const { data, error } = await supabase
          .from('memories')
          .update(updates)
          .eq('id', args.id)
          .select('*')
          .single()

        if (error) {
          console.log(`update_memory failed: ${error.message}`)
          return fail('Could not update memory.')
        }

        return ok({ id: (data as Memory).id, updated: true })
      } catch (e) {
        return failInternal('Failed to update memory.', e, 'update_memory')
      }
    },
  })

  mcp.tool('forget', {
    description: 'Permanently delete a memory. Requires edit permission on it.',
    inputSchema: z.object({ id: z.string().max(36).describe('Memory id') }),
    handler: async (args) => {
      try {
        if (!user.grant.canDelete) return fail('This connection cannot delete memories.')

        if (!UUID_RE.test(args.id)) return fail('Memory not found or not accessible.')
        const found = await getMemoryWithProject(args.id)
        if (!found || !canEditMemory(found.memory, found.project, user)) {
          return fail('Memory not found or not accessible.')
        }
        if (user.grant.projects !== null && !user.grant.projects.includes(found.memory.project_slug)) {
          return fail('Memory not found or not accessible.')
        }

        const { error } = await supabase.from('memories').delete().eq('id', args.id)
        if (error) {
          console.log(`forget failed: ${error.message}`)
          return fail('Could not delete memory.')
        }

        return ok({ deleted: true, id: args.id })
      } catch (e) {
        return failInternal('Failed to delete memory.', e, 'forget')
      }
    },
  })
}
