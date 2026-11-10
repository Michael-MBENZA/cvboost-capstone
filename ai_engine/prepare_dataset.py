"""Pipeline anti-fuite (Sprint 3) : corpus parallèle Français -> Lingala pour le fine-tuning de NLLB.

Corpus Hugging Face : michsethowusu/french-lingala_sentence-pairs (691 895 paires alignées
automatiquement, chacune avec un score de similarité).

Étapes :
  1. chargement depuis Hugging Face (mis en cache localement après le 1er téléchargement : 55 Mo)
  2. nettoyage : alignements douteux (similarité < 1.08), phrases trop courtes ou trop longues,
     rapport de longueur incohérent, « traduction » identique à l'original
  3. empreintes SHA-256 de la phrase française ET de la phrase lingala normalisées
     -> déduplication stricte : une même phrase (d'un côté ou de l'autre) n'existe qu'une fois
  4. découpage étanche 80 % train / 10 % validation / 10 % test (graine fixe = reproductible)
  5. assertions automatiques : aucune empreinte partagée entre deux sous-ensembles, etc.
  6. sauvegarde : ai_engine/data/{train,validation,test}.jsonl + stats.json

Utilisation :
    python -m ai_engine.prepare_dataset
"""
import argparse
import hashlib
import json
import os
import random
import re
import unicodedata

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOSSIER_DATA = os.path.join(BASE_DIR, "ai_engine", "data")

NOM_CORPUS = "michsethowusu/french-lingala_sentence-pairs"
GRAINE = 42
PROPORTIONS = {"train": 0.8, "validation": 0.1, "test": 0.1}
SIMILARITE_MIN = 1.08   # score d'alignement du corpus : en dessous, les paires sont souvent fausses
LONGUEUR_MIN, LONGUEUR_MAX = 3, 300  # caractères
RAPPORT_MAX = 3         # une traduction n'est pas 3 fois plus longue (ou plus courte) que l'original


# ---------- 1. Chargement ----------

def charger():
    """Renvoie la liste des paires brutes {French, Lingala, similarity}."""
    from datasets import load_dataset  # import ici : les tests n'en ont pas besoin

    corpus = load_dataset(NOM_CORPUS, split="train")
    return corpus.to_list()


# ---------- 2. Nettoyage ----------

def espaces(texte):
    return " ".join((texte or "").split())


def paire_valide(fr, ln, similarite):
    return (
        similarite is not None and similarite >= SIMILARITE_MIN
        and LONGUEUR_MIN <= len(fr) <= LONGUEUR_MAX
        and LONGUEUR_MIN <= len(ln) <= LONGUEUR_MAX
        and 1 / RAPPORT_MAX <= len(fr) / len(ln) <= RAPPORT_MAX
        and fr.lower() != ln.lower()
    )


def nettoyer(lignes):
    paires = []
    for l in lignes:
        fr, ln = espaces(l.get("French")), espaces(l.get("Lingala"))
        if fr and ln and paire_valide(fr, ln, l.get("similarity")):
            paires.append({"fr": fr, "ln": ln, "similarite": round(float(l["similarity"]), 4)})
    return paires


# ---------- 3. Empreintes SHA-256 et déduplication ----------

def normaliser(texte):
    """Forme canonique : minuscules, sans accents, sans ponctuation, espaces uniques.
    « Bonjour, Kinshasa ! » et « bonjour kinshasa » donnent la même empreinte."""
    texte = unicodedata.normalize("NFKD", texte.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = re.sub(r"[^\w\s]", " ", texte)
    return " ".join(texte.split())


def empreinte(texte):
    return hashlib.sha256(normaliser(texte).encode("utf-8")).hexdigest()


def dedupliquer(paires):
    """Déduplication stricte sur les DEUX langues.

    On garde d'abord les paires les mieux alignées (similarité la plus haute). Une paire est
    écartée si sa phrase française OU sa phrase lingala a déjà été vue : sinon, la même phrase
    pourrait se retrouver dans le train et dans le test avec une autre traduction (fuite).
    """
    vues_fr, vues_ln, uniques = set(), set(), []
    for p in sorted(paires, key=lambda p: p["similarite"], reverse=True):
        cle_fr, cle_ln = empreinte(p["fr"]), empreinte(p["ln"])
        if cle_fr in vues_fr or cle_ln in vues_ln:
            continue
        vues_fr.add(cle_fr)
        vues_ln.add(cle_ln)
        uniques.append({**p, "empreinte_fr": cle_fr, "empreinte_ln": cle_ln})
    return uniques, len(paires) - len(uniques)


# ---------- 4. Découpage étanche ----------

def decouper(paires, graine=GRAINE):
    melange = list(paires)
    random.Random(graine).shuffle(melange)
    n = len(melange)
    n_val = round(n * PROPORTIONS["validation"])
    n_test = round(n * PROPORTIONS["test"])
    return {
        "test": melange[:n_test],
        "validation": melange[n_test:n_test + n_val],
        "train": melange[n_test + n_val:],
    }


# ---------- 5. Assertions anti-fuite ----------

def verifier(splits, total_attendu):
    """Lève une AssertionError si une seule règle anti-fuite est violée."""
    for langue in ("fr", "ln"):
        cle = f"empreinte_{langue}"
        ensembles = {nom: [p[cle] for p in exs] for nom, exs in splits.items()}
        for nom, liste in ensembles.items():
            assert liste, f"le sous-ensemble {nom} est vide"
            assert len(liste) == len(set(liste)), f"doublon ({langue}) à l'intérieur de {nom}"
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test")):
            communes = set(ensembles[a]) & set(ensembles[b])
            assert not communes, f"FUITE {a}/{b} : {len(communes)} phrases ({langue}) communes"

    total = sum(len(exs) for exs in splits.values())
    assert total == total_attendu, f"{total} paires réparties au lieu de {total_attendu}"
    for nom, part in PROPORTIONS.items():
        reel = len(splits[nom]) / total
        assert abs(reel - part) <= 0.01, f"{nom} : {reel:.1%} au lieu de {part:.0%}"


# ---------- Pipeline complet ----------

def preparer(lignes_brutes, graine=GRAINE):
    """Brut -> (splits vérifiés, statistiques). Aucun accès réseau ni disque."""
    paires = nettoyer(lignes_brutes)
    uniques, nb_doublons = dedupliquer(paires)
    splits = decouper(uniques, graine)
    verifier(splits, total_attendu=len(uniques))
    stats = {
        "corpus": NOM_CORPUS,
        "graine": graine,
        "paires_brutes": len(lignes_brutes),
        "paires_apres_nettoyage": len(paires),
        "doublons_supprimes_sha256": nb_doublons,
        "paires_finales": len(uniques),
        "taille": {nom: len(exs) for nom, exs in splits.items()},
        "assertions_anti_fuite": "OK",
    }
    return splits, stats


def sauvegarder(splits, stats, dossier=DOSSIER_DATA):
    """Écrit les sous-ensembles au format Hugging Face (DatasetDict) puis en JSONL."""
    from datasets import Dataset, DatasetDict

    os.makedirs(dossier, exist_ok=True)
    dataset = DatasetDict({nom: Dataset.from_list(exs) for nom, exs in splits.items()})
    for nom, ds in dataset.items():
        ds.to_json(os.path.join(dossier, f"{nom}.jsonl"), force_ascii=False)
    with open(os.path.join(dossier, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return dataset


if __name__ == "__main__":
    argparse.ArgumentParser(description="Corpus Français-Lingala sans fuite").parse_args()
    brutes = charger()
    splits, stats = preparer(brutes)
    dataset = sauvegarder(splits, stats)

    print(dataset)
    print(f"\nPaires brutes            : {stats['paires_brutes']}")
    print(f"Après nettoyage          : {stats['paires_apres_nettoyage']}")
    print(f"Doublons supprimés (SHA) : {stats['doublons_supprimes_sha256']}")
    print(f"Taille des sous-ensembles: {stats['taille']}")
    print("Assertions anti-fuite    : OK (aucune phrase commune entre train, validation et test)")
    exemple = splits["test"][0]
    print(f"Exemple (test)           : {exemple['fr']}  =>  {exemple['ln']}")
