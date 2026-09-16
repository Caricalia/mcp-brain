-- Manual RLS proof for the policies in
-- supabase/migrations/20260915180143_lock_down_tables.sql.
--
-- This is NOT a migration and is never applied by `supabase db reset` /
-- `supabase db push` - it's a standalone script you run by hand against a
-- database that already has an admin user and at least one team project
-- (e.g. after `supabase db reset`, which seeds one via supabase/seed.sql).
--
-- What it proves: a teammate (non-admin, non-owner) can see a `shared`
-- memory in a team project, but not a `private` one, and not a memory in
-- someone else's personal project - exactly what tools/access.ts and the
-- RLS policies are both supposed to enforce.
--
-- Run it with:
--   supabase db reset   # if you haven't already - creates the seed admin
--   psql "$(supabase status -o env | grep DB_URL | cut -d= -f2)" \
--     -f supabase/tests/rls_test.sql
-- or paste it into Studio's SQL editor (http://127.0.0.1:54323).
--
-- It cleans up its own throwaway rows (teammate user + test memories) at
-- the end, whether it passed or failed.

do $$
declare
  v_admin_id     uuid;
  v_project_slug text;
begin
  select id into v_admin_id from users where is_admin = true order by created_at limit 1;
  if v_admin_id is null then
    raise exception 'no admin user found - run `supabase db reset` first (it seeds one via seed.sql)';
  end if;

  select slug into v_project_slug from projects where owner_id is null order by slug limit 1;
  if v_project_slug is null then
    raise exception 'no team project found - run `supabase db reset` first (it seeds some via seed.sql)';
  end if;

  insert into users (email, name, is_admin, auth_user_id)
  values ('rls-test-teammate@example.invalid', 'RLS Test Teammate', false, gen_random_uuid())
  on conflict (email) do nothing;

  -- Stash the teammate's auth_user_id in a session GUC *before* switching
  -- role below. Reading it back with a plain `select ... from users` after
  -- `set local role authenticated` would itself be subject to the
  -- users_select_own RLS policy (id = app_user_id()) - and at that point
  -- app_user_id() is still null (the JWT claims that make it resolve
  -- haven't been set yet), so the row would come back empty and `sub` would
  -- silently end up null, making every subsequent check pass for the wrong
  -- reason (an unauthenticated/no-such-user session, not the teammate).
  perform set_config(
    'rls_test.teammate_auth_id',
    (select auth_user_id::text from users where email = 'rls-test-teammate@example.invalid'),
    false
  );

  insert into projects (slug, owner_id, name)
  values ('rls-test-admin-personal', v_admin_id, 'RLS Test Admin Personal')
  on conflict (slug) do nothing;

  insert into memories (project_slug, created_by, visibility, content)
  values (v_project_slug, v_admin_id, 'shared', 'RLS TEST: shared team memory');

  insert into memories (project_slug, created_by, visibility, content)
  values (v_project_slug, v_admin_id, 'private', 'RLS TEST: private team memory');

  insert into memories (project_slug, created_by, visibility, content)
  values ('rls-test-admin-personal', v_admin_id, 'shared', 'RLS TEST: memory in admin personal project');
end $$;

do $$
declare
  v_project_slug text;
begin
  select slug into v_project_slug from projects where owner_id is null order by slug limit 1;
  raise notice 'RLS TEST: using team project %', v_project_slug;
end $$;

begin;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object(
    'sub', current_setting('rls_test.teammate_auth_id', true),
    'role', 'authenticated'
  )::text,
  true
);

do $$
declare
  can_see_shared          boolean;
  can_see_private         boolean;
  can_see_admin_personal  boolean;
  v_memory_id             uuid;
  update_succeeded        boolean := false;
  v_sub                   text := current_setting('request.jwt.claims', true)::json ->> 'sub';
begin
  if v_sub is null or v_sub = '' then
    raise exception 'RLS TEST SETUP FAILED: JWT sub is empty - impersonation did not take effect, this run would prove nothing';
  end if;

  select exists(
    select 1 from memories where content = 'RLS TEST: shared team memory'
  ) into can_see_shared;
  select exists(
    select 1 from memories where content = 'RLS TEST: private team memory'
  ) into can_see_private;
  select exists(
    select 1 from memories where content = 'RLS TEST: memory in admin personal project'
  ) into can_see_admin_personal;

  raise notice 'RLS PROOF: teammate sees shared team memory = % (expected true)', can_see_shared;
  raise notice 'RLS PROOF: teammate sees private team memory = % (expected false)', can_see_private;
  raise notice 'RLS PROOF: teammate sees memory in admin personal project = % (expected false)', can_see_admin_personal;

  if not can_see_shared then
    raise exception 'RLS PROOF FAILED: shared team memory should be visible to a teammate';
  end if;
  if can_see_private then
    raise exception 'RLS PROOF FAILED: private team memory leaked to a teammate';
  end if;
  if can_see_admin_personal then
    raise exception 'RLS PROOF FAILED: memory in admin personal project leaked to a teammate';
  end if;

  -- A teammate may edit shared content, but cannot claim it and make it
  -- private. The database trigger/policy must reject that direct PostgREST
  -- update even though the current row itself is editable.
  select id into v_memory_id
  from memories
  where content = 'RLS TEST: shared team memory';

  begin
    update memories
    set created_by = app_user_id(), visibility = 'private'
    where id = v_memory_id;
    update_succeeded := true;
  exception when others then
    if position('Memory author cannot be changed' in sqlerrm) = 0 then
      raise exception 'RLS PROOF FAILED: unexpected error while blocking memory reassignment: %', sqlerrm;
    end if;
  end;

  if update_succeeded then
    raise exception 'RLS PROOF FAILED: teammate could claim a shared memory and make it private';
  end if;
end $$;
commit;

-- Cleanup (runs even if the assertions above raised - psql/Studio stop on
-- error, so if you saw a FAILED notice above, re-run just the two DELETEs
-- below by hand).
delete from memories where content like 'RLS TEST: %';
delete from projects where slug = 'rls-test-admin-personal';
delete from users where email = 'rls-test-teammate@example.invalid';
