"""Tests du Sprint 3 : Assistant DRH (extraction du texte des CV)."""
import pytest
from docx import Document as DocumentWord

from app_web.extraction import CVIllisible, extraire_texte, nettoyer


def creer_pdf(chemin, lignes):
    """Fabrique un vrai petit PDF contenant du texte (sans bibliothèque externe)."""
    contenu = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lignes) + " ET"
    objets = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(contenu)} >>\nstream\n{contenu}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    sortie, positions = "%PDF-1.4\n", []
    for numero, objet in enumerate(objets, start=1):
        positions.append(len(sortie))
        sortie += f"{numero} 0 obj\n{objet}\nendobj\n"
    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n"
    sortie += "".join(f"{p:010d} 00000 n \n" for p in positions)
    sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n"
    chemin.write_bytes(sortie.encode("latin-1"))
    return chemin


def test_extraction_pdf(tmp_path):
    pdf = creer_pdf(tmp_path / "cv.pdf", [
        "Michael Mbenza - Developpeur IA",
        "Competences : Python, SQL, Flask, Pandas",
        "Experience : 3 ans en support informatique a Kinshasa",
    ])
    texte = extraire_texte(str(pdf), "pdf")
    assert "Python, SQL, Flask" in texte
    assert "Kinshasa" in texte


def test_extraction_docx_paragraphes_et_tableaux(tmp_path):
    doc = DocumentWord()
    doc.add_paragraph("Grace Kabila")
    doc.add_paragraph("Comptable senior   avec  8 ans d'expérience en ONG")
    tableau = doc.add_table(rows=2, cols=2)
    tableau.cell(0, 0).text, tableau.cell(0, 1).text = "Langues", "Français, Anglais, Lingala"
    tableau.cell(1, 0).text, tableau.cell(1, 1).text = "Logiciels", "Excel, Sage"
    chemin = tmp_path / "cv.docx"
    doc.save(chemin)

    texte = extraire_texte(str(chemin), "docx")
    assert "Comptable senior avec 8 ans" in texte          # espaces multiples nettoyés
    assert "Langues | Français, Anglais, Lingala" in texte  # le texte des tableaux est lu


def test_pdf_sans_texte_refuse(tmp_path):
    pdf = creer_pdf(tmp_path / "scan.pdf", [])  # comme un CV scanné : aucune ligne de texte
    with pytest.raises(CVIllisible, match="scanné"):
        extraire_texte(str(pdf), "pdf")


def test_fichier_endommage_refuse(tmp_path):
    faux = tmp_path / "abime.pdf"
    faux.write_bytes(b"%PDF-1.4 ceci n'est pas un vrai PDF")
    with pytest.raises(CVIllisible):
        extraire_texte(str(faux), "pdf")
    faux_docx = tmp_path / "abime.docx"
    faux_docx.write_bytes(b"PK\x03\x04 pas un vrai docx")
    with pytest.raises(CVIllisible):
        extraire_texte(str(faux_docx), "docx")


def test_nettoyer():
    assert nettoyer("  Bonjour   le\t monde \n\n\n\n Fin ") == "Bonjour le monde\n\nFin"


# ---------- Assistant DRH : classement, analyse détaillée, cache ----------

import csv
import io
import json

from app_web import ia
from app_web.models import Analyse, Document

COLONNES_OFFRES = ["reference", "intitule", "entreprise", "lieu", "province", "date_limite",
                   "description", "description_disponible", "url", "comp_python", "comp_comptabilite"]

OFFRES = [
    {"reference": "OEM1", "intitule": "Développeur Python", "entreprise": "ACME", "lieu": "Kinshasa",
     "province": "Kinshasa", "date_limite": "2026-12-01",
     "description": "Développer des API Flask, écrire du SQL, analyser des données avec Pandas. " * 3,
     "description_disponible": "True", "url": "https://exemple.cd/1", "comp_python": "1", "comp_comptabilite": "0"},
    {"reference": "OEM2", "intitule": "Comptable", "entreprise": "ONG Espoir", "lieu": "Goma",
     "province": "Nord-Kivu", "date_limite": "2026-12-05", "description": "",
     "description_disponible": "False", "url": "https://exemple.cd/2", "comp_python": "0", "comp_comptabilite": "1"},
]


def ecrire_offres(app):
    with open(app.config["OFFRES_CSV"], "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLONNES_OFFRES)
        writer.writeheader()
        writer.writerows(OFFRES)


def deposer_cv_docx(client, texte):
    doc = DocumentWord()
    for ligne in texte.split("\n"):
        doc.add_paragraph(ligne)
    flux = io.BytesIO()
    doc.save(flux)
    flux.seek(0)
    client.post("/upload", data={"cv": (flux, "mon_cv.docx")}, content_type="multipart/form-data")


CV_DEV = ("Michael Mbenza, développeur Python à Kinshasa.\n"
          "Compétences : Python, Flask, SQL, Pandas, analyse de données.\n"
          "Expérience : 3 ans de support informatique et de développement d'API.")


def preparer(connecte, app):
    ecrire_offres(app)
    deposer_cv_docx(connecte, CV_DEV)
    with app.app_context():
        return Document.query.one().id


def test_classement_demo_et_cache(connecte, app):
    doc_id = preparer(connecte, app)

    page = connecte.get(f"/documents/{doc_id}/offres").get_data(as_text=True)
    assert "Lancer l'analyse" in page and "Mode démo" in page   # rien en cache : bouton

    connecte.post(f"/documents/{doc_id}/offres")
    page = connecte.get(f"/documents/{doc_id}/offres").get_data(as_text=True)
    assert page.index("Développeur Python") < page.index("Comptable")  # trié par score
    assert "Annonce en image" in page                                   # OEM2 sans description

    connecte.post(f"/documents/{doc_id}/offres")  # 2e clic : le cache évite un nouveau calcul
    with app.app_context():
        assert Analyse.query.filter_by(type="classement").count() == 1


def test_analyse_detaillee_demo(connecte, app):
    doc_id = preparer(connecte, app)
    connecte.post(f"/documents/{doc_id}/offres/OEM1")
    page = connecte.get(f"/documents/{doc_id}/offres/OEM1").get_data(as_text=True)
    assert "Points forts" in page and "Première question d'entretien" in page
    with app.app_context():
        analyse = Analyse.query.filter_by(type="detail").one()
        assert analyse.mode == "demo" and analyse.offre_reference == "OEM1"


def test_offre_inconnue_404(connecte, app):
    doc_id = preparer(connecte, app)
    assert connecte.get(f"/documents/{doc_id}/offres/INCONNUE").status_code == 404


def test_cv_d_un_autre_utilisateur_404(connecte, app):
    doc_id = preparer(connecte, app)
    connecte.post("/logout")
    connecte.post("/register", data={"username": "intrus", "email": "intrus@test.cd",
                                     "password": "secret1234", "confirm": "secret1234"})
    connecte.post("/login", data={"email": "intrus@test.cd", "password": "secret1234"})
    assert connecte.get(f"/documents/{doc_id}/offres").status_code == 404
    assert connecte.post(f"/documents/{doc_id}/offres/OEM1").status_code == 404


def test_mode_openai_simule_et_validation(connecte, app, monkeypatch):
    """On remplace l'appel réseau par une fausse réponse : on teste notre code, pas OpenAI."""
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    fausse_reponse = {"classement": [
        {"reference": "OEM2", "score": 140, "raison": "Peu de lien."},     # score hors bornes
        {"reference": "OEM999", "score": 90, "raison": "Offre inventée"},  # référence inconnue
    ]}
    monkeypatch.setattr(ia, "appeler_llm", lambda message: (fausse_reponse, 1234))

    connecte.post(f"/documents/{doc_id}/offres")
    with app.app_context():
        analyse = Analyse.query.filter_by(type="classement").one()
        resultat = json.loads(analyse.resultat)
    assert analyse.mode == "openai" and analyse.tokens == 1234
    assert [r["reference"] for r in resultat] == ["OEM2", "OEM1"]  # OEM999 ignorée
    assert resultat[0]["score"] == 100                             # 140 ramené à 100
    assert resultat[1]["raison"] == "Offre non évaluée par l'IA."  # OEM1 oubliée : ajoutée à 0


def test_ia_indisponible_message_clair(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"

    def panne(message):
        raise ia.IAIndisponible("Le service d'IA est momentanément indisponible. Réessayez plus tard.")

    monkeypatch.setattr(ia, "appeler_llm", panne)
    page = connecte.post(f"/documents/{doc_id}/offres", follow_redirects=True).get_data(as_text=True)
    assert "momentanément indisponible" in page
    with app.app_context():
        assert Analyse.query.count() == 0  # rien n'est mis en cache en cas d'échec


def test_valider_analyse_resiste_a_une_reponse_bizarre():
    propre = ia.valider_analyse({
        "score": "abc", "points_forts": "pas une liste",
        "suggestions_cv": [{"section": "Expérience", "conseil": "Chiffrez vos résultats."}, "texte", {}],
        "question_entretien": "Parlez-moi de vous.",
    })
    assert propre["score"] == 0
    assert propre["points_forts"] == []
    assert propre["suggestions_cv"] == [{"section": "Expérience", "conseil": "Chiffrez vos résultats."}]
    assert propre["resume_profil"] == ""


def test_empreinte_change_si_le_cv_change(app):
    with app.app_context():
        offre = {"reference": "OEM1", "intitule": "Dev", "description": "Python"}
        assert ia.cle_detail("CV version 1", offre) != ia.cle_detail("CV version 2", offre)
        assert ia.cle_detail("CV version 1", offre) == ia.cle_detail("CV version 1", offre)


# ---------- Catalogue public des offres ----------

def test_catalogue_public_et_filtres(client, app):
    ecrire_offres(app)
    page = client.get("/offres").get_data(as_text=True)          # accessible sans compte
    assert "Développeur Python" in page and "Comptable" in page
    page = client.get("/offres?q=flask").get_data(as_text=True)  # recherche dans la description
    assert "Développeur Python" in page and "Comptable" not in page
    page = client.get("/offres?province=Nord-Kivu").get_data(as_text=True)
    assert "Comptable" in page and "Développeur Python" not in page


def test_fiche_offre(client, app):
    ecrire_offres(app)
    page = client.get("/offres/OEM1").get_data(as_text=True)
    assert "Description du poste" in page and "Créez un compte" in page
    assert client.get("/offres/INCONNUE").status_code == 404
