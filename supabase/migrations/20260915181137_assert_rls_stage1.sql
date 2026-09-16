-- Stage 1 RLS proof, step 2/3 (assert) - HISTORICAL, now a guarded no-op.
--
-- See the note at the top of 20260915180907_verify_rls_stage1.sql: this
-- step impersonated the throwaway teammate created there exactly like
-- PostgREST does for a real request (SET ROLE authenticated + a
-- request.jwt.claims sub), then reran the same visibility queries a client
-- would run and asserted the expected result. On a database where step 1
-- was itself a no-op (no admin user / no 'admin-personal' project yet -
-- the normal case on `supabase db reset` or a first `supabase db push`),
-- the throwaway teammate never got created, so this step now checks for it
-- first and skips instead of failing.
--
-- Own session/migration run, deliberately: this project's migration role
-- (cli_login_postgres) is not the table owner, not a superuser and does not
-- have BYPASSRLS, so once it SET ROLEs to `authenticated` there is no
-- reliable way back to delete privileges within the same session/transaction
-- (confirmed empirically - RESET ROLE correctly restores current_user but
-- table DELETE still comes back permission-denied). This step only reads,
-- so that limitation doesn't matter here; step 3
-- (20260915181804_cleanup_rls_stage1.sql) runs the cleanup DELETEs in its
-- own fresh session where the role was never touched.
--
-- The actual RLS proof now lives outside the migration history, as a
-- standalone script: see supabase/tests/rls_test.sql, documented in
-- CONTRIBUTING.md.

begin;
set local role authenticated;
select set_config(
  'request.jwt.claims',
  json_build_object(
    'sub', (select auth_user_id from users where email = 'rls-test-teammate@example.invalid'),
    'role', 'authenticated'
  )::text,
  true
);

do $$
declare
  can_see_shared          boolean;
  can_see_private         boolean;
  can_see_admin_personal  boolean;
begin
  if not exists (select 1 from users where email = 'rls-test-teammate@example.invalid') then
    raise notice 'skipping RLS stage1 assert: no throwaway teammate (step 1 was a no-op on this database). See supabase/tests/rls_test.sql for the real RLS proof.';
    return;
  end if;

  select exists(
    select 1 from memories where content = 'RLS TEST: shared acme memory'
  ) into can_see_shared;
  select exists(
    select 1 from memories where content = 'RLS TEST: private acme memory'
  ) into can_see_private;
  select exists(
    select 1 from memories where content = 'RLS TEST: memory in admin personal project'
  ) into can_see_admin_personal;

  raise notice 'RLS PROOF: teammate sees shared acme memory = % (expected true)', can_see_shared;
  raise notice 'RLS PROOF: teammate sees private acme memory = % (expected false)', can_see_private;
  raise notice 'RLS PROOF: teammate sees memory in admin personal project = % (expected false)', can_see_admin_personal;

  if not can_see_shared then
    raise exception 'RLS PROOF FAILED: shared acme memory should be visible to a teammate';
  end if;
  if can_see_private then
    raise exception 'RLS PROOF FAILED: private acme memory leaked to a teammate';
  end if;
  if can_see_admin_personal then
    raise exception 'RLS PROOF FAILED: memory in admin personal project leaked to a teammate';
  end if;
end $$;
commit;
