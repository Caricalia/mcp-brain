create extension if not exists vector;
create extension if not exists pgcrypto;

-- ---------- Users and tokens ----------

create table users (
  id         uuid primary key default gen_random_uuid(),
  email      text not null unique,
  name       text not null,
  is_admin   boolean not null default false,
  created_at timestamptz not null default now()
);

create table api_keys (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references users(id) on delete cascade,
  token_hash   text not null unique,
  label        text,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at   timestamptz
);

-- Issues a token for a user. Returns the token in plaintext ONCE.
-- Usage from Studio: select issue_api_key('teammate@example.com', 'macbook teammate');
create or replace function issue_api_key(p_email text, p_label text default null)
returns text language plpgsql as $$
declare
  v_user  uuid;
  v_token text;
begin
  select id into v_user from users where email = p_email;
  if v_user is null then
    raise exception 'user % not found', p_email;
  end if;
  v_token := 'brain_' || encode(gen_random_bytes(32), 'hex');
  insert into api_keys (user_id, token_hash, label)
  values (v_user, encode(digest(v_token, 'sha256'), 'hex'), p_label);
  return v_token;
end $$;

-- Resolves a token to its user. 0 rows if invalid or revoked.
create or replace function resolve_api_key(p_token text)
returns table (user_id uuid, email text, name text, is_admin boolean)
language plpgsql as $$
declare
  v_hash text := encode(digest(p_token, 'sha256'), 'hex');
begin
  update api_keys set last_used_at = now()
  where token_hash = v_hash and revoked_at is null;

  return query
    select u.id, u.email, u.name, u.is_admin
    from api_keys k
    join users u on u.id = k.user_id
    where k.token_hash = v_hash and k.revoked_at is null;
end $$;

-- ---------- Projects ----------

create table projects (
  slug        text primary key,
  parent_slug text references projects(slug) on delete restrict,
  owner_id    uuid references users(id) on delete cascade,  -- null = proyecto de equipo
  name        text not null,
  description text,
  created_at  timestamptz not null default now(),
  constraint projects_slug_format check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
);

create index projects_owner_idx on projects (owner_id);

-- At most two levels, and the child inherits the parent's owner
create or replace function projects_validate_parent() returns trigger as $$
declare
  v_parent projects%rowtype;
begin
  if new.parent_slug is not null then
    select * into v_parent from projects where slug = new.parent_slug;
    if v_parent.parent_slug is not null then
      raise exception 'Only two levels of projects are allowed';
    end if;
    if v_parent.owner_id is distinct from new.owner_id then
      raise exception 'Child project must have the same owner as its parent';
    end if;
  end if;
  return new;
end $$ language plpgsql;

create trigger projects_validate_parent_trg
  before insert or update on projects
  for each row execute function projects_validate_parent();

-- Project visible to a user: a team project, or their own
create or replace function project_visible(p_slug text, p_user uuid)
returns boolean language sql stable as $$
  select exists (
    select 1 from projects
    where slug = p_slug and (owner_id is null or owner_id = p_user)
  );
$$;

-- Scope: the slug and its parent
create or replace function project_scope(p_slug text)
returns text[] language sql stable as $$
  select array_remove(array[p.slug, p.parent_slug], null)
  from projects p where p.slug = p_slug;
$$;

-- ---------- Memories ----------

create table memories (
  id           uuid primary key default gen_random_uuid(),
  project_slug text not null references projects(slug) on delete cascade,
  created_by   uuid not null references users(id) on delete cascade,
  visibility   text not null default 'shared' check (visibility in ('shared', 'private')),
  content      text not null,
  tags         text[] not null default '{}',
  pinned       boolean not null default false,
  source       text not null default 'claude',
  embedding    vector(1536),                       -- null en modo full-text
  fts          tsvector generated always as (to_tsvector('spanish', content)) stored,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint memories_content_not_blank check (length(trim(content)) > 0)
);

create index memories_embedding_idx on memories using hnsw (embedding vector_cosine_ops);
create index memories_fts_idx       on memories using gin (fts);
create index memories_project_idx   on memories (project_slug);
create index memories_tags_idx      on memories using gin (tags);
create index memories_updated_idx   on memories (updated_at desc);

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end $$ language plpgsql;

create trigger memories_set_updated_at
  before update on memories
  for each row execute function set_updated_at();

-- Memory visible to a user
create or replace function memory_visible(m memories, p_user uuid)
returns boolean language sql stable as $$
  select project_visible(m.project_slug, p_user)
     and (m.visibility = 'shared' or m.created_by = p_user);
$$;

-- Semantic search (only with OPENAI_API_KEY). p_scope null = everything visible.
create or replace function match_memories(
  query_embedding vector(1536),
  p_user          uuid,
  p_scope         text[] default null,
  match_count     int    default 8,
  min_similarity  float  default 0.0
)
returns table (
  id uuid, project_slug text, content text, tags text[], pinned boolean,
  visibility text, created_by uuid, similarity float, updated_at timestamptz
)
language sql stable as $$
  select m.id, m.project_slug, m.content, m.tags, m.pinned, m.visibility, m.created_by,
         1 - (m.embedding <=> query_embedding) as similarity, m.updated_at
  from memories m
  where m.embedding is not null
    and memory_visible(m, p_user)
    and (p_scope is null or m.project_slug = any(p_scope))
    and 1 - (m.embedding <=> query_embedding) >= min_similarity
  order by m.embedding <=> query_embedding
  limit match_count;
$$;

-- Full-text search (default mode).
create or replace function search_memories_text(
  p_query     text,
  p_user      uuid,
  p_scope     text[] default null,
  match_count int    default 8
)
returns table (
  id uuid, project_slug text, content text, tags text[], pinned boolean,
  visibility text, created_by uuid, rank float, updated_at timestamptz
)
language sql stable as $$
  select m.id, m.project_slug, m.content, m.tags, m.pinned, m.visibility, m.created_by,
         ts_rank(m.fts, websearch_to_tsquery('spanish', p_query)) as rank, m.updated_at
  from memories m
  where memory_visible(m, p_user)
    and (p_scope is null or m.project_slug = any(p_scope))
    and m.fts @@ websearch_to_tsquery('spanish', p_query)
  order by rank desc, m.updated_at desc
  limit match_count;
$$;
