-- Stage 1 RLS proof, step 1/3 (setup) - HISTORICAL, now a guarded no-op.
--
-- This file (and the two that follow it, 20260915181137 and 20260915181804)
-- was originally an RLS *test* run against the already-provisioned
-- production database, not a schema migration: it inserted a throwaway
-- teammate + memories, impersonated that teammate the way PostgREST does,
-- and asserted the visibility rules in 20260915180143_lock_down_tables.sql
-- actually held. That's fine replayed against prod (where an admin user and
-- an 'admin-personal' project already exist) but breaks `supabase db reset`
-- / a first `supabase db push` on any other database: `seed.sql` (which
-- creates the admin user and its personal project) only runs *after*
-- migrations, so this step always found zero admin users and aborted the
-- whole migration run before the database was even usable.
--
-- Migrations must never be edited once applied to a real environment, but
-- this file's actual DDL/schema footprint is empty - it only wrote test
-- rows, which the cleanup step deletes - so turning it into a guarded no-op
-- does not change any deployed schema. On a database that happens to have
-- an admin user and that project already (a fresh push against prod's
-- existing history), it behaves exactly as before; everywhere else it just
-- logs a notice and exits.
--
-- The actual RLS proof now lives outside the migration history, as a
-- standalone script: see supabase/tests/rls_test.sql, documented in
-- CONTRIBUTING.md.

do $$
declare
  v_admin_id uuid;
begin
  select id into v_admin_id from users where is_admin = true order by created_at limit 1;
  if v_admin_id is null then
    raise notice 'skipping RLS stage1 setup: no admin user yet (expected on db reset / a fresh db push - seed.sql runs after migrations). See supabase/tests/rls_test.sql for the real RLS proof.';
    return;
  end if;

  if not exists (select 1 from projects where slug = 'admin-personal') then
    raise notice 'skipping RLS stage1 setup: no "admin-personal" project (expected outside the original prod database). See supabase/tests/rls_test.sql for the real RLS proof.';
    return;
  end if;

  -- Throwaway second user. No FK from users.auth_user_id to auth.users, so a
  -- fabricated uuid is enough to impersonate this user's JWT `sub` in the
  -- assert step.
  insert into users (email, name, is_admin, auth_user_id)
  values ('rls-test-teammate@example.invalid', 'RLS Test Teammate', false, gen_random_uuid())
  on conflict (email) do nothing;

  insert into memories (project_slug, created_by, visibility, content)
  values ('acme', v_admin_id, 'shared', 'RLS TEST: shared acme memory');

  insert into memories (project_slug, created_by, visibility, content)
  values ('acme', v_admin_id, 'private', 'RLS TEST: private acme memory');

  insert into memories (project_slug, created_by, visibility, content)
  values ('admin-personal', v_admin_id, 'shared', 'RLS TEST: memory in admin personal project');
end $$;
