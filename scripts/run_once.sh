#!/usr/bin/env bash
# One manual cycle. Pass --dry-run to persist nothing, --notify to send the digest afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run summer-hunter run "$@"
