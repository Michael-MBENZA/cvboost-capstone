"""Tests du Sprint 1 : authentification, upload sécurisé et historique."""
import io
import os

from app_web.models import User, Document


def envoyer(client, nom, contenu):
    """Simule l'envoi d'un fichier via le formulaire /upload."""
    return client.post(
        "/upload",
        data={"cv": (io.BytesIO(contenu), nom)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


# ---------- Authentification ----------

def test_pages_publiques_accessibles(client):
    for page in ["/", "/login", "/register"]:
        assert client.get(page).status_code == 200


def test_inscription_hashe_le_mot_de_passe(client, app):
    client.post("/register", data={
        "username": "alice", "email": "alice@test.cd",
        "password": "secret1234", "confirm": "secret1234",
    })
    with app.app_context():
        user = User.query.filter_by(email="alice@test.cd").first()
        assert user is not None
        assert user.password_hash != "secret1234"  # jamais en clair
        assert user.check_password("secret1234")


def test_inscription_refuse_email_en_double(client, app):
    donnees = {"username": "bob", "email": "bob@test.cd",
               "password": "secret1234", "confirm": "secret1234"}
    client.post("/register", data=donnees)
    donnees["username"] = "bob2"
    reponse = client.post("/register", data=donnees)
    assert "déjà utilisé" in reponse.get_data(as_text=True)
    with app.app_context():
        assert User.query.count() == 1


def test_connexion_mauvais_mot_de_passe(client):
    client.post("/register", data={
        "username": "carl", "email": "carl@test.cd",
        "password": "secret1234", "confirm": "secret1234",
    })
    reponse = client.post("/login", data={"email": "carl@test.cd", "password": "faux"})
    assert "incorrect" in reponse.get_data(as_text=True)


def test_navigation_dynamique(connecte):
    page = connecte.get("/").get_data(as_text=True)
    assert "Déconnexion" in page
    connecte.get("/logout")
    page = connecte.get("/").get_data(as_text=True)
    assert "Connexion" in page and "Déconnexion" not in page


def test_pages_protegees_sans_connexion(client):
    for page in ["/upload", "/history", "/logout"]:
        reponse = client.get(page)
        assert reponse.status_code == 302
        assert "/login" in reponse.headers["Location"]


# ---------- Téléversement sécurisé ----------

def test_upload_pdf_valide_renomme_en_uuid(connecte, app):
    reponse = envoyer(connecte, "mon cv.pdf", b"%PDF-1.4 contenu")
    assert "succès" in reponse.get_data(as_text=True)
    with app.app_context():
        doc = Document.query.one()
        assert doc.original_name == "mon_cv.pdf"
        assert doc.stored_name != "mon cv.pdf"
        assert len(doc.stored_name) == 32 + len(".pdf")  # uuid4().hex = 32 caractères
        assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], doc.stored_name))


def test_upload_docx_valide(connecte, app):
    envoyer(connecte, "cv.docx", b"PK\x03\x04 contenu")
    with app.app_context():
        assert Document.query.one().file_type == "docx"


def test_upload_refuse_mauvaise_extension(connecte, app):
    reponse = envoyer(connecte, "cv.txt", b"bonjour")
    assert "Format refusé" in reponse.get_data(as_text=True)
    with app.app_context():
        assert Document.query.count() == 0


def test_upload_refuse_faux_pdf(connecte, app):
    reponse = envoyer(connecte, "virus.pdf", b"MZ\x90\x00")  # un .exe renommé
    assert "ne correspond pas" in reponse.get_data(as_text=True)
    with app.app_context():
        assert Document.query.count() == 0


def test_upload_refuse_plus_de_5_mo(connecte, app):
    gros = b"%PDF" + b"0" * (5 * 1024 * 1024 + 1)
    reponse = envoyer(connecte, "gros.pdf", gros)
    assert "trop volumineux" in reponse.get_data(as_text=True)
    with app.app_context():
        assert Document.query.count() == 0


# ---------- Historique ----------

def test_historique_ne_montre_que_mes_cv(connecte, app):
    envoyer(connecte, "cv_michael.pdf", b"%PDF-1.4")
    connecte.get("/logout")

    # Un autre utilisateur ne doit pas voir le CV de michael
    connecte.post("/register", data={
        "username": "dina", "email": "dina@test.cd",
        "password": "secret1234", "confirm": "secret1234",
    })
    connecte.post("/login", data={"email": "dina@test.cd", "password": "secret1234"})
    page = connecte.get("/history").get_data(as_text=True)
    assert "cv_michael.pdf" not in page
    assert "aucun CV" in page
