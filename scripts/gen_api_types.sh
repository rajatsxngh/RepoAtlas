#!/usr/bin/env sh
# Regenerate the web app's API types (API-7):
#   1. backend/openapi.json from the code (repoatlas api openapi);
#   2. frontend/src/lib/api/schema.d.ts from that spec, with the openapi-typescript version
#      pinned in frontend/package-lock.json (installed with `npm ci` when missing).
# Commit both files. With --check, nothing is written: the script fails if either is stale.
#
# Usage: scripts/gen_api_types.sh [--check]        (Windows: scripts/gen_api_types.ps1)
set -eu

root="$(cd "$(dirname "$0")/.." && pwd)"
types="src/lib/api/schema.d.ts"
check=0
if [ "${1:-}" = "--check" ]; then
  check=1
elif [ $# -gt 0 ]; then
  echo "usage: scripts/gen_api_types.sh [--check]" >&2
  exit 2
fi

if [ ! -e "$root/frontend/node_modules/.bin/openapi-typescript" ]; then
  npm --prefix "$root/frontend" ci --no-audit --no-fund
fi

cd "$root/backend"
if [ "$check" -eq 1 ]; then
  uv run repoatlas api openapi --out openapi.json --check
else
  uv run repoatlas api openapi --out openapi.json
fi

cd "$root/frontend"
if [ "$check" -eq 1 ]; then
  fresh=".schema.d.ts.check"
  npx --no openapi-typescript ../backend/openapi.json --output "$fresh"
  if cmp -s "$fresh" "$types"; then
    rm -f "$fresh"
    echo "frontend/$types is up to date."
  else
    rm -f "$fresh"
    echo "frontend/$types is out of date. Run: scripts/gen_api_types.sh" >&2
    exit 1
  fi
else
  npx --no openapi-typescript ../backend/openapi.json --output "$types"
fi
