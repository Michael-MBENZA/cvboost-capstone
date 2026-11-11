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


# ---------- Entretien vocal (temps réel) ----------

from app_web.models import Entretien


def session_ok(monkeypatch):
    """Remplace l'appel réseau à OpenAI qui ouvre la session vocale (échange SDP)."""
    appels = []

    def fausse_session(texte_cv, offre, sdp):
        appels.append((texte_cv, offre["reference"], sdp))
        return "v=0 reponse-sdp-openai"

    monkeypatch.setattr(ia, "creer_session_vocale", fausse_session)
    return appels


TRANSCRIPTION = [
    {"role": "recruteuse", "texte": "Bonjour, je suis Amina. Parlez-moi de vous."},
    {"role": "candidat", "texte": "Je suis développeur Python depuis 3 ans à Kinshasa."},
    {"role": "recruteuse", "texte": "Quelle API avez-vous construite récemment ?"},
    {"role": "candidat", "texte": "Une API Flask pour gérer 2 000 dossiers clients par mois."},
]


def test_page_entretien_sans_cle(connecte, app):
    doc_id = preparer(connecte, app)
    page = connecte.get(f"/documents/{doc_id}/offres/OEM1/entretien").get_data(as_text=True)
    assert "Entretien vocal" in page and "nécessite une clé OpenAI" in page


def test_session_vocale_echange_sdp(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    appels = session_ok(monkeypatch)
    page = connecte.get(f"/documents/{doc_id}/offres/OEM1/entretien").get_data(as_text=True)
    assert "Démarrer l'entretien" in page
    reponse = connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session",
                            json={"sdp": "v=0 offre-du-navigateur"}).get_json()
    assert reponse["sdp"] == "v=0 reponse-sdp-openai"    # le navigateur reçoit le SDP, jamais la clé
    assert reponse["duree_max"] == 300                    # 5 minutes
    assert "sk-test" not in str(reponse)
    assert "Python" in appels[0][0]                       # le CV a bien été extrait au déclenchement
    assert appels[0][2] == "v=0 offre-du-navigateur"
    with app.app_context():
        assert Entretien.query.count() == 1


def test_bilan_note_sur_100(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    session_ok(monkeypatch)
    eid = connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session").get_json()["entretien_id"]
    monkeypatch.setattr(ia, "appeler_llm", lambda message: ({
        "criteres": {"pertinence": {"points": 25, "justification": "a"}, "exemples": {"points": 24, "justification": "b"}, "clarte": {"points": 15, "justification": "c"}, "adequation": {"points": 14, "justification": "d"}}, "message": "Bon entretien, continuez ainsi.", "deroulement": "Entretien fluide.",
        "points_forts": ["Exemples chiffrés"], "a_ameliorer": ["Motivation"],
        "conseils": ["Préparez votre motivation.", "Chiffrez vos résultats."]}, 900))
    reponse = connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/{eid}/bilan",
                            json={"echanges": TRANSCRIPTION, "duree": 245}).get_json()
    bilan = reponse["bilan"]
    assert bilan["note"] == 78 and bilan["mention"] == "Très bien"
    assert bilan["message"].startswith("Bon entretien") and len(bilan["conseils"]) == 2
    stats = bilan["statistiques"]                       # calculées par le serveur, pas par le LLM
    assert stats["reponses_candidat"] == 2 and stats["interventions_recruteuse"] == 2
    assert stats["duree_secondes"] == 245 and stats["mots_prononces"] == 20
    with app.app_context():
        entretien = Entretien.query.one()
        assert entretien.duree_secondes == 245 and entretien.tokens == 900
        assert len(json.loads(entretien.transcription)) == 4
    # La page affiche ensuite l'entretien dans l'historique
    page = connecte.get(f"/documents/{doc_id}/offres/OEM1/entretien").get_data(as_text=True)
    assert "78 / 100" in page


def test_pas_de_bilan_si_aucune_reponse_entendue(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    session_ok(monkeypatch)
    eid = connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session").get_json()["entretien_id"]
    reponse = connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/{eid}/bilan",
                            json={"echanges": TRANSCRIPTION[:1], "duree": 30})
    assert reponse.status_code == 400 and "micro" in reponse.get_json()["erreur"]


def test_sauvegarde_en_cours_puis_bilan_meme_si_la_page_a_perdu_la_transcription(connecte, app, monkeypatch):
    """Le bug de l'ancienne version : transcription vide à la fin = pas de bilan.
    Les sauvegardes faites pendant l'entretien permettent quand même le bilan."""
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    session_ok(monkeypatch)
    base = f"/documents/{doc_id}/offres/OEM1/entretien"
    eid = connecte.post(base + "/session").get_json()["entretien_id"]

    # Pendant l'entretien : sauvegarde sans bilan
    r = connecte.post(f"{base}/{eid}/bilan", json={"echanges": TRANSCRIPTION, "duree": 120, "final": False})
    assert r.get_json() == {"sauvegarde": 4}
    with app.app_context():
        assert Entretien.query.one().bilan is None

    # La page affiche un bouton pour récupérer le bilan de cet entretien interrompu
    assert "Obtenir son bilan" in connecte.get(base).get_data(as_text=True)

    # Fin : le navigateur n'a plus rien (page rechargée), le serveur utilise la sauvegarde
    monkeypatch.setattr(ia, "appeler_llm", lambda message: ({"criteres": {"pertinence": {"points": 20}, "exemples": {"points": 20}, "clarte": {"points": 10}, "adequation": {"points": 10}}, "message": "Correct."}, 500))
    bilan = connecte.post(f"{base}/{eid}/bilan", json={"echanges": [], "duree": 0}).get_json()["bilan"]
    assert bilan["note"] == 60 and bilan["statistiques"]["duree_secondes"] == 120


def test_statistiques_entretien():
    stats = ia.statistiques_entretien(TRANSCRIPTION, 60)
    assert stats["reponses_candidat"] == 2
    assert stats["mots_par_reponse"] == 10          # 20 mots en 2 réponses
    assert stats["mots_par_minute"] == 20
    assert 0 < stats["part_de_parole_pct"] < 100


def test_limite_d_entretiens_par_heure(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    session_ok(monkeypatch)
    for _ in range(ia.LIMITE_PAR_HEURE):
        assert connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session").status_code == 200
    assert connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session").status_code == 429


def test_mentions():
    assert [ia.mention(n) for n in (90, 75, 60, 45, 10)] == [
        "Excellent", "Très bien", "Bien", "Passable", "À retravailler"]


def test_nettoyer_transcription():
    propre = ia.nettoyer_transcription([{"role": "candidat", "texte": " Oui "}, {"role": "pirate", "texte": "x"},
                                        "texte", {"role": "recruteuse", "texte": ""}])
    assert propre == [{"role": "candidat", "texte": "Oui"}]


def test_entretien_d_un_autre_utilisateur_404(connecte, app, monkeypatch):
    doc_id = preparer(connecte, app)
    connecte.post("/logout")
    connecte.post("/register", data={"username": "z", "email": "z@test.cd", "password": "secret1234", "confirm": "secret1234"})
    connecte.post("/login", data={"email": "z@test.cd", "password": "secret1234"})
    assert connecte.get(f"/documents/{doc_id}/offres/OEM1/entretien").status_code == 404
    assert connecte.post(f"/documents/{doc_id}/offres/OEM1/entretien/session").status_code == 404


def test_sdp_invalide_refuse(app):
    import pytest as _pytest
    app.config["OPENAI_API_KEY"] = "sk-test-factice"
    with app.app_context():
        with _pytest.raises(ia.EntretienRefuse):
            ia.creer_session_vocale("cv", {"intitule": "x", "entreprise": "y", "lieu": "z", "reference": "R",
                                           "description": "", "description_disponible": False}, "pas un sdp")


def test_note_est_la_somme_des_criteres_bornes(app, monkeypatch):
    """Le LLM note chaque critère ; le serveur additionne et borne (pas de note globale inventée)."""
    monkeypatch.setattr(ia, "appeler_llm", lambda message: ({"note": 99, "criteres": {
        "pertinence": {"points": 50, "justification": "Répond aux questions."},   # 50 > 30 : ramené à 30
        "exemples": {"points": "12", "justification": "Un exemple chiffré."},
        "clarte": {"points": -5},                                                 # négatif : ramené à 0
        "adequation": {"points": 15}}}, 100))
    with app.app_context():
        offre = {"intitule": "Dev", "entreprise": "ACME"}
        bilan, _ = ia.bilan_entretien(offre, TRANSCRIPTION, 60)
    assert [c["points"] for c in bilan["criteres"]] == [30, 12, 0, 15]
    assert bilan["note"] == 57                       # la note « 99 » du LLM est ignorée
    assert bilan["criteres"][0]["sur"] == 30 and bilan["criteres"][0]["justification"]
