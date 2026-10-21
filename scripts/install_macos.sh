#!/usr/bin/env bash
# Install Summer Hunter 2027 locally and register the nightly LaunchAgent.
# Usage: scripts/install_macos.sh [--with-bot]
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "uv is required: brew install uv"; exit 1; }
uv sync
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env from .env.example (edit it)."; }
chmod 600 .env
uv run summer-hunter init
uv run summer-hunter launchd install "$@"
cat <<'MSG'

Installed. The Mac must be awake (or set to wake) for the 00:05 run:
  sudo pmset repeat wakeorpoweron MTWRFSU 23:55:00     # optional, your decision
A sleeping or powered-off Mac does not run jobs; launchd runs the missed job at next wake,
and the runner then does one catch-up cycle and sends the digest.
Check: uv run summer-hunter launchd status
MSG
