import os
from dotenv import load_dotenv

# Dossier racine du projet (le dossier parent de app_web/)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Charge les variables du fichier .env (SECRET_KEY, etc.)
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    # Clé qui signe les cookies de session
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-cle-a-changer")

    # Base de données principale : database/cvboost.db
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "database", "cvboost.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Téléversement des CV
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 Mo maximum
    ALLOWED_EXTENSIONS = {"pdf", "docx"}

    # Journal des téléversements (lu par data_pipeline/logs_parser.py au Sprint 2)
    LOG_FOLDER = os.path.join(BASE_DIR, "logs")

    # Assistant DRH (Sprint 3) : clé lue dans .env, jamais écrite dans le code.
    # Sans clé, l'assistant fonctionne en "mode démo" (score par mots-clés, gratuit).
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # Offres d'emploi réelles préparées au Sprint 2 (scraping + nettoyage Pandas)
    OFFRES_CSV = os.path.join(BASE_DIR, "data", "processed", "jobs_clean.csv")

    # Pitch audio (Sprint 4) : le site appelle l'API FastAPI /speak-smart (api_service/).
    # En local : http://127.0.0.1:8000 ; dans Docker : http://api:8000 (réseau interne, docker-compose.yml).
    API_PITCH_URL = os.getenv("API_PITCH_URL", "http://127.0.0.1:8000")
    API_PITCH_CLE = os.getenv("API_PITCH_CLE", "")  # même valeur que l'API (en-tête X-API-Key), lue dans .env
    PITCH_PROMPT_VERSION = "v2"  # suivre api_service/services/llm_reformulator.PROMPT_VERSION (cache du site)

    # Production : site derrière le proxy HTTPS Caddy (docker-compose.prod.yml met DERRIERE_PROXY=1)
    DERRIERE_PROXY = os.getenv("DERRIERE_PROXY", "") == "1"