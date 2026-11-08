import logging
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

from .config import Config, BASE_DIR

# Extensions créées une seule fois, branchées sur l'app dans create_app()
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "main.login"  # page où rediriger si on n'est pas connecté
login_manager.login_message = "Connectez-vous pour accéder à cette page."


def creer_logger_uploads(chemin_fichier):
    """Logger dédié qui écrit chaque tentative d'upload dans un fichier texte."""
    logger = logging.getLogger("cvboost.uploads")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # n'affiche pas ces lignes dans le terminal
    # On retire les anciens fichiers ouverts (utile quand create_app est appelé plusieurs fois)
    for ancien in list(logger.handlers):
        logger.removeHandler(ancien)
        ancien.close()
    handler = logging.FileHandler(chemin_fichier, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Crée les dossiers nécessaires s'ils n'existent pas
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)

    # Journal des téléversements : logs/uploads.log (une ligne par tentative)
    os.makedirs(app.config["LOG_FOLDER"], exist_ok=True)
    app.logger_uploads = creer_logger_uploads(
        os.path.join(app.config["LOG_FOLDER"], "uploads.log")
    )

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import bp  # NOUVEAU
    app.register_blueprint(bp)  # NOUVEAU

    with app.app_context():
        from . import models  # noqa: F401  (enregistre les tables)
        db.create_all()

    return app
