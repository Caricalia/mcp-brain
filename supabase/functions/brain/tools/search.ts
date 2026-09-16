import type { McpServer } from 'mcp-lite'
import { z } from 'zod'
import { supabase } from '../db.ts'
import { embed, embeddingsEnabled } from '../embeddings.ts'
import type { Memory, Project, User } from '../types.ts'
import { accessibleSlugs, applyVisibility, authorMap, getAccessibleProject, resolveScope, toMemoryOutput } from './access.ts'
import { fail, failInternal, ok } from './util.ts'

const RELATIVE_CUTOFF = 0.1

export function registerSearchTools(mcp: McpServer, user: User): void {
  mcp.tool('recall', {
    description:
      "Search saved memories by meaning or keyword. Use it before assuming you don't know something about the user, the team or a project. In text mode, search with concrete keywords rather than full sentences.",
    inputSchema: z.object({
      query: z.string().max(600),
      project_slug: z.string().max(200).optional().describe('Restrict to this project and its parent.'),
      limit: z.number().int().min(1).max(30).default(8),
      min_similarity: z.number().min(0).max(1).default(0.5).describe(
        'Semantic mode only. Results are also cut to those within 0.1 of the top match.',
      ),
    }),
    handler: async (args) => {
      try {
        let scope: string[] | null = null
        if (args.project_slug) {
          const project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else if (user.grant.projects !== null) {
          // No project filter given, but the connection's grant restricts to
          // a subset of projects - pass it explicitly so the search RPCs
          // (which only know about visibility, not grants) stay scoped.
          scope = await accessibleSlugs(user)
        }

        if (embeddingsEnabled) {
          let embedding: number[] | null
          try {
            embedding = await embed(args.query)
          } catch (e) {
            return failInternal('Failed to generate embedding.', e, 'recall/embed')
          }
          const { data, error } = await supabase.rpc('match_memories', {
            query_embedding: embedding,
            p_user: user.id,
            p_scope: scope,
            match_count: args.limit,
            min_similarity: args.min_similarity,
          })
          if (error) return fail('Search failed.')
          let rows = (data ?? []) as (Memory & { similarity?: number })[]
          if (rows.length > 0) {
            const topSimilarity = Math.max(...rows.map((m) => m.similarity ?? 0))
            rows = rows.filter((m) => (m.similarity ?? 0) >= topSimilarity - RELATIVE_CUTOFF)
          }
          const authors = await authorMap(rows)
          return ok({ mode: 'semantic', results: rows.map((m) => toMemoryOutput(m, authors)) })
        }

        const { data, error } = await supabase.rpc('search_memories_text', {
          p_query: args.query,
          p_user: user.id,
          p_scope: scope,
          match_count: args.limit,
        })
        if (error) return fail('Search failed.')
        const rows = (data ?? []) as (Memory & { rank?: number })[]
        const authors = await authorMap(rows)
        return ok({ mode: 'text', results: rows.map((m) => toMemoryOutput(m, authors)) })
      } catch (e) {
        return failInternal('Recall failed.', e, 'recall')
      }
    },
  })

  mcp.tool('search_by_tag', {
    description: 'List visible memories carrying a given tag, most recently updated first.',
    inputSchema: z.object({
      tag: z.string().max(64),
      project_slug: z.string().max(200).optional(),
      limit: z.number().int().min(1).max(50).default(20),
    }),
    handler: async (args) => {
      try {
        let scope: string[] | null
        if (args.project_slug) {
          const project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else {
          scope = await accessibleSlugs(user)
        }

        let query = supabase.from('memories').select('*').contains('tags', [args.tag])
        query = applyVisibility(query, user, scope)
        query = query.order('updated_at', { ascending: false }).limit(args.limit)

        const { data, error } = await query
        if (error) return fail('Search failed.')
        const rows = (data ?? []) as Memory[]
        const authors = await authorMap(rows)
        return ok({ results: rows.map((m) => toMemoryOutput(m, authors)) })
      } catch (e) {
        return failInternal('Search by tag failed.', e, 'search_by_tag')
      }
    },
  })

  mcp.tool('list_recent', {
    description: 'List the most recently updated visible memories, optionally scoped to a project.',
    inputSchema: z.object({
      project_slug: z.string().max(200).optional(),
      limit: z.number().int().min(1).max(50).default(15),
    }),
    handler: async (args) => {
      try {
        let scope: string[] | null
        if (args.project_slug) {
          const project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else {
          scope = await accessibleSlugs(user)
        }

        let query = supabase.from('memories').select('*')
        query = applyVisibility(query, user, scope)
        query = query.order('updated_at', { ascending: false }).limit(args.limit)

        const { data, error } = await query
        if (error) return fail('Failed to list recent memories.')
        const rows = (data ?? []) as Memory[]
        const authors = await authorMap(rows)
        return ok({ results: rows.map((m) => toMemoryOutput(m, authors)) })
      } catch (e) {
        return failInternal('Failed to list recent memories.', e, 'list_recent')
      }
    },
  })

  mcp.tool('get_project_context', {
    description:
      "Load a project's context in one call: the project (and its parent, if any), all visible pinned memories, and the most recent visible non-pinned ones. Call this at the start of a session in a repo.",
    inputSchema: z.object({ project_slug: z.string().max(200) }),
    handler: async (args) => {
      try {
        const project = await getAccessibleProject(args.project_slug, user)
        if (!project) return fail('Project not found or not accessible.')

        let parent: Project | null = null
        if (project.parent_slug) {
          parent = await getAccessibleProject(project.parent_slug, user)
        }

        const scope = await resolveScope(args.project_slug, user)

        let pinnedQuery = supabase.from('memories').select('*').eq('pinned', true)
        pinnedQuery = applyVisibility(pinnedQuery, user, scope)
        pinnedQuery = pinnedQuery.order('updated_at', { ascending: false }).limit(16)
        const { data: pinnedData, error: pinnedError } = await pinnedQuery
        if (pinnedError) return fail('Failed to load pinned memories.')
        const pinnedRows = (pinnedData ?? []) as Memory[]
        const pinned_truncated = pinnedRows.length > 15
        const pinnedSlice = pinnedRows.slice(0, 15)

        let recentQuery = supabase.from('memories').select('*').eq('pinned', false)
        recentQuery = applyVisibility(recentQuery, user, scope)
        recentQuery = recentQuery.order('updated_at', { ascending: false }).limit(10)
        const { data: recentData, error: recentError } = await recentQuery
        if (recentError) return fail('Failed to load recent memories.')
        const recentRows = (recentData ?? []) as Memory[]

        const authors = await authorMap([...pinnedSlice, ...recentRows])
        const pinned = pinnedSlice.map((m) => toMemoryOutput(m, authors, { omitPinned: true }))
        const recent = recentRows.map((m) => toMemoryOutput(m, authors))

        return ok({
          project: {
            slug: project.slug,
            name: project.name,
            description: project.description,
            kind: project.owner_id === null ? 'team' : 'personal',
          },
          parent: parent
            ? {
              slug: parent.slug,
              name: parent.name,
              description: parent.description,
              kind: parent.owner_id === null ? 'team' : 'personal',
            }
            : null,
          pinned,
          pinned_truncated,
          recent,
        })
      } catch (e) {
        return failInternal('Failed to load project context.', e, 'get_project_context')
      }
    },
  })
}
