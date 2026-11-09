"""Préparation d'un jeu de données NLP SANS FUITE (Sprint 3).

Corpus : offres d'emploi réelles de France Travail, publiées sur Hugging Face
         (France-Travail-ML/Offres-Emplois-JAN-26-JUIN-26-RAW).
Tâche visée : à partir du texte d'une offre (titre + description), prédire son
         DOMAINE PROFESSIONNEL (grand domaine du code ROME : santé, commerce, industrie...).
         Utile plus tard pour classer automatiquement les offres scrapées.

Étapes :
  1. téléchargement en streaming (sans tout télécharger) + cache local
  2. nettoyage (textes trop courts, codes ROME manquants)
  3. empreinte SHA-256 de chaque texte normalisé -> déduplication stricte
  4. découpage étanche 80 % train / 10 % validation / 10 % test, stratifié par domaine
  5. assertions automatiques : aucune empreinte partagée entre deux sous-ensembles, etc.
  6. sauvegarde : ai_engine/data/{train,validation,test}.jsonl + stats.json

Utilisation :
    python -m ai_engine.prepare_dataset            # 5 000 offres (1er lancement : quelques minutes)
    python -m ai_engine.prepare_dataset --n 10000
"""
import argparse
import hashlib
import json
import os
import random
import re
import unicodedata
from collections import Counter, defaultdict

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOSSIER_DATA = os.path.join(BASE_DIR, "ai_engine", "data")
CACHE_BRUT = os.path.join(DOSSIER_DATA, "cache_brut.jsonl")

NOM_CORPUS = "France-Travail-ML/Offres-Emplois-JAN-26-JUIN-26-RAW"
# On ne garde QUE ces colonnes : le corpus contient aussi des contacts (e-mails, téléphones)
# qu'on ne veut pas manipuler (données personnelles).
COLONNES = ["id", "intitule", "description", "romeCode", "romeLibelle", "typeContrat"]

GRAINE = 42  # même graine = même découpage à chaque exécution (reproductibilité)
PROPORTIONS = {"train": 0.8, "validation": 0.1, "test": 0.1}
LONGUEUR_MIN = 100  # caractères : en dessous, la description est inexploitable
MIN_PAR_DOMAINE = 10  # un domaine trop rare ne peut pas être réparti sur 3 sous-ensembles

# Première lettre du code ROME = grand domaine professionnel
DOMAINES_ROME = {
    "A": "Agriculture et pêche, espaces naturels et verts, soins aux animaux",
    "B": "Arts et façonnage d'ouvrages d'art",
    "C": "Banque, assurance, immobilier",
    "D": "Commerce, vente et grande distribution",
    "E": "Communication, média et multimédia",
    "F": "Construction, bâtiment et travaux publics",
    "G": "Hôtellerie-restauration, tourisme, loisirs et animation",
    "H": "Industrie",
    "I": "Installation et maintenance",
    "J": "Santé",
    "K": "Services à la personne et à la collectivité",
    "L": "Spectacle",
    "M": "Support à l'entreprise",
    "N": "Transport et logistique",
}


# ---------- 1. Téléchargement ----------

def telecharger(n):
    """Renvoie n offres brutes. Utilise le cache local s'il contient assez de lignes."""
    if os.path.exists(CACHE_BRUT):
        with open(CACHE_BRUT, encoding="utf-8") as f:
            lignes = [json.loads(l) for l in f]
        if len(lignes) >= n:
            print(f"Cache local utilisé : {CACHE_BRUT} ({len(lignes)} offres)")
            return lignes[:n]

    from datasets import load_dataset  # import ici : inutile si le cache suffit

    print(f"Téléchargement en streaming de {n} offres depuis Hugging Face ({NOM_CORPUS})...")
    flux = load_dataset(NOM_CORPUS, split="train", streaming=True).select_columns(COLONNES)
    lignes = list(flux.take(n))  # streaming : on ne lit que le début du corpus (366 Mo au total)

    os.makedirs(DOSSIER_DATA, exist_ok=True)
    with open(CACHE_BRUT, "w", encoding="utf-8") as f:
        for ligne in lignes:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    return lignes


# ---------- 2-3. Nettoyage, empreinte, déduplication ----------

def normaliser(texte):
    """Forme canonique pour comparer deux textes : minuscules, sans accents, espaces uniques."""
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texte).strip().lower()


def empreinte(intitule, description):
    """SHA-256 du titre + description normalisés : deux offres identiques ont la même empreinte."""
    contenu = normaliser(intitule) + " || " + normaliser(description)
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def nettoyer(lignes):
    """Garde les offres exploitables et construit les champs utiles."""
    propres = []
    for l in lignes:
        description = re.sub(r"\s+", " ", l.get("description") or "").strip()
        intitule = re.sub(r"\s+", " ", l.get("intitule") or "").strip()
        code = (l.get("romeCode") or "").strip()
        if len(description) < LONGUEUR_MIN or not code or code[0] not in DOMAINES_ROME:
            continue
        propres.append({
            "id": str(l.get("id")),
            "intitule": intitule,
            "description": description,
            "texte": f"{intitule}. {description}",
            "rome_code": code,
            "metier": l.get("romeLibelle") or "",
            "domaine_code": code[0],
            "domaine": DOMAINES_ROME[code[0]],
            "type_contrat": l.get("typeContrat") or "",
            "empreinte": empreinte(intitule, description),
        })
    return propres


def dedupliquer(exemples):
    """Garde la première occurrence de chaque empreinte. Renvoie (uniques, nb_supprimés)."""
    vues, uniques = set(), []
    for ex in exemples:
        if ex["empreinte"] not in vues:
            vues.add(ex["empreinte"])
            uniques.append(ex)
    return uniques, len(exemples) - len(uniques)


def retirer_domaines_rares(exemples, minimum=MIN_PAR_DOMAINE):
    compte = Counter(ex["domaine_code"] for ex in exemples)
    gardes = [ex for ex in exemples if compte[ex["domaine_code"]] >= minimum]
    retires = sorted(code for code, n in compte.items() if n < minimum)
    return gardes, retires


# ---------- 4. Découpage étanche ----------

def decouper(exemples, graine=GRAINE):
    """Découpage 80/10/10 stratifié : chaque domaine est réparti dans les mêmes proportions."""
    par_domaine = defaultdict(list)
    for ex in exemples:
        par_domaine[ex["domaine_code"]].append(ex)

    hasard = random.Random(graine)
    splits = {nom: [] for nom in PROPORTIONS}
    for code in sorted(par_domaine):
        groupe = par_domaine[code]
        hasard.shuffle(groupe)
        n = len(groupe)
        n_val = max(1, round(n * PROPORTIONS["validation"]))
        n_test = max(1, round(n * PROPORTIONS["test"]))
        splits["test"].extend(groupe[:n_test])
        splits["validation"].extend(groupe[n_test:n_test + n_val])
        splits["train"].extend(groupe[n_test + n_val:])
    for nom in splits:
        hasard.shuffle(splits[nom])
    return splits


# ---------- 5. Assertions anti-fuite ----------

def verifier(splits, total_attendu):
    """Lève une AssertionError si une seule règle anti-fuite est violée."""
    empreintes = {nom: [ex["empreinte"] for ex in exs] for nom, exs in splits.items()}

    # a) Aucun doublon à l'intérieur d'un sous-ensemble
    for nom, liste in empreintes.items():
        assert len(liste) == len(set(liste)), f"Doublon à l'intérieur de {nom}"

    # b) Aucune empreinte partagée entre deux sous-ensembles (le cœur de l'anti-fuite)
    tr, va, te = (set(empreintes[n]) for n in ("train", "validation", "test"))
    assert not (tr & va), f"FUITE train/validation : {len(tr & va)} textes communs"
    assert not (tr & te), f"FUITE train/test : {len(tr & te)} textes communs"
    assert not (va & te), f"FUITE validation/test : {len(va & te)} textes communs"

    # c) Rien de perdu, rien d'inventé
    total = sum(len(exs) for exs in splits.values())
    assert total == total_attendu, f"{total} exemples répartis au lieu de {total_attendu}"

    # d) Proportions respectées (tolérance de 2 points)
    for nom, part in PROPORTIONS.items():
        reel = len(splits[nom]) / total
        assert abs(reel - part) <= 0.02, f"{nom} : {reel:.1%} au lieu de {part:.0%}"

    # e) Chaque domaine du test et de la validation existe aussi dans le train
    domaines_train = {ex["domaine_code"] for ex in splits["train"]}
    for nom in ("validation", "test"):
        inconnus = {ex["domaine_code"] for ex in splits[nom]} - domaines_train
        assert not inconnus, f"Domaines de {nom} absents du train : {inconnus}"


# ---------- 6. Pipeline complet ----------

def preparer(lignes_brutes, graine=GRAINE):
    """Brut -> (splits vérifiés, statistiques). Ne fait aucun accès réseau ni disque."""
    propres = nettoyer(lignes_brutes)
    uniques, nb_doublons = dedupliquer(propres)
    gardes, domaines_retires = retirer_domaines_rares(uniques)
    splits = decouper(gardes, graine)
    verifier(splits, total_attendu=len(gardes))

    stats = {
        "corpus": NOM_CORPUS,
        "graine": graine,
        "offres_brutes": len(lignes_brutes),
        "offres_exploitables": len(propres),
        "doublons_supprimes_sha256": nb_doublons,
        "domaines_trop_rares_retires": domaines_retires,
        "offres_finales": len(gardes),
        "taille": {nom: len(exs) for nom, exs in splits.items()},
        "repartition_domaines": {
            nom: dict(sorted(Counter(ex["domaine_code"] for ex in exs).items()))
            for nom, exs in splits.items()
        },
        "assertions_anti_fuite": "OK",
    }
    return splits, stats


def sauvegarder(splits, stats, dossier=DOSSIER_DATA):
    """Écrit les 3 sous-ensembles au format Hugging Face (DatasetDict) puis en JSONL."""
    from datasets import Dataset, DatasetDict

    os.makedirs(dossier, exist_ok=True)
    dataset = DatasetDict({nom: Dataset.from_list(exs) for nom, exs in splits.items()})
    for nom, ds in dataset.items():
        ds.to_json(os.path.join(dossier, f"{nom}.jsonl"), force_ascii=False)
    with open(os.path.join(dossier, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return dataset


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dataset d'offres d'emploi sans fuite")
    parser.add_argument("--n", type=int, default=5000, help="nombre d'offres à télécharger (défaut : 5000)")
    args = parser.parse_args()

    brutes = telecharger(args.n)
    splits, stats = preparer(brutes)
    dataset = sauvegarder(splits, stats)

    print(dataset)
    print(f"\nOffres brutes            : {stats['offres_brutes']}")
    print(f"Offres exploitables      : {stats['offres_exploitables']}")
    print(f"Doublons supprimés (SHA) : {stats['doublons_supprimes_sha256']}")
    print(f"Domaines rares retirés   : {stats['domaines_trop_rares_retires']}")
    print(f"Taille des sous-ensembles: {stats['taille']}")
    print("Assertions anti-fuite    : OK (aucun texte commun entre train, validation et test)")
    print(f"Fichiers                 : {os.path.relpath(DOSSIER_DATA, BASE_DIR)}")
