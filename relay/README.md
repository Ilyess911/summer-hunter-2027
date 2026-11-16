# Relay VPS (Instinct)

Point d'entrée toujours allumé pour l'API agent d'Instinct, sur la VM GCP de Crypto Sentinel.

- URL pour Instinct : `https://crypto-sentinel.tailca4ea2.ts.net/summer-hunter` (même jeton qu'avant, mêmes routes `/agent/v1/...`).
- Mac joignable : le relais transmet et renvoie la réponse du Mac telle quelle (`X-Relay: forwarded`).
- Mac absent : un POST est mis en file (`202`, `X-Relay: queued`), la liste des candidatures vient du dernier cache (`X-Relay: cache`).
- Le Mac vide la file toutes les 2 min (`summer-hunter relay-sync`, launchd `com.summerhunter.relay`), dans l'ordre, et n'acquitte qu'une réponse jugée par ses règles (200/201/400/409/413/422).

## VPS

- `/opt/shrelay/sh_relay.py` (bibliothèque standard seulement), base `/opt/shrelay/relay.db`, compte `shrelay` sans shell.
- `/etc/shrelay.env` (600) : empreintes SHA-256 des deux jetons, aucun jeton en clair ; `MAC_URL`.
- Service `shrelay` (systemd durci, `MemoryMax=80M`), Funnel `tailscale funnel --bg --set-path /summer-hunter http://127.0.0.1:8790`.
- Mise à jour : `gcloud compute scp --tunnel-through-iap relay/sh_relay.py instance-20260928-105126:/tmp/`, puis `sudo install -m 644 /tmp/sh_relay.py /opt/shrelay/ && sudo systemctl restart shrelay`.
- Journal : `sudo journalctl -u shrelay` (aucun corps de requête n'est journalisé).

## Mac

`.env` : `SH_RELAY_URL`, `SH_RELAY_TOKEN` (jeton du canal Mac ↔ relais, distinct du jeton d'Instinct).
