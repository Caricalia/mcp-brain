-- Two hardening fixes flagged in review:
--
-- 1. Pin `search_path` on every plpgsql/sql function that lacked it. Without
--    this, a function invoked in a session/role whose search_path has been
--    tampered with could resolve an unqualified identifier to an
--    attacker-controlled object instead of the intended `public` one.
--    `app_user_id()` already had this from a previous migration.
--
-- 2. Add `memory_counts`, a single grouped query replacing the N+1
--    per-project `count` loop that `list_projects` and `open_memory_manager`
--    used to run (one query per accessible project).

alter function issue_api_key(text, text) set search_path = public, pg_temp;
alter function resolve_api_key(text) set search_path = public, pg_temp;
alter function projects_validate_parent() set search_path = public, pg_temp;
alter function project_visible(text, uuid) set search_path = public, pg_temp;
alter function project_scope(text) set search_path = public, pg_temp;
alter function set_updated_at() set search_path = public, pg_temp;
alter function memory_visible(memories, uuid) set search_path = public, pg_temp;
alter function match_memories(vector, uuid, text[], int, float) set search_path = public, pg_temp;
alter function search_memories_text(text, uuid, text[], int) set search_path = public, pg_temp;
alter function app_similar_pairs(uuid, text[], float, int) set search_path = public, pg_temp;

-- Visible memory count per project slug, scoped to what `p_user` can see
-- (shared, or their own private ones) - mirrors the visibility rule
-- `memory_visible()`/`applyVisibility()` already apply everywhere else.
create or replace function memory_counts(p_user uuid, p_slugs text[])
returns table (project_slug text, cnt bigint)
language sql stable
set search_path = public, pg_temp
as $$
  select m.project_slug, count(*) as cnt
  from memories m
  where m.project_slug = any(p_slugs)
    and memory_visible(m, p_user)
  group by m.project_slug;
$$;
