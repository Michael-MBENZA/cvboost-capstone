"""Tests du Sprint 2 : audit des logs d'upload, base analytique."""
import os
import sqlite3

from data_pipeline.analytics import construire
from data_pipeline.logs_parser import auditer, parser_ligne

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_EXEMPLE = os.path.join(BASE_DIR, "data", "samples", "uploads_sample.log")


# ---------- Audit des logs ----------

def test_parser_ligne_extrait_les_metadonnees():
    ligne = "2026-11-08 09:56:41 | user=1 | ip=127.0.0.1 | statut=ACCEPTE | motif=OK | taille=2048 | fichier=mon cv.pdf"
    evt = parser_ligne(ligne)
    assert evt["user"] == "1"
    assert evt["ip"] == "127.0.0.1"
    assert evt["statut"] == "ACCEPTE"
    assert evt["taille"] == 2048
    assert evt["fichier"] == "mon cv.pdf"  # les espaces du nom sont conservés
    assert evt["extension"] == "pdf"


def test_parser_ligne_mal_formee_renvoie_none():
    assert parser_ligne("ligne corrompue sans le bon format") is None


def test_parser_detecte_double_extension():
    ligne = "2026-11-08 09:56:41 | user=1 | ip=1.1.1.1 | statut=REFUSE | motif=EXTENSION_INTERDITE | taille=10 | fichier=cv.pdf.exe"
    evt = parser_ligne(ligne)
    assert evt["extension"] == "exe"
    assert evt["double_extension"] is True


def test_audit_du_journal_exemple():
    evenements, rapport = auditer(LOG_EXEMPLE)
    resume = rapport["resume"]
    assert resume["total_tentatives"] == 13
    assert resume["acceptees"] == 6
    assert resume["refusees"] == 7
    assert resume["lignes_invalides"] == 1

    types = {a["type"] for a in rapport["alertes"]}
    assert {"FAUX_FICHIER", "EXTENSION_DANGEREUSE", "DOUBLE_EXTENSION", "RAFALE_DE_REFUS"} <= types

    # Toutes les alertes viennent du même fraudeur (user 4)
    assert {a["user"] for a in rapport["alertes"]} == {"4"}


# ---------- Base analytique SQL ----------

def test_analytics_kpi_group_by_having(tmp_path):
    analytics = str(tmp_path / "analytics.db")
    cvboost = str(tmp_path / "cvboost.db")

    # Fausse base cvboost.db : user 1 a 2 CV, user 2 en a 1
    src = sqlite3.connect(cvboost)
    src.execute("CREATE TABLE documents (id INTEGER, user_id INTEGER, file_type TEXT, size_bytes INTEGER, uploaded_at TEXT)")
    src.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", [
        (1, 1, "pdf", 2048, "2026-11-01"), (2, 1, "docx", 4096, "2026-11-02"), (3, 2, "pdf", 1024, "2026-11-03"),
    ])
    src.commit()
    src.close()

    nb_evt, nb_docs, resultats, kpis = construire(LOG_EXEMPLE, analytics, cvboost)
    assert nb_evt == 13 and nb_docs == 3

    statuts = dict(resultats["tentatives_par_statut"][2])
    assert statuts == {"REFUSE": 7, "ACCEPTE": 6}

    # HAVING : seule l'IP du fraudeur a 3 refus ou plus
    ip_suspectes = resultats["ip_suspectes"][2]
    assert ip_suspectes == [("102.68.33.7", "4", 4)]

    # HAVING : seul l'utilisateur 1 a plusieurs CV
    assert resultats["utilisateurs_multi_cv"][2] == [(1, 2)]

    assert kpis["tentatives_total"] == 13
    assert kpis["taux_acceptation_pct"] == round(100 * 6 / 13, 1)

    # Le snapshot est bien enregistré dans la base
    conn = sqlite3.connect(analytics)
    assert conn.execute("SELECT COUNT(*) FROM kpi_snapshots").fetchone()[0] == 4
    conn.close()


# ---------- Scraping (hors ligne, sur des pages HTML d'exemple) ----------

FIXTURES = os.path.join(BASE_DIR, "tests", "fixtures")


def lire_fixture(nom):
    with open(os.path.join(FIXTURES, nom), encoding="utf-8") as f:
        return f.read()


def test_scraper_parse_la_liste_des_offres():
    from data_pipeline.scraper_jobs import parser_liste
    offres = parser_liste(lire_fixture("liste_offres.html"))
    assert len(offres) == 2
    premiere = offres[0]
    assert premiere["reference"] == "OEM1001"
    assert premiere["intitule"] == "Développeur Python"
    assert premiere["entreprise"] == "ACME RDC"
    assert premiere["lieu"] == "Kinshasa"
    assert premiere["date_publication"] == "05.11.2026"
    assert premiere["url"] == "https://www.mediacongo.net/emploi-societe-1001_acme_developpeur_python.html"


def test_scraper_parse_le_detail_et_le_salaire():
    from data_pipeline.scraper_jobs import parser_detail
    detail = parser_detail(lire_fixture("detail_offre.html"))
    assert "Flask et SQL" in detail["description"]
    assert "<li>" not in detail["description"]  # le HTML a été retiré
    assert detail["date_limite"] == "2026-11-30"
    assert "1 500 USD" in detail["salaire"]


# ---------- Nettoyage Pandas ----------

def test_conversion_des_salaires():
    from data_pipeline.pandas_cleaning import convertir_salaire
    assert convertir_salaire("Salaire : 1 500 USD par mois") == 1500.0
    assert convertir_salaire("Rémunération : 1.200 $") == 1200.0
    assert convertir_salaire("2 850 000 FC") == 1000.0
    assert convertir_salaire("Salaire annuel : 24 000 USD") == 2000.0
    assert convertir_salaire('{"currency": "USD", "value": {"value": 36000, "unitText": "YEAR"}}') == 3000.0
    assert convertir_salaire("") is None
    assert convertir_salaire("à négocier") is None


def test_province_et_ville():
    from data_pipeline.pandas_cleaning import province_de, ville_de
    assert province_de("Beni (Nord-Kivu)") == "Nord-Kivu"
    assert province_de("Lubumbashi") == "Haut-Katanga"
    assert province_de("RD Congo") == "National"
    assert province_de("") == "Non précisé"
    assert ville_de("Tshikapa / Mweka") == "Tshikapa"
    assert ville_de("Nord-Kivu") == "Non précisé"   # une province n'est pas une ville
    assert ville_de("Kinshasa") == "Kinshasa"       # sauf Kinshasa (ville-province)


def test_nettoyage_complet():
    import pandas as pd
    from data_pipeline.pandas_cleaning import nettoyer
    longue = "Missions : développer des API en Python et SQL, travailler en anglais avec l'équipe. " * 3
    brut = pd.DataFrame([
        {"reference": "OEM1", "intitule": "Développeur  Python ", "entreprise": "ACME", "lieu": "Goma",
         "date_publication": "05.11.2026", "date_limite": "2026-11-30", "salaire": "Salaire : 1 500 USD",
         "description": longue, "url": "u1"},
        {"reference": "OEM1", "intitule": "Développeur Python", "entreprise": "ACME", "lieu": "Goma",  # même référence
         "date_publication": "05.11.2026", "date_limite": "2026-11-30", "salaire": "", "description": longue, "url": "u1"},
        {"reference": "OEM2", "intitule": "Comptable", "entreprise": "", "lieu": "RD Congo",
         "date_publication": "01.11.2026", "date_limite": "2026-11-10", "salaire": "",
         "description": "Comptable", "url": "u2"},  # description = titre -> indisponible
    ])
    propre, rapport = nettoyer(brut)

    assert rapport["doublons_supprimes"] == 1
    assert len(propre) == 2
    dev = propre[propre["reference"] == "OEM1"].iloc[0]
    assert dev["intitule"] == "Développeur Python"           # espaces nettoyés
    assert dev["province"] == "Nord-Kivu"
    assert dev["salaire_usd_mensuel"] == 1500.0
    assert dev["duree_candidature_jours"] == 25
    assert dev["comp_python"] == 1 and dev["comp_sql"] == 1 and dev["comp_anglais"] == 1
    assert dev["comp_comptabilite"] == 0

    compta = propre[propre["reference"] == "OEM2"].iloc[0]
    assert compta["entreprise"] == "Non précisé"              # valeur manquante remplie
    assert compta["province"] == "National"
    assert bool(compta["description_disponible"]) is False
    assert bool(compta["salaire_indique"]) is False
    assert compta["comp_comptabilite"] == 1                   # trouvée dans le titre
