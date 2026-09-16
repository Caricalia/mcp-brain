// Shared domain types for the brain MCP server.

// What an OAuth connection is allowed to do. `projects: null` means "every
// project the user can otherwise see" (the default). API-key auth always
// gets the full grant, since those tokens are issued manually by an admin.
export interface Grant {
  projects: string[] | null
  canWrite: boolean
  canDelete: boolean
}

export interface User {
  id: string
  email: string
  name: string
  isAdmin: boolean
  grant: Grant
}

export interface Project {
  slug: string
  parent_slug: string | null
  owner_id: string | null
  name: string
  description: string | null
  created_at: string
}

export interface Memory {
  id: string
  project_slug: string
  created_by: string
  visibility: 'shared' | 'private'
  content: string
  tags: string[]
  pinned: boolean
  source: string | null
  created_at: string
  updated_at: string
}
