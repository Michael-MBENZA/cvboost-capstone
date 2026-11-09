"""Tests du Sprint 3 : jeu de données NLP sans fuite (hors ligne, sur des données fabriquées)."""
import pytest

from ai_engine.prepare_dataset import (
    decouper, dedupliquer, empreinte, nettoyer, normaliser, preparer, verifier,
)

DESCRIPTION = "Nous recherchons une personne motivée pour rejoindre notre équipe. " * 3


def fausses_offres(n_par_domaine=30, domaines="DJN"):
    """Fabrique des offres brutes au format du corpus France Travail."""
    lignes = []
    for code in domaines:
        for i in range(n_par_domaine):
            lignes.append({
                "id": f"{code}{i}", "intitule": f"Poste {code} numéro {i}",
                "description": f"{DESCRIPTION} Référence {code}-{i}.",
                "romeCode": f"{code}1101", "romeLibelle": "Métier", "typeContrat": "CDI",
            })
    return lignes


def test_normaliser_ignore_casse_accents_et_espaces():
    assert normaliser("  Équipe   SANTÉ ") == "equipe sante"


def test_empreinte_identique_pour_textes_equivalents():
    assert empreinte("Infirmier", "Soins  à l'hôpital") == empreinte("INFIRMIER", "soins a l'hopital")
    assert empreinte("Infirmier", "Soins") != empreinte("Infirmier", "Soins de nuit")
    assert len(empreinte("a", "b")) == 64  # SHA-256 = 64 caractères hexadécimaux


def test_nettoyer_retire_descriptions_courtes_et_codes_absents():
    lignes = [
        {"id": 1, "intitule": "A", "description": "trop court", "romeCode": "D1101"},
        {"id": 2, "intitule": "B", "description": DESCRIPTION, "romeCode": None},
        {"id": 3, "intitule": "C", "description": DESCRIPTION, "romeCode": "J1506"},
    ]
    propres = nettoyer(lignes)
    assert [p["id"] for p in propres] == ["3"]
    assert propres[0]["domaine_code"] == "J"


def test_dedupliquer_supprime_les_copies():
    lignes = fausses_offres(5, "D")
    copie = dict(lignes[0], id="copie", intitule=lignes[0]["intitule"].upper())  # même texte, autre casse
    uniques, supprimes = dedupliquer(nettoyer(lignes + [copie]))
    assert supprimes == 1
    assert len(uniques) == 5


def test_decoupage_80_10_10_sans_fuite():
    splits, stats = preparer(fausses_offres(30, "DJN"))
    assert stats["taille"] == {"train": 72, "validation": 9, "test": 9}
    assert stats["assertions_anti_fuite"] == "OK"
    # Chaque domaine est présent dans les 3 sous-ensembles (stratification)
    for nom in ("train", "validation", "test"):
        assert {ex["domaine_code"] for ex in splits[nom]} == {"D", "J", "N"}


def test_decoupage_reproductible_avec_la_meme_graine():
    a, _ = preparer(fausses_offres(), graine=7)
    b, _ = preparer(fausses_offres(), graine=7)
    assert [ex["id"] for ex in a["test"]] == [ex["id"] for ex in b["test"]]


def test_les_doublons_ne_peuvent_pas_fuiter():
    # 10 copies exactes d'une même offre : après déduplication, elle n'existe qu'une fois
    lignes = fausses_offres(30, "DJN") + [fausses_offres(1, "D")[0]] * 10
    splits, stats = preparer(lignes)
    assert stats["doublons_supprimes_sha256"] == 10


def test_verifier_detecte_une_fuite():
    exemples = nettoyer(fausses_offres(30, "DJN"))
    splits = decouper(exemples)
    splits["test"].append(splits["train"][0])  # on provoque volontairement une fuite
    with pytest.raises(AssertionError, match="FUITE train/test"):
        verifier(splits, total_attendu=len(exemples) + 1)


def test_domaine_rare_retire():
    lignes = fausses_offres(30, "DJ") + fausses_offres(3, "L")  # seulement 3 offres "Spectacle"
    _, stats = preparer(lignes)
    assert stats["domaines_trop_rares_retires"] == ["L"]
