-- Row Level Security so the public PostgREST REST API (reachable with the
-- anon key) enforces the same visibility rules the `brain` MCP tools
-- enforce in application code: team resources shared with everyone,
-- personal resources private to their owner. Supabase's default setup
-- grants broad table privileges to anon/authenticated at the schema level,
-- which is what makes every table readable via PostgREST with just the
-- anon key until policies (and matching grants, see the bottom of this
-- file) are added - this migration adds both.
--
-- The `brain` Edge Function itself uses the service role key, which
-- bypasses RLS entirely, so none of this changes its behaviour - see
-- SECURITY.md for the full picture of what does and doesn't rely on RLS.

-- ---------- Helper: map the current JWT to our own users.id ----------

-- security definer so it can read `users` even though `users` itself will
-- have RLS enabled below; search_path is pinned so it can't be hijacked by
-- a role that can create objects earlier in a malicious search_path.
create or replace function app_user_id()
returns uuid
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select id from users where auth_user_id = auth.uid()
$$;

-- Only the roles PostgREST uses need to call it.
revoke all on function app_user_id() from public;
grant execute on function app_user_id() to anon, authenticated;

-- ---------- Enable RLS everywhere ----------

alter table users        enable row level security;
alter table api_keys     enable row level security;
alter table projects     enable row level security;
alter table memories     enable row level security;
alter table oauth_grants enable row level security;

-- Belt and suspenders: even the table owner is bound by RLS (only the
-- service role, which Supabase always exempts from RLS, bypasses this).
alter table users        force row level security;
alter table api_keys     force row level security;
alter table projects     force row level security;
alter table memories     force row level security;
alter table oauth_grants force row level security;

-- ---------- users ----------
-- Closed: everyone can read their own row (needed by the app to resolve
-- "who am I"), nothing else. Author names for other people's memories are
-- resolved by the Edge Functions via the service role
-- (tools/access.ts#authorMap), which bypasses RLS - so there is no need to
-- expose a wider view of `users` through PostgREST. No insert/update/delete
-- policies: provisioning and admin flag changes go through the service role
-- only.

create policy users_select_own on users
  for select to authenticated
  using (id = app_user_id());

-- ---------- api_keys ----------
-- No policies at all for anon/authenticated: this table is service-role
-- only (hashes of long-lived credentials). RLS is enabled with no matching
-- policy, so every row is denied for both roles.

-- ---------- projects ----------
-- SELECT: team projects (owner_id is null) or your own personal projects.
create policy projects_select on projects
  for select to authenticated
  using (owner_id is null or owner_id = app_user_id());

-- INSERT/UPDATE/DELETE: your own personal projects always; team projects
-- only if you are an admin.
create policy projects_insert on projects
  for insert to authenticated
  with check (
    owner_id = app_user_id()
    or (owner_id is null and exists (select 1 from users u where u.id = app_user_id() and u.is_admin))
  );

create policy projects_update on projects
  for update to authenticated
  using (
    owner_id = app_user_id()
    or (owner_id is null and exists (select 1 from users u where u.id = app_user_id() and u.is_admin))
  )
  with check (
    owner_id = app_user_id()
    or (owner_id is null and exists (select 1 from users u where u.id = app_user_id() and u.is_admin))
  );

create policy projects_delete on projects
  for delete to authenticated
  using (
    owner_id = app_user_id()
    or (owner_id is null and exists (select 1 from users u where u.id = app_user_id() and u.is_admin))
  );

-- ---------- memories ----------
-- SELECT: the project must be visible (team, or your own) AND the memory
-- must be shared or created by you - same rule as tools/access.ts#applyVisibility.
create policy memories_select on memories
  for select to authenticated
  using (
    exists (
      select 1 from projects p
      where p.slug = memories.project_slug
        and (p.owner_id is null or p.owner_id = app_user_id())
    )
    and (visibility = 'shared' or created_by = app_user_id())
  );

-- INSERT: only as yourself, into a project you can see.
create policy memories_insert on memories
  for insert to authenticated
  with check (
    created_by = app_user_id()
    and exists (
      select 1 from projects p
      where p.slug = memories.project_slug
        and (p.owner_id is null or p.owner_id = app_user_id())
    )
  );

-- UPDATE/DELETE: same edit rule as tools/access.ts#canEditMemory - your own
-- memory, or a shared memory in a project you can see.
create policy memories_update on memories
  for update to authenticated
  using (
    created_by = app_user_id()
    or (
      visibility = 'shared'
      and exists (
        select 1 from projects p
        where p.slug = memories.project_slug
          and (p.owner_id is null or p.owner_id = app_user_id())
      )
    )
  )
  with check (
    created_by = app_user_id()
    or (
      visibility = 'shared'
      and exists (
        select 1 from projects p
        where p.slug = memories.project_slug
          and (p.owner_id is null or p.owner_id = app_user_id())
      )
    )
  );

create policy memories_delete on memories
  for delete to authenticated
  using (
    created_by = app_user_id()
    or (
      visibility = 'shared'
      and exists (
        select 1 from projects p
        where p.slug = memories.project_slug
          and (p.owner_id is null or p.owner_id = app_user_id())
      )
    )
  );

-- ---------- oauth_grants ----------
-- The owner may read and revoke (update) their own rows. Grants are created
-- by the oauth-consent Edge Function via the service role, so no insert
-- policy for authenticated/anon.
create policy oauth_grants_select on oauth_grants
  for select to authenticated
  using (user_id = app_user_id());

create policy oauth_grants_update on oauth_grants
  for update to authenticated
  using (user_id = app_user_id())
  with check (user_id = app_user_id());

-- ---------- anon gets nothing ----------
-- No policies were created for `anon` on any table above, so with RLS
-- enabled every anon query returns zero rows / permission-denied.

-- ---------- Grants: replace the blanket PostgREST defaults ----------
-- Supabase's default setup grants broad table privileges to anon/authenticated
-- at the schema level, which is exactly how the anon key could read every
-- table today regardless of policies. Revoke that blanket grant and grant
-- back only what each role's policies actually need. Note this only fixes
-- the tables that exist right now - Postgres's own default privileges still
-- grant anon/authenticated broad access on *new* tables created later in
-- `public` (and RLS is off by default on a new table too), so a future
-- table doesn't automatically fail closed just because of what's below;
-- see 20260915200000_default_privileges.sql for the migration that also
-- fixes new tables going forward.

revoke all on users, api_keys, projects, memories, oauth_grants from anon, authenticated;

-- anon: nothing, anywhere.

-- authenticated: exactly the operations covered by policies above.
grant select                          on users        to authenticated;
grant select, insert, update, delete  on projects     to authenticated;
grant select, insert, update, delete  on memories     to authenticated;
grant select, update                  on oauth_grants to authenticated;
-- api_keys: no grant at all for authenticated/anon (service role only).
