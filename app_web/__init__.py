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


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Crée les dossiers nécessaires s'ils n'existent pas
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import bp  # NOUVEAU
    app.register_blueprint(bp)  # NOUVEAU

    with app.app_context():
        from . import models  # noqa: F401  (enregistre les tables)
        db.create_all()

    return app
