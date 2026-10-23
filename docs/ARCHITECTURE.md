# Architecture

## Flux d'un cycle (`pipeline.Hunter.run_cycle`)

1. **Collecte** (`collectors/`) : chaque source implémente `Collector.collect(ctx) -> CollectorResult`. Jusqu'à 4 sources en parallèle, chacune isolée (timeout 10 min, toute exception devient un statut `failed`). Les titres sont filtrés avant tout appel détaillé (`TitleRules.worth_collecting`) : stage, et famille de poste non exclue.
2. **Incrémental** : `is_unchanged(source, id, marqueur)` évite de recharger une offre dont le marqueur (`updated_at` Greenhouse, `updatedAt` Lever, `publishedAt` Ashby) n'a pas changé. Sans marqueur (Workday), revérification tous les 7 jours.
3. **Ingestion** : résolution du pays (`engine/geo.py`, sans géocodage externe), famille de poste, clé canonique, puis dédoublonnage (`engine/deduplication.py`) : même identifiant de réquisition, sinon même entreprise + titre normalisé + pays, sinon similarité de titre ≥ 0,86 dans un pays compatible, et jamais de fusion ambiguë. Une `Opportunity` garde toutes ses `SourceRecord`. La description la plus riche et le lien officiel ATS l'emportent.
4. **Évaluation** (seulement si le contenu a changé) : extraction déterministe (`engine/extraction.py`), éligibilité (`engine/eligibility.py`), fit (`engine/scoring.py`), puis, si le fit atteint 60 et que l'offre n'est pas rejetée, analyse IA bornée (`engine/ai_analysis.py`). Les faits IA vérifiés repassent par l'éligibilité, le score IA est mélangé 60/40 avec le score déterministe. Networking et priorité ensuite.
5. **Notification** (`notifications/`) : au matin, digest + cartes des opportunités `notify` jamais envoyées. Clé de dédoublonnage `card:<id>` et `digest:<date>`.

## Modules

| Module | Rôle |
| --- | --- |
| `utils/settings.py` | secrets depuis `.env`, comportement depuis `config/*.yaml`, validation des codes pays |
| `utils/http.py` | `PoliteClient` : robots.txt, rate limit et concurrence par hôte, retries exponentiels, `Retry-After` |
| `utils/text.py` | HTML non fiable vers texte, détection d'injection, vérification de citation |
| `utils/logging.py` | logs JSON (`data/logs/summer_hunter.jsonl`), masquage des secrets |
| `collectors/*` | Greenhouse, Lever, Ashby, Workday, JSON-LD, alertes LinkedIn, API de recherche |
| `engine/*` | géo, normalisation, extraction, éligibilité, scoring, networking, IA, feedback |
| `integrations/*` | Anthropic, Telegram, Brave/Google CSE, France Diplomatie |
| `bot/handlers.py` | commandes, boutons, autorisation par `chat_id` |
| `bot/crm_handlers.py` | commandes et boutons du CRM, import d'un `.xlsx` envoyé au bot |
| `engine/compensation.py`, `fx.py`, `priority.py`, `strategist.py`, `semantic.py`, `signals.py`, `knowledge.py`, `analytics.py` | V3 : rémunération, taux de change, priorité globale, action recommandée, classification par missions, signaux précoces, base de connaissances, indicateurs ([V3.md](V3.md)) |
| `crm/*` | candidatures, transitions de statut, contacts, journal, relances, métriques, documents, export et import Excel ([CRM.md](CRM.md)) |
| `scheduler/*` | fenêtre de nuit, verrou `fcntl`, génération des LaunchAgents |

## Base SQLite

| Table | Contenu |
| --- | --- |
| `opportunities` | opportunité canonique, dates de découverte et de vérification, statuts, derniers scores (dont recruitability, priorité industrielle, piste et catégorie) |
| `source_records` | une ligne par (source, id source), URL, marqueur, empreinte |
| `companies` | entreprises, watchlist, priorité, secteur |
| `recruiter_contacts` | nom, rôle, URL publique, post source, relation, confiance, preuve (≤ 400 caractères) |
| `linkedin_posts` | URL du post, auteur, texte tronqué à 2 000 caractères, classe, confiance |
| `eligibility_evaluations` | les 13 critères en JSON, décision, statut de sponsorship |
| `fit_evaluations` | rapport de fit, networking, priorité, recruitability |
| `ai_analyses` | cache des analyses validées (empreinte, modèle, version de prompt) |
| `user_feedback`, `preference_adjustments` | apprentissage explicite |
| `applications` | ancienne table du bouton Applied, conservée ; migrée vers `crm_applications` (v2) |
| `crm_applications` | une candidature suivie par opportunité : statut, dates, prochaine action, entretien, documents, version de ligne |
| `crm_contacts` | contacts gérés (preuve publique ou saisie manuelle), statut d'interaction |
| `crm_events` | journal en ajout seul (ancienne et nouvelle valeur, source) |
| `recruitment_signals` | signaux précoces (niveau, source, date, confirmation par une offre) |
| `fx_rates` | taux de change par devise et par jour, avec leur source |
| `crm_reminders` | rappels déjà envoyés (anti-doublon) |
| `notification_history` | envois Telegram et leur statut |
| `collection_runs`, `source_health` | historique des cycles, santé par source |
| `llm_usage` | jetons et coût par appel, par jour de Paris |
| `travel_advisories`, `kv_state` | avis officiels, état (offset Telegram, dernier digest) |

Données personnelles de tiers : seulement nom, rôle, URL publique et un extrait de preuve court. Aucune adresse email n'est stockée.

## Sécurité

- Secrets uniquement dans `.env` (chmod 600, ignoré par git), masqués dans les logs, jamais dans les URL journalisées (les erreurs Telegram n'incluent pas l'URL qui contient le jeton).
- Le bot ne répond qu'au `TELEGRAM_CHAT_ID` configuré.
- HTML externe : `script`, `style`, `iframe`, `form` supprimés ; rien n'est exécuté. Dans Telegram, tout texte externe est échappé et seuls les liens `http(s)` deviennent des boutons.
- Requêtes SQL : uniquement via SQLAlchemy (paramétrées).
- Injection de prompt : le texte d'annonce est placé entre balises `<untrusted_job_posting>`, les balises imitées sont neutralisées, des motifs d'injection sont détectés ; si injection, les scores du modèle sont ignorés. Le modèle ne décide jamais l'éligibilité et chaque exigence qu'il rapporte doit être une citation exacte.
- Sortie LLM : schéma JSON imposé côté API, puis validation Pydantic, bornes sur les scores, identifiants d'expérience filtrés.
- Brouillons de messages : jamais envoyés, filtre d'intégrité (pas de quota, de deals, de certifications, de langues non vérifiées).
