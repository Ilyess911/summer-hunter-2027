# Dépannage

| Symptôme | Cause probable | Action |
| --- | --- | --- |
| Pas de digest le matin | Mac en veille ou éteint à 00:05 et 06:00 | `launchd status` ; réveil programmé `sudo pmset repeat wakeorpoweron MTWRFSU 23:55:00` ; `summer-hunter catch-up` |
| « Another run is in progress » | une nuit tourne déjà (verrou `data/night.lock`) | normal ; sinon vérifier `ps aux \| grep summer-hunter` |
| Digest imprimé au lieu d'envoyé | `TELEGRAM_BOT_TOKEN` ou `TELEGRAM_CHAT_ID` absent | `summer-hunter telegram-setup` |
| Boutons sans réponse | le bot ne tourne pas | `summer-hunter bot` ou `launchd install --with-bot` |
| Erreur Telegram 401 | jeton invalide ou révoqué | régénérer dans @BotFather |
| Une source en `failed` | API modifiée, jeton de board changé, panne | `summer-hunter sources` puis `probe-boards` ; corriger `target_companies.yaml` |
| `detail fetch cap reached` | beaucoup d'offres Workday d'un coup | normal : la suite est traitée au cycle suivant (incrémental) |
| Presque tout en NEEDS_VERIFICATION | UNKNOWN dans le profil | compléter `candidate_profile.yaml`, puis `evaluate --force` |
| `invalid country code False` | `NO` (Norvège) non quoté en YAML | écrire `'NO'` |
| Analyse IA jamais lancée | pas de clé, budget atteint, fit < 60 | `summer-hunter status` (dépenses), `llm-check --yes`, `config/llm.yaml` |
| `model not found` | identifiant de modèle invalide | corriger `model` dans `config/llm.yaml` |
| Alertes LinkedIn ignorées | expéditeur différent ou format changé | vérifier l'expéditeur ; tester un `.eml` dans `LINKEDIN_ALERTS_DIR` |
| IMAP refusé | mot de passe principal au lieu d'un mot de passe d'application | créer un mot de passe d'application |
| Base verrouillée | deux processus écrivent en même temps | attendre ; WAL + `busy_timeout` 10 s |
| Restaurer la base | corruption ou erreur de manipulation | copier une sauvegarde de `data/backups/` vers `data/summer_hunter.db` (processus arrêtés) |

Journaux : `data/logs/summer_hunter.jsonl` (JSON, secrets masqués), `data/logs/launchd-night.*.log`.

Diagnostic rapide :

```bash
uv run summer-hunter status
uv run summer-hunter sources
uv run summer-hunter launchd status
tail -n 50 data/logs/summer_hunter.jsonl
```
