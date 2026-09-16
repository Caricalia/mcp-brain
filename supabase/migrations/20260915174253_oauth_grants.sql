-- OAuth 2.1 login support (additive, does not touch the brain_<hex> API key
-- path). Two pieces:
--
--   1. users.auth_user_id: links a users row to the Supabase Auth user id
--      (auth.users.id) created when someone signs in via the OAuth consent
--      page. Nullable - only set for people who have logged in via OAuth at
--      least once. Filled by ../functions/_shared/provision.ts.
--
--   2. oauth_grants: per-connection permissions, one row per (user, OAuth
--      client). Written by the consent screen (supabase/functions/oauth-consent)
--      before it calls approveAuthorization, and read by brain/auth.ts on
--      every OAuth-authenticated request to narrow project visibility and
--      write/delete rights for that specific connection.

alter table users add column auth_user_id uuid;

create unique index users_auth_user_id_key on users (auth_user_id) where auth_user_id is not null;

create table oauth_grants (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users(id) on delete cascade,
  client_id   text not null,
  -- null = every project the user can otherwise see (team + own personal).
  -- A non-null array narrows to that subset; a project outside it behaves
  -- exactly like one the user can't see.
  projects    text[],
  can_write   boolean not null default true,
  can_delete  boolean not null default true,
  created_at  timestamptz not null default now(),
  revoked_at  timestamptz
);

-- One active grant per (user, client). Re-approving the same connection
-- replaces it rather than piling up rows.
create unique index oauth_grants_active_unique on oauth_grants (user_id, client_id) where revoked_at is null;

create index oauth_grants_user_id_idx on oauth_grants (user_id);
