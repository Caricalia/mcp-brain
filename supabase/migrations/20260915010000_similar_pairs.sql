-- Additive migration for the memory manager MCP App.
-- Finds pairs of visible memories in scope whose embeddings are similar,
-- so the UI can surface possible duplicates. Semantic mode only (needs
-- embeddings); text-mode deployments simply never call this function.

create or replace function app_similar_pairs(
  p_user      uuid,
  p_scope     text[] default null,
  p_threshold float default 0.8,
  match_count int    default 30
)
returns table (
  id_a uuid, content_a text, project_slug_a text,
  id_b uuid, content_b text, project_slug_b text,
  similarity float
)
language sql stable as $$
  select a.id, a.content, a.project_slug,
         b.id, b.content, b.project_slug,
         1 - (a.embedding <=> b.embedding) as similarity
  from memories a
  cross join lateral (
    select m.id, m.content, m.project_slug, m.embedding
    from memories m
    where m.embedding is not null
      and m.id <> a.id
      and m.id > a.id -- avoid symmetric duplicates without an extra sort
      and memory_visible(m, p_user)
      and (p_scope is null or m.project_slug = any(p_scope))
      and 1 - (a.embedding <=> m.embedding) >= p_threshold
    order by a.embedding <=> m.embedding
    limit 3
  ) b
  where a.embedding is not null
    and memory_visible(a, p_user)
    and (p_scope is null or a.project_slug = any(p_scope))
  order by similarity desc
  limit match_count;
$$;
