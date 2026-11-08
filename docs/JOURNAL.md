# Journal de développement : CVBoost Capstone

Historique du projet, tenu au fil de l'eau : ce qui a été fait, les problèmes rencontrés,
comment ils ont été corrigés, et pourquoi tel choix technique a été fait.

Dépôt : https://github.com/Michael-MBENZA/cvboost-capstone

---

## 0. Redémarrage du projet (2026-11-07)

**Contexte.** Le Capstone avait déjà été réalisé une première fois dans le dépôt GitHub Classroom
de Kadea (`kadea-academy-learners/...-michael-mbenza`). Décision : **tout recommencer de zéro**
et publier sur un dépôt personnel.

| Action | Détail |
|---|---|
| Suppression des fichiers | tout le dossier sauf `README.md` |
| README | restauré dans sa version d'origine (commit `b4a583f` « Initial commit ») avec `git checkout b4a583f -- README.md`, puis suppression de la section « Avancement » ajoutée par l'ancien travail |
| Historique Git | dossier `.git` supprimé, puis `git init -b main` : l'ancien historique a disparu localement |
| Dépôt distant | nouveau dépôt `Michael-MBENZA/cvboost-capstone` (le dépôt Kadea n'a pas pu être supprimé : pas de droits administrateur) |

**Problèmes rencontrés :**
- Le README a été modifié deux fois (version raccourcie dans l'éditeur + version restaurée). Cause : VS Code avait l'ancien fichier ouvert et l'a ré-enregistré. Correction : fermer l'onglet sans enregistrer, puis `git restore README.md`.
- L'ancienne clé OpenAI du `.env` avait déjà expiré : la perdre n'était pas un problème.

---

## Sprint 1 : socle web et gestion des utilisateurs

### Environnement
- Venv `env/` en **Python 3.11** (Coqui TTS, au Sprint 4, ne fonctionne pas avec les versions plus récentes).
- **Problème :** la mise à jour de pip a été interrompue (Ctrl+C) après la désinstallation de pip 24. Un dossier `~ip-24.0.dist-info` est resté et provoquait un avertissement.
  **Correction :** pip 26.3 s'était finalement installé ; le dossier résiduel a été supprimé à la main.
- `requirements.txt` est écrit en **ASCII** (`Out-File -Encoding ascii`), parce que PowerShell 5.1 écrit en UTF-16 par défaut avec `>` et que pip et Docker lisent mal ce format.

### Configuration (`app_web/config.py`, `.env`)
- `SECRET_KEY` générée avec `secrets.token_hex(32)` et rangée dans `.env` (jamais poussé sur GitHub).
- `.env.example` est poussé pour documenter les variables à remplir.
- **Problème :** `config.py` avait été créé dans `app_web/app_web/` (dossier en double). Python répondait `No module named 'app_web.config'`.
  **Correction :** déplacer le fichier, supprimer le dossier en double. Depuis, chaque fichier est donné avec son chemin complet.

### Modèles et fabrique d'application
- Patron **Application Factory** (`create_app`) : il permet de créer une application avec une autre configuration, comme le font les tests.
- Tables `users` et `documents` (SQLAlchemy). Les mots de passe sont hashés par Werkzeug (`scrypt`).
- `db.create_all()` crée `database/cvboost.db` automatiquement au premier lancement.

### Authentification (`app_web/routes.py`)
- Inscription (contrôles : champs, 8 caractères minimum, confirmation, doublons), connexion, déconnexion.
- Même message d'erreur que l'e-mail soit inconnu ou que le mot de passe soit faux : on ne révèle pas quels comptes existent.
- Redirection `next` limitée aux adresses internes (protection contre les redirections ouvertes).

### Templates Jinja2
- `base.html` est le gabarit commun ; les autres pages font `{% extends "base.html" %}`.
- La navigation est dynamique, grâce à `current_user.is_authenticated`.

### Upload sécurisé
5 protections successives : fichier présent, extension (`pdf`/`docx`), **signature binaire**
(`%PDF` et `PK\x03\x04`), taille (5 Mo, via `MAX_CONTENT_LENGTH` et une erreur 413 gérée) et
**renommage UUID**.
- `accept=".pdf,.docx"` dans le HTML n'est qu'un confort pour l'utilisateur : on peut le contourner (« Tous les fichiers »). La vraie sécurité est côté serveur.

### Tests (`tests/`)
- `conftest.py` : chaque test reçoit une base, un dossier d'upload et un dossier de logs **temporaires** (`tmp_path`).
- **Problème :** un commit a été fait avec le message du commit précédent.
  **Correction :** `git commit --amend -m "..."` puis `git push --force-with-lease`. C'est acceptable parce que le dépôt est personnel et qu'il n'y a qu'un seul contributeur.

### Design
- Refonte dans le style des sites de recrutement : bandeau dégradé, cartes, boutons arrondis, police Inter. Les couleurs sont regroupées dans les variables CSS `:root`.

---

## Sprint 2 : ingestion de données, scraping et analytics

### Journal des uploads (`logs/uploads.log`)
- Logger dédié `cvboost.uploads` (module `logging`), au format `date | user | ip | statut | motif | taille | fichier`.
- Le nom du fichier est placé **en dernier**, parce qu'il peut contenir des espaces et des `|` (ces derniers sont remplacés par `_`).
- `propagate = False` pour que ces lignes n'apparaissent pas dans le terminal du serveur.

### Audit des logs (`data_pipeline/logs_parser.py`)
- Lecture ligne par ligne, découpage par expression régulière. Les lignes mal formées sont comptées, pas fatales.
- 4 règles de fraude : `FAUX_FICHIER`, `EXTENSION_DANGEREUSE`, `DOUBLE_EXTENSION`, `RAFALE_DE_REFUS` (3 refus en 5 minutes depuis la même IP).
- Sorties générées et **non versionnées** : `data/processed/upload_events.csv` et `data/reports/audit_uploads.json`.
- `data/samples/uploads_sample.log` est un journal d'exemple versionné, avec un fraudeur simulé (user 4), pour que les tests et la démonstration soient reproductibles.

### Base analytique (`data_pipeline/analytics.py`)
- `database/analytics.db` est séparée de `cvboost.db` : l'analyse ne doit pas gêner l'application.
- On ne copie **pas** les noms de fichiers des CV (ce sont des données personnelles).
- 8 KPI SQL, dont 3 utilisant `HAVING` (IP suspectes, utilisateurs actifs, utilisateurs ayant plusieurs CV).
- Table `kpi_snapshots` : chaque exécution ajoute un instantané daté, ce qui permet de suivre l'évolution.
- Le module `sqlite3` de la bibliothèque standard est préféré à SQLAlchemy ici, pour écrire le SQL « à la main » (c'est l'objectif pédagogique).

### Scraping (`data_pipeline/scraper_jobs.py`)
- Site choisi : **mediacongo.net**, de vraies offres en RDC. Son `robots.txt` autorise tout le monde avec `Crawl-delay: 10`.
- La courtoisie est **lue** dans `robots.txt` (`RobotFileParser`), pas codée en dur. Le robot s'identifie avec un User-Agent explicite et fait une pause avant chaque requête.
- La liste vient du tableau HTML (`strong.format_id_emploi`). Le détail vient du **JSON-LD** `schema.org/JobPosting` intégré à la page, plus stable que la mise en page.
- **Constat sur les données réelles** (test du 2026-11-08 sur 5 offres) : beaucoup d'offres n'ont pas de vrai texte (la « description » répète le titre, car l'annonce est une image ou un PDF), et presque aucune n'indique de salaire. Ce sera traité au nettoyage Pandas (valeurs manquantes).
- **Problème :** le premier lancement s'est arrêté net avec
  `ConnectionError: Remote end closed connection without response`.
  **Cause :** `requests.Session` réutilise la même connexion (keep-alive). Après 10 s de pause, le serveur
  l'avait déjà fermée de son côté.
  **Correction :** la fonction `creer_session()` envoie l'en-tête `Connection: close` (une connexion neuve à chaque page)
  et ajoute des **réessais automatiques** (`Retry` : 3 essais, attente de 2, 4 puis 8 s, aussi sur les codes 429 et 5xx).
  Si une page de liste reste inaccessible, la collecte s'arrête proprement au lieu de planter.
- Les fonctions d'analyse HTML (`parser_liste`, `parser_detail`) sont séparées du réseau. Les tests tournent donc **hors ligne**, sur des pages d'exemple (`tests/fixtures/`).
