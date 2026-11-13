#!/usr/bin/env bash
# Keeps Tailscale (and so the Mini App Funnel) up: run by launchd at login and every 5 minutes.
# Starts the Tailscale app if it is not running, then brings the node up if it is Stopped.
# NeedsLogin is only logged: signing in needs a human.
set -uo pipefail
TS=/Applications/Tailscale.app/Contents/MacOS/tailscale
log() { echo "$(date '+%Y-%m-%dT%H:%M:%S') $*"; }
[ -x "$TS" ] || { log "Tailscale.app not found"; exit 0; }
if ! pgrep -qf "io.tailscale.ipn.macsys" ; then
  open -g -a Tailscale && log "Tailscale app started"; sleep 15
fi
state=$("$TS" status --json 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null)
case "$state" in
  Running) ;;
  Stopped|"") log "state=${state:-unknown}, running tailscale up"; "$TS" up 2>&1 | tail -2 ;;
  *) log "state=$state, no action" ;;
esac
exit 0
