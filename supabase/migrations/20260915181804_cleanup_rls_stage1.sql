-- Stage 1 RLS proof, step 3/3 (cleanup): delete the throwaway rows created
-- by 20260915180907_verify_rls_stage1.sql, in a fresh session where the
-- migration role was never SET ROLE'd away from its default (bypassing)
-- context - see the note in 20260915181137_assert_rls_stage1.sql about why
-- this had to be a separate run. Both DELETEs are no-ops (zero rows
-- affected) when step 1 itself was a no-op, so this is safe on every
-- database. The actual RLS proof now lives outside the migration history,
-- as a standalone script: see supabase/tests/rls_test.sql, documented in
-- CONTRIBUTING.md.
delete from memories where content like 'RLS TEST: %';
delete from users where email = 'rls-test-teammate@example.invalid';
