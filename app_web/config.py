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