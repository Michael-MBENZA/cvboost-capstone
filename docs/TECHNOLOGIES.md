# Technologies utilisées : CVBoost Capstone

Fiche courte par technologie, mise à jour au fil du projet. Les explications détaillées
(fonctionnement interne, alternatives, exercices) seront développées dans le cours final.

| Technologie | Version | Sprint | Rôle dans le projet | Pourquoi ce choix | Alternatives |
|---|---|---|---|---|---|
| Python | 3.11 | tous | langage du projet | imposé par la formation ; 3.11 est requis par Coqui TTS (Sprint 4) | — |
| Flask | 3.1.3 | 1 | framework web (routes, requêtes, réponses) | léger, imposé par l'énoncé | Django, FastAPI |
| Jinja2 | 3.1.6 | 1 | moteur de templates HTML (`{{ }}`, `{% %}`) | intégré à Flask | Mako |
| Werkzeug | 3.1.9 | 1 | hash des mots de passe, `secure_filename` | intégré à Flask, imposé par l'énoncé | bcrypt, argon2 |
| Flask-SQLAlchemy / SQLAlchemy | 3.1.1 / 2.1.8 | 1 | ORM : tables `User` / `Document` sous forme de classes Python | imposé par l'énoncé | Peewee, SQL brut |
| Flask-Login | 0.6.3 | 1 | session de l'utilisateur connecté, `@login_required` | imposé par l'énoncé | Flask-Security |
| python-dotenv | 1.2.4 | 1 | lit les secrets du fichier `.env` | imposé par l'énoncé | variables d'environnement système |
| SQLite | (inclus dans Python) | 1, 2 | bases `cvboost.db` et `analytics.db` | un simple fichier, sans serveur à installer | PostgreSQL (prévu par l'énoncé en option) |
| pytest | 9.2.0 | 1, 2 | tests automatiques | standard de fait en Python | unittest |
| logging | (standard) | 2 | journal `logs/uploads.log` | horodatage, fichiers, niveaux | `print` (à éviter), loguru |
| re (regex) | (standard) | 2 | découpage des lignes de log, détection de salaire | précis et rapide sur du texte structuré | `str.split` |
| sqlite3 | (standard) | 2 | requêtes SQL KPI écrites à la main | montrer le SQL (`GROUP BY`, `HAVING`) | SQLAlchemy, Pandas `read_sql` |
| requests | 2.34.2 | 2 | téléchargement des pages web | simple, sessions, timeouts | httpx, urllib |
| BeautifulSoup 4 | 4.15.0 | 2 | analyse du HTML (sélecteurs CSS) | imposé par l'énoncé, tolérant au HTML imparfait | lxml, Scrapy, Selenium (sites JavaScript) |
| urllib.robotparser | (standard) | 2 | lecture de `robots.txt` (autorisations, délai) | courtoisie web demandée par l'énoncé | — |

## Dépendances installées (requirements.txt)

Les paquets listés **en plus** de ceux du tableau sont des dépendances installées automatiquement :
`blinker`, `click`, `itsdangerous`, `MarkupSafe` (pour Flask), `certifi`, `charset-normalizer`,
`idna`, `urllib3` (pour requests), `soupsieve` (pour BeautifulSoup), `iniconfig`, `pluggy`,
`packaging`, `Pygments`, `colorama` (pour pytest), `typing_extensions` (pour SQLAlchemy).

## Outils

| Outil | Rôle |
|---|---|
| Git / GitHub | versionnage, commits sémantiques (`feat:`, `fix:`, `docs:`, `chore:`, `test:`, `style:`) |
| VS Code | éditeur |
| PowerShell | terminal Windows |
