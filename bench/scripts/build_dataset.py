#!/usr/bin/env python3
"""Generator skeleton for bench/dataset.json (Part A/B) — NOT published with
real content in this repo.

This repository publishes no benchmark results. This script generates a local
fictional placeholder dataset so you can inspect the expected shape and run
the harness with your own approved data. What IS published is this harness:
the exact shape the dataset must have, the sweep/scoring code
(scripts/sweep.py), the LLM/agent runners (run_llm.py, run_agent.py), and the
charting code, so anyone can point them at their OWN memories and reproduce
the methodology with their own numbers.

To reproduce this benchmark on your own data:
  1. Fill in MEMORIES and QUERIES below with your own project's facts and
     questions (see the placeholder examples — obviously fictional, replace
     them all). Keep content ideally in the language your `to_tsvector(...)`
     config in the schema is set to (see supabase/migrations/*.sql): mixing
     languages between the dataset and the FTS config makes the text-mode
     condition unfairly weak.
  2. Add enough fictional or approved memories and queries for the comparison
     you want to make, covering the query types relevant to your use case.
  3. `python3 bench/run_retrieval.py` (Part A), then
     `bench/run_llm.py prepare && run && summarize` (Part B).

Each memory is (key, content, tags); each query is
(key, query_text, type, gold_memory_keys, reference_answer). `type` must be
one of: paraphrase, keyword, multi, negative.
"""
import json
import os

MEMORIES = [
    # Placeholder examples only — obviously fictional, not real data.
    # Replace every entry with your own project's memories before running.
    ("m01", "Ejemplo: el plan Basico de un SaaS ficticio pasa de 9$/mes a 15$/mes el mes que viene.", ["example", "pricing"]),
    ("m02", "Ejemplo: el backend de ese mismo SaaS ficticio está en Python con FastAPI sobre Postgres.", ["example", "backend"]),
    ("m03", "Ejemplo: se descartó construir un dashboard de analítica propio; se decidió integrar una herramienta externa.", ["example", "decision"]),
]

QUERIES = [
    # Placeholder examples only — see MEMORIES above.
    ("q01", "¿Cuánto cuesta el plan Basico del SaaS de ejemplo?", "paraphrase", ["m01"], "15$/mes (subió desde 9$/mes)."),
    ("q02", "stack backend saas ejemplo", "keyword", ["m02"], "Python con FastAPI sobre Postgres."),
    ("q03", "¿Construyeron un dashboard de analítica propio?", "paraphrase", ["m03"], "No, se descartó a favor de una herramienta externa."),
    ("q04", "¿tiene oficina en Madrid el SaaS de ejemplo?", "negative", [], "No hay memoria sobre eso."),
]


def main():
    out_path = os.path.join(os.path.dirname(__file__), "..", "dataset.json")
    mem_keys = {m[0] for m in MEMORIES}
    for q in QUERIES:
        for g in q[3]:
            assert g in mem_keys, (q[0], g)
    dataset = {
        "memories": [
            {"key": k, "content": txt, "tags": tags} for k, txt, tags in MEMORIES
        ],
        "queries": [
            {"key": k, "query": q, "type": typ, "gold": gold, "answer": ans}
            for k, q, typ, gold, ans in QUERIES
        ],
    }
    with open(out_path, "w") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"memories={len(MEMORIES)} queries={len(QUERIES)}")
    print("NOTE: this is placeholder/example content. Replace MEMORIES and "
          "QUERIES with your own project's data before running the benchmark.")


if __name__ == "__main__":
    main()
