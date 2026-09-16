#!/usr/bin/env python3
"""Part B: LLM answer quality benchmark, built on top of Part A's retrieval configs.

Subcommands:
  prepare    - build bench/cache/llm_jobs.jsonl (one job per model x condition x query)
               plus the keyword-mode DB round trip -> bench/results/text_keywords_retrieval.csv
  run        - execute answer jobs then judge jobs (resumable, cached) -> bench/results/llm_raw.csv
  summarize  - build bench/results/llm_summary.csv + append metadata to bench/results/run_meta.json

Usage:
  python3 bench/run_llm.py prepare
  python3 bench/run_llm.py run [--limit-queries N] [--concurrency 4]
  python3 bench/run_llm.py summarize
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
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
from lib import BENCH, DB_URL, load_dataset, psql  # noqa: E402

CACHE_DIR = os.path.join(BENCH, "cache")
RESULTS_DIR = os.path.join(BENCH, "results")
JOBS_PATH = os.path.join(CACHE_DIR, "llm_jobs.jsonl")
CALLS_CACHE_PATH = os.path.join(CACHE_DIR, "llm_calls.jsonl")
IDS_PATH = os.path.join(CACHE_DIR, "ids.json")
RETRIEVAL_RAW_PATH = os.path.join(RESULTS_DIR, "retrieval_raw.csv")
TEXT_KEYWORDS_PATH = os.path.join(RESULTS_DIR, "text_keywords_retrieval.csv")
LLM_RAW_PATH = os.path.join(RESULTS_DIR, "llm_raw.csv")
LLM_SUMMARY_PATH = os.path.join(RESULTS_DIR, "llm_summary.csv")
RUN_META_PATH = os.path.join(RESULTS_DIR, "run_meta.json")

SCRATCH_CWD = os.environ.get("BENCH_SCRATCH_CWD") or tempfile.gettempdir()

# model alias -> (CLI --model value, canonical model id substring used to key modelUsage)
# The default matrix is intentionally small; add more model aliases when needed.
MODELS = {
    "haiku": "haiku",
    "sonnet": "sonnet",
}

CONDITIONS = ["sin_memoria", "texto_keywords", "semantica_actual", "semantica_recomendada", "oraculo"]

MODEL_CONDITIONS = {
    "haiku": CONDITIONS,
    "sonnet": CONDITIONS,
}

AUTHOR = "the maintainer"
PROJECT_SLUG = "bench"

TODAY = os.environ.get("BENCH_TODAY") or date.today().isoformat()

# NOTE: these prompts are in Spanish on purpose, matching the fictional
# placeholder dataset and this project's Spanish FTS configuration. Benchmark
# inputs and outputs are generated locally and must not contain private data.
ANSWER_SYSTEM_PROMPT = (
    f"Fecha de hoy: {TODAY}.\n"
    "Eres el asistente de un equipo, respondiendo preguntas sobre su propio proyecto/negocio "
    "a partir de su memoria compartida. Si se te proporciona un bloque <memoria>, es la memoria compartida del "
    "equipo: úsala como única fuente para responder (un bloque <memoria>[]</memoria> "
    "significa que la búsqueda no encontró ninguna memoria relevante, trátalo igual que si "
    "no supieras la respuesta). Responde en castellano, en como máximo 3 frases, usando solo "
    "información de la que estés seguro, interpretando fechas relativas ('ahora mismo', "
    "'actualmente') respecto a la fecha de hoy indicada arriba. Si la respuesta no está en la "
    "memoria proporcionada ni la sabes con certeza, dilo explícitamente (por ejemplo: \"No "
    "tengo esa información en la memoria\"). No inventes nunca datos, cifras ni fechas."
)

KEYWORD_SYSTEM_PROMPT = (
    "Convierte la pregunta en 2-4 palabras clave de búsqueda en castellano, separadas por "
    "espacios. Responde solo con las palabras."
)

JUDGE_SYSTEM_PROMPT = (
    f"Fecha de hoy: {TODAY}.\n"
    "Eres un juez imparcial que evalúa si una respuesta de un asistente es correcta, dada "
    "una pregunta, una respuesta de referencia, (si existen) los textos de las memorias "
    "'gold' relevantes, y la memoria que efectivamente tenía disponible el asistente al "
    "responder (puede ser distinta de las memorias gold: puede tener menos, más, o "
    "memorias irrelevantes; '(ninguna)' significa que no tenía memoria alguna). No sabes "
    "qué modelo ni qué condición de contexto generó la respuesta candidata: júzgala solo "
    "por su contenido frente a la referencia, las memorias gold, y la memoria que tenía "
    "disponible. Interpreta fechas relativas ('ahora mismo', 'actualmente') respecto a la "
    "fecha de hoy indicada arriba.\n\n"
    "Devuelve EXCLUSIVAMENTE un objeto JSON (sin texto adicional, sin markdown) con esta forma:\n"
    '{"verdict": "correct|partial|incorrect|abstained", "hallucination": true|false, "reason": "<=20 palabras"}\n\n'
    "Reglas:\n"
    "- \"abstained\": la respuesta dice explícitamente que no lo sabe / no tiene esa información, sin inventar datos.\n"
    "- \"correct\": la respuesta coincide en sustancia con la referencia (mismos hechos clave).\n"
    "- \"partial\": acierta parte de la referencia pero omite o difumina datos clave.\n"
    "- \"incorrect\": contradice la referencia, o afirma con seguridad algún dato concreto "
    "(cifra, fecha, nombre, hecho) que no está respaldado NI por la memoria que tenía "
    "disponible el asistente NI por la respuesta de referencia.\n"
    "- Si NO hay memorias gold (pregunta sin respuesta real, tipo 'negative'), lo correcto es "
    "abstenerse sobre el hecho concreto preguntado: verdict \"abstained\" si la respuesta no "
    "afirma nada concreto sobre lo preguntado (aunque mencione de pasada otros hechos "
    "verdaderos presentes en la memoria que tenía disponible, eso no cuenta como alucinación "
    "ni cambia el verdict); si afirma algo concreto e inventado sobre lo preguntado, verdict "
    "\"incorrect\" y hallucination true.\n"
    "- \"hallucination\" es true SOLO si la respuesta afirma con seguridad algún dato concreto "
    "(cifra, fecha, nombre, hecho) que NO está respaldado ni por la memoria que tenía "
    "disponible el asistente ni por la referencia/memorias gold. Un dato correcto que sí "
    "aparece en la memoria que tenía disponible NUNCA es hallucination, aunque no sea lo que "
    "se preguntaba."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def call_key(model_cli: str, system: str, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(model_cli.encode("utf-8"))
    h.update(b"|")
    h.update(system.encode("utf-8"))
    h.update(b"|")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


def load_calls_cache() -> dict:
    cache = {}
    if os.path.exists(CALLS_CACHE_PATH):
        with open(CALLS_CACHE_PATH) as f:
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


_cache_lock_file = None


def append_call_cache(rec: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CALLS_CACHE_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pick_model_id(model_usage: dict, requested_alias: str) -> str:
    """modelUsage can list more than one entry (e.g. an auxiliary haiku helper
    call alongside the main sonnet/opus response) and the requested model is
    not reliably first. Prefer the entry whose key contains the requested
    alias; fall back to the entry with the most output tokens."""
    if not model_usage:
        return requested_alias
    for k in model_usage:
        if requested_alias.lower() in k.lower():
            return k
    return max(model_usage.items(), key=lambda kv: (kv[1] or {}).get("outputTokens", 0))[0]


def claude_call(model_cli: str, system: str, prompt: str, retries: int = 3) -> dict:
    """Runs `claude -p` isolated, returns parsed usage/result dict. Never raises;
    returns a dict with 'error' set on failure after retries."""
    last_err = None
    for attempt in range(retries):
        try:
            r = subprocess.run(
                [
                    "claude", "-p",
                    "--model", model_cli,
                    "--strict-mcp-config",
                    "--tools", "",
                    "--setting-sources", "",
                    "--no-session-persistence",
                    "--system-prompt", system,
                    "--output-format", "json",
                    prompt,
                ],
                cwd=SCRATCH_CWD,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode != 0:
                last_err = f"exit {r.returncode}: {r.stderr[:500]}"
                time.sleep(1.5 * (attempt + 1))
                continue
            data = json.loads(r.stdout)
            if data.get("is_error"):
                last_err = f"is_error: {data.get('result')}"
                time.sleep(1.5 * (attempt + 1))
                continue
            usage = data.get("usage", {})
            model_usage = data.get("modelUsage", {}) or {}
            model_id = pick_model_id(model_usage, model_cli)
            in_tok = usage.get("input_tokens", 0) or 0
            cache_read = usage.get("cache_read_input_tokens", 0) or 0
            cache_create = usage.get("cache_creation_input_tokens", 0) or 0
            return {
                "ok": True,
                "result": data.get("result", ""),
                "input_tokens": in_tok,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
                "input_tokens_total": in_tok + cache_read + cache_create,
                "output_tokens": usage.get("output_tokens", 0),
                "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0),
                "cost_usd": data.get("total_cost_usd", 0.0),
                "duration_ms": data.get("duration_ms", 0),
                "model_id": model_id,
            }
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            time.sleep(1.5 * (attempt + 1))
        except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": last_err}


def cached_call(cache: dict, model_cli: str, system: str, prompt: str) -> dict:
    k = call_key(model_cli, system, prompt)
    if k in cache:
        return cache[k]["response"]
    resp = claude_call(model_cli, system, prompt)
    rec = {"key": k, "model": model_cli, "response": resp}
    append_call_cache(rec)
    cache[k] = rec
    return resp


def compact_memory_json(key: str, content: str, tags, similarity=None) -> dict:
    out = {
        "id": key,
        "project_slug": PROJECT_SLUG,
        "content": content,
        "visibility": "shared",
        "author": AUTHOR,
    }
    if tags:
        out["tags"] = tags
    if similarity is not None:
        out["similarity"] = round(float(similarity), 2)
    return out


def build_memoria_block(memories_json: list, always_include: bool = True) -> str:
    """always_include=True (the default, used for every memory condition) emits
    <memoria>[]</memoria> even when the list is empty, so an empty retrieval is
    never byte-identical to sin_memoria (which omits the block entirely via
    always_include=False)."""
    if not memories_json and not always_include:
        return ""
    return "<memoria>" + json.dumps(memories_json, ensure_ascii=False, separators=(",", ":")) + "</memoria>"


def build_user_prompt(memoria_block: str, question: str) -> str:
    if memoria_block:
        return f"{memoria_block}\nPregunta: {question}"
    return f"Pregunta: {question}"


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def read_retrieval_raw() -> dict:
    """returns {(min_similarity_str, delta_str, limit_str): {query_key: [keys...]}}"""
    idx = {}
    with open(RETRIEVAL_RAW_PATH) as f:
        for row in csv.DictReader(f):
            if row["mode"] != "semantic":
                continue
            ck = (row["min_similarity"], row["delta"], row["limit"])
            idx.setdefault(ck, {})[row["query_key"]] = {
                "keys": row["returned_keys"].split("|") if row["returned_keys"] else [],
                "sims": [float(x) for x in row["similarities"].split("|")] if row["similarities"] else [],
            }
    return idx


def db_counts() -> dict:
    """Global counts in the local database, printed for visibility only.
    Concurrent local processes may change them; the authoritative isolation
    check is db_counts_bench_scoped()."""
    out = {}
    for t in ("users", "projects", "memories", "api_keys"):
        try:
            out[t] = int(psql(f"select count(*) from {t};").strip())
        except Exception as e:  # noqa: BLE001
            out[t] = f"error: {e}"
    return out


def db_counts_bench_scoped() -> dict:
    """Counts scoped to our own bench@local user / 'bench' project - the only
    rows this script ever creates. Must be 0 before and after every run,
    regardless of what else is happening in the shared DB."""
    return {
        "bench_users": int(psql("select count(*) from users where email = 'bench@local';").strip()),
        "bench_projects": int(psql("select count(*) from projects where slug = 'bench';").strip()),
        "bench_memories": int(psql("select count(*) from memories where project_slug = 'bench';").strip()),
    }


def run_keyword_and_db_roundtrip(ds: dict, calls_cache: dict) -> dict:
    """Rewrites all dataset queries into keywords (haiku), (re)creates the bench DB
    rows, runs search_memories_text for each, tears down, verifies counts.
    Returns {query_key: {"keywords": str, "returned_keys": [...]}}."""
    print("== texto_keywords: DB round trip ==")
    baseline = db_counts()
    bench_baseline = db_counts_bench_scoped()
    print("Baseline counts (global, informational):", baseline)
    print("Baseline counts (bench-scoped, must be 0/0/0):", bench_baseline)

    scripts_dir = os.path.join(BENCH, "scripts")
    print("Running setup_db.py (idempotent, cached embeddings)...")
    r = subprocess.run([sys.executable, "setup_db.py"], cwd=scripts_dir)
    if r.returncode != 0:
        raise RuntimeError("setup_db.py failed")

    with open(IDS_PATH) as f:
        ids = json.load(f)
    user_id = ids["user_id"]
    id_to_key = {v: k for k, v in ids["memory_ids"].items()}

    print(f"Rewriting {len(ds['queries'])} queries into keywords via haiku...")
    kw_results = {}
    for i, q in enumerate(ds["queries"], start=1):
        resp = cached_call(calls_cache, "haiku", KEYWORD_SYSTEM_PROMPT, q["query"])
        if not resp.get("ok"):
            print(f"  [WARN] keyword rewrite failed for {q['key']}: {resp.get('error')}")
            keywords = q["query"]
        else:
            keywords = resp["result"].strip()
        kw_results[q["key"]] = {"keywords": keywords}
        if i % 10 == 0 or i == len(ds["queries"]):
            print(f"  [keywords {i}/{len(ds['queries'])}]")

    print("Running search_memories_text for each query's keywords...")
    for q in ds["queries"]:
        keywords = kw_results[q["key"]]["keywords"]
        qsafe = keywords.replace("'", "''")
        sql = (
            "select id from search_memories_text("
            f"p_query => '{qsafe}', p_user => '{user_id}'::uuid, "
            "p_scope => null, match_count => 8);"
        )
        out = psql(sql).strip()
        db_ids = [line.strip() for line in out.splitlines() if line.strip()]
        returned_keys = [id_to_key.get(i, "?") for i in db_ids]
        kw_results[q["key"]]["returned_keys"] = returned_keys

    print("Tearing down bench user...")
    psql("delete from users where email = 'bench@local';")
    after = db_counts()
    bench_after = db_counts_bench_scoped()
    print("Post-delete counts (global, informational):", after)
    print("Post-delete counts (bench-scoped, must be 0/0/0):", bench_after)
    ok = all(v == 0 for v in bench_after.values())
    if not ok:
        print("[ERROR] bench-scoped rows were not fully deleted! %s" % bench_after)
    else:
        print("DB isolation verified: bench@local/bench project/memories fully removed.")
        if baseline != after:
            print("  (NOTE: global counts differ baseline->after - that's other concurrent "
                  "activity on the shared DB, not our isolation; bench-scoped counts are "
                  "the authoritative check and they are clean.)")

    # write text_keywords_retrieval.csv
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(TEXT_KEYWORDS_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query_key", "keywords", "returned_keys", "recall"])
        for q in ds["queries"]:
            gold = set(q["gold"])
            rk = kw_results[q["key"]]["returned_keys"]
            if gold:
                recall = len(gold & set(rk)) / len(gold)
            else:
                recall = 1.0 if not rk else 0.0
            w.writerow([q["key"], kw_results[q["key"]]["keywords"], "|".join(rk), round(recall, 4)])
    print(f"Wrote {TEXT_KEYWORDS_PATH}")

    return {"baseline": baseline, "after": after, "db_ok": ok, "kw_results": kw_results}


def context_for_condition(ds: dict, mem_by_key: dict, sem_idx: dict, kw_results: dict,
                           condition: str, q: dict) -> tuple:
    """Returns (memoria_block, context_keys_list)."""
    qkey = q["key"]
    if condition == "sin_memoria":
        return "", []

    if condition == "texto_keywords":
        returned = kw_results[qkey]["returned_keys"] if kw_results else []
        mems = [compact_memory_json(k, mem_by_key[k]["content"], mem_by_key[k]["tags"])
                for k in returned if k in mem_by_key]
        return build_memoria_block(mems), returned

    if condition == "semantica_actual":
        ck = ("0.3", "", "8")
        entry = sem_idx.get(ck, {}).get(qkey, {"keys": [], "sims": []})
        mems = [
            compact_memory_json(k, mem_by_key[k]["content"], mem_by_key[k]["tags"], sim)
            for k, sim in zip(entry["keys"], entry["sims"]) if k in mem_by_key
        ]
        return build_memoria_block(mems), entry["keys"]

    if condition == "semantica_recomendada":
        ck = ("0.5", "0.1", "8")
        entry = sem_idx.get(ck, {}).get(qkey, {"keys": [], "sims": []})
        mems = [
            compact_memory_json(k, mem_by_key[k]["content"], mem_by_key[k]["tags"], sim)
            for k, sim in zip(entry["keys"], entry["sims"]) if k in mem_by_key
        ]
        return build_memoria_block(mems), entry["keys"]

    if condition == "oraculo":
        gold = q["gold"]
        mems = [compact_memory_json(k, mem_by_key[k]["content"], mem_by_key[k]["tags"])
                for k in gold if k in mem_by_key]
        return build_memoria_block(mems), gold

    raise ValueError(condition)


def cmd_prepare(args):
    ds = load_dataset()
    mem_by_key = {m["key"]: m for m in ds["memories"]}
    calls_cache = load_calls_cache()

    roundtrip = run_keyword_and_db_roundtrip(ds, calls_cache)
    kw_results = roundtrip["kw_results"]

    sem_idx = read_retrieval_raw()

    print("Building answer jobs...")
    jobs = []
    for model in MODELS:
        for condition in MODEL_CONDITIONS[model]:
            for q in ds["queries"]:
                memoria_block, context_keys = context_for_condition(
                    ds, mem_by_key, sem_idx, kw_results, condition, q
                )
                user_prompt = build_user_prompt(memoria_block, q["query"])
                context_tokens_est = len(memoria_block) / 3.5 if memoria_block else 0.0
                jobs.append({
                    "type": "answer",
                    "model": model,
                    "condition": condition,
                    "query_key": q["key"],
                    "query_type": q["type"],
                    "question": q["query"],
                    "reference_answer": q["answer"],
                    "gold_keys": q["gold"],
                    "context_keys": context_keys,
                    "n_context": len(context_keys),
                    "context_tokens_est": round(context_tokens_est, 1),
                    "memoria_block": memoria_block,
                    "system": ANSWER_SYSTEM_PROMPT,
                    "prompt": user_prompt,
                })

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(JOBS_PATH, "w") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(f"Wrote {len(jobs)} answer jobs -> {JOBS_PATH}")
    print(f"DB round trip ok: {roundtrip['db_ok']}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def stratified_query_keys(ds: dict, limit_queries: int) -> list:
    by_type = {}
    for q in ds["queries"]:
        by_type.setdefault(q["type"], []).append(q["key"])
    for v in by_type.values():
        v.sort()
    priority = []
    for t in ("paraphrase", "multi", "negative", "keyword"):
        if t in by_type and by_type[t]:
            priority.append(by_type[t][0])
    rest = sorted(q["key"] for q in ds["queries"] if q["key"] not in priority)
    ordered = priority + rest
    if limit_queries is None:
        return ordered
    return ordered[:limit_queries]


def run_jobs_pool(jobs, calls_cache, concurrency, label):
    results = [None] * len(jobs)
    done = 0
    total = len(jobs)

    def work(i, job):
        resp = cached_call(calls_cache, job["model"], job["system"], job["prompt"])
        return i, resp

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(work, i, j) for i, j in enumerate(jobs)]
        for fut in as_completed(futs):
            i, resp = fut.result()
            results[i] = resp
            done += 1
            j = jobs[i]
            tag = f"{j['model']}/{j.get('condition', '')}/{j.get('query_key', '')}"
            ok = resp.get("ok")
            print(f"[{label} {done}/{total}] {tag} ok={ok}")
    return results


def cmd_run(args):
    ds = load_dataset()
    queries_by_key = {q["key"]: q for q in ds["queries"]}
    mem_by_key = {m["key"]: m for m in ds["memories"]}
    gold_content = {q["key"]: [mem_by_key[k]["content"] for k in q["gold"] if k in mem_by_key]
                    for q in ds["queries"]}

    if not os.path.exists(JOBS_PATH):
        print("No jobs file found, run `prepare` first.", file=sys.stderr)
        sys.exit(1)

    with open(JOBS_PATH) as f:
        all_jobs = [json.loads(line) for line in f if line.strip()]

    selected_keys = set(stratified_query_keys(ds, args.limit_queries))
    jobs = [j for j in all_jobs if j["query_key"] in selected_keys]
    print(f"Selected {len(selected_keys)} query keys -> {len(jobs)} answer jobs "
          f"(of {len(all_jobs)} total)")

    calls_cache = load_calls_cache()

    # ---- answers ----
    answer_resps = run_jobs_pool(jobs, calls_cache, args.concurrency, "answers")

    # ---- judges ----
    judge_jobs = []
    for j, resp in zip(jobs, answer_resps):
        answer_text = resp.get("result", "") if resp.get("ok") else f"[ERROR: {resp.get('error')}]"
        q = queries_by_key[j["query_key"]]
        gold_texts = gold_content[j["query_key"]]
        seen_memoria = j.get("memoria_block", "") or "(ninguna)"
        judge_prompt = (
            f"Pregunta: {q['query']}\n"
            f"Respuesta de referencia: {q['answer']}\n"
            f"Memorias gold: {json.dumps(gold_texts, ensure_ascii=False)}\n"
            f"Memoria que tenía el asistente: {seen_memoria}\n"
            f"Respuesta candidata a evaluar: {answer_text}"
        )
        judge_jobs.append({
            "model": "sonnet",
            "condition": "judge",
            "query_key": j["query_key"],
            "system": JUDGE_SYSTEM_PROMPT,
            "prompt": judge_prompt,
            "answer_text": answer_text,
        })

    judge_resps = run_jobs_pool(judge_jobs, calls_cache, args.concurrency, "judges")

    # ---- combine into llm_raw.csv ----
    rows = []
    for j, aresp, jj, jresp in zip(jobs, answer_resps, judge_jobs, judge_resps):
        answer_ok = aresp.get("ok", False)
        answer_text = aresp.get("result", "") if answer_ok else f"[ERROR: {aresp.get('error')}]"

        verdict, hallucination, reason = "error", "", ""
        if jresp.get("ok"):
            raw = jresp.get("result", "")
            parsed = extract_json_object(raw)
            if parsed:
                verdict = parsed.get("verdict", "error")
                hallucination = bool(parsed.get("hallucination", False))
                reason = str(parsed.get("reason", ""))[:200]
            else:
                verdict = "parse_error"
        score_map = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0, "abstained": 0.0}
        score = score_map.get(verdict, 0.0)
        if j["query_type"] == "negative" and verdict == "abstained":
            score = 1.0

        in_tok = aresp.get("input_tokens", 0) or 0
        cache_read = aresp.get("cache_read_input_tokens", 0) or 0
        cache_create = aresp.get("cache_creation_input_tokens", 0) or 0

        rows.append({
            "model_id": aresp.get("model_id", j["model"]),
            "model_alias": j["model"],
            "condition": j["condition"],
            "query_key": j["query_key"],
            "query_type": j["query_type"],
            "context_keys": "|".join(j["context_keys"]),
            "n_context": j.get("n_context", len(j["context_keys"])),
            "context_tokens_est": j["context_tokens_est"],
            "answer": answer_text,
            "verdict": verdict,
            "score": score,
            "hallucination": hallucination,
            "judge_reason": reason,
            "input_tokens": in_tok,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
            "input_tokens_total": aresp.get("input_tokens_total", in_tok + cache_read + cache_create),
            "output_tokens": aresp.get("output_tokens", 0),
            "thinking_tokens": aresp.get("thinking_tokens", 0),
            "cost_usd": round((aresp.get("cost_usd", 0) or 0) + (jresp.get("cost_usd", 0) or 0), 6),
            "duration_ms": aresp.get("duration_ms", 0),
        })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    fieldnames = [
        "model_id", "model_alias", "condition", "query_key", "query_type",
        "context_keys", "n_context", "context_tokens_est", "answer", "verdict", "score",
        "hallucination", "judge_reason", "input_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "input_tokens_total", "output_tokens",
        "thinking_tokens", "cost_usd", "duration_ms",
    ]
    # merge with any pre-existing rows for query keys not in this run (resumability
    # across --limit-queries runs): keep old rows whose (model,condition,query_key)
    # isn't being overwritten now.
    existing = []
    if os.path.exists(LLM_RAW_PATH):
        with open(LLM_RAW_PATH) as f:
            existing = list(csv.DictReader(f))
    new_keys = {(r["model_alias"], r["condition"], r["query_key"]) for r in rows}
    kept = [r for r in existing if (r["model_alias"], r["condition"], r["query_key"]) not in new_keys]
    all_rows = kept + rows

    with open(LLM_RAW_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Wrote {len(all_rows)} rows ({len(rows)} new/updated) -> {LLM_RAW_PATH}")


def extract_json_object(text: str):
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    snippet = text[start:end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        # try trimming trailing commas etc. - best effort
        try:
            return json.loads(snippet.replace(",\n}", "\n}"))
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def bootstrap_ci(values, n_resamples=1000, seed=42):
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_resamples)]
    hi = means[min(int(0.975 * n_resamples), n_resamples - 1)]
    return (round(lo, 4), round(hi, 4))


def cmd_summarize(args):
    if not os.path.exists(LLM_RAW_PATH):
        print("No llm_raw.csv found, run `run` first.", file=sys.stderr)
        sys.exit(1)
    with open(LLM_RAW_PATH) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["score"] = float(r["score"])
        r["hallucination"] = r["hallucination"] in ("True", "true", "1")
        r["input_tokens_total"] = float(r.get("input_tokens_total") or r.get("input_tokens") or 0)
        r["output_tokens"] = float(r["output_tokens"] or 0)
        r["thinking_tokens"] = float(r["thinking_tokens"] or 0)
        r["cost_usd"] = float(r["cost_usd"] or 0)
        r["duration_ms"] = float(r["duration_ms"] or 0)

    from collections import defaultdict
    by_mc = defaultdict(list)
    by_mc_type = defaultdict(list)
    for r in rows:
        by_mc[(r["model_alias"], r["condition"])].append(r)
        by_mc_type[(r["model_alias"], r["condition"], r["query_type"])].append(r)

    def agg(group_rows, model, condition, qtype):
        n = len(group_rows)
        scores = [r["score"] for r in group_rows]
        neg = [r for r in group_rows if r["query_type"] == "negative"]
        mean_score = sum(scores) / n if n else 0.0
        lo, hi = bootstrap_ci(scores)
        halluc = sum(1 for r in group_rows if r["hallucination"]) / n if n else 0.0
        halluc_neg = (sum(1 for r in neg if r["hallucination"]) / len(neg)) if neg else ""
        return {
            "model_alias": model,
            "condition": condition,
            "query_type": qtype,
            "n": n,
            "mean_score": round(mean_score, 4),
            "ci_low": lo,
            "ci_high": hi,
            "hallucination_rate": round(halluc, 4),
            "hallucination_rate_negatives": halluc_neg if halluc_neg == "" else round(halluc_neg, 4),
            "mean_input_tokens": round(sum(r["input_tokens_total"] for r in group_rows) / n, 1) if n else 0,
            "mean_output_tokens": round(sum(r["output_tokens"] for r in group_rows) / n, 1) if n else 0,
            "mean_thinking_tokens": round(sum(r["thinking_tokens"] for r in group_rows) / n, 1) if n else 0,
            "mean_cost_usd": round(sum(r["cost_usd"] for r in group_rows) / n, 6) if n else 0,
            "mean_duration_ms": round(sum(r["duration_ms"] for r in group_rows) / n, 1) if n else 0,
        }

    summary_rows = []
    for (model, condition), grp in by_mc.items():
        summary_rows.append(agg(grp, model, condition, "ALL"))
    for (model, condition, qtype), grp in by_mc_type.items():
        summary_rows.append(agg(grp, model, condition, qtype))

    fieldnames = [
        "model_alias", "condition", "query_type", "n", "mean_score", "ci_low", "ci_high",
        "hallucination_rate", "hallucination_rate_negatives",
        "mean_input_tokens", "mean_output_tokens", "mean_thinking_tokens",
        "mean_cost_usd", "mean_duration_ms",
    ]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(LLM_SUMMARY_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(summary_rows)
    print(f"Wrote {len(summary_rows)} summary rows -> {LLM_SUMMARY_PATH}")

    meta = {}
    if os.path.exists(RUN_META_PATH):
        with open(RUN_META_PATH) as f:
            meta = json.load(f)
    meta["part_b"] = {
        "generated_at": time.strftime("%Y-%m-%d"),
        "models": list(MODELS.keys()),
        "conditions": CONDITIONS,
        "model_conditions": MODEL_CONDITIONS,
        "n_answer_rows": len(rows),
        "judge_model": "sonnet",
        "score_map": {"correct": 1.0, "partial": 0.5, "incorrect": 0.0, "abstained": 0.0,
                      "abstained_on_negative": 1.0},
        "bootstrap_resamples": 1000,
    }
    with open(RUN_META_PATH, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"Appended part_b metadata -> {RUN_META_PATH}")


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("prepare")

    p_run = sub.add_parser("run")
    p_run.add_argument("--limit-queries", type=int, default=None)
    p_run.add_argument("--concurrency", type=int, default=4)

    sub.add_parser("summarize")

    args = p.parse_args()
    if args.cmd == "prepare":
        cmd_prepare(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "summarize":
        cmd_summarize(args)


if __name__ == "__main__":
    main()
