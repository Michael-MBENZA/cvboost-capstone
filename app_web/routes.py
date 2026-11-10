import json
import os
import uuid

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename

from . import db, ia
from .extraction import CVIllisible, extraire_texte
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


def journaliser(statut, motif, nom_fichier, taille):
    """Écrit une ligne dans logs/uploads.log pour chaque tentative de téléversement.

    Format : date | user=ID | ip=IP | statut=... | motif=... | taille=octets | fichier=nom
    Le nom du fichier est mis en dernier car il peut contenir des espaces.
    """
    user_id = current_user.id if current_user.is_authenticated else "-"
    nom_propre = nom_fichier.replace("|", "_").replace("\n", "_")
    current_app.logger_uploads.info(
        f"user={user_id} | ip={request.remote_addr} | statut={statut} | "
        f"motif={motif} | taille={taille} | fichier={nom_propre}"
    )


@bp.app_template_filter("date_fr")
def date_fr(valeur):
    """'2026-11-20' -> '20/11/2026' (format de date habituel en français)."""
    texte = str(valeur or "")
    if len(texte) >= 10 and texte[4] == "-" and texte[7] == "-":
        return f"{texte[8:10]}/{texte[5:7]}/{texte[0:4]}"
    return texte


NOMS_SOURCES = {"mediacongo": "mediacongo.net", "radiookapi": "Radio Okapi", "reliefweb": "ReliefWeb"}


@bp.app_template_filter("nom_source")
def nom_source(code):
    return NOMS_SOURCES.get(code, code)


@bp.route("/")
def index():
    # Les 5 offres les plus récentes (le CSV est déjà trié par date de publication)
    try:
        offres = ia.charger_offres()
    except FileNotFoundError:
        offres = []
    return render_template("index.html", dernieres_offres=offres[:5], nb_offres=len(offres))


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


@bp.route("/logout", methods=["POST"])  # POST + jeton CSRF : un lien piégé ne peut pas déconnecter
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
            journaliser("REFUSE", "AUCUN_FICHIER", "-", 0)
            flash("Veuillez choisir un fichier.", "danger")
            return redirect(url_for("main.upload"))

        # Taille réelle du fichier reçu (on va à la fin du flux, puis on revient au début)
        fichier.stream.seek(0, os.SEEK_END)
        taille = fichier.stream.tell()
        fichier.stream.seek(0)

        # 2. L'extension est-elle autorisée (pdf, docx) ?
        ext = extension_de(fichier.filename)
        if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
            journaliser("REFUSE", "EXTENSION_INTERDITE", fichier.filename, taille)
            flash("Format refusé : seuls les fichiers PDF et DOCX sont acceptés.", "danger")
            return redirect(url_for("main.upload"))

        # 3. Le contenu correspond-il vraiment à l'extension ?
        #    (un .exe renommé en .pdf sera refusé)
        debut = fichier.stream.read(4)
        fichier.stream.seek(0)
        if debut != SIGNATURES[ext]:
            journaliser("REFUSE", "SIGNATURE_INVALIDE", fichier.filename, taille)
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

        journaliser("ACCEPTE", "OK", fichier.filename, taille)
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


# ---------- Assistant DRH (Sprint 3) ----------

def mon_document(doc_id):
    """Le document doit appartenir à l'utilisateur connecté (sinon 404 : on ne révèle rien)."""
    return Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()


def texte_du_cv(document):
    chemin = os.path.join(current_app.config["UPLOAD_FOLDER"], document.stored_name)
    return extraire_texte(chemin, document.file_type)


@bp.route("/documents/<int:doc_id>/offres", methods=["GET", "POST"])
@login_required
def offres_compatibles(doc_id):
    """GET : affiche le classement s'il est déjà en cache. POST (bouton) : lance l'analyse."""
    document = mon_document(doc_id)
    try:
        texte_cv = texte_du_cv(document)
    except CVIllisible as erreur:
        flash(str(erreur), "danger")
        return redirect(url_for("main.history"))

    offres = ia.charger_offres()
    cle = ia.cle_classement(texte_cv, offres)
    en_cache = ia.chercher_en_cache(document.id, "classement", None, cle)

    if request.method == "POST" and en_cache is None:
        try:
            classement, tokens, mode = ia.classer_offres(texte_cv, offres)
        except ia.IAIndisponible as erreur:
            flash(str(erreur), "danger")
            return redirect(url_for("main.offres_compatibles", doc_id=doc_id))
        ia.enregistrer(document, "classement", None, cle, classement, tokens, mode)
        return redirect(url_for("main.offres_compatibles", doc_id=doc_id))  # POST -> GET (pas de double envoi)

    lignes = []
    if en_cache:
        for item in json.loads(en_cache.resultat):
            offre = ia.trouver_offre(item["reference"], offres)
            if offre:
                lignes.append({**offre, "score": item["score"], "raison": item["raison"]})
    return render_template(
        "offres.html", document=document, lignes=lignes, analyse=en_cache,
        nb_offres=len(offres), mode=ia.mode_actuel(),
    )


@bp.route("/documents/<int:doc_id>/offres/<reference>", methods=["GET", "POST"])
@login_required
def analyse_offre(doc_id, reference):
    document = mon_document(doc_id)
    offre = ia.trouver_offre(reference, ia.charger_offres())
    if offre is None:
        abort(404)
    try:
        texte_cv = texte_du_cv(document)
    except CVIllisible as erreur:
        flash(str(erreur), "danger")
        return redirect(url_for("main.history"))

    cle = ia.cle_detail(texte_cv, offre)
    en_cache = ia.chercher_en_cache(document.id, "detail", reference, cle)

    if request.method == "POST" and en_cache is None:
        try:
            resultat, tokens, mode = ia.analyser_offre(texte_cv, offre)
        except ia.IAIndisponible as erreur:
            flash(str(erreur), "danger")
            return redirect(url_for("main.analyse_offre", doc_id=doc_id, reference=reference))
        ia.enregistrer(document, "detail", reference, cle, resultat, tokens, mode)
        return redirect(url_for("main.analyse_offre", doc_id=doc_id, reference=reference))

    return render_template(
        "analyse.html", document=document, offre=offre, analyse=en_cache,
        resultat=json.loads(en_cache.resultat) if en_cache else None, mode=ia.mode_actuel(),
    )


@bp.app_errorhandler(413)
def fichier_trop_gros(erreur):
    # Déclenché automatiquement par Flask si le fichier dépasse MAX_CONTENT_LENGTH (5 Mo)
    journaliser("REFUSE", "TROP_VOLUMINEUX", "-", request.content_length or 0)
    flash("Fichier trop volumineux : 5 Mo maximum.", "danger")
    return redirect(url_for("main.upload"))
