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
