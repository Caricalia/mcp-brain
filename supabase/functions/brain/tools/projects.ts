import type { McpServer } from 'mcp-lite'
import { z } from 'zod'
import { supabase } from '../db.ts'
import { embeddingsEnabled } from '../embeddings.ts'
import type { Project, User } from '../types.ts'
import { accessibleSlugs, getAccessibleProject } from './access.ts'
import { fail, failInternal, ok } from './util.ts'

const SLUG_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/

export function registerProjectTools(mcp: McpServer, user: User): void {
  mcp.tool('whoami', {
    description: 'Identify the current user: id, name, email, admin status and whether search runs in semantic or text mode.',
    inputSchema: z.object({}),
    // deno-lint-ignore require-await
    handler: async () => {
      try {
        return ok({
          id: user.id,
          name: user.name,
          email: user.email,
          is_admin: user.isAdmin,
          search_mode: embeddingsEnabled ? 'semantic' : 'text',
          connection: {
            projects: user.grant.projects ?? 'all',
            can_write: user.grant.canWrite,
            can_delete: user.grant.canDelete,
          },
        })
      } catch (e) {
        return failInternal('Failed to resolve identity.', e, 'whoami')
      }
    },
  })

  mcp.tool('list_projects', {
    description:
      'List projects accessible to the user (team projects plus their own personal projects), each with its memory count. Parents appear before children.',
    inputSchema: z.object({}),
    handler: async () => {
      try {
        const slugs = await accessibleSlugs(user)
        if (slugs.length === 0) return ok({ projects: [] })

        const { data: projects, error } = await supabase.from('projects').select('*').in('slug', slugs)
        if (error || !projects) return fail('Failed to list projects.')

        const { data: countRows, error: countError } = await supabase.rpc('memory_counts', {
          p_user: user.id,
          p_slugs: slugs,
        })
        if (countError) console.log(`memory_counts failed: ${countError.message}`)
        const counts = new Map<string, number>()
        for (const row of (countRows ?? []) as { project_slug: string; cnt: number }[]) {
          counts.set(row.project_slug, Number(row.cnt))
        }

        const sorted = (projects as Project[]).slice().sort((a, b) => a.slug.localeCompare(b.slug))
        const parents = sorted.filter((p) => p.parent_slug === null)
        const children = sorted.filter((p) => p.parent_slug !== null)

        const ordered: Project[] = []
        for (const parent of parents) {
          ordered.push(parent)
          for (const child of children) {
            if (child.parent_slug === parent.slug) ordered.push(child)
          }
        }
        for (const child of children) {
          if (!ordered.includes(child)) ordered.push(child) // orphaned child, shouldn't happen
        }

        const result = ordered.map((p) => {
          const out: Record<string, unknown> = {
            slug: p.slug,
            name: p.name,
            kind: p.owner_id === null ? 'team' : 'personal',
            memory_count: counts.get(p.slug) ?? 0,
          }
          if (p.parent_slug !== null) out.parent_slug = p.parent_slug
          if (p.description !== null) out.description = p.description
          return out
        })

        return ok({ projects: result })
      } catch (e) {
        return failInternal('Failed to list projects.', e, 'list_projects')
      }
    },
  })

  mcp.tool('create_project', {
    description:
      'Create a new project. Team projects (kind "team") are shared with everyone and require admin rights; personal projects (default) belong only to the creator. An optional parent must already be accessible to you and cannot itself have a parent.',
    inputSchema: z.object({
      slug: z.string().max(100).describe('Lowercase, hyphen-separated identifier, e.g. "acme-docs".'),
      name: z.string().max(200),
      description: z.string().max(2000).optional(),
      parent_slug: z.string().max(100).optional().describe('Slug of an existing top-level project to nest this one under.'),
      kind: z.enum(['team', 'personal']).default('personal'),
    }),
    handler: async (args) => {
      try {
        if (!user.grant.canWrite) return fail('This connection is read-only.')
        if (!SLUG_RE.test(args.slug)) {
          return fail('Invalid slug: use lowercase letters, digits and hyphens only, e.g. "my-project".')
        }
        if (args.kind === 'team' && !user.isAdmin) {
          return fail('Only admins can create team projects.')
        }
        if (args.parent_slug) {
          const parent = await getAccessibleProject(args.parent_slug, user)
          if (!parent) return fail('Parent project not found or not accessible.')
        }

        const owner_id = args.kind === 'team' ? null : user.id

        const { data, error } = await supabase
          .from('projects')
          .insert({
            slug: args.slug,
            name: args.name,
            description: args.description ?? null,
            parent_slug: args.parent_slug ?? null,
            owner_id,
          })
          .select('*')
          .single()

        if (error) {
          if (error.code === '23505') return fail(`A project with slug "${args.slug}" already exists.`)
          console.log(`create_project failed: ${error.message}`)
          return fail('Could not create project.')
        }

        const project = data as Project
        return ok({
          slug: project.slug,
          kind: project.owner_id === null ? 'team' : 'personal',
        })
      } catch (e) {
        return failInternal('Failed to create project.', e, 'create_project')
      }
    },
  })
}
