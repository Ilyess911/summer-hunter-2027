# Éligibilité, scores et apprentissage

Trois résultats séparés : **Éligibilité** (décision), **Career Fit** (0-100), **Networking** (0-100). La **priorité** les combine seulement pour les offres non rejetées.

## Éligibilité (`engine/eligibility.py`)

Chaque critère rend PASS, FAIL, UNKNOWN ou NOT_APPLICABLE avec explication, preuves, URL, confiance et date.

| Critère | Type | PASS | FAIL | UNKNOWN |
| --- | --- | --- | --- | --- |
| internship_status | preuve positive | titre de stage | apprentissage, alternance, working student, bénévolat, CDI, poste jeune diplômé | contrat non dit, mixte |
| dates | preuve positive | période explicite incluse dans 1er avril - 31 août 2027, ou début + durée qui finit avant | période ou début hors fenêtre (sauf « dates flexibles »), cycle passé, hiver/automne | « Summer 2027 » sans dates, été austral, rien |
| duration | preuve positive | durée qui tient dans les 22 semaines | durée minimale > 22 semaines (6 mois, 12 mois) | non dite, fourchette dont seul le bas tient |
| country | preuve positive | pays résolu hors exclusions | France, Israël (ou toutes les localisations exclues) | remote sans pays, lieu non résolu |
| safety | non bloquant si inconnu | aucun « formellement déconseillé » sur France Diplomatie | pays sur ta liste, ou niveau « pays entier » fixé par toi | pas d'avis stocké, zones partielles |
| language | scan de restrictions | aucune langue imposée hors français/anglais | langue obligatoire non maîtrisée (thaï, coréen…) | description trop courte |
| academic | scan | pas de restriction, ou niveau confirmé dans le profil | PhD, MBA exclusifs | Bachelor/Master non confirmés dans le profil |
| graduation | scan | fenêtre qui contient toute l'année 2027 (ou ton mois) | fenêtre qui exclut 2027 (ex. « graduating in 2028 ») | fenêtre partielle avec mois UNKNOWN, « returning to school » non confirmé |
| citizenship | scan | pas de restriction | nationalité requise différente (si la tienne est renseignée) | restriction et nationalité UNKNOWN, habilitation de sécurité |
| work_authorization | preuve positive | autorisation confirmée dans le profil, libre circulation UE (si nationalité UE renseignée), sponsorship disponible | « pas de sponsorship » et profil sans autorisation + besoin de sponsorship | non précisé (le silence n'est jamais une autorisation) |
| qualifications | scan | aucune licence obligatoire | CPA, Series 7, licence PE… obligatoire | permis de conduire demandé |
| compensation | non bloquant si inconnu | rémunération mentionnée | non rémunéré (sauf autorisation) | non dite |

Les critères « scan » ne passent que si une description d'au moins 300 caractères a été lue (confiance ≤ 0,7).

**Décision** : un FAIL de confiance ≥ 0,7 donne REJECTED ; un UNKNOWN matériel ou un FAIL faible donne NEEDS_VERIFICATION ; sinon ELIGIBLE.

Statut de sponsorship, toujours affiché : disponible, explicitement indisponible, autorisation existante requise, non précisé, ambigu.

## Career Fit (`engine/scoring.py`, `config/scoring_rules.yaml`)

| Composante | Poids | Calcul |
| --- | --- | --- |
| career_alignment | 30 % | base de la famille de poste (tier 1 : 92-96, tier 2 : 85-88, tier 3 : 74-80, tier 4 : 62) + signaux d'industrie (≤ 10) + signaux de vente consultative (≤ 12) − cold calling |
| experience_relevance | 25 % | 30 + 5 par mot-clé d'expérience vérifiée retrouvé (≤ 70) |
| mission_quality | 20 % | 55 + signaux positifs (≤ 35) − tâches à faible valeur (≤ 40) − cold calling |
| skill_development | 15 % | 45 + mots techniques et commerciaux (≤ 55) + 5 si les deux |
| long_term_value | 10 % | 50 + priorité watchlist (35 / 25 / 15) + programme structuré (10) |

Ajustements visibles : cold calling > 30 appels/jour ou « high-volume » : −35 sur alignement et mission ; simple mention : −8 ; académie de vente structurée chez une entreprise priorité 1 : +10. Durée hors 8-12 semaines : −3, hors 4-20 : −8. Pays prioritaire : +2. Familles hors cible (dev, data, hardware, finance…) : plafond à 40 + alignement / 2 (≤ 57). Préférences apprises : ±.

Avec l'IA : `0,6 × déterministe + 0,4 × IA` (ajustements appliqués une fois, en plus). Si une injection est suspectée, le score IA est ignoré.

Explications stockées : trois raisons, deux faiblesses, expériences pertinentes (identifiants du profil uniquement), compétences manquantes, valeur de carrière, extraits de preuve.

## Networking

| Relation (preuve exigée) | Score |
| --- | --- |
| Hiring manager qui publie lui-même (« my team », « I'm hiring ») | 100 |
| Recruteur qui invite à postuler | 95 |
| Membre de l'équipe qui annonce l'ouverture | 85 |
| Recruteur nommé sur l'offre officielle | 75 |
| Équipe de recrutement | 50 |
| Autre preuve (repost) | 40 |
| Aucun contact | 20 |

−15 si la confiance du contact est < 0,6. Heuristique, pas une probabilité de réponse. Ne lève jamais une exclusion.

## Priorité

`0,75 × Fit + 0,25 × Networking`, seulement si aucune exclusion confirmée. Paliers : 90+ exceptionnel, 85+ haute, 75+ à examiner, sinon masqué. Bypass stratégique : famille tier 1, entreprise priorité 1, fit ≥ 70 ; jamais sur une offre rejetée.

## Apprentissage (`engine/feedback.py`)

| Action | Effet (famille ; entreprise et pays à mi-poids) |
| --- | --- |
| Save | +2 |
| Applied | +3 |
| Dismiss | −2 |
| Raison « cold calling » | −3 sur le motif cold calling |
| Raison « rôle », « entreprise », « pays » | −3 sur la dimension |

Bornes −15 / +10, recalcul complet à chaque feedback, visibles dans `/settings` et `preferences show`, effacées par `preferences reset`.
