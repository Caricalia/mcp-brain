# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability. Instead, use GitHub's private reporting: repository → **Security** tab → **Report a vulnerability** (GitHub Security Advisories). If that's not available, open a normal issue asking for a private contact channel and we'll follow up without discussing details in public.

Please include: the affected version/commit, a description of the issue, and reproduction steps if possible. We'll acknowledge reports as soon as we can and keep you updated as we work on a fix.

## Security model

`brain` runs as a single Supabase Edge Function using the **service-role** Postgres client — it bypasses Row-Level Security by design, because access control needs request-scoped context (who the caller is, what their connection is allowed to do) that plain RLS policies can't see on their own without a Supabase Auth session. Every tool that reads or writes `memories`/`projects` goes through a shared set of scope/visibility helpers (`supabase/functions/brain/tools/access.ts`) that encode the visibility rule: a project is visible if it's a team project or owned by the caller; a memory is visible if it's `shared` (and its project is visible) or owned by the caller. This application-code layer is the thing actually enforcing access control on `master` today.

**RLS as a fail-closed backstop, not a live second opinion**: RLS policies encoding those same rules are also applied directly in Postgres (see `supabase/migrations/20260915180143_lock_down_tables.sql`), covering the path `brain`'s own checks don't: direct PostgREST access with the anon/authenticated key. In this token-auth release, nothing populates `users.auth_user_id`, so the policies' `app_user_id()` helper resolves to null and every policy evaluates to false for every row. The practical effect is that PostgREST is simply **closed**: an anon/authenticated request sees nothing, not "the same rules applied a second time" against real per-user data. That's the correct, safe outcome for a deployment that only uses token auth, but don't read it as RLS actively re-deriving `brain`'s access decisions today; [`supabase/tests/rls_test.sql`](./supabase/tests/rls_test.sql) proves the policies are *written correctly* for when `auth_user_id` is populated, not that they're currently doing live work.

**No secrets in the repo.** `supabase/functions/.env` (local OpenAI key) and any `.env*` file are gitignored and must never be committed. Production secrets (`SUPABASE_SERVICE_ROLE_KEY`, `OPENAI_API_KEY`) are set via `supabase secrets set` / the Supabase dashboard, never checked in. API keys (`brain_<hex>`) are stored hashed (`sha256`) in `api_keys.token_hash`, never in plaintext, and shown to the issuer exactly once at creation time.

**Data sent to third parties.** When `OPENAI_API_KEY` is configured, memory
content is sent to OpenAI's embeddings API. The optional benchmark also sends
dataset text to OpenAI and Claude. Do not store secrets or personal/customer
data in `brain` unless the deployment's data-processing, retention and vendor
terms have been reviewed and approved.

**Operational hardening.** The function deliberately disables Supabase's JWT
verification because it authenticates `brain_<hex>` tokens itself. Production
deployments should put it behind rate limiting and monitoring, issue separate
tokens per person/device, and revoke lost tokens promptly. The local app proxy
is loopback-only and must not be exposed as a shared development service.
