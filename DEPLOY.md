# Production deployment

Commands in exact order. `<project-ref>` is your Supabase project's project ref; substitute it in every command.

## 1. Link the project

```bash
supabase login
supabase link --project-ref <project-ref>
```

## 2. Migrations

```bash
supabase db push
```

Note: the migrations include authentication-related columns and grant tables used by the schema. Don't delete or rewrite applied migrations: they don't affect the token flow in this release, and changing migration history can break existing deployments.

Migrations must run before the seed step below (the seed data references the schema they create), and that ordering is safe: a few of these migrations used to be an RLS test that required seed data to already exist and failed a first `db push` on any database that hadn't been seeded yet — they're now guarded no-ops when that data isn't there (see CONTRIBUTING.md, "Verifying RLS"). After pushing, you can optionally run [`supabase/tests/rls_test.sql`](./supabase/tests/rls_test.sql) by hand against the remote database (once seeded) to prove the RLS policies enforce the intended visibility rules.

## 3. Seed (users and projects)

Before running it, edit `supabase/seed.sql`: **remove the `dev@example.com` row** (that's a local-dev-only convenience account, not meant for production) and replace the example `<email-...>` users/projects with your team's real ones.

Copy the contents of `supabase/seed.sql` and run it from Studio's SQL editor on the remote project (Supabase dashboard → SQL Editor).

## 4. Deploy the function

```bash
supabase functions deploy --no-verify-jwt brain
```

## 5. (Optional) Enable semantic search

```bash
supabase secrets set OPENAI_API_KEY=<key>
supabase functions deploy --no-verify-jwt brain
```

Backfill embeddings for already-saved memories (one time only, after enabling the key):

```bash
SUPABASE_URL=https://<project-ref>.supabase.co \
SUPABASE_SERVICE_ROLE_KEY=<service-role-key-from-dashboard> \
OPENAI_API_KEY=<key> \
deno run --allow-net --allow-env scripts/backfill-embeddings.ts
```

## 6. Issue tokens (one per person and per machine)

From Studio's SQL editor on remote:

```sql
select issue_api_key('<person-email>', '<machine-label>');
```

The token is shown in plain text only once: copy it and pass it to the person through a secure channel (not plain Slack, not email).

The MCP client can send the token as `Authorization: Bearer <token>` or as the `x-api-key: <token>` header (see auth.ts) — the second form exists for clients that reserve `Authorization`, such as Claude.ai's custom connector dialog.

Revoke a token (e.g., lost machine):

```sql
update api_keys set revoked_at = now() where label = '<machine-label>';
```

Register a new user:

```sql
insert into users (email, name, is_admin) values ('<real-email>', '<Name>', false);
```

`supabase/config.toml` sets `enable_signup = false` under `[auth]`: this
release uses token authentication, so leaving signups open would let anyone
with the anon key create auth users and trigger confirmation emails for no
reason.

## 7. Post-deploy smoke test

- [ ] `curl https://<project-ref>.supabase.co/functions/v1/brain/health` → `200` with no token.
- [ ] `curl -X POST https://<project-ref>.supabase.co/functions/v1/brain/mcp -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'` with no token → `401`.
- [ ] Connect an MCP inspector (or `claude mcp add`) with a valid token and check that the 14 tools appear.
- [ ] Repeat the previous call sending the token as the `x-api-key` header instead of `Authorization` and check that it also works.
- [ ] With two different users, check that each one sees what they should: the `shared` content of team projects, and only their own `private` content.
