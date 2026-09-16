-- Fixes a regression from 20260915193327: this Supabase Cloud project has
-- `pgcrypto` and `vector` installed in the `extensions` schema (Supabase's
-- default location for extensions), not `public`. Pinning
-- `search_path = public, pg_temp` on functions that call `digest()`,
-- `gen_random_bytes()` or use the `vector`/`<=>` operator dropped
-- `extensions` from the path and broke them (confirmed: `resolve_api_key`
-- started erroring in prod right after that migration).
--
-- Fix: include `extensions` in the pinned search_path for every function
-- that needs it. Still safe against search_path hijacking - both `public`
-- and `extensions` are trusted, admin-controlled schemas, and `pg_temp` is
-- listed last so a session-local object still can't shadow either.

alter function issue_api_key(text, text) set search_path = public, extensions, pg_temp;
alter function resolve_api_key(text) set search_path = public, extensions, pg_temp;
alter function match_memories(vector, uuid, text[], int, float) set search_path = public, extensions, pg_temp;
alter function app_similar_pairs(uuid, text[], float, int) set search_path = public, extensions, pg_temp;
