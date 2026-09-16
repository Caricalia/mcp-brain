#!/usr/bin/env python3
"""Part C: agent-level benchmark — does the brain MCP make a coding agent answer
project questions better/faster/cheaper than the same agent without it?

Subcommands:
  setup      - create bench-agent@local user + personal project + insert memories
               via the real `remember` MCP tool, write bench/cache/agent_token.txt
               and bench/cache/agent_mcp.json. Idempotent.
  run        - run the agent matrix (model x condition x question x rep) via
               `claude -p`, judge each answer blindly, cache to
               bench/cache/agent_runs.jsonl (resumable)
  summarize  - build bench/results/agent_raw.csv, agent_summary.csv, and append
               the Part C section to bench/results/run_meta.json
  teardown   - delete bench-agent@local (cascades project + memories)

Usage:
  python3 bench/run_agent.py setup
  python3 bench/run_agent.py run --limit 4 --reps 1 --models haiku,sonnet
  python3 bench/run_agent.py summarize
  python3 bench/run_agent.py teardown
"""
import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
BENCH = ROOT
sys.path.insert(0, os.path.join(BENCH, "scripts"))
from lib import psql  # noqa: E402

DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
CACHE_DIR = os.path.join(BENCH, "cache")
RESULTS_DIR = os.path.join(BENCH, "results")
DATASET_PATH = os.path.join(BENCH, "agent_dataset.json")
TOKEN_PATH = os.path.join(CACHE_DIR, "agent_token.txt")
MCP_CONFIG_PATH = os.path.join(CACHE_DIR, "agent_mcp.json")
RUNS_PATH = os.path.join(CACHE_DIR, "agent_runs.jsonl")
JUDGE_CACHE_PATH = os.path.join(CACHE_DIR, "agent_judge.jsonl")
RAW_CSV_PATH = os.path.join(RESULTS_DIR, "agent_raw.csv")
SUMMARY_CSV_PATH = os.path.join(RESULTS_DIR, "agent_summary.csv")
RUN_META_PATH = os.path.join(RESULTS_DIR, "run_meta.json")

_SCRATCH_BASE = os.environ.get("BENCH_SCRATCH_DIR") or os.path.join(tempfile.gettempdir(), "mcp-brain-bench")
SANDBOX_REPO = os.environ.get("BENCH_SANDBOX_REPO") or os.path.join(_SCRATCH_BASE, "brain-sandbox", "mcp-brain")
ISOLATED_CWD = os.environ.get("BENCH_ISOLATED_CWD") or os.path.join(_SCRATCH_BASE, "brain-agent-cwd")  # for conditions without repo access

BENCH_EMAIL = "bench-agent@local"
BENCH_NAME = "bench agent"
PROJECT_SLUG = "mcp-brain-agentbench"
BRAIN_URL_LOCAL = "http://127.0.0.1:54321/functions/v1/brain/mcp"

# The default matrix is intentionally small; add more model aliases when needed.
MODELS = ["haiku", "sonnet"]
CONDITIONS = ["repo_sin_memoria", "repo_con_brain", "solo_brain", "sin_nada"]
REPS_DEFAULT = 2
MAX_TURNS = 25
TIMEOUT_S = 300
RETRIES = 2

# NOTE: prompts below are in Spanish on purpose, matching agent_dataset.json (see
# build_dataset.py docstring: mcp-brain's `recall` FTS path is hardwired to
# to_tsvector('spanish', ...), so the agent's questions/memories are Spanish too).
# Code comments and report prose stay in English.
COMMON_SYSTEM_PROMPT = (
    "Eres el agente de un compañero de equipo que te hace una pregunta sobre este "
    "proyecto/repositorio (mcp-brain) o sobre decisiones de contexto relacionadas. "
    "Responde en castellano, en como máximo 3 frases. Si no encuentras la respuesta "
    "con las herramientas disponibles, dilo explícitamente en vez de inventar "
    "(por ejemplo: \"No lo sé\" o \"No encuentro esa información\"). No modifiques "
    "ningún fichero."
)

BRAIN_CONVENTION = (
    "\n\nConvención de memoria compartida (MCP brain): este repo corresponde al "
    f"proyecto `{PROJECT_SLUG}` en el MCP `brain`. Al empezar, llama a "
    f"`get_project_context` con project_slug \"{PROJECT_SLUG}\". Usa `recall` "
    "cuando te falte contexto sobre el proyecto o decisiones relacionadas."
)

JUDGE_SYSTEM_PROMPT = (
    "Eres un juez imparcial que evalúa si la respuesta de un asistente a una "
    "pregunta de un compañero de equipo es correcta, dada una pregunta y una "
    "respuesta de referencia. No sabes qué modelo ni qué condición de acceso a "
    "herramientas generó la respuesta candidata (puede venir de una exploración "
    "de código, de una memoria compartida, de ambas o de ninguna): júzgala solo "
    "por su contenido frente a la referencia.\n\n"
    "Devuelve EXCLUSIVAMENTE un objeto JSON (sin texto adicional, sin markdown) "
    "con esta forma:\n"
    '{"verdict": "correct|partial|incorrect|abstained", "hallucination": true|false, "reason": "<=20 palabras"}\n\n'
    "Reglas:\n"
    "- \"abstained\": la respuesta dice explícitamente que no lo sabe / no lo "
    "encuentra, sin inventar datos concretos.\n"
    "- \"correct\": coincide en sustancia con la referencia (mismos hechos clave).\n"
    "- \"partial\": acierta parte de la referencia pero omite o difumina datos clave.\n"
    "- \"incorrect\": contradice la referencia o inventa datos no respaldados por ella.\n"
    "- Si la referencia es \"No hay esa información disponible.\" (pregunta sin "
    "respuesta real), lo correcto es abstenerse: verdict \"abstained\" si la "
    "respuesta no afirma nada concreto; si afirma algo concreto e inventado, "
    "verdict \"incorrect\" y hallucination true.\n"
    "- \"hallucination\" es true si la respuesta afirma con seguridad algún dato "
    "concreto (cifra, fecha, nombre, hecho) no respaldado por la referencia."
)

# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def cmd_setup(args):
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(ISOLATED_CWD, exist_ok=True)

    # 1. sandbox repo
    subprocess.run([os.path.join(BENCH, "scripts", "agent_sandbox.sh")], check=True)

    # 2. wait for recall defaults to show min_similarity 0.5 (other agent's change),
    #    poll every 30s up to 15 min, then proceed regardless.
    deadline = time.time() + 15 * 60
    saw_05 = False
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["curl", "-s", BRAIN_URL_LOCAL, "-X", "POST",
                 "-H", "Content-Type: application/json",
                 "-H", f"Authorization: Bearer {_read_token_if_any()}",
                 "-d", '{"jsonrpc":"2.0","method":"tools/list","id":1}'],
                capture_output=True, text=True, timeout=15,
            )
            if '"default":0.5' in r.stdout.replace(" ", "") or '"default": 0.5' in r.stdout:
                saw_05 = True
                break
        except Exception:
            pass
        if not os.path.exists(TOKEN_PATH):
            break  # no token yet, nothing to poll with; proceed to create it below then recheck once
        time.sleep(30)

    # 3. DB user + project + token (idempotent)
    psql(f"insert into users (email, name, is_admin) values ('{BENCH_EMAIL}', '{BENCH_NAME}', false) on conflict (email) do nothing;")
    token = psql(f"select issue_api_key('{BENCH_EMAIL}', 'bench-agent');").strip()
    if not token:
        token = _read_token_if_any()
    if token:
        with open(TOKEN_PATH, "w") as f:
            f.write(token)

    if not saw_05:
        r = subprocess.run(
            ["curl", "-s", BRAIN_URL_LOCAL, "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", f"Authorization: Bearer {token}",
             "-d", '{"jsonrpc":"2.0","method":"tools/list","id":1}'],
            capture_output=True, text=True, timeout=15,
        )
        saw_05 = '"default":0.5' in r.stdout.replace(" ", "") or '"default": 0.5' in r.stdout
        print(f"[setup] recall min_similarity default 0.5 present: {saw_05}", file=sys.stderr)

    with open(MCP_CONFIG_PATH, "w") as f:
        json.dump({
            "mcpServers": {
                "brain": {
                    "type": "http",
                    "url": BRAIN_URL_LOCAL,
                    "headers": {"Authorization": f"Bearer {token}"},
                }
            }
        }, f, indent=2)

    # 4. insert memories via remember (skip if already present)
    existing = int(psql(f"select count(*) from memories where project_slug='{PROJECT_SLUG}';").strip() or "0")
    ds = json.load(open(DATASET_PATH))
    if existing >= len(ds["memories"]):
        print(f"[setup] {existing} memories already present for {PROJECT_SLUG}, skipping insert.")
    else:
        import urllib.request

        def call(name, call_args):
            body = json.dumps({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": call_args}, "id": 1}).encode()
            req = urllib.request.Request(BRAIN_URL_LOCAL, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        # ensure project exists
        pr = call("create_project", {"slug": PROJECT_SLUG, "name": "mcp-brain (agent bench)", "description": "Personal project for the agent benchmark (Part C)"})
        ok = 0
        for m in ds["memories"]:
            res = call("remember", {"content": m["content"], "project_slug": PROJECT_SLUG, "tags": m.get("tags", [])})
            txt = res["result"]["content"][0]["text"]
            parsed = json.loads(txt)
            if "duplicate_of" not in parsed and not res["result"].get("isError"):
                ok += 1
        print(f"[setup] inserted {ok}/{len(ds['memories'])} memories")

    counts = {t: psql(f"select count(*) from {t};").strip() for t in ("users", "projects", "memories", "api_keys")}
    print("[setup] db counts:", counts)
    print("[setup] done.")


def _read_token_if_any():
    if os.path.exists(TOKEN_PATH):
        return open(TOKEN_PATH).read().strip()
    return ""


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def load_dataset():
    return json.load(open(DATASET_PATH))


def stratified_pilot_questions(ds, n=4):
    by_type = {}
    for q in ds["queries"]:
        by_type.setdefault(q["type"], []).append(q)
    picks = []
    plan = [("code", 1), ("decision", 2), ("negative", 1)]
    for t, k in plan:
        picks.extend(by_type.get(t, [])[:k])
    return picks


def run_key(model, condition, qkey, rep):
    h = hashlib.sha256(f"{model}|{condition}|{qkey}|{rep}".encode()).hexdigest()
    return h


def load_runs_cache():
    cache = {}
    if os.path.exists(RUNS_PATH):
        with open(RUNS_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cache[rec["key"]] = rec
    return cache


def append_run(rec):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(RUNS_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def condition_cli_args(condition):
    """Returns (extra_args, cwd, system_prompt_suffix)."""
    if condition == "repo_sin_memoria":
        return (
            ["--strict-mcp-config", "--tools", "Read,Grep,Glob,Bash",
             "--setting-sources", "", "--no-session-persistence",
             "--permission-mode", "bypassPermissions"],
            SANDBOX_REPO,
            "\n\nTienes acceso de solo lectura al repo (Read, Grep, Glob, Bash). No modifiques ficheros; usa Bash solo para listar/leer (ls, cat, find, grep), nunca para escribir.",
        )
    if condition == "repo_con_brain":
        return (
            ["--strict-mcp-config", "--mcp-config", MCP_CONFIG_PATH,
             "--tools", "Read,Grep,Glob,Bash,mcp__brain__*",
             "--setting-sources", "", "--no-session-persistence",
             "--permission-mode", "bypassPermissions"],
            SANDBOX_REPO,
            "\n\nTienes acceso de solo lectura al repo (Read, Grep, Glob, Bash) y al MCP `brain`. No modifiques ficheros; usa Bash solo para listar/leer." + BRAIN_CONVENTION,
        )
    if condition == "solo_brain":
        return (
            ["--strict-mcp-config", "--mcp-config", MCP_CONFIG_PATH,
             "--tools", "mcp__brain__*",
             "--setting-sources", "", "--no-session-persistence",
             "--permission-mode", "bypassPermissions"],
            ISOLATED_CWD,
            "\n\nSolo tienes acceso al MCP `brain` (no al código del repo)." + BRAIN_CONVENTION,
        )
    if condition == "sin_nada":
        return (
            ["--strict-mcp-config", "--tools", "",
             "--setting-sources", "", "--no-session-persistence",
             "--permission-mode", "bypassPermissions"],
            ISOLATED_CWD,
            "\n\nNo tienes ninguna herramienta disponible: responde solo con lo que ya sepas.",
        )
    raise ValueError(condition)


def pick_model_id(model_usage, requested_alias):
    """modelUsage can list more than one entry (e.g. an auxiliary haiku helper
    call alongside the main sonnet/opus response) and the requested model is
    not reliably first. Prefer the entry whose key contains the requested
    alias; fall back to the entry with the most output tokens. Note this means
    Claude Code's reported cost/token totals for a run may include usage from
    a helper model in addition to the requested one."""
    if not model_usage:
        return requested_alias
    for k in model_usage:
        if requested_alias.lower() in k.lower():
            return k
    return max(model_usage.items(), key=lambda kv: (kv[1] or {}).get("outputTokens", 0))[0]


def claude_call_agentic(model, condition, prompt, retries=RETRIES):
    extra_args, cwd, sys_suffix = condition_cli_args(condition)
    system = COMMON_SYSTEM_PROMPT + sys_suffix
    last_err = None
    for attempt in range(retries + 1):
        try:
            cmd = ["claude", "-p", "--model", model,
                   "--system-prompt", system,
                   "--output-format", "stream-json", "--verbose",
                   "--max-turns", str(MAX_TURNS)] + extra_args + [prompt]
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S)
            if r.returncode != 0:
                last_err = f"exit {r.returncode}: {r.stderr[:800]}"
                time.sleep(2 * (attempt + 1))
                continue
            tool_calls = {}
            final = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "assistant":
                    msg = ev.get("message", {})
                    for block in msg.get("content", []) or []:
                        if block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            tool_calls[name] = tool_calls.get(name, 0) + 1
                if ev.get("type") == "result":
                    final = ev
            if final is None:
                last_err = "no result event"
                time.sleep(2 * (attempt + 1))
                continue
            if final.get("is_error"):
                last_err = f"is_error: {final.get('result')}"
                time.sleep(2 * (attempt + 1))
                continue
            usage = final.get("usage", {}) or {}
            model_usage = final.get("modelUsage", {}) or {}
            model_id = pick_model_id(model_usage, model)
            total_tokens = (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0))
            return {
                "ok": True,
                "answer": final.get("result", ""),
                "duration_ms": final.get("duration_ms", 0),
                "num_turns": final.get("num_turns", 0),
                "total_cost_usd": final.get("total_cost_usd", 0.0),
                "usage": usage,
                "total_tokens": total_tokens,
                "tool_calls": tool_calls,
                "model_id": model_id,
            }
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            time.sleep(2 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return {"ok": False, "error": last_err}


def judge_key(question, reference, candidate):
    return hashlib.sha256(f"{question}|{reference}|{candidate}".encode()).hexdigest()


def load_judge_cache():
    cache = {}
    if os.path.exists(JUDGE_CACHE_PATH):
        with open(JUDGE_CACHE_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    cache[rec["key"]] = rec["verdict"]
                except json.JSONDecodeError:
                    continue
    return cache


def append_judge(rec):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(JUDGE_CACHE_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def judge(question, reference, candidate, retries=RETRIES):
    k = judge_key(question, reference, candidate)
    cache = load_judge_cache()
    if k in cache:
        return cache[k]
    prompt = f"Pregunta: {question}\nRespuesta de referencia: {reference}\nRespuesta candidata: {candidate}"
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(
                ["claude", "-p", "--model", "sonnet",
                 "--strict-mcp-config", "--tools", "",
                 "--setting-sources", "", "--no-session-persistence",
                 "--system-prompt", JUDGE_SYSTEM_PROMPT,
                 "--output-format", "json", prompt],
                cwd=ISOLATED_CWD, capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                last_err = r.stderr[:500]
                time.sleep(1.5 * (attempt + 1))
                continue
            data = json.loads(r.stdout)
            if data.get("is_error"):
                last_err = str(data.get("result"))
                time.sleep(1.5 * (attempt + 1))
                continue
            text = data.get("result", "").strip()
            if text.startswith("```"):
                text = text.strip("`").split("\n", 1)[-1]
            verdict = json.loads(text)
            append_judge({"key": k, "verdict": verdict})
            return verdict
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    verdict = {"verdict": "error", "hallucination": False, "reason": last_err or "judge failed"}
    append_judge({"key": k, "verdict": verdict})
    return verdict


def score_of(qtype, verdict):
    v = verdict.get("verdict")
    if qtype == "negative":
        return 1.0 if v == "abstained" else (0.5 if v == "partial" else 0.0)
    if v == "correct":
        return 1.0
    if v == "partial":
        return 0.5
    return 0.0


def do_one(model, condition, q, rep):
    key = run_key(model, condition, q["key"], rep)
    cache = load_runs_cache()
    if key in cache and cache[key].get("ok"):
        rec = cache[key]
    else:
        result = claude_call_agentic(model, condition, q["query"])
        rec = {"key": key, "model": model, "condition": condition, "question_key": q["key"],
               "question_type": q["type"], "rep": rep, **result}
        append_run(rec)
    if rec.get("ok"):
        verdict = judge(q["query"], q["answer"], rec.get("answer", ""))
        rec = dict(rec)
        rec["verdict"] = verdict.get("verdict")
        rec["hallucination"] = verdict.get("hallucination")
        rec["score"] = score_of(q["type"], verdict)
    else:
        rec = dict(rec)
        rec["verdict"] = "failed"
        rec["hallucination"] = False
        rec["score"] = 0.0
    return rec


def cmd_run(args):
    ds = load_dataset()
    if args.pilot:
        questions = stratified_pilot_questions(ds, 4)
        models = ["haiku", "sonnet"]
        conditions = CONDITIONS
        reps = 1
    else:
        questions = ds["queries"][: args.limit] if args.limit else ds["queries"]
        models = args.models.split(",") if args.models else MODELS
        conditions = CONDITIONS
        reps = args.reps

    jobs = []
    for model in models:
        for condition in conditions:
            for q in questions:
                for rep in range(reps):
                    jobs.append((model, condition, q, rep))

    print(f"[run] {len(jobs)} jobs ({len(models)} models x {len(conditions)} conditions x {len(questions)} questions x {reps} reps)")

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(do_one, m, c, q, r): (m, c, q["key"], r) for m, c, q, r in jobs}
        for fut in as_completed(futs):
            m, c, qk, r = futs[fut]
            try:
                rec = fut.result()
                results.append(rec)
                print(f"[run] done {m}/{c}/{qk}/rep{r}: score={rec.get('score')} verdict={rec.get('verdict')}")
            except Exception as e:  # noqa: BLE001
                print(f"[run] FAILED {m}/{c}/{qk}/rep{r}: {e}")

    print(f"[run] completed {len(results)}/{len(jobs)}")


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def bootstrap_ci(values, n_boot=1000, seed=0):
    if not values:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[min(int(0.975 * n_boot), n_boot - 1)]
    return (sum(values) / n, lo, hi)


def cmd_summarize(args):
    ds = load_dataset()
    q_by_key = {q["key"]: q for q in ds["queries"]}
    cache = load_runs_cache()
    jcache = {}
    if os.path.exists(JUDGE_CACHE_PATH):
        with open(JUDGE_CACHE_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                jcache[rec["key"]] = rec["verdict"]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    for rec in cache.values():
        q = q_by_key.get(rec["question_key"])
        if q is None:
            continue
        answer = rec.get("answer", "")
        jk = judge_key(q["query"], q["answer"], answer) if rec.get("ok") else None
        verdict = jcache.get(jk, {"verdict": "failed", "hallucination": False, "reason": ""}) if jk else {"verdict": "failed", "hallucination": False, "reason": rec.get("error", "")}
        score = score_of(q["type"], verdict) if rec.get("ok") else 0.0
        usage = rec.get("usage", {}) or {}
        tool_calls = rec.get("tool_calls", {}) or {}
        rows.append({
            "model": rec.get("model"),
            "model_id": rec.get("model_id", rec.get("model")),
            "condition": rec["condition"],
            "question_key": rec["question_key"],
            "question_type": q["type"],
            "rep": rec["rep"],
            "answer": answer,
            "duration_ms": rec.get("duration_ms", 0),
            "num_turns": rec.get("num_turns", 0),
            "total_cost_usd": rec.get("total_cost_usd", 0.0),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "total_tokens": rec.get("total_tokens", 0),
            "tool_calls_json": json.dumps(tool_calls, ensure_ascii=False),
            "n_tool_calls": sum(tool_calls.values()),
            "ok": rec.get("ok", False),
            "verdict": verdict.get("verdict"),
            "hallucination": verdict.get("hallucination"),
            "score": score,
        })

    with open(RAW_CSV_PATH, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for row in rows:
                w.writerow(row)
    print(f"[summarize] wrote {len(rows)} rows -> {RAW_CSV_PATH}")

    # summary by model x condition, and model x condition x type
    def agg(group_rows):
        scores = [r["score"] for r in group_rows]
        durations = sorted(r["duration_ms"] for r in group_rows)
        costs = [r["total_cost_usd"] for r in group_rows]
        toks = [r["total_tokens"] for r in group_rows]
        turns = [r["num_turns"] for r in group_rows]
        halluc = [1 if r["hallucination"] else 0 for r in group_rows]
        mean_score, lo, hi = bootstrap_ci(scores)
        n = len(durations)
        median_dur = durations[n // 2] if n else 0
        return {
            "n": n,
            "accuracy": round(mean_score, 3),
            "accuracy_ci_lo": round(lo, 3),
            "accuracy_ci_hi": round(hi, 3),
            "median_duration_ms": median_dur,
            "mean_duration_ms": round(sum(durations) / n, 1) if n else 0,
            "mean_cost_usd": round(sum(costs) / n, 5) if n else 0,
            "mean_total_tokens": round(sum(toks) / n, 1) if n else 0,
            "mean_turns": round(sum(turns) / n, 2) if n else 0,
            "hallucination_rate": round(sum(halluc) / n, 3) if n else 0,
        }

    summary_rows = []
    from itertools import groupby

    def keyf(r):
        return (r["model"], r["condition"])

    for k, g in groupby(sorted(rows, key=keyf), key=keyf):
        g = list(g)
        s = agg(g)
        summary_rows.append({"model": k[0], "condition": k[1], "question_type": "all", **s})

    def keyf2(r):
        return (r["model"], r["condition"], r["question_type"])

    for k, g in groupby(sorted(rows, key=keyf2), key=keyf2):
        g = list(g)
        s = agg(g)
        summary_rows.append({"model": k[0], "condition": k[1], "question_type": k[2], **s})

    with open(SUMMARY_CSV_PATH, "w", newline="") as f:
        if summary_rows:
            fieldnames = ["model", "condition", "question_type", "n", "accuracy", "accuracy_ci_lo",
                          "accuracy_ci_hi", "median_duration_ms", "mean_duration_ms", "mean_cost_usd",
                          "mean_total_tokens", "mean_turns", "hallucination_rate"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in summary_rows:
                w.writerow(row)
    print(f"[summarize] wrote {len(summary_rows)} summary rows -> {SUMMARY_CSV_PATH}")

    meta = {}
    if os.path.exists(RUN_META_PATH):
        try:
            meta = json.load(open(RUN_META_PATH))
        except Exception:
            meta = {}
    meta["part_c_agent_benchmark"] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dataset": os.path.relpath(DATASET_PATH, ROOT),
        "n_runs_cached": len(cache),
        "n_rows_summarized": len(rows),
        "models": sorted({r["model"] for r in rows}),
        "conditions": CONDITIONS,
        "project_slug": PROJECT_SLUG,
        "sandbox_repo": SANDBOX_REPO,
    }
    with open(RUN_META_PATH, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"[summarize] updated {RUN_META_PATH}")


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------

def cmd_teardown(args):
    before = {t: psql(f"select count(*) from {t};").strip() for t in ("users", "projects", "memories", "api_keys")}
    psql(f"delete from users where email = '{BENCH_EMAIL}';")
    after = {t: psql(f"select count(*) from {t};").strip() for t in ("users", "projects", "memories", "api_keys")}
    print("[teardown] before:", before)
    print("[teardown] after:", after)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup")

    pr = sub.add_parser("run")
    pr.add_argument("--limit", type=int, default=None)
    pr.add_argument("--reps", type=int, default=REPS_DEFAULT)
    pr.add_argument("--models", type=str, default=None, help="comma-separated: haiku,sonnet")
    pr.add_argument("--concurrency", type=int, default=3)
    pr.add_argument("--pilot", action="store_true", help="stratified 4-question pilot, reps=1")

    sub.add_parser("summarize")
    sub.add_parser("teardown")

    args = p.parse_args()
    if args.cmd == "setup":
        cmd_setup(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "summarize":
        cmd_summarize(args)
    elif args.cmd == "teardown":
        cmd_teardown(args)


if __name__ == "__main__":
    main()
