# brain

Shared, durable memory for Claude — across Claude Code, Claude Desktop and Claude.ai — backed by one small Supabase Edge Function and a Postgres database.

Claude clients don't share memory with each other, and within a single client, context resets between sessions. Agents re-derive decisions that were already made, or forget them outright, because the reasoning behind a choice never made it into the code — it lived in a chat that's gone. `brain` is an [MCP](https://modelcontextprotocol.io) server that gives Claude a place to save durable facts — decisions, preferences, project context — and recall them later, from any client, scoped to projects that can be team-wide or personal. It runs as a single Supabase Edge Function; there's nothing to install locally to use it, only an endpoint + token to register with your MCP client.

## Quickstart

Requires Docker (`supabase start` runs Postgres + the Edge Functions runtime locally) and the [Supabase CLI](https://supabase.com/docs/guides/cli).

```bash
git clone https://github.com/Caricalia/mcp-brain.git
cd mcp-brain
cp .env.example supabase/functions/.env
supabase start
supabase db reset                     # applies migrations + seed data
supabase functions serve --no-verify-jwt brain --env-file supabase/functions/.env
```

Issue yourself a token from the local Studio SQL editor (http://localhost:54323). The seed data includes a ready-to-use `dev@example.com` admin:

```sql
select issue_api_key('dev@example.com', 'laptop');
```

This prints a `brain_<hex>` token once, in plain text — copy it now. The local endpoint is `http://localhost:54321/functions/v1/brain/mcp`.

### Connect Claude Code

```bash
claude mcp add brain --transport http \
  http://localhost:54321/functions/v1/brain/mcp \
  --header "Authorization: Bearer <token>"
```

Add `--scope user` if you want `brain` available in every project, not just the current one.

### Connect Claude Desktop / claude.ai custom connector

Claude's own dialogs reserve the `Authorization` header and won't let you set it by hand, so `brain` also accepts the token as `x-api-key`. If the connector dialog lets you add a custom header, use:

```
x-api-key: <token>
```

Otherwise, bridge through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) in `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "brain": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "<url>",
        "--header",
        "Authorization: Bearer <token>"
      ]
    }
  }
}
```

Replace `<url>` with your endpoint and `<token>` with your token, then restart the client.

### Verify it's connected

Ask the model: **"who am I in brain?"** — it should call `whoami` and answer with your identity, admin status, and whether search is running in text or semantic mode.

For the full walkthrough (recommended prompt text for `CLAUDE.md` / Claude Desktop preferences, a first-week checklist) see [`ONBOARDING.md`](./ONBOARDING.md). For running your own production instance, see [`DEPLOY.md`](./DEPLOY.md).

## Architecture

- **Postgres (Supabase)** holds `users`, `projects`, `memories` and `api_keys`, plus the SQL functions that enforce visibility (`memory_visible`, `project_scope`, `match_memories`, ...).
- **pgvector** stores an embedding per memory when semantic search is enabled, for `recall` and duplicate detection.
- **One Edge Function** (`supabase/functions/brain`) is the entire server: a small [Hono](https://hono.dev) app wraps an [mcp-lite](https://www.npmjs.com/package/mcp-lite) MCP server exposing 14 tools over Streamable HTTP, authenticated by a per-user API key.
- **mcp-lite** also serves a bundled single-file HTML "MCP App" (`open_memory_manager`) — an interactive memory browser rendered inside Claude Desktop/Claude.ai, built separately under `app/`.

No ORM, no background workers, no separate API server: request in, SQL out, JSON back.

## Tools

| Tool | What it does |
|---|---|
| `whoami` | Current identity, admin status, and whether search runs in text or semantic mode. |
| `list_projects` | Projects accessible to the caller (team + own personal), each with a memory count. |
| `create_project` | Create a project; `team` projects require admin rights. |
| `remember` | Save a new memory (fact, decision, durable context). In semantic mode, flags near-duplicates. |
| `recall` | Search memories by meaning (semantic) or keyword (full-text). |
| `search_by_tag` | List visible memories carrying a given tag. |
| `list_recent` | Most recently updated visible memories. |
| `get_project_context` | One-call bootstrap: a project (+ parent), its pinned memories, and recent ones. |
| `update_memory` | Edit an existing memory in place (avoids duplicates vs. `remember`). |
| `forget` | Permanently delete a memory. |
| `open_memory_manager` | Opens the visual memory manager (MCP App). |
| `app_list_memories`, `app_search`, `app_similar_pairs` | App-only tools (hidden from the model) that power the memory manager UI. |

## Permission / visibility model

- A project is **team** (`owner_id is null`, visible to everyone) or **personal** (visible only to its owner). Only admins can create team projects.
- A memory is **shared** (visible to anyone who can see its project) or **private** (visible only to its author). Default: `private` in personal projects, `shared` in team projects.
- A memory is editable by its author, or by anyone with project access if it's `shared`.
- Projects nest at most two levels (parent/child); `recall` on a child also searches the parent.
- Application code enforces all of this: every tool goes through one shared set of scope/visibility helpers (`tools/access.ts`). The Edge Function itself connects with the Postgres **service role**, which bypasses Row-Level Security by design (see [`SECURITY.md`](./SECURITY.md) for why). Postgres RLS policies mirroring the same rules are also applied directly (`supabase/migrations/20260915180143_lock_down_tables.sql`) as a second, independent layer for a path the Edge Function doesn't use: direct PostgREST access with the anon/authenticated key. In this token-auth release, nothing populates `users.auth_user_id`, so `app_user_id()` resolves to null and every one of those policies evaluates to false for every row — PostgREST is simply **closed** end-to-end today, not "the same rules enforced a second time" against real per-user data. That's the correct, fail-closed outcome for a deployment that doesn't use Supabase Auth, but it means the RLS layer isn't currently exercised as a live second opinion; `supabase/tests/rls_test.sql` proves the policies are *correct*, not that they're presently doing live work.

## Search: text vs. semantic

By default `recall` runs Postgres full-text search — no external dependency, no cost. Setting `OPENAI_API_KEY` switches it to semantic search (`text-embedding-3-small`) with near-duplicate detection on `remember`. Cost is trivial: the model is $0.02 per million input tokens, no output cost; even heavy daily use across a whole team runs a few cents a month. See "Enable semantic search" in [`DEPLOY.md`](./DEPLOY.md) for the exact steps.

**Text mode is hardwired to Spanish** (`to_tsvector('spanish', content)` / `websearch_to_tsquery('spanish', ...)` in the SQL functions, from this project's original deployment) even though this repository and its docs are in English. If you deploy a fresh instance and rely on `recall` in text mode (no `OPENAI_API_KEY`) with English content, keyword search quality will suffer — Spanish stemming doesn't match English words well. Workarounds until this is made configurable: enable semantic search (`OPENAI_API_KEY`), which doesn't use `tsvector` at all, or edit the `'spanish'` regconfig in `supabase/migrations/20260915000000_init.sql`'s `memories.fts` column and `search_memories_text()` function to `'english'` in your own fork before your first deploy (the `fts` column is a stored generated column, so changing it after data exists requires a migration that rewrites the table).

## Deploy

See [`DEPLOY.md`](./DEPLOY.md) for the full procedure (migrations, seed, deploy, tokens, embedding backfill, smoke test). Uses your own Supabase project; there is no shared hosted instance.

## Benchmark

`bench/` contains an optional bring-your-own-data benchmark for retrieval, LLM
answers, and agent workflows. It publishes the harness and fictional
placeholders only; it does not publish experiment results or project-specific
data. See [`bench/README.md`](./bench/README.md) for setup instructions.

## Limitations & anonymization

- No web UI beyond the MCP App, no hybrid text+semantic search, no automatic memory summarization/compaction, no local embeddings (OpenAI only, optional).
- The benchmark requires you to provide your own synthetic or approved dataset; do not commit real customer, business, or project data.
- This has been used by one small team, not battle-tested at scale — expect rough edges.

## Authentication status

Token-based auth is the supported authentication flow in this release. OAuth
login is not included.

## Project layout

```
supabase/
  functions/brain/
    index.ts          # Hono app: /health and /mcp, resolves the credential
    auth.ts            # API key verification (Authorization or x-api-key)
    server.ts           # registers tools on mcp-lite
    db.ts                 # Supabase client (service role)
    embeddings.ts          # OpenAI embeddings call (optional)
    tools/
      projects.ts            # whoami, list_projects, create_project
      memories.ts              # remember, update_memory, forget
      search.ts                  # recall, search_by_tag, list_recent, get_project_context
      access.ts                    # shared visibility/permission helpers
      app.ts                         # open_memory_manager + app-only tools
      util.ts                          # ok()/fail() MCP response helpers
    ui/
      manager_html.ts                    # bundled memory-manager HTML, built from app/
  migrations/            # schema + SQL functions (RLS, search, dedup, counts)
  tests/rls_test.sql       # standalone RLS proof (not run by db reset/push - see CONTRIBUTING.md)
  seed.sql                   # example users/projects for local dev
scripts/
  backfill-embeddings.ts      # one-off embedding backfill after enabling OPENAI_API_KEY
bench/                          # bring-your-own-data benchmark harness
app/                               # memory manager MCP App (separate Vite build)
```

## License

[MIT](./LICENSE).
