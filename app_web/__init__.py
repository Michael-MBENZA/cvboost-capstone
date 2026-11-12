import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from .config import Config, BASE_DIR

# Extensions créées une seule fois, branchées sur l'app dans create_app()
db = SQLAlchemy()
login_manager = LoginManager()
# Protection CSRF : chaque formulaire POST doit renvoyer un jeton secret généré par le site.
# Un site pirate ne connaît pas ce jeton, il ne peut donc pas faire agir un utilisateur connecté à sa place.
csrf = CSRFProtect()
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


FORMAT_ERREURS = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def journal_erreurs(chemin_fichier):
    """Fichier logs/erreurs.log : avertissements et erreurs (avec la trace complète des exceptions).

    Rotation : 5 fichiers de 1 Mo au maximum (erreurs.log, erreurs.log.1...), le disque ne se remplit jamais."""
    handler = RotatingFileHandler(chemin_fichier, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(FORMAT_ERREURS, "%Y-%m-%d %H:%M:%S"))
    handler.set_name("cvboost-erreurs")
    return handler


def brancher_journal_erreurs(app):
    """Toute erreur du site (exception non prévue = erreur 500, panne OpenAI...) est écrite dans le fichier.
    Flask enregistre déjà chaque exception non gérée avec app.logger.error : il suffit d'y brancher le fichier."""
    for ancien in [h for h in app.logger.handlers if h.get_name() == "cvboost-erreurs"]:
        app.logger.removeHandler(ancien)
        ancien.close()
    app.logger.addHandler(journal_erreurs(os.path.join(app.config["LOG_FOLDER"], "erreurs.log")))
    if app.logger.level == logging.NOTSET or app.logger.level > logging.INFO:
        app.logger.setLevel(logging.INFO)


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
    # Journal des erreurs (Sprint 4, MLOps) : logs/erreurs.log
    brancher_journal_erreurs(app)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .routes import bp  # NOUVEAU
    app.register_blueprint(bp)  # NOUVEAU

    with app.app_context():
        from . import models  # noqa: F401  (enregistre les tables)
        db.create_all()

    return app
