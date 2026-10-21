# Installation et exploitation

## 1. Installer

```bash
cd "summer-hunter-2027"
uv sync                                   # crée .venv et installe les dépendances
cp .env.example .env && chmod 600 .env
uv run summer-hunter init                 # crée data/summer_hunter.db, valide la config
```

`init` liste les champs UNKNOWN du profil. Tant qu'ils le restent, les opportunités concernées restent NEEDS_VERIFICATION.

## 2. Compléter le profil (`config/candidate_profile.yaml`)

| Champ | Effet quand il est renseigné |
| --- | --- |
| `expected_graduation_month` | tranche les fenêtres « graduating between Dec 2027 and Jun 2028 » |
| `immigration.citizenship` (ex. `[FR]`) | active la libre circulation UE/EEE/Suisse et les restrictions de nationalité |
| `immigration.work_authorization` (ex. `{GB: false}`) | permet de conclure sur « must have the right to work » |
| `requires_visa_sponsorship` | avec `false` ci-dessus, rejette les annonces « no sponsorship » |
| `degree_equivalence.masters_enrolled` | tranche « currently pursuing a Master's » |
| `returning_to_studies_after_internship` | tranche « penultimate year / returning to school » |

Ne mets `true` que pour un fait confirmé. Après modification : `uv run summer-hunter evaluate --force`.

## 3. Variables d'environnement (`.env`)

| Variable | Obligatoire | Rôle |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | pour Telegram | jeton du bot (@BotFather) |
| `TELEGRAM_CHAT_ID` | pour Telegram | ton chat, seul servi par le bot |
| `ANTHROPIC_API_KEY` | non | active l'analyse IA (sinon tout est déterministe) |
| `BRAVE_SEARCH_API_KEY` | non | découverte de posts/offres LinkedIn publics |
| `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX` | non | alternative à Brave |
| `LINKEDIN_ALERTS_DIR` | non | dossier de fichiers `.eml` d'alertes LinkedIn |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | non | lecture seule des alertes (mot de passe d'application) |
| `SH_*` | non | fuseau, fenêtre, intervalle, niveau de logs |

Les secrets ne sont jamais écrits dans les logs (filtre de masquage) ni dans Telegram.

## 4. Telegram

1. Dans Telegram, ouvre **@BotFather**, `/newbot`, choisis un nom : tu reçois un jeton.
2. Mets-le dans `.env` : `TELEGRAM_BOT_TOKEN=...`
3. Ouvre ton bot et envoie-lui `/start`.
4. `uv run summer-hunter telegram-setup` affiche ton `chat_id` ; copie-le dans `TELEGRAM_CHAT_ID`.
5. `uv run summer-hunter telegram-setup --test` envoie un message de test.
6. `uv run summer-hunter digest --force` envoie le digest maintenant.

Les boutons (Save, Dismiss, Applied, Explain, Networking msg) et les commandes (`/top10`, `/stats`…) répondent quand le bot tourne : `uv run summer-hunter bot`, ou en permanence avec `uv run summer-hunter launchd install --with-bot`. Sinon Telegram garde les clics 24 h et le bot les traite au démarrage suivant.

## 5. Anthropic (optionnel)

1. Crée une clé sur la console Anthropic, mets-la dans `ANTHROPIC_API_KEY`.
2. Réglages dans `config/llm.yaml` : `model` (défaut `claude-opus-5-5`), `daily_budget_usd` (défaut 0,50 $), `max_analyses_per_run`.
3. Vérifie avec un appel unique et payant : `uv run summer-hunter llm-check --yes`.

L'IA ne s'applique qu'aux annonces non rejetées dont le fit déterministe atteint 60. Résultats en cache par contenu, modèle et version de prompt. Pour diviser le coût par environ 40, `model: claude-haiku-5-5`. Sans clé, `SH_LLM_PROVIDER=none` ou rien : le système reste complet.

## 6. Lancer à la main

```bash
uv run summer-hunter run --dry-run              # rien n'est écrit
uv run summer-hunter run                        # cycle complet
uv run summer-hunter run --source greenhouse:stripe --source workday
uv run summer-hunter run --notify               # puis digest Telegram
uv run summer-hunter catch-up                   # cycle manqué + digest
```

## 7. Planification nocturne

```bash
scripts/install_macos.sh            # ou : uv run summer-hunter launchd install [--with-bot]
uv run summer-hunter launchd status
```

- Le LaunchAgent `com.summerhunter.night` lance `summer-hunter night` à **00:05** et **06:00** (heure locale du Mac, à régler sur Europe/Paris), via `caffeinate -i -s` pour éviter la mise en veille pendant le travail.
- Entre 00:00 et 06:00 (Europe/Paris, changements d'heure gérés par la base tz), un cycle toutes les 90 minutes, puis le digest à 06:00.
- Hors fenêtre (Mac réveillé tard), un seul cycle de rattrapage s'il n'y en a pas eu depuis 8 h, puis le digest s'il n'est pas déjà parti.
- Un verrou (`data/night.lock`) empêche deux exécutions simultanées. SIGTERM arrête proprement entre deux sources.
- Une sauvegarde SQLite est faite après chaque nuit (`data/backups`, 14 conservées).

**Un Mac en veille ou éteint n'exécute rien.** Pour qu'il se réveille seul (décision à prendre toi-même, demande `sudo`) :

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 23:55:00
pmset -g sched                      # vérifier
sudo pmset repeat cancel            # annuler
```

Capot fermé sur batterie, macOS peut refuser de rester éveillé : branche le Mac.

Désinstaller : `scripts/uninstall_macos.sh` (les données restent).

## 8. Consulter les résultats

```bash
uv run summer-hunter list                       # par priorité
uv run summer-hunter list --decision rejected
uv run summer-hunter list --user-status saved
uv run summer-hunter show 42                    # détail : critères, preuves, contacts
uv run summer-hunter status                     # derniers cycles, compteurs, dépenses IA
uv run summer-hunter sources                    # santé des sources
uv run summer-hunter feedback 42 dismiss && uv run summer-hunter feedback 42 reason --reason cold
uv run summer-hunter preferences show | reset
uv run summer-hunter safety refresh             # avis France Diplomatie
uv run summer-hunter safety set TH --level WHOLE_COUNTRY_ADVISED_AGAINST --note "décision perso"
uv run summer-hunter backup
```

## 9. Ajouter une source

- Entreprise sur Greenhouse, Lever ou Ashby : ajoute `{name, ats, token}` dans `target_companies.yaml`, puis `uv run summer-hunter probe-boards`.
- Workday : `{ats: workday, tenant, host, site}` (lisibles dans l'URL `https://<tenant>.<host>.myworkdayjobs.com/<site>`).
- Page carrière avec JSON-LD `JobPosting` : `{ats: career_page, urls: [...]}` (robots.txt respecté).

## 10. Tests

```bash
uv run pytest                 # unitaires + intégration, sans réseau
uv run pytest -m live         # appels réels aux ATS publics et à France Diplomatie
```
