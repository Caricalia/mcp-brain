"""Shared helpers: env loading, embedding cache, psql runner."""
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # mcp-brain/
BENCH = os.path.join(ROOT, "bench")
CACHE_PATH = os.path.join(BENCH, "cache", "embeddings.json")
DATASET_PATH = os.path.join(BENCH, "dataset.json")
DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
EMBED_MODEL = "text-embedding-3-small"


def load_openai_key() -> str:
    env_path = os.path.join(ROOT, "supabase", "functions", ".env")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENAI_API_KEY not found in supabase/functions/.env")


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


class Embedder:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=load_openai_key())
        self.cache = load_cache()
        self.calls = 0

    def embed(self, text: str) -> list:
        k = text_key(text)
        if k in self.cache:
            return self.cache[k]
        resp = self.client.embeddings.create(model=EMBED_MODEL, input=text)
        vec = resp.data[0].embedding
        self.cache[k] = vec
        self.calls += 1
        return vec

    def flush(self):
        save_cache(self.cache)


def psql(sql: str, tuples_only=True) -> str:
    cmd = ["psql", DB_URL, "-v", "ON_ERROR_STOP=1", "-q"]
    if tuples_only:
        cmd += ["-t", "-A", "-F", "\t"]
    cmd += ["-c", sql]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql error: {r.stderr}\nSQL: {sql[:500]}")
    return r.stdout


def vec_literal(vec: list) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def load_dataset() -> dict:
    with open(DATASET_PATH) as f:
        return json.load(f)
