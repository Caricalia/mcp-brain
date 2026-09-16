#!/usr/bin/env python3
"""Creates bench@local user + bench project, embeds & inserts memories.
Idempotent: safe to re-run (drops bench user first, which cascades)."""
import json
import os
import subprocess
import sys

from lib import BENCH, DB_URL, Embedder, load_dataset, psql, vec_literal

IDS_PATH = os.path.join(BENCH, "cache", "ids.json")


def sql_escape(s: str) -> str:
    return s.replace("'", "''")


def main():
    ds = load_dataset()
    memories = ds["memories"]

    print("Baseline counts:")
    for t in ("users", "projects", "memories"):
        out = psql(f"select count(*) from {t};").strip()
        print(f"  {t}: {out}")

    # Clean slate for bench user (cascade removes projects/memories/api_keys)
    psql("delete from users where email = 'bench@local';")

    print("Embedding memories (cached)...")
    emb = Embedder()
    vectors = {}
    for m in memories:
        vectors[m["key"]] = emb.embed(m["content"])
    emb.flush()
    print(f"  new OpenAI embedding calls so far: {emb.calls}")

    # Create user + project, capture user id
    user_id = psql(
        "insert into users (email, name, is_admin) values "
        "('bench@local', 'Bench', false) returning id;"
    ).strip()

    psql(
        "insert into projects (slug, parent_slug, owner_id, name, description) values "
        f"('bench', null, '{user_id}', 'Bench', 'Retrieval benchmark dataset (isolated, safe to delete)');"
    )

    # Build insert SQL for memories
    lines = []
    key_to_row = {}
    for m in memories:
        content = sql_escape(m["content"])
        tags_sql = "ARRAY[" + ",".join(f"'{sql_escape(t)}'" for t in m["tags"]) + "]::text[]" if m["tags"] else "'{}'::text[]"
        vec = vec_literal(vectors[m["key"]])
        lines.append(
            f"('{m['key']}', 'bench', '{user_id}', 'shared', '{content}', {tags_sql}, false, 'bench', '{vec}'::vector)"
        )

    sql_path = os.path.join(BENCH, "cache", "insert_memories.sql")
    with open(sql_path, "w") as f:
        f.write("begin;\n")
        for m in memories:
            content = sql_escape(m["content"])
            tags_sql = (
                "ARRAY[" + ",".join(f"'{sql_escape(t)}'" for t in m["tags"]) + "]::text[]"
                if m["tags"]
                else "'{}'::text[]"
            )
            vec = vec_literal(vectors[m["key"]])
            f.write(
                "insert into memories "
                "(project_slug, created_by, visibility, content, tags, pinned, source, embedding) "
                f"values ('bench', '{user_id}', 'shared', '{content}', {tags_sql}, false, 'bench', '{vec}'::vector);\n"
            )
        f.write("commit;\n")

    r = subprocess.run(
        ["psql", DB_URL, "-v", "ON_ERROR_STOP=1", "-q", "-f", sql_path],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError("insert failed")

    # Map content -> key, then fetch (id, content) for bench project to build id_map
    content_to_key = {m["content"]: m["key"] for m in memories}
    out = psql("select id, content from memories where project_slug = 'bench';")
    id_map = {}
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        mid, content = line.split("\t", 1)
        key = content_to_key.get(content)
        if key:
            id_map[key] = mid

    with open(IDS_PATH, "w") as f:
        json.dump({"user_id": user_id, "memory_ids": id_map}, f, indent=2)

    print(f"Inserted {len(id_map)} memories for user {user_id}")

    print("Embedding queries (cached)...")
    for q in ds["queries"]:
        emb.embed(q["query"])
    emb.flush()
    print(f"Total new OpenAI embedding calls this run: {emb.calls}")

    print("Post-insert counts:")
    for t in ("users", "projects", "memories"):
        out = psql(f"select count(*) from {t};").strip()
        print(f"  {t}: {out}")


if __name__ == "__main__":
    main()
