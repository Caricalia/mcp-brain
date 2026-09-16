#!/usr/bin/env python3
"""Runs the retrieval sweep against match_memories / search_memories_text
and writes bench/results/retrieval_raw.csv + retrieval_summary.csv.

Efficient by construction: each query needs only ONE SQL call for semantic
mode (fetch top 15 at min_similarity=0, i.e. the full ranked pool) and ONE
for text mode (limit 15); min_similarity, delta and limit are then applied
client-side on that cached ranked list, exactly reproducing what the SQL
functions would return for any (min_similarity, delta, limit) combo since
filtering a similarity threshold, applying a relative cutoff, and truncating
to a limit all preserve the original ranked order (they only drop tail
items, never reorder), so slicing/filtering a prefix of a longer ranked
list is equivalent to re-querying with those parameters.
"""
import csv
import json
import math
import os
import random
import subprocess

from lib import BENCH, DB_URL, Embedder, load_dataset, vec_literal

MIN_SIMS = [0.2, 0.3, 0.35, 0.4, 0.45, 0.5]
DELTAS = [None, 0.1, 0.15, 0.2, 0.25, 0.3]
LIMITS = [3, 5, 8, 15]
MAX_LIMIT = 15
DEFAULT_MIN_SIM = 0.3
DEFAULT_DELTA = None
DEFAULT_LIMIT = 8
CHARS_PER_TOKEN = 3.5  # heuristic: est_tokens = len(compact_json) / 3.5

RESULTS_DIR = os.path.join(BENCH, "results")
IDS_PATH = os.path.join(BENCH, "cache", "ids.json")


def psql_json_rows(sql: str) -> list:
    r = subprocess.run(
        ["psql", DB_URL, "-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr}\nSQL: {sql[:300]}")
    rows = []
    for line in r.stdout.strip().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def sql_escape(s: str) -> str:
    return s.replace("'", "''")


def fetch_semantic_pool(user_id: str, query_text: str, emb: Embedder) -> list:
    vec = vec_literal(emb.embed(query_text))
    sql = (
        "select row_to_json(m) from match_memories("
        f"query_embedding => '{vec}'::vector, "
        f"p_user => '{user_id}'::uuid, "
        "p_scope => ARRAY['bench']::text[], "
        f"match_count => {MAX_LIMIT}, "
        "min_similarity => 0.0) m;"
    )
    return psql_json_rows(sql)


def fetch_text_pool(user_id: str, query_text: str) -> list:
    q = sql_escape(query_text)
    sql = (
        "select row_to_json(m) from search_memories_text("
        f"p_query => '{q}', "
        f"p_user => '{user_id}'::uuid, "
        "p_scope => ARRAY['bench']::text[], "
        f"match_count => {MAX_LIMIT}) m;"
    )
    return psql_json_rows(sql)


def to_memory_output(row: dict, author_name: str, score_field: str) -> dict:
    out = {
        "id": row["id"],
        "project_slug": row["project_slug"],
        "content": row["content"],
        "visibility": row["visibility"],
        "author": author_name,
        "updated_at": str(row["updated_at"])[:10],
    }
    tags = row.get("tags") or []
    if tags:
        out["tags"] = tags
    if row.get("pinned"):
        out["pinned"] = True
    val = row.get(score_field)
    if val is not None:
        out[score_field] = round(val, 2)
    return out


def est_tokens(rows_out: list) -> float:
    s = json.dumps(rows_out, ensure_ascii=False, separators=(",", ":"))
    return len(s) / CHARS_PER_TOKEN


def apply_semantic_config(pool: list, min_sim: float, delta, limit: int) -> list:
    # pool already ordered by similarity desc (SQL order), min_similarity=0
    filtered = [r for r in pool if r["similarity"] >= min_sim]
    if not filtered:
        return []
    if delta is not None:
        top = filtered[0]["similarity"]
        filtered = [r for r in filtered if r["similarity"] >= top - delta]
    return filtered[:limit]


def apply_text_config(pool: list, limit: int) -> list:
    return pool[:limit]


def metrics_for(returned_keys: list, gold: list) -> dict:
    gold_set = set(gold)
    n = len(returned_keys)
    hits = [1 if k in gold_set else 0 for k in returned_keys]
    if gold_set:
        recall = len(gold_set & set(returned_keys)) / len(gold_set)
    else:
        recall = 1.0 if n == 0 else 0.0  # negative query: recall trivially defined; noise tracked separately
    hit_at_1 = 1 if (n > 0 and returned_keys[0] in gold_set) else 0
    rr = 0.0
    for i, k in enumerate(returned_keys, start=1):
        if k in gold_set:
            rr = 1.0 / i
            break
    precision = (sum(hits) / n) if n > 0 else (1.0 if not gold_set else 0.0)
    return {"recall": recall, "hit_at_1": hit_at_1, "rr": rr, "precision": precision, "n_returned": n}


def main():
    ds = load_dataset()
    with open(IDS_PATH) as f:
        ids = json.load(f)
    user_id = ids["user_id"]
    id_to_key = {v: k for k, v in ids["memory_ids"].items()}
    author_name = "Bench"

    emb = Embedder()
    random.seed(42)

    raw_rows = []
    call_counts = {"semantic_sql": 0, "text_sql": 0}

    for q in ds["queries"]:
        qkey = q["key"]
        qtype = q["type"]
        gold = q["gold"]

        sem_pool_raw = fetch_semantic_pool(user_id, q["query"], emb)
        call_counts["semantic_sql"] += 1
        sem_pool = [
            {**r, "key": id_to_key.get(r["id"], "?")}
            for r in sem_pool_raw
        ]

        text_pool_raw = fetch_text_pool(user_id, q["query"])
        call_counts["text_sql"] += 1
        text_pool = [
            {**r, "key": id_to_key.get(r["id"], "?")}
            for r in text_pool_raw
        ]

        # semantic configs
        for min_sim in MIN_SIMS:
            for delta in DELTAS:
                for limit in LIMITS:
                    chosen = apply_semantic_config(sem_pool, min_sim, delta, limit)
                    keys = [c["key"] for c in chosen]
                    out_json = [to_memory_output(c, author_name, "similarity") for c in chosen]
                    tokens = est_tokens(out_json)
                    m = metrics_for(keys, gold)
                    raw_rows.append({
                        "mode": "semantic",
                        "min_similarity": min_sim,
                        "delta": delta if delta is not None else "",
                        "limit": limit,
                        "query_key": qkey,
                        "query_type": qtype,
                        "gold_keys": "|".join(gold),
                        "returned_keys": "|".join(keys),
                        "similarities": "|".join(str(round(c["similarity"], 3)) for c in chosen),
                        "n_returned": m["n_returned"],
                        "hit_at_1": m["hit_at_1"],
                        "recall": round(m["recall"], 4),
                        "precision": round(m["precision"], 4),
                        "rr": round(m["rr"], 4),
                        "est_tokens": round(tokens, 1),
                    })

        # text configs
        for limit in LIMITS:
            chosen = apply_text_config(text_pool, limit)
            keys = [c["key"] for c in chosen]
            out_json = [to_memory_output(c, author_name, "rank") for c in chosen]
            tokens = est_tokens(out_json)
            m = metrics_for(keys, gold)
            raw_rows.append({
                "mode": "text",
                "min_similarity": "",
                "delta": "",
                "limit": limit,
                "query_key": qkey,
                "query_type": qtype,
                "gold_keys": "|".join(gold),
                "returned_keys": "|".join(keys),
                "similarities": "",
                "n_returned": m["n_returned"],
                "hit_at_1": m["hit_at_1"],
                "recall": round(m["recall"], 4),
                "precision": round(m["precision"], 4),
                "rr": round(m["rr"], 4),
                "est_tokens": round(tokens, 1),
            })

    emb.flush()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    raw_path = os.path.join(RESULTS_DIR, "retrieval_raw.csv")
    fieldnames = [
        "mode", "min_similarity", "delta", "limit", "query_key", "query_type",
        "gold_keys", "returned_keys", "similarities", "n_returned", "hit_at_1",
        "recall", "precision", "rr", "est_tokens",
    ]
    with open(raw_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(raw_rows)

    # ---- summary (per config, and per config x query_type) ----
    def config_key(r):
        return (r["mode"], r["min_similarity"], r["delta"], r["limit"])

    from collections import defaultdict
    by_config = defaultdict(list)
    by_config_type = defaultdict(list)
    for r in raw_rows:
        by_config[config_key(r)].append(r)
        by_config_type[(config_key(r), r["query_type"])].append(r)

    def agg(rows, key_fields):
        n = len(rows)
        neg = [r for r in rows if r["query_type"] == "negative"]
        return {
            **key_fields,
            "n_queries": n,
            "mean_recall": round(sum(r["recall"] for r in rows) / n, 4) if n else 0,
            "mean_hit_at_1": round(sum(r["hit_at_1"] for r in rows) / n, 4) if n else 0,
            "mean_rr": round(sum(r["rr"] for r in rows) / n, 4) if n else 0,
            "mean_precision": round(sum(r["precision"] for r in rows) / n, 4) if n else 0,
            "mean_n_returned": round(sum(r["n_returned"] for r in rows) / n, 2) if n else 0,
            "mean_est_tokens": round(sum(r["est_tokens"] for r in rows) / n, 1) if n else 0,
            "mean_n_returned_negative": round(sum(r["n_returned"] for r in neg) / len(neg), 2) if neg else "",
        }

    summary_rows = []
    for ck, rows in by_config.items():
        mode, ms, delta, limit = ck
        summary_rows.append(agg(rows, {
            "mode": mode, "min_similarity": ms, "delta": delta, "limit": limit, "query_type": "ALL",
        }))
    for (ck, qtype), rows in by_config_type.items():
        mode, ms, delta, limit = ck
        summary_rows.append(agg(rows, {
            "mode": mode, "min_similarity": ms, "delta": delta, "limit": limit, "query_type": qtype,
        }))

    summary_fields = [
        "mode", "min_similarity", "delta", "limit", "query_type", "n_queries",
        "mean_recall", "mean_hit_at_1", "mean_rr", "mean_precision",
        "mean_n_returned", "mean_est_tokens", "mean_n_returned_negative",
    ]
    summary_path = os.path.join(RESULTS_DIR, "retrieval_summary.csv")
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields)
        w.writeheader()
        w.writerows(summary_rows)

    print(f"raw rows: {len(raw_rows)} -> {raw_path}")
    print(f"summary rows: {len(summary_rows)} -> {summary_path}")
    print(f"SQL calls: {call_counts}")
    print(f"new OpenAI embedding calls this run: {emb.calls}")


if __name__ == "__main__":
    main()
