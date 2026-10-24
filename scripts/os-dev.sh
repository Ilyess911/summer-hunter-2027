#!/usr/bin/env bash
# Summer Hunter OS, local mode: API (127.0.0.1:8800) + web app (127.0.0.1:3000), stopped together with Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! grep -q '^SH_OS_PASSWORD_HASH=' .env 2>/dev/null; then
  echo "No password yet: run 'uv run summer-hunter os set-password' first." >&2
  exit 1
fi
API_PORT="${SH_OS_API_PORT:-8800}"
WEB_PORT="${SH_OS_WEB_PORT:-3000}"
uv run summer-hunter --quiet os serve --port "$API_PORT" &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT INT TERM
(cd apps/web && [ -d node_modules ] || pnpm install --frozen-lockfile)
( sleep 4 && open "http://127.0.0.1:$WEB_PORT" ) >/dev/null 2>&1 &
cd apps/web && SH_API_URL="http://127.0.0.1:$API_PORT" pnpm exec next dev --hostname 127.0.0.1 --port "$WEB_PORT"
