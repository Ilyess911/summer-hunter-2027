# AI CV Builder

Un CV adapté à n'importe quelle demande, envoyé sur Telegram en PDF et en DOCX, à partir du profil
vérifié uniquement. Aucun modèle de langage n'écrit le texte du CV : tout vient du CV approuvé
(`config/cv_content.yaml`) ou d'un fait que le candidat a confirmé.

## Utilisation

| Commande | Effet |
| --- | --- |
| `/cv <demande>` | Génère le CV : offre complète, intitulé, entreprise, secteur ou demande libre |
| `/cv` | Demande la cible ; le message suivant (même une longue offre coupée par Telegram) est la demande |
| `/cv_profile` | Profil permanent : vérifié, confirmé, à confirmer (boutons ✅ / ❌) |
| `/cv_profile add <texte>` | Propose un fait ; enregistré seulement après « Confirmer » |
| `/cv_upload` | Lit un CV existant (PDF/DOCX) et propose les lignes que le profil n'a pas |
| `/cv_history [n]` | CV générés (dernière version de chacun), versions de CV-000n, renvoi des fichiers |
| `/cv_settings` | Langue par défaut, pages, style, formats envoyés, personnalisation |
| `/cv_analyze <demande>` | Montre l'analyse sans générer |

Après un CV, un message court le modifie et crée la version suivante :

- « Fais-le en anglais et sur une seule page. »
- « Mets davantage en avant mon projet de robotique médicale. »
- « Retire Amundi », « version générique », « style ATS », « pour un poste de Sales Engineer ».

Répondre au message d'un CV modifie ce CV précis. « J'ai obtenu une certification IBM » ne modifie pas
le CV : le bot propose d'ajouter le fait au profil, puis 🔁 Regénérer l'intègre.

Boutons sous chaque CV : Regénérer, Modifier, Style, Langue, PDF, DOCX, Sauvegarder (⭐).

Un PDF ou DOCX n'est lu comme un CV qu'après `/cv_upload` (un PDF peut être une offre : ses exigences ne
doivent jamais entrer dans le profil ; les sections « Requirements », « Profil recherché »… sont de toute façon
ignorées). Un `.xlsx` reste un import du CRM.

## Mini App : onglet CV

Le 6e onglet de la Mini App fait la même chose qu'au clavier : zone de texte (offre ou demande), puces
Langue et Pages, bouton Générer, puis fiche du CV avec téléchargement PDF / DOCX, envoi dans le chat,
Sauvegarder, Regénérer, changement de langue, retouches rapides (1 page, 2 pages, générique, ciblé) ou
libres (« mets en avant Nexya »), rapport, profil (faits à confirmer ✓ / ✕, ajout d'un fait) et
historique avec toutes les versions.

API (`web/cv_api.py`, authentification Telegram initData sur `/api/`, liens signés sur `/files/`) :
`POST /api/cv`, `GET /api/cv/history`, `GET|POST /api/cv/profile`, `POST /api/cv/profile/{id}`,
`GET /api/cv/{id}`, `POST /api/cv/{id}/edit|regenerate|save|send`, `GET /files/cvb/{id}/pdf|docx`.

Le bot et la Mini App sont deux processus : `cv_builder/service.py` porte la logique commune et sérialise
les générations par un verrou de fichier (`data/cv_builder/.build.lock`).

## Email de candidature (moteur V3)

Écrit automatiquement après chaque CV (bot), ou à la demande (bouton « Email », onglet CV). Jamais envoyé.

1. **Stratégie** (code) : situation (spontanée, offre, recruteur, dirigeant, mise en relation, relance), fil
   conducteur du parcours choisi selon le poste (`config/application_voice.yaml` : business, international,
   industrie, robotique, produit, stratégie), expériences et angle, faits vérifiés sur l'organisation
   (`config/organisations.yaml`, sinon interdiction de la décrire), différenciation, action attendue.
2. **Rédaction** : un seul appel structuré au modèle (local par défaut, `llm.yaml` → `emails`), qui répond
   d'abord aux 5 questions (pourquoi l'organisation, le poste, le parcours, la différence, l'action), puis écrit.
   Salutation et signature ajoutées par le code.
3. **Contrôles** : destinataire, chiffres inventés ou chiffre associé à un autre mot (« 140 attendees » contre
   « 140 registrations »), rôle de direction non prouvé, outils, années, sujets interdits (ESSEC, ESCP, AST…),
   clichés, copie du CV, liste de compétences, formulations de l'email de référence, accroche déjà utilisée,
   longueur, description non sourcée de l'organisation, idée répétée.
4. **Une révision** ciblée si un contrôle échoue ; si un problème bloquant reste, l'email déterministe le remplace.
5. **Révisions à la demande** : Plus naturel, Plus convaincant, Moins formel, Plus personnel, Revois l'accroche,
   Change la conclusion, Explique mieux ma motivation, « Ne parle pas de X » (boutons, texte libre, puces Mini App).

L'email FCCS de référence sert de modèle de raisonnement : son texte n'est jamais montré au modèle et ses
formulations sont détectées, pour qu'il ne devienne pas un gabarit. Signature générée depuis les données
vérifiées, en texte et en HTML. Évaluation : `scripts/eval_emails.py` (cas A à J, avant/après, grille de
relecture) → `docs/email_examples/`.

## Chaîne

```
demande ─▶ intent.analyze ─▶ prefs.apply ─▶ document.compose ─▶ PDF (LaTeX) + DOCX ─▶ contrôles ─▶ versions ─▶ Telegram
            (familles, exigences,          (web.cv.tailor +       même liste de blocs     pages, dates, sections,
             pays, langue, pages)           faits confirmés)                              faits, parité PDF = DOCX
```

| Module | Rôle |
| --- | --- |
| `cv_builder/profile.py` | Faits hors YAML : INFERRED → USER_CONFIRMED ou REJECTED, table `profile_facts` |
| `cv_builder/intent.py` | Analyse déterministe : poste, entreprise, pays, langue, exigences couvertes / partielles / à confirmer / manquantes |
| `cv_builder/document.py` | Composition, rendu PDF et DOCX, ajustement au nombre de pages, contrôles |
| `cv_builder/versions.py` | Versions en ajout seul, table `cv_versions`, dossiers `data/cv_builder/CV-000n/vN/` |
| `cv_builder/edits.py` | Compréhension des retouches par message |
| `cv_builder/prefs.py` | Réglages `/cv_settings` (kv `cv_settings`) |
| `cv_builder/upload.py` | Lecture d'un CV importé, lignes nouvelles seulement |
| `bot/cv_handlers.py` | Commandes, conversation, boutons |

## Destinataire : ApplicationContext

Incident du 15 nov. 2026 : une demande spontanée de 3 ko pour la French Chamber of Commerce in Singapore a
produit un CV et un mail pour Airbus. Cause : tout texte de plus de 600 caractères était lu comme une offre,
la formule « candidature spontanée à la … » n'était pas reconnue, et le premier nom d'entreprise connu du
texte (Airbus, dans la liste de tes propres expériences) devenait la cible.

Désormais :

- `cv_builder/context.py` : `ApplicationContext` (target_company, aliases, target_role, application_type,
  target_country, availability, recipient, language) est l'unique description du destinataire. C'est une
  vue de la demande enregistrée avec la version : le CV et le mail la lisent tous les deux.
- La cible vient seulement de la demande en cours, d'une formule de destinataire (« candidature (spontanée)
  à / chez / auprès de X », « je postule chez X », « CV pour X », « chez X » hors contexte passé). Un nom
  simplement cité n'est jamais une cible ; tes employeurs, écoles et projets non plus, sauf si la demande
  les nomme comme destinataires. Pour une offre : ligne « Entreprise : », en-tête hors lignes partenaires /
  outils, puis « About X / Join X ». Plusieurs candidats : aucune cible, une question.
- Une longue demande écrite par toi n'est plus prise pour une offre.
- Un nouveau message qui vise une autre entreprise crée un nouveau CV, jamais une version du précédent.
- Contrôles bloquants avant tout enregistrement ou envoi (`validate_cv`, `validate_email`) : le destinataire
  imprimé sur le CV et chaque emplacement de destinataire du mail (salutation, ouverture, clôture) doivent
  être exactement la cible ou son sigle ; un emplacement « expérience » ne peut citer que ton parcours. En cas
  de doute, ils bloquent. Un CV bloqué n'est jamais enregistré.

## Règles d'intégrité

- Statuts : VERIFIED (CV approuvé, profil), USER_CONFIRMED (bouton Confirmer), INFERRED (à confirmer,
  jamais affiché comme un fait), REJECTED (jamais reproposé), MISSING.
- Une exigence d'offre n'est jamais ajoutée au profil. Sans offre, seules les compétences « généralement
  attendues pour ce poste » sont citées, étiquetées comme telles. Aucune offre n'est inventée pour une
  candidature spontanée.
- Le contrôle « faits » refuse toute puce, tout projet et toute compétence absents du profil vérifié.
- Aucun score ATS inventé : le rapport liste exigences couvertes, partielles et non couvertes.
- Les textes des faits ne sont jamais écrits dans les logs (une erreur n'y laisse que son type et sa
  position dans le code) ; un CV importé est supprimé dès sa lecture.
- Le rendu se fait hors transaction d'écriture, dans `data/cv_builder/_staging/`, puis les fichiers sont
  déplacés vers leur version : la base n'est jamais verrouillée pendant pdflatex. Un build à la fois, livraison
  comprise, pour que le CV « actif » soit toujours le dernier livré.

## Limites

- **Français** : titres en français, texte en anglais tant que `config/cv_content_fr.yaml` (version
  française validée par le candidat, même structure que `cv_content.yaml`) n'existe pas. Pas de
  traduction automatique.
- **Styles** : seul ATS Classic (mise en page LaTeX approuvée). Modern Professional et Consulting /
  Business restent à faire.
- **Orthographe** : non vérifiée automatiquement ; le texte vient de contenus déjà relus.
- **Retouches** : comprises par motifs (langue, pages, style, mettre en avant, retirer, poste visé,
  personnalisation). Une demande hors de ces motifs reçoit des exemples, rien n'est deviné.
- **Import** : lecture ligne à ligne ; le type de fait est deviné (compétence, projet…) et reste à confirmer.
- **Rendu DOCX** : vérifié par son XML et par la parité de texte ; l'aperçu macOS ignore les tabulations
  droites, Word les respecte.
