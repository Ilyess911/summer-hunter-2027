#!/usr/bin/env bash
# Remove the LaunchAgents. Data (data/) and configuration are kept.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run summer-hunter launchd uninstall
echo "LaunchAgents removed. If you set a wake schedule: sudo pmset repeat cancel"
