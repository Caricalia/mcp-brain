#!/usr/bin/env python3
"""Single-command reproduction of the mcp-brain retrieval benchmark.

Usage:
    python3 bench/run_retrieval.py            # setup DB + sweep + charts
    python3 bench/run_retrieval.py --no-setup # reuse existing bench DB rows, just re-sweep + re-chart
    python3 bench/run_retrieval.py --charts-only

All OpenAI embedding calls are cached in bench/cache/embeddings.json (keyed by
sha256 of the text), so reruns make 0 new API calls unless the dataset changes.
Requires: local Supabase running on 127.0.0.1:54322, psql on PATH, and
OPENAI_API_KEY in supabase/functions/.env.
"""
import argparse
import os
import subprocess
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")


def run(script):
    r = subprocess.run([sys.executable, script], cwd=SCRIPTS)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-setup", action="store_true", help="skip DB (re)creation, reuse existing bench rows")
    p.add_argument("--charts-only", action="store_true", help="only regenerate charts from existing CSVs")
    args = p.parse_args()

    if args.charts_only:
        run(os.path.join(SCRIPTS, "charts.py"))
        return

    if not args.no_setup:
        run(os.path.join(SCRIPTS, "build_dataset.py"))
        run(os.path.join(SCRIPTS, "setup_db.py"))

    run(os.path.join(SCRIPTS, "sweep.py"))
    run(os.path.join(SCRIPTS, "charts.py"))
    print("\nDone. See bench/results/*.csv, bench/results/run_meta.json and bench/charts/*.png|svg")


if __name__ == "__main__":
    main()
