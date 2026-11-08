import os
import uuid

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, current_app
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename

from . import db
from .models import User, Document

bp = Blueprint("main", __name__)

# Premiers octets attendus pour chaque type de fichier ("signature")
# PDF commence par %PDF, DOCX est une archive ZIP qui commence par PK
SIGNATURES = {"pdf": b"%PDF", "docx": b"PK\x03\x04"}


def extension_de(filename):
    """Renvoie l'extension en minuscules ('pdf', 'docx'...) ou ''."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not username or not email or not password:
            flash("Tous les champs sont obligatoires.", "danger")
        elif len(password) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "danger")
        elif password != confirm:
            flash("Les deux mots de passe ne correspondent pas.", "danger")
        elif User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            flash("Ce nom d'utilisateur ou cet e-mail est déjà utilisé.", "danger")
        else:
            user = User(username=username, email=email)
            user.set_password(password)  # hash Werkzeug, jamais en clair
            db.session.add(user)
            db.session.commit()
            flash("Compte créé ! Vous pouvez vous connecter.", "success")
            return redirect(url_for("main.login"))

    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)  # ouvre la session
            flash(f"Bienvenue {user.username} !", "success")

            # Retour à la page demandée, seulement si c'est une adresse interne
            next_page = request.args.get("next")
            if not next_page or not next_page.startswith("/") or next_page.startswith("//"):
                next_page = url_for("main.index")
            return redirect(next_page)

        # Même message dans les deux cas : on ne révèle pas si l'e-mail existe
        flash("E-mail ou mot de passe incorrect.", "danger")

    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()  # ferme la session
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("main.index"))


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        fichier = request.files.get("cv")

        # 1. Un fichier a-t-il été choisi ?
        if not fichier or fichier.filename == "":
            flash("Veuillez choisir un fichier.", "danger")
            return redirect(url_for("main.upload"))

        # 2. L'extension est-elle autorisée (pdf, docx) ?
        ext = extension_de(fichier.filename)
        if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
            flash("Format refusé : seuls les fichiers PDF et DOCX sont acceptés.", "danger")
            return redirect(url_for("main.upload"))

        # 3. Le contenu correspond-il vraiment à l'extension ?
        #    (un .exe renommé en .pdf sera refusé)
        debut = fichier.stream.read(4)
        fichier.stream.seek(0)
        if debut != SIGNATURES[ext]:
            flash("Le contenu du fichier ne correspond pas à un vrai PDF/DOCX.", "danger")
            return redirect(url_for("main.upload"))

        # 4. Renommage UUID : nom unique et imprévisible sur le disque
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        chemin = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name)
        fichier.save(chemin)

        # 5. Enregistrement en base
        doc = Document(
            original_name=secure_filename(fichier.filename) or f"cv.{ext}",
            stored_name=stored_name,
            file_type=ext,
            size_bytes=os.path.getsize(chemin),
            owner=current_user,
        )
        db.session.add(doc)
        db.session.commit()

        flash("CV téléversé avec succès !", "success")
        return redirect(url_for("main.history"))

    return render_template("upload.html")


@bp.route("/history")
@login_required
def history():
    # Uniquement les documents de l'utilisateur connecté, du plus récent au plus ancien
    documents = (
        Document.query.filter_by(user_id=current_user.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return render_template("history.html", documents=documents)


@bp.app_errorhandler(413)
def fichier_trop_gros(erreur):
    # Déclenché automatiquement par Flask si le fichier dépasse MAX_CONTENT_LENGTH (5 Mo)
    flash("Fichier trop volumineux : 5 Mo maximum.", "danger")
    return redirect(url_for("main.upload"))
