// Tools that power the "Memory Manager" MCP App: one model-visible tool
// that opens the UI, plus a few app-only tools (visibility: ["app"]) the UI
// calls directly. All of them go through the same permission helpers as the
// rest of the server (see access.ts) - nothing here bypasses project/memory
// visibility rules.

import type { McpServer } from 'mcp-lite'
import { z } from 'zod'
import { supabase } from '../db.ts'
import { embed, embeddingsEnabled } from '../embeddings.ts'
import type { Memory, Project, User } from '../types.ts'
import { accessibleSlugs, applyVisibility, authorMap, canEditMemory, getAccessibleProject, resolveScope } from './access.ts'
import { fail, failInternal, ok } from './util.ts'

const RELATIVE_CUTOFF = 0.1

const MANAGER_RESOURCE_URI = 'ui://brain/manager.html'

function memoryToApp(m: Memory, authors: Map<string, string>, user: User, project: Project | null) {
  return {
    id: m.id,
    project_slug: m.project_slug,
    content: m.content,
    tags: m.tags ?? [],
    pinned: m.pinned,
    visibility: m.visibility,
    author: authors.get(m.created_by) ?? m.created_by,
    updated_at: m.updated_at.slice(0, 10),
    can_edit: canEditMemory(m, project, user),
  }
}

export function registerAppTools(mcp: McpServer, user: User): void {
  // Model-visible entry point: opens the interactive manager UI.
  mcp.tool('open_memory_manager', {
    description: 'Open an interactive view to browse, edit, pin, delete and add memories.',
    _meta: { ui: { resourceUri: MANAGER_RESOURCE_URI } },
    inputSchema: z.object({
      project_slug: z.string().max(200).optional().describe('Project to open the manager on, if any.'),
    }),
    handler: async (args) => {
      try {
        const slugs = await accessibleSlugs(user)
        if (slugs.length === 0) return ok({ text: 'No accessible projects yet.' })

        const { data: projects } = await supabase.from('projects').select('*').in('slug', slugs)
        const rows = (projects as Project[] | null) ?? []

        const { data: countRows } = await supabase.rpc('memory_counts', { p_user: user.id, p_slugs: slugs })
        const countMap = new Map<string, number>()
        for (const row of (countRows ?? []) as { project_slug: string; cnt: number }[]) {
          countMap.set(row.project_slug, Number(row.cnt))
        }
        const counts = rows.map(
          (p) => `${p.slug} (${p.owner_id === null ? 'team' : 'personal'}): ${countMap.get(p.slug) ?? 0}`,
        )

        const summary = args.project_slug
          ? `Opening the memory manager on "${args.project_slug}".`
          : `Opening the memory manager. Projects: ${counts.join(', ') || 'none'}.`

        return ok({ text: summary, project_slug: args.project_slug ?? null })
      } catch (e) {
        return failInternal('Failed to open memory manager.', e, 'open_memory_manager')
      }
    },
  })

  // App-only tools below: hidden from the model, only callable by the UI.

  mcp.tool('app_list_memories', {
    description: 'List memories for the memory manager UI, with filters and pagination.',
    _meta: { ui: { visibility: ['app'] } },
    inputSchema: z.object({
      project_slug: z.string().max(200).optional(),
      visibility: z.enum(['shared', 'private']).optional(),
      tag: z.string().max(64).optional(),
      pinned: z.boolean().optional(),
      offset: z.number().int().min(0).default(0),
      limit: z.number().int().min(1).max(50).default(30),
    }),
    handler: async (args) => {
      try {
        let scope: string[] | null
        let project: Project | null = null
        if (args.project_slug) {
          project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else {
          scope = await accessibleSlugs(user)
        }

        let query = supabase.from('memories').select('*', { count: 'exact' })
        query = applyVisibility(query, user, scope)
        if (args.visibility) query = query.eq('visibility', args.visibility)
        if (args.tag) query = query.contains('tags', [args.tag])
        if (args.pinned !== undefined) query = query.eq('pinned', args.pinned)
        query = query
          .order('pinned', { ascending: false })
          .order('updated_at', { ascending: false })
          .range(args.offset, args.offset + args.limit - 1)

        const { data, error, count } = await query
        if (error) return fail('Failed to list memories.')
        const rows = (data ?? []) as Memory[]
        const authors = await authorMap(rows)

        // Projects referenced, for can_edit (need project.owner_id per row when
        // no single project_slug was given).
        const slugsInRows = [...new Set(rows.map((m) => m.project_slug))]
        const projectsMap = new Map<string, Project>()
        if (project) {
          projectsMap.set(project.slug, project)
        } else if (slugsInRows.length > 0) {
          const { data: projRows } = await supabase.from('projects').select('*').in('slug', slugsInRows)
          for (const p of (projRows as Project[] | null) ?? []) projectsMap.set(p.slug, p)
        }

        const items = rows.map((m) => memoryToApp(m, authors, user, projectsMap.get(m.project_slug) ?? null))
        return ok({ items, total: count ?? items.length, offset: args.offset, limit: args.limit })
      } catch (e) {
        return failInternal('Failed to list memories.', e, 'app_list_memories')
      }
    },
  })

  mcp.tool('app_similar_pairs', {
    description: 'Find pairs of visible memories that look like possible duplicates, for the memory manager UI.',
    _meta: { ui: { visibility: ['app'] } },
    inputSchema: z.object({
      project_slug: z.string().max(200).optional(),
      threshold: z.number().min(0).max(1).default(0.8),
    }),
    handler: async (args) => {
      try {
        if (!embeddingsEnabled) return ok({ available: false })

        let scope: string[] | null = null
        if (args.project_slug) {
          const project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else {
          scope = await accessibleSlugs(user)
        }

        const { data, error } = await supabase.rpc('app_similar_pairs', {
          p_user: user.id,
          p_scope: scope,
          p_threshold: args.threshold,
          match_count: 30,
        })
        if (error) return fail('Failed to compute similar pairs.')

        const rows = (data ?? []) as {
          id_a: string
          content_a: string
          project_slug_a: string
          id_b: string
          content_b: string
          project_slug_b: string
          similarity: number
        }[]

        const pairs = rows.map((r) => ({
          a: { id: r.id_a, content: r.content_a, project_slug: r.project_slug_a },
          b: { id: r.id_b, content: r.content_b, project_slug: r.project_slug_b },
          similarity: Math.round(r.similarity * 100) / 100,
        }))

        return ok({ available: true, pairs })
      } catch (e) {
        return failInternal('Failed to compute similar pairs.', e, 'app_similar_pairs')
      }
    },
  })

  // Search variant for the manager UI: same query as `recall`, but each
  // result also carries `can_edit` (the model-facing `recall` tool doesn't,
  // since the model never renders Pin/Edit/Delete controls). Without this,
  // the UI's search box returned items with can_edit undefined, which
  // disabled every action button as soon as a search term was typed.
  mcp.tool('app_search', {
    description: 'Search memories for the memory manager UI, with can_edit included on each result.',
    _meta: { ui: { visibility: ['app'] } },
    inputSchema: z.object({
      query: z.string().max(600),
      project_slug: z.string().max(200).optional(),
      limit: z.number().int().min(1).max(50).default(30),
    }),
    handler: async (args) => {
      try {
        let scope: string[] | null = null
        let project: Project | null = null
        if (args.project_slug) {
          project = await getAccessibleProject(args.project_slug, user)
          if (!project) return fail('Project not found or not accessible.')
          scope = await resolveScope(args.project_slug, user)
        } else {
          scope = await accessibleSlugs(user)
        }

        let rows: Memory[]
        if (embeddingsEnabled) {
          let embedding: number[] | null
          try {
            embedding = await embed(args.query)
          } catch (e) {
            return failInternal('Failed to generate embedding.', e, 'app_search/embed')
          }
          const { data, error } = await supabase.rpc('match_memories', {
            query_embedding: embedding,
            p_user: user.id,
            p_scope: scope,
            match_count: args.limit,
            min_similarity: 0.5,
          })
          if (error) return fail('Search failed.')
          rows = (data ?? []) as (Memory & { similarity?: number })[]
          if (rows.length > 0) {
            const topSimilarity = Math.max(...rows.map((m) => (m as Memory & { similarity?: number }).similarity ?? 0))
            rows = rows.filter((m) =>
              ((m as Memory & { similarity?: number }).similarity ?? 0) >= topSimilarity - RELATIVE_CUTOFF
            )
          }
        } else {
          const { data, error } = await supabase.rpc('search_memories_text', {
            p_query: args.query,
            p_user: user.id,
            p_scope: scope,
            match_count: args.limit,
          })
          if (error) return fail('Search failed.')
          rows = (data ?? []) as Memory[]
        }

        const authors = await authorMap(rows)

        const slugsInRows = [...new Set(rows.map((m) => m.project_slug))]
        const projectsMap = new Map<string, Project>()
        if (project) {
          projectsMap.set(project.slug, project)
        } else if (slugsInRows.length > 0) {
          const { data: projRows } = await supabase.from('projects').select('*').in('slug', slugsInRows)
          for (const p of (projRows as Project[] | null) ?? []) projectsMap.set(p.slug, p)
        }

        const items = rows.map((m) => memoryToApp(m, authors, user, projectsMap.get(m.project_slug) ?? null))
        return ok({ items })
      } catch (e) {
        return failInternal('Search failed.', e, 'app_search')
      }
    },
  })
}

export { MANAGER_RESOURCE_URI }
