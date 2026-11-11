from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from . import db, login_manager


def maintenant():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=maintenant)

    # Un utilisateur possède plusieurs documents
    documents = db.relationship(
        "Document", backref="owner", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password):
        # Werkzeug transforme le mot de passe en hash (irréversible)
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)  # ex : mon_cv.pdf
    stored_name = db.Column(db.String(64), unique=True, nullable=False)  # ex : 3f2a...e1.pdf
    file_type = db.Column(db.String(10), nullable=False)  # pdf ou docx
    size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=maintenant)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    analyses = db.relationship(
        "Analyse", backref="document", lazy=True, cascade="all, delete-orphan"
    )


class Analyse(db.Model):
    """Mémoire (cache) des réponses de l'Assistant DRH : on ne repaie pas OpenAI
    pour une analyse déjà faite avec le même CV, la même offre et le même prompt."""

    __tablename__ = "analyses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)          # "classement" ou "detail"
    offre_reference = db.Column(db.String(30))               # ex : OEM45094 (vide pour un classement)
    empreinte = db.Column(db.String(64), nullable=False, index=True)  # SHA-256 (CV + offre(s) + prompt + modèle)
    resultat = db.Column(db.Text, nullable=False)            # réponse au format JSON
    mode = db.Column(db.String(10), nullable=False)          # "openai" ou "demo"
    modele = db.Column(db.String(50))                        # ex : gpt-4o-mini
    tokens = db.Column(db.Integer, default=0)                # consommation (suivi des coûts)
    cree_le = db.Column(db.DateTime, default=maintenant)


class Entretien(db.Model):
    """Simulation d'entretien VOCAL : une recruteuse IA mène l'entretien à voix haute (5 minutes)."""

    __tablename__ = "entretiens_vocaux"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False)
    offre_reference = db.Column(db.String(80), nullable=False)
    transcription = db.Column(db.Text, default="[]")    # JSON : [{role: recruteuse|candidat, texte}, ...]
    duree_secondes = db.Column(db.Integer, default=0)
    bilan = db.Column(db.Text)                          # JSON : note /100, mention, déroulement, conseils
    tokens = db.Column(db.Integer, default=0)           # tokens du bilan (le vocal est facturé à la minute)
    cree_le = db.Column(db.DateTime, default=maintenant)

    document = db.relationship("Document", backref=db.backref("entretiens", cascade="all, delete-orphan"))


@login_manager.user_loader
def load_user(user_id):
    # Flask-Login appelle cette fonction pour retrouver l'utilisateur de la session
    return db.session.get(User, int(user_id))
