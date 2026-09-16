# mcp-brain benchmark harness

This directory publishes a **bring-your-own-data benchmark harness**, not
experiment results. It contains code and fictional placeholder examples only.
Create your own synthetic or approved dataset locally and keep generated
datasets, results, charts, logs, and model outputs out of Git.

There are three parts, each answering a different question:

## Part A — Retrieval quality (`run_retrieval.py`)

No LLM calls, embeddings only. Sweeps `recall`'s parameters (`min_similarity`,
a relative cutoff `delta`, `limit`) against a set of memories/queries with
known gold answers, comparing semantic search (`match_memories`) against
full-text search (`search_memories_text`), to pick the retrieval config with
the best recall-per-token.

```
python3 bench/run_retrieval.py            # full: dataset + DB + sweep + charts
python3 bench/run_retrieval.py --no-setup # reuse rows already in the DB
python3 bench/run_retrieval.py --charts-only
```

Requires: local Supabase running (`supabase start`), `psql` on `PATH`, and
`OPENAI_API_KEY` set in `supabase/functions/.env` (embeddings are cached in
`bench/cache/embeddings.json`, so reruns don't repeat API calls unless the
dataset text changes).

The embedding calls send the dataset's memory and query text to OpenAI. Part B
and Part C also send prompts and answers to Claude. Use only synthetic or
approved data when running the benchmark.

## Part B — LLM answer quality with/without memory (`run_llm.py`)

Builds on Part A's retrieval configs. For each question, asks an isolated
LLM (`claude -p`, no tools) to answer with different context conditions (no
memory, full-text-with-keywords, semantic-current, semantic-recommended,
oracle/gold-memory-injected), then has a separate model judge each answer
blind (correct/partial/incorrect/abstained + hallucination flag) against a
reference answer.

```
python3 bench/run_llm.py prepare
python3 bench/run_llm.py run [--limit-queries N] [--concurrency 4] [--models haiku,sonnet]
python3 bench/run_llm.py summarize
```

The model matrix (`MODELS` near the top of the file) is intentionally small;
add more model aliases when you want additional data points.

## Part C — Agentic: Claude Code with/without the `brain` MCP (`run_agent.py`)

The most realistic test: a real Claude Code agent (`claude -p` with tools)
working on an isolated copy of this repo, with four tool-access conditions
(repo only, repo + `brain` MCP, `brain` MCP only, nothing), scored the same
way as Part B (blind judge, correctness + hallucination).

```
python3 bench/run_agent.py setup       # creates an isolated bench-agent@local user + project
python3 bench/run_agent.py run --reps 2 --models haiku,sonnet
python3 bench/run_agent.py summarize
python3 bench/run_agent.py teardown    # deletes the bench user (cascades project + memories)
```

`setup` builds a clean, secret-free copy of the repo for the agent to explore
(`scripts/agent_sandbox.sh`) under a scratch directory — override the
location with the `BENCH_SCRATCH_DIR` / `BENCH_SANDBOX_REPO` /
`BENCH_ISOLATED_CWD` env vars if you don't want the OS temp dir default.
Because the sandbox script uses `rsync --delete`, a custom
`BENCH_SANDBOX_REPO` also requires the explicit `BENCH_ALLOW_DELETE=1` opt-in.
`teardown` only ever deletes the `bench-agent@local` user it created; it
never touches other users' data. Before running at scale, check
`whoami`'s `search_mode` against your local `brain` endpoint — if
`OPENAI_API_KEY` isn't configured, `brain` falls back to full-text search and
Part C measures text-mode retrieval, not semantic.

## Bringing your own data

Both dataset files are plain JSON, not checked in:

- **`bench/dataset.json`** (Parts A & B): `{"memories": [...], "queries": [...]}`.
  Each memory is `{"key", "content", "tags"}`. Each query is
  `{"key", "query", "type", "gold", "answer"}`, where `type` is one of
  `paraphrase`, `keyword`, `multi`, `negative`, `gold` is a list of memory
  keys (empty for `negative`), and `answer` is a short reference answer used
  by the judge. `bench/scripts/build_dataset.py` is a runnable skeleton with
  a few obviously fictional placeholder entries — replace them with your own
  and run it to produce `dataset.json`.
- **`bench/agent_dataset.json`** (Part C): `{"memories": [...], "queries": [...]}`
  with the same memory shape, and queries as
  `{"key", "type", "query", "answer"}` where `type` is one of `code`
  (answerable by reading the repo), `decision` (answerable only from
  memory — verify with a repo-wide grep that the fact genuinely isn't
  documented anywhere, or the comparison is meaningless), or `negative` (no
  real answer, to measure fabrication).

Match the dataset's language to your schema's full-text search
configuration (`to_tsvector('<language>', content)` in your migrations) —
mismatched languages give the text-mode condition unrepresentative stemming
and stop-words, and the text-vs-semantic comparison stops being a fair
comparison of retrieval methods and starts being a comparison of language
support.

## Isolation discipline

Every part creates its own throwaway user (`bench@local` for A/B,
`bench-agent@local` for C) and, where relevant, its own project — never
writes to or reads from real user data, and deletes what it created at the
end. All three parts run against a **local** Supabase stack
(`127.0.0.1:54321`/`:54322`); none of this is meant to run against
production.
