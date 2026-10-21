# LinkedIn : ce qui est collecté, et ce qui ne l'est pas

## Le constat

Le `robots.txt` de linkedin.com interdit tout accès automatisé sans autorisation écrite (« The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited »). Les API officielles de LinkedIn (Jobs, Posts) ne sont ouvertes qu'à des partenaires. Un outil personnel n'a donc aucun moyen autorisé de lire LinkedIn directement.

Summer Hunter ne fait **aucune** requête vers linkedin.com. Il n'utilise ni cookie de session, ni navigateur automatisé, ni proxy, ni contournement de CAPTCHA, et n'envoie aucun message.

## Ce qui fonctionne à la place

| Canal | Ce qui entre | Statut |
| --- | --- | --- |
| Emails d'alertes LinkedIn Jobs | id de l'offre, titre, entreprise, lieu, URL publique `linkedin.com/jobs/view/<id>/` | Implémenté et testé sur un email type. Non vérifié sur ta boîte réelle (le format des emails LinkedIn peut changer). |
| API de recherche (Brave ou Google Programmable Search) | URL, titre et extrait des posts `linkedin.com/posts/...` et offres `linkedin.com/jobs/view/...` indexés publiquement | Implémenté et testé avec des réponses simulées. Non vérifié en réel : nécessite ta clé. |
| Ajout manuel | `add-post` (texte du post collé, auteur, URL du profil, titre de l'auteur) ; `add-job` (offre que tu as ouverte) | Implémenté et testé (scénario G). |
| Dédoublonnage | une offre vue sur LinkedIn et sur le site carrière devient une seule opportunité avec deux sources | Implémenté et testé (scénario H). |

### Mettre en place les alertes

1. Sur LinkedIn Jobs, crée des alertes (ex. « Solutions Engineer Intern », « Product Marketing Intern ») par pays prioritaire, fréquence quotidienne, envoi par email.
2. Choisis une méthode :
   - **IMAP en lecture seule** : mot de passe d'application (Gmail : Compte Google > Sécurité > Mots de passe des applications), puis `IMAP_HOST=imap.gmail.com`, `IMAP_USER`, `IMAP_PASSWORD`. Seuls les messages des expéditeurs `jobalerts-noreply@linkedin.com` et similaires des 3 derniers jours sont lus, sans les marquer comme lus.
   - **Dossier `.eml`** : enregistre les emails (Mail.app : Fichier > Enregistrer sous, format source brute) dans un dossier, puis `LINKEDIN_ALERTS_DIR=/chemin/du/dossier`.

### Mettre en place la découverte de posts

`BRAVE_SEARCH_API_KEY=...` (ou `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX`). Les requêtes sont dans `config/search_preferences.yaml` (`discovery.post_queries`, `job_queries`, plafond `max_queries_per_run`).

## Ce que le système sait et ne sait pas d'un post

- Il ne voit que le **titre et l'extrait** renvoyés par le moteur de recherche, pas le post complet. La confiance de classification est donc réduite (x 0,8).
- Il classe en sept catégories : stage confirmé, annonce recruteur avec offre, manager annonçant une ouverture, campagne générale, repost, conseil de carrière, hors sujet. Règles explicites, puis LLM si configuré.
- Il crée une opportunité seulement si l'entreprise est connue (titre de l'auteur ou `--company`). Sinon le post est gardé comme **piste** et listé dans le digest.
- Le titre du poste est la phrase exacte du post, ou « Internship (role not specified in post) ». Jamais inventé.
- Le nom de l'auteur n'est extrait que d'un motif explicite (« Jane Doe on LinkedIn: ... »). L'URL du profil n'est jamais reconstruite : sans URL fournie, le bouton Contact pointe vers le post.
- « Hiring manager » n'est retenu que si l'auteur écrit lui-même « my team », « I'm hiring », « reporting to me »… Travailler dans l'entreprise ne suffit pas.

## Ce que le système ne prétend pas

Il ne surveille pas « tout LinkedIn Jobs » ni « tout LinkedIn Posts ». Il surveille tes alertes, ce que l'API de recherche indexe, et ce que tu lui donnes. Le digest et `summer-hunter sources` affichent `disabled` pour un canal non configuré.
