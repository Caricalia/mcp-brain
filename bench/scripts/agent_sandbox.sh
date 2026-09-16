#!/usr/bin/env bash
# Part C: builds a clean copy of the repo (no bench/, no secrets) for the agent to explore.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRATCH_BASE="${BENCH_SCRATCH_DIR:-${TMPDIR:-/tmp}/mcp-brain-bench}"
DEST="${BENCH_SANDBOX_REPO:-$SCRATCH_BASE/brain-sandbox/mcp-brain}"

# This script uses rsync --delete. Keep the safe temporary-directory default,
# and require an explicit opt-in before deleting from a custom destination.
if [[ -n "${BENCH_SANDBOX_REPO:-}" && "${BENCH_ALLOW_DELETE:-}" != "1" ]]; then
  echo "Refusing custom BENCH_SANDBOX_REPO without BENCH_ALLOW_DELETE=1 because rsync --delete can remove files there." >&2
  exit 1
fi

case "$DEST" in
  /|"$ROOT"|"$ROOT"/*|"$PWD"|"$PWD"/*)
    echo "Refusing unsafe sandbox destination: $DEST" >&2
    exit 1
    ;;
esac

mkdir -p "$(dirname "$DEST")"
mkdir -p "$DEST"

rsync -a --delete \
  --exclude 'bench/' \
  --exclude '.git/' \
  --exclude 'supabase/functions/.env' \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude 'supabase/.temp/' \
  --exclude 'supabase/.branches/' \
  --exclude 'node_modules/' \
  --exclude 'deno.lock' \
  "$ROOT"/ "$DEST"/

# .env.example is explicitly allowed back in (rsync excludes it via the .env* pattern above only
# if literally named .env, .env.local, .env.production — .env.example is not excluded).

echo "Sandbox ready at $DEST"
echo "--- verifying no secrets leaked ---"
if [ -f "$DEST/supabase/functions/.env" ]; then
  echo "FAIL: supabase/functions/.env present in sandbox" >&2
  exit 1
fi
if [ -d "$DEST/bench" ]; then
  echo "FAIL: bench/ present in sandbox" >&2
  exit 1
fi
echo "OK"
