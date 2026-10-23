# Piste Industrie & Robotique

Deuxième piste de recherche, parallèle à la piste Business & Technologie (vente technique,
solutions, product marketing, business development, produit). Elle vise les stages où ton
expérience Air France Industries et Airbus Helicopters fait de toi un candidat crédible :
maintenance, projets industriels, opérations, robotique en déploiement, industrie 4.0.
Rien n'a été retiré de la piste Business : ses familles, ses requêtes et sa formule de priorité
sont inchangées.

## Familles de postes (`config/scoring_rules.yaml`)

| Famille | Catégorie | Niveau | Exemples de titres |
| --- | --- | --- | --- |
| `maintenance_reliability` | Maintenance | 1 | Maintenance Engineering, Reliability Engineering, MRO, Maintenance Planning, Technical Services |
| `industrial_project_management` | Industrial Project Management | 1 | Industrial / Engineering / Technical Project, Project Engineer, Industrialization, Technical Operations |
| `robotics_raas` | Robotics | 1 | Robotics Operations / Deployment / Customer Success / Solutions, RaaS, Automation Solutions, AMR |
| `industrial_operations` | Industrial Operations | 2 | Operational Excellence, Continuous Improvement, Lean, Production Planning, Supply Chain Project |
| `industrial_digital` | Industrial Digitalization | 2 | Industry 4.0, Smart Manufacturing, Industrial AI / IoT, Digital Operations, Automation Project |
| `robotics_specialized` | Robotics (specialized engineering) | 0 | Robotics Software, Perception, SLAM, ROS, Motion Planning, Controls |
| `design_engineering` | Other | 0 | Industrial / Product / Mechanical Design (ignorés à la collecte) |

Les familles spécialisées restent collectées (sauf la conception pure) : c'est le contenu de l'offre,
pas un mot du titre, qui décide. « Site Reliability Engineer » reste un poste logiciel ;
« Manufacturing Sales » reste un poste de vente.

## Quatre scores, jamais fusionnés

| Score | Question |
| --- | --- |
| Career Fit | Le poste va-t-il dans le sens de mes objectifs ? |
| **Recruitability** | Mon expérience vérifiée correspond-elle à ce qui est réellement demandé ? |
| Networking | Ai-je une personne identifiable à contacter ? |
| Eligibility | Puis-je réellement candidater ? |

**Recruitability n'est pas une probabilité d'embauche.** C'est une correspondance 0-100 entre ton
profil vérifié (`config/candidate_profile.yaml`) et les exigences écrites dans l'offre :

| Composante | Poids | Calcul |
| --- | --- | --- |
| Expérience professionnelle directe | 30 % | mots-clés de tes expériences pro trouvés dans l'offre, bonus si l'expérience est directe pour cette famille (règle `experience_families`) |
| Compétences techniques | 25 % | compétences citées, pondérées par niveau : obligatoire ×3, souhaitée ×1,5, simple exposition ×0,5 ; 60 si l'offre n'en cite aucune (signalé « inconnu ») |
| Formation | 20 % | école d'ingénieurs en dernière année, bonus si le domaine demandé correspond, bonus maintenance (BUT GIM), malus doctorat exigé |
| Expérience transférable | 15 % | projets, entrepreneuriat, associatif |
| Secteur | 10 % | aéronautique / MRO 90, industrie / logistique 75, robotique / automatisation 65 |

Une compétence spécialisée **obligatoire** que tu n'as pas (ROS2, SLAM, CAO, éléments finis, CFD,
automates programmables, systèmes embarqués, automatique, vision, C++, électronique,
semi-conducteurs) retire 15 points ; deux ou plus plafonnent le score à 35.

### Obligatoire, souhaité, exposition

`engine/requirements.py` lit chaque phrase et la section où elle se trouve (« Requirements » contre
« Nice to have ») :

- « Proven PLC programming experience is mandatory » : obligatoire.
- « ROS2 is a plus » ou sous « Preferred qualifications » : souhaité.
- « You will get exposure to PLCs » : exposition, sans effet sur l'éligibilité.
- « C++, Java or Python » : une alternative que tu as suffit, la ligne n'est pas bloquante.

Le nouveau critère d'éligibilité `technical_requirements` (13e critère) n'échoue franchement que pour
une expertise spécialisée obligatoire formulée comme cœur du poste (« strong », « proven »,
« N years »…). Une simple mention « required » donne un échec faible : l'offre passe en
NEEDS_VERIFICATION, jamais en REJECTED.

## Priorité industrielle

Pour la piste Industrie & Robotique, le classement et le seuil de notification utilisent :

```
Industrial Priority = 40 % Recruitability + 35 % Career Fit + 25 % Networking
```

Uniquement pour les offres non rejetées. Poids modifiables (`industrial_priority.weights`),
désactivable (`industrial_priority.enabled: false`). La piste Business garde
`75 % fit + 25 % networking`.

## Contraintes inchangées

Fenêtre 1er avril - 31 août 2027, pays exclus (dont France et Israël), règles de sécurité, langues
(un allemand ou un japonais obligatoire rejette l'offre), diplôme, dates et visa (non précisé =
UNKNOWN). Aucune certification, licence de maintenance aéronautique ou compétence robotique n'est
supposée.

## Découverte : deux pistes en parallèle

`config/search_preferences.yaml`, section `discovery` :

- `allocation: {business_tech: 0.5, industry_robotics: 0.5}` partage le budget de requêtes par
  passage (30 par défaut). Une piste qui n'a plus de requêtes prête sa part à l'autre.
- Les requêtes tournent d'une heure à l'autre : toutes servent au fil des passages.
- `add_yearless_variants: true` relance chaque requête d'offres sans « 2027 » (les dates restent
  vérifiées ensuite).
- La répartition ne touche que l'effort de recherche : aucun score n'est ajusté pour équilibrer
  le nombre de recommandations.

Ces requêtes passent par l'API de recherche (Brave ou Google CSE), dans les mêmes limites LinkedIn
qu'avant ([LINKEDIN_LIMITATIONS.md](LINKEDIN_LIMITATIONS.md)). Sans clé de recherche, la piste
s'appuie sur les flux publics des entreprises.

## Entreprises ajoutées (`config/target_companies.yaml`)

Aéronautique et MRO, technologie industrielle, robotique et automatisation, logistique. Flux
publics vérifiés le 23 oct. 2026 (HTTP 200 avec des offres) : Airbus, Rolls-Royce, GE Aerospace,
Rockwell Automation, Hitachi, Maersk, Boston Dynamics (Workday) ; Locus Robotics, Formic, Agility
Robotics, Path Robotics, Diligent Robotics, Outrider, GROPYUS (Greenhouse) ; ANYbotics, Dexterity,
Veo Robotics, OSARO, Pickle Robot, Ambi Robotics (Lever) ; Standard Bots, Sereact, RobCo (Ashby).
Les autres (Lufthansa Technik, Safran, ST Engineering, SIA Engineering, KUKA, FANUC, Universal
Robots, Exotec, AutoStore, DHL…) n'ont pas de flux public lisible : elles ne remontent que via
LinkedIn. Ce sont des cibles de découverte, pas des employeurs garantis.

## Telegram

Carte « 🏭 INDUSTRIAL INTERNSHIP — SUMMER 2027 » avec Career Fit, Recruitability, Networking,
Eligibility, les raisons tirées de ton expérience, les principales réserves et la catégorie.

| Commande | Contenu |
| --- | --- |
| `/industry` | Meilleures offres de la piste Industrie & Robotique |
| `/maintenance` | Maintenance et fiabilité |
| `/robotics` | Robotique et RaaS |
| `/industrial_projects` | Gestion de projets industriels |
| `/high_recruitability` | Recruitability ≥ 75, toutes pistes |
| `/applications maintenance` | Candidatures CRM filtrées par catégorie |

Le bouton « Explain Score » détaille aussi la recruitabilité (composantes, expérience pertinente,
exigences manquantes).

## CRM et Excel

Nouvelles colonnes (lecture seule) dans la feuille Applications : Career Category, Recruitability
Score, Industrial Priority Score, Relevant Industrial Experience, Missing Technical Requirements ;
Career Category, Recruitability et Industrial Priority dans Opportunities. Le filtre automatique
d'Excel sur Career Category trie par piste. Un seul CRM, une seule base.
