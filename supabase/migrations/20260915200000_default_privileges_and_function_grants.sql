-- Two hardening fixes flagged in the pre-publication security review:
--
-- 1. Default privileges for *future* tables. 20260915180143_lock_down_tables
--    revoked/re-granted privileges on the tables that existed at the time,
--    but Postgres's own default privileges still grant anon/authenticated
--    broad access on any new table created afterwards in `public` (and RLS
--    is off by default on a new table too) - so, contrary to that
--    migration's own comment, a future table with no policy did NOT
--    actually fail closed. This pins default privileges so anon/
--    authenticated get nothing on a table by default; anyone adding a new
--    table must explicitly grant + write policies for it, same as the
--    tables above.
--
-- 2. SQL functions are only ever called by `brain`'s Edge Function via the
--    service role client (supabase-js), which doesn't go through the
--    PostgREST role grants below at all - but Supabase's default project
--    setup still grants EXECUTE on every new function to PUBLIC (which
--    includes anon and authenticated), so each of these was directly
--    callable by anyone with the anon key over PostgREST's `/rpc/<fn>`
--    endpoint. None of them take a caller identity from RLS context (most
--    accept `p_user`/`p_scope` as plain arguments), so that grant would let
--    the anon key read or issue tokens for *any* user by passing their id
--    directly - RLS on the underlying tables doesn't help here because
--    these are all `security invoker` (the default) SQL functions run as
--    whatever role calls them, but they don't rely on `app_user_id()`/
--    `auth.uid()` to select the user - the caller supplies it.

revoke execute on function issue_api_key(text, text)                              from public, anon, authenticated;
revoke execute on function resolve_api_key(text)                                  from public, anon, authenticated;
revoke execute on function match_memories(vector, uuid, text[], int, float)       from public, anon, authenticated;
revoke execute on function search_memories_text(text, uuid, text[], int)          from public, anon, authenticated;
revoke execute on function memory_counts(uuid, text[])                            from public, anon, authenticated;
revoke execute on function app_similar_pairs(uuid, text[], float, int)            from public, anon, authenticated;
revoke execute on function project_scope(text)                                    from public, anon, authenticated;
revoke execute on function project_visible(text, uuid)                           from public, anon, authenticated;
revoke execute on function memory_visible(memories, uuid)                         from public, anon, authenticated;
-- app_user_id() is the one exception: PostgREST's own RLS policies call it
-- as the authenticated role (see 20260915180143_lock_down_tables.sql), so it
-- keeps its existing grant to anon/authenticated.

-- Postgres default privileges only apply to objects created by the role
-- that runs this statement (the migration role), going forward - this does
-- not touch the grants already fixed above for tables that exist today.
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke execute on functions from public, anon, authenticated;
