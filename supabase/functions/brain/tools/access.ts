// Shared permission/scope helpers used by every tool. Plain table queries go
// through the service-role client, so these helpers are the only place that
// enforces project/memory visibility for non-rpc queries.

import { supabase } from '../db.ts'
import type { Memory, Project, User } from '../types.ts'

export const MAX_CONTENT_LENGTH = 600

export function validateContent(content: string): string | null {
  if (content.trim().length === 0) return 'Content cannot be blank.'
  if (content.length > MAX_CONTENT_LENGTH) {
    return `Content is too long (${content.length} > ${MAX_CONTENT_LENGTH} chars). Split it into several memories.`
  }
  return null
}

// Whether an OAuth grant (if any) allows this project slug. API-key users
// have grant.projects === null (full access), same as an unrestricted OAuth
// connection.
function grantAllows(slug: string, user: User): boolean {
  return user.grant.projects === null || user.grant.projects.includes(slug)
}

// Fetch a project only if it is accessible to the user (team project, or
// owned by them) AND allowed by their connection's grant. Returns null if it
// doesn't exist or isn't accessible - callers must not reveal which case it
// was (an out-of-grant project looks exactly like a nonexistent one).
export async function getAccessibleProject(slug: string, user: User): Promise<Project | null> {
  if (!grantAllows(slug, user)) return null
  const { data, error } = await supabase.from('projects').select('*').eq('slug', slug).maybeSingle()
  if (error || !data) return null
  const project = data as Project
  if (project.owner_id !== null && project.owner_id !== user.id) return null
  return project
}

// All project slugs accessible to the user (team projects + their own),
// narrowed by the connection's grant if it restricts to a subset.
export async function accessibleSlugs(user: User): Promise<string[]> {
  const { data, error } = await supabase
    .from('projects')
    .select('slug')
    .or(`owner_id.is.null,owner_id.eq.${user.id}`)
  if (error || !data) return []
  const slugs = data.map((p: { slug: string }) => p.slug)
  if (user.grant.projects === null) return slugs
  const allowed = new Set(user.grant.projects)
  return slugs.filter((s: string) => allowed.has(s))
}

// Resolve the search/list scope: project + parent when a slug is given
// (via project_scope), or every accessible slug otherwise. Always narrowed
// by the connection's grant.
export async function resolveScope(slug: string | undefined, user: User): Promise<string[] | null> {
  if (slug) {
    const { data, error } = await supabase.rpc('project_scope', { p_slug: slug })
    if (error || !data) return []
    const scope = data as string[]
    if (user.grant.projects === null) return scope
    const allowed = new Set(user.grant.projects)
    return scope.filter((s) => allowed.has(s))
  }
  return await accessibleSlugs(user)
}

// Apply the visibility filter (shared, or owned by the user) plus scope to a
// plain memories query builder.
// deno-lint-ignore no-explicit-any
export function applyVisibility(query: any, user: User, scope: string[] | null) {
  let q = query.or(`visibility.eq.shared,created_by.eq.${user.id}`)
  if (scope !== null) q = q.in('project_slug', scope)
  return q
}

export function canEditMemory(memory: Memory, project: Project | null, user: User): boolean {
  if (memory.created_by === user.id) return true
  if (
    memory.visibility === 'shared' &&
    project !== null &&
    (project.owner_id === null || project.owner_id === user.id)
  ) {
    return true
  }
  return false
}

// Fetch a memory, along with its project (if still accessible), for edit checks.
export async function getMemoryWithProject(
  id: string,
): Promise<{ memory: Memory; project: Project | null } | null> {
  const { data, error } = await supabase.from('memories').select('*').eq('id', id).maybeSingle()
  if (error || !data) return null
  const memory = data as Memory
  const { data: projectData } = await supabase
    .from('projects')
    .select('*')
    .eq('slug', memory.project_slug)
    .maybeSingle()
  return { memory, project: (projectData as Project) ?? null }
}

// Resolve created_by ids to author names with a single query.
export async function authorMap(rows: { created_by: string }[]): Promise<Map<string, string>> {
  const ids = [...new Set(rows.map((r) => r.created_by))]
  const map = new Map<string, string>()
  if (ids.length === 0) return map
  const { data, error } = await supabase.from('users').select('id, name').in('id', ids)
  if (error || !data) return map
  for (const u of data as { id: string; name: string }[]) map.set(u.id, u.name)
  return map
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

export function toMemoryOutput(
  m: Memory & { similarity?: number; rank?: number },
  authors: Map<string, string>,
  opts: { omitPinned?: boolean } = {},
) {
  const out: Record<string, unknown> = {
    id: m.id,
    project_slug: m.project_slug,
    content: m.content,
    visibility: m.visibility,
    author: authors.get(m.created_by) ?? m.created_by,
    updated_at: m.updated_at.slice(0, 10),
  }
  if (m.tags && m.tags.length > 0) out.tags = m.tags
  if (m.pinned && !opts.omitPinned) out.pinned = true
  if (m.similarity !== undefined) out.similarity = round2(m.similarity)
  if (m.rank !== undefined) out.rank = round2(m.rank)
  return out
}
