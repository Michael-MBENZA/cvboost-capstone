"""Tests du Sprint 2 : audit des logs d'upload."""
import os

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
