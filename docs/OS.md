# Summer Hunter OS (V4) : CRM web et networking

Summer Hunter OS est l'application web du projet : un CRM de recrutement (opportunités, pipeline,
candidatures, networking, messages LinkedIn, analytics) branché sur la même base et les mêmes moteurs
que la découverte nocturne, le bot Telegram et le classeur Excel. Rien n'est dupliqué : l'API
FastAPI appelle les services Python existants (`crm`, `engine`, `os`).

## Démarrer en local

```bash
uv sync
uv run summer-hunter os set-password     # mot de passe stocké haché (scrypt) dans .env, mode 600
make dev                                 # API 127.0.0.1:8800 + web 127.0.0.1:3000, ouvre le navigateur
```

- Premier lancement du front : `cd apps/web && pnpm install`.
- `make api` ou `make web` lancent un seul des deux serveurs.
- Les deux serveurs écoutent sur `127.0.0.1` seulement. Ne jamais les exposer tels quels sur Internet
  (pas de tunnel, pas de Funnel) : l'accès distant passe par le mode hébergé ci-dessous.
- Changer le mot de passe régénère `SH_OS_SECRET` : toutes les sessions ouvertes sont déconnectées.

## Pages

| Page | Contenu |
| --- | --- |
| Command Center | Priorités du jour avec bouton d'action direct, compteurs, top opportunités, contacts à approcher |
| Opportunities | Table dense, filtres (région, pays, catégorie, paie, recruitability, éligibilité, échéance), vues sauvegardées |
| Pipeline | Kanban glisser-déposer et vue table ; toute soumission demande une confirmation avec date |
| Application | Statut, entretiens, notes, prochaine action, CV et lettre (génération locale) |
| Networking | File des contacts classés, raison, moment conseillé |
| Outreach Studio | Message personnalisé, validation, variantes, copier, ouvrir LinkedIn, marquer envoyé, relance, réponse |
| Analytics, Hunter Performance, Activity | Conversion, paie par statut, sources, journal |

Raccourcis : `⌘K` palette (recherche et commandes), `1` à `5` pour les pages principales.

## Networking : règles d'intégrité

- **Aucun contact inventé.** Un contact vient d'une preuve publique déjà collectée (post, offre)
  ou de l'utilisateur. La recherche de profils (`Find`) produit des liens de recherche, ou des
  candidats marqués `unverified` si `BRAVE_SEARCH_API_KEY` est configurée.
- **Aucun email deviné**, aucune connexion commune supposée, aucun contenu lu prétendu.
- **Score de priorité** (0 à 100, heuristique de classement, pas une probabilité de réponse) :
  implication dans le recrutement 30, pertinence pour le poste 25, lien avec le profil 20,
  activité de recrutement récente 15, accessibilité 10. Les seniors sont plafonnés, un lien
  direct avec l'offre ajoute un bonus. Réglages : `config/networking.yaml`.
- **Moment** : `CONTACT_BEFORE_APPLICATION`, `CONTACT_AFTER_APPLICATION`, `FOLLOW_UP` (5 jours ouvrés,
  une seule relance), `WAIT` (après une réponse), `DO_NOT_CONTACT` (refus, contact écarté).
- **Messages** construits à partir des faits vérifiés (parcours de `config/networking.yaml`, preuves
  stockées). Validation avant affichage : longueur, formules bannies, tiret cadratin, répétitions,
  affirmations non étayées (post lu, candidature envoyée, ancien élève, recommandation). Le français
  est utilisé pour les pays francophones. La reformulation par Ollama est optionnelle et revalidée.
- **Rien n'est envoyé automatiquement.** L'utilisateur copie, envoie lui-même sur LinkedIn, puis
  marque le message comme envoyé.

## Mode hébergé (accès quand le Mac est éteint)

Le Mac reste la source de vérité. Une copie hébergée sert le dernier instantané et met en file les
modifications faites en ligne ; le Mac les rejoue à la synchronisation suivante.

```
Mac (SQLite, découverte, LaTeX, Ollama) --push instantané--> API hébergée (Vercel Python + Postgres)
                                        <--pull modifications--
Navigateur --> Next.js (Vercel) --/api/* réécrit--> API hébergée
```

Déroulé d'une synchronisation (`summer-hunter os sync`, ou automatiquement après la nuit) :

1. Le Mac récupère les modifications faites en ligne et les rejoue dans l'ordre, avec les mêmes
   commandes que l'API (`os/commands.py`) : mêmes événements CRM, retours, interactions.
2. Chaque modification rejouée est inscrite dans un registre local : un accusé de réception perdu
   ne la rejoue jamais deux fois.
3. Les lignes créées en ligne (contacts, brouillons) reçoivent un identifiant du Mac ; la table de
   correspondance évite qu'une modification en ligne touche la mauvaise ligne.
4. Une modification en ligne plus ancienne qu'un changement fait sur le Mac n'écrase rien : elle
   est mise de côté comme conflit (compteur dans la barre latérale, détail dans `os sync`).
5. L'instantané part en zone de transit, puis remplace la copie hébergée en une seule transaction,
   seulement si aucune modification en ligne n'est arrivée entre-temps (sinon le cycle recommence,
   trois fois au plus).
6. Au premier échec de rejeu, la file s'arrête, rien n'est poussé, l'erreur est affichée.

Ce qui ne tourne pas en ligne : la découverte nocturne, la recherche de profils, la génération de
CV et de lettres, la reformulation IA, l'export Excel. Ces actions répondent 409 en mode hébergé. Un processus Python
ne tourne pas en continu sur Vercel : chaque requête est une fonction courte.

### Mise en place (manuelle, une fois)

1. Créer une base Postgres (Neon, Supabase ou Vercel Postgres) et noter son URL.
2. Projet Vercel **API** : racine du dépôt (utilise `vercel.json`, `api/index.py`, `requirements.txt`).
   Variables d'environnement :
   - `DATABASE_URL` : URL Postgres (`postgres://` accepté).
   - `SH_OS_PASSWORD_HASH` : copier la valeur de `.env` après `os set-password`.
   - `SH_OS_SECRET` : 48 caractères aléatoires (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
   - `SH_SYNC_TOKEN` : 40 caractères aléatoires ou plus, partagé avec le Mac.
   L'API refuse de démarrer s'il en manque une.
3. Projet Vercel **web** : racine `apps/web`, variable `SH_API_URL` = URL du projet API
   (lue au build : redéployer après l'avoir changée).
4. Sur le Mac, dans `.env` : `SH_OS_HOSTED_URL=<URL du projet API>` et le même `SH_SYNC_TOKEN`.
5. `make sync` une première fois. Ensuite la nuit synchronise seule après la découverte
   (`summer-hunter night` : rafraîchit le networking, puis pousse si l'URL est configurée).

La barre latérale affiche « Hosted · synced il y a N h » : c'est l'âge de la dernière synchronisation.

### Régénérer `requirements.txt`

```bash
uv export --no-hashes --extra hosted --no-emit-project --no-dev | grep -v "^#\|^    #\|^-e" > requirements.txt
```

## Sécurité

- Mot de passe unique, haché scrypt, jamais stocké en clair. Session : jeton HMAC signé par
  `SH_OS_SECRET`, cookie HttpOnly, SameSite=Lax, Secure en mode hébergé, 14 jours.
- Chaque route sauf `/api/health` et `/api/auth/login` exige la session. Toute requête qui modifie
  exige l'en-tête `X-SH-Request: 1` (protection CSRF), sans CORS ouvert.
- Connexion limitée à 8 essais par client et 40 au total par 15 minutes ; les compteurs sont en base
  pour tenir entre les instances serverless, et toute écriture en ligne passe par un verrou Postgres.
- Synchronisation : HTTPS + `Authorization: Bearer SH_SYNC_TOKEN` (32 caractères minimum, comparaison
  à temps constant), HTTPS obligatoire côté Mac. Les routes de sync n'existent qu'en mode hébergé.
- Aucune clé ni secret n'est envoyé au navigateur ; le front ne parle qu'à `/api` sur sa propre origine.
- Pas de documentation OpenAPI publique, en-têtes `nosniff`, `no-referrer`, `DENY`, `no-store`.

## Tests

```bash
make test                                         # Python (dont l'API OS)
SH_TEST_POSTGRES_URL=postgres://... make test     # ajoute le test hébergé sur un vrai Postgres jetable
make test-web                                     # composants (vitest)
make e2e                                          # parcours complets (Playwright, base de test jetable)
```

Le e2e démarre sa propre API sur une base temporaire seedée (`apps/web/e2e/server.py`), jamais sur
la base réelle.

## Limites connues

- La base réelle ne contient encore aucun contact recruteur public : le networking démarre vide et
  se remplit avec les ajouts manuels et les preuves collectées.
- Sans `BRAVE_SEARCH_API_KEY`, `Find` ne donne que des liens de recherche à ouvrir soi-même.
- Sans coûts de la vie sourcés dans `config/compensation.yaml`, le solde mensuel n'est pas estimé.
- Une déconnexion efface le cookie mais ne révoque pas un jeton copié ailleurs ; changer le mot de
  passe (nouveau `SH_OS_SECRET`) révoque toutes les sessions.
- Le mode hébergé est testé contre un Postgres local ; le déploiement Vercel lui-même dépend de tes comptes.
