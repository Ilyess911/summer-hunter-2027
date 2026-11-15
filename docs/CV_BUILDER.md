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

Un PDF ou DOCX envoyé au bot est lu comme un CV ; un `.xlsx` reste un import du CRM.

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

## Règles d'intégrité

- Statuts : VERIFIED (CV approuvé, profil), USER_CONFIRMED (bouton Confirmer), INFERRED (à confirmer,
  jamais affiché comme un fait), REJECTED (jamais reproposé), MISSING.
- Une exigence d'offre n'est jamais ajoutée au profil. Sans offre, seules les compétences « généralement
  attendues pour ce poste » sont citées, étiquetées comme telles. Aucune offre n'est inventée pour une
  candidature spontanée.
- Le contrôle « faits » refuse toute puce, tout projet et toute compétence absents du profil vérifié.
- Aucun score ATS inventé : le rapport liste exigences couvertes, partielles et non couvertes.
- Les textes des faits ne sont jamais écrits dans les logs ; un CV importé est supprimé dès sa lecture.

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
