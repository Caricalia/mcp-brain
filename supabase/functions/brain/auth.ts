// Authentication for the `brain` MCP endpoint.
//
// Token auth only: a `brain_<hex>` API key, resolved via the `resolve_api_key`
// SQL function against the `api_keys` table. Keys are issued manually by an
// admin and always get full access to every project the user can otherwise
// see (no per-connection grant restriction in this mode).
//
// The credential can arrive in either the standard `Authorization: Bearer`
// header, or an `x-api-key` header. The second exists because some MCP
// clients (e.g. Claude's custom-connector dialog) reserve the `Authorization`
// header and don't let the user set it manually.
//
import { supabase } from './db.ts'
import type { Grant, User } from './types.ts'

const FULL_GRANT: Grant = { projects: null, canWrite: true, canDelete: true }

export type AuthResult = { user: User; credential: 'api_key' } | { error: string; status: 401 | 500 }

export async function authenticate(
  authorizationHeader: string | undefined,
  apiKeyHeader: string | undefined,
): Promise<AuthResult> {
  const bearer = authorizationHeader ?? ''
  const fromBearer = bearer.startsWith('Bearer ') ? bearer.slice(7).trim() : ''
  const fromApiKey = (apiKeyHeader ?? '').trim()

  // Prefer whichever of the two carries our token shape. A client that sends
  // an unrelated Authorization: Bearer value (e.g. a bridge/proxy's own
  // OAuth token) alongside a valid x-api-key should still authenticate,
  // rather than 401 because the wrong header happened to be checked first.
  const token = fromBearer.startsWith('brain_') ? fromBearer : fromApiKey.startsWith('brain_') ? fromApiKey : ''

  if (!token) return { error: 'Unauthorized', status: 401 }

  const { data, error } = await supabase.rpc('resolve_api_key', { p_token: token })
  if (error) {
    console.log(`api key lookup failed: ${error.message}`)
    return { error: 'Auth lookup failed', status: 500 }
  }
  if (!data || data.length === 0) return { error: 'Unauthorized', status: 401 }
  const row = data[0]
  return {
    credential: 'api_key',
    user: { id: row.user_id, email: row.email, name: row.name, isAdmin: row.is_admin, grant: FULL_GRANT },
  }
}
