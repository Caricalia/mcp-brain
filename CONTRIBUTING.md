# Contributing

Thanks for looking at `brain`. This is a small, focused MCP server — most contributions should keep it that way.

## Running it locally

Requires Docker.

```bash
cp .env.example supabase/functions/.env
supabase start
supabase db reset                     # applies migrations + supabase/seed.sql
supabase functions serve --no-verify-jwt brain --env-file supabase/functions/.env
```

Issue yourself a token from Studio (`http://localhost:54323` → SQL editor). `supabase/seed.sql` already seeds a `dev@example.com` admin for exactly this:

```sql
select issue_api_key('dev@example.com', 'local');
```

Endpoint: `http://localhost:54321/functions/v1/brain/mcp`. Test with `curl` or an MCP inspector:

```bash
curl -i --request POST 'http://127.0.0.1:54321/functions/v1/brain/mcp' \
  --header 'Authorization: Bearer <token>' \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

Type-check before sending a PR (run from `supabase/functions/brain` — the import map and fmt/lint options live in that directory's `deno.json`; running these from the repo root silently uses the wrong config):

```bash
cd supabase/functions/brain && deno check index.ts && deno fmt --check . && deno lint .
```

(CI runs the same checks — see `.github/workflows/ci.yml`.)

## Verifying RLS

`supabase db reset` / `supabase db push` apply the schema and its RLS policies, but don't prove the policies actually enforce the visibility rules — that used to be baked into three migrations (`20260915180907`/`181137`/`181804`) that ran as a real test against production and broke bootstrapping on every other database (see the comments at the top of those files). The real RLS proof now lives outside the migration history: run [`supabase/tests/rls_test.sql`](./supabase/tests/rls_test.sql) by hand against a database that already has an admin user and a team project (i.e. after `supabase db reset`):

```bash
psql "$(supabase status -o env | grep DB_URL= | cut -d= -f2- | tr -d '"')" -f supabase/tests/rls_test.sql
```

or paste its contents into Studio's SQL editor. It creates throwaway rows, asserts a non-admin teammate can see a `shared` team memory but not a `private` one or another owner's personal-project memory, and cleans up after itself.

## Running the benchmark

`bench/` holds a retrieval/LLM/agent benchmark comparing Claude with and without `brain`. It's Python, with its own scripts under `bench/scripts/`. The dataset is **bring-your-own**: the repo publishes only the harness and fictional placeholders. Read [`bench/README.md`](./bench/README.md) for the dataset JSON shapes and workflow; `bench/scripts/build_dataset.py` is a runnable skeleton to replace with your own data, and `bench/run_retrieval.py` / `run_llm.py` / `run_agent.py` run each part against generated local datasets. Keep all experiment inputs and outputs local or synthetic — don't introduce anything that identifies a real company, person, customer, or private project.

## Code conventions

- TypeScript, Deno, strict mode. No framework beyond Hono (routing) and mcp-lite (MCP protocol).
- All plain-table Postgres queries (not going through an RPC) must be scoped through the shared helpers in `supabase/functions/brain/tools/access.ts` (`getAccessibleProject`, `resolveScope`, `applyVisibility`, `accessibleSlugs`) — this is the one place that encodes the visibility/grant rule. Don't hand-roll a `.or('visibility.eq.shared,...')` filter in a new tool; use the helper so the rule can't be forgotten. **Known gap**: this repo does not yet have a single exported "get me a scoped memories query" helper used by literally every read path — `tools/search.ts`, `tools/app.ts` and `tools/memories.ts` each call `applyVisibility` + `resolveScope` themselves rather than one function returning an already-filtered query builder. Consolidating that into one helper (e.g. `scopedMemoriesQuery(user, opts)`) is a good, contained first PR if you want one.
- Don't return raw Postgres error messages (`error.message`) to MCP clients — log the detail with `console.log`, return a generic message via `fail(...)`.
- Every SQL function should `set search_path = public, pg_temp` (see the most recent migration for the pattern) — this is a real hardening measure, not boilerplate.
- Memory `created_by` and `project_slug` are immutable after insert. Keep that invariant in database triggers/policies, not only in tool schemas.
- Run `deno check`, `deno fmt --check`, `deno lint`, and `cd app && npm ci && npm run build` before opening a PR. The generated `supabase/functions/brain/ui/manager_html.ts` must remain committed and in sync.
- Migrations are append-only once applied to any real environment. Never edit an applied migration's DDL/logic; add a new one. (Exception made once, deliberately: the three RLS-test migrations described in "Verifying RLS" above were turned into guarded no-ops because their original logic broke bootstrapping and their DDL/schema footprint was always empty — see the comments in those files.)

## Reporting a security issue

See [`SECURITY.md`](./SECURITY.md) — please don't open a public issue for a vulnerability.
