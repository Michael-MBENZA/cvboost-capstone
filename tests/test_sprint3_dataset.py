"""Tests du Sprint 3 : pipeline anti-fuite Français -> Lingala (hors ligne, sur des paires fabriquées)."""
import pytest

from ai_engine.prepare_dataset import (
    decouper, dedupliquer, empreinte, nettoyer, normaliser, preparer, verifier,
)


def fausses_paires(n=100):
    """Paires brutes au format du corpus Hugging Face."""
    return [{"French": f"Je travaille à Kinshasa depuis {i} ans.",
             "Lingala": f"Nasalaka na Kinshasa uta mibu {i}.",
             "similarity": 1.10} for i in range(n)]


def test_normaliser_et_empreinte():
    assert normaliser("  Bonjour,   KINSHASA ! ") == "bonjour kinshasa"
    assert empreinte("Ça va ?") == empreinte("ca va")          # accents, casse, ponctuation ignorés
    assert empreinte("Ça va ?") != empreinte("Ça va bien ?")
    assert len(empreinte("x")) == 64                             # SHA-256 = 64 caractères hexadécimaux


def test_nettoyage_ecarte_les_mauvaises_paires():
    brutes = [
        {"French": "Bonjour mon ami.", "Lingala": "Mbote moninga na ngai.", "similarity": 1.12},  # gardée
        {"French": "Bonjour mon ami.", "Lingala": "Mbote moninga na ngai.", "similarity": 1.01},  # mal alignée
        {"French": "Oui", "Lingala": "Iyo, ezali bongo mpenza na mokili mobimba oyo", "similarity": 1.2},  # rapport
        {"French": "Kinshasa", "Lingala": "Kinshasa", "similarity": 1.3},                         # identiques
        {"French": "Ok", "Lingala": "Ee", "similarity": 1.3},                                     # trop court
    ]
    assert [p["fr"] for p in nettoyer(brutes)] == ["Bonjour mon ami."]


def test_deduplication_stricte_sur_les_deux_langues():
    paires = nettoyer([
        {"French": "Merci beaucoup.", "Lingala": "Matondi mingi.", "similarity": 1.20},
        {"French": "merci beaucoup", "Lingala": "Natondi yo mingi.", "similarity": 1.15},  # même FR
        {"French": "Grand merci.", "Lingala": "Matondi mingi !", "similarity": 1.10},       # même LN
        {"French": "Bonne nuit.", "Lingala": "Butu malamu.", "similarity": 1.10},
    ])
    uniques, supprimes = dedupliquer(paires)
    assert supprimes == 2
    assert [p["fr"] for p in uniques] == ["Merci beaucoup.", "Bonne nuit."]  # la mieux alignée est gardée


def test_decoupage_80_10_10_sans_fuite():
    splits, stats = preparer(fausses_paires(100))
    assert stats["taille"] == {"test": 10, "validation": 10, "train": 80}
    assert stats["assertions_anti_fuite"] == "OK"


def test_decoupage_reproductible():
    a, _ = preparer(fausses_paires(), graine=7)
    b, _ = preparer(fausses_paires(), graine=7)
    assert [p["fr"] for p in a["test"]] == [p["fr"] for p in b["test"]]


def test_les_doublons_ne_peuvent_pas_fuiter():
    lignes = fausses_paires(100) + [fausses_paires(1)[0]] * 20  # 20 copies d'une même paire
    _, stats = preparer(lignes)
    assert stats["doublons_supprimes_sha256"] == 20
    assert stats["paires_finales"] == 100


def test_verifier_detecte_une_fuite_francais():
    uniques, _ = dedupliquer(nettoyer(fausses_paires(100)))
    splits = decouper(uniques)
    splits["test"].append(splits["train"][0])  # fuite provoquée volontairement
    with pytest.raises(AssertionError, match="FUITE train/test"):
        verifier(splits, total_attendu=101)


def test_verifier_detecte_une_fuite_lingala():
    uniques, _ = dedupliquer(nettoyer(fausses_paires(100)))
    splits = decouper(uniques)
    # Phrase française différente, mais même phrase lingala que dans le train
    intrus = {**splits["train"][0], "fr": "Autre phrase", "empreinte_fr": empreinte("Autre phrase")}
    splits["test"][0] = intrus
    with pytest.raises(AssertionError, match=r"FUITE train/test .*\(ln\)"):
        verifier(splits, total_attendu=100)
