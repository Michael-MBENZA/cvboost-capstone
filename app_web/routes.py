from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from . import db
from .models import User

bp = Blueprint("main", __name__)


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
