"""Collecteur d'offres d'emploi réelles en RDC (Sprint 2), multi-sources et INCRÉMENTAL.

Sources (une par module dans data_pipeline/sources/) :
  - mediacongo.net  : scraping BeautifulSoup (robots.txt : autorisé, 10 s entre deux pages)
  - Radio Okapi     : scraping BeautifulSoup (robots.txt : autorisé, 10 s entre deux pages)
  - ReliefWeb       : API officielle (active seulement si RELIEFWEB_APPNAME est dans .env)

Incrémental : les offres déjà présentes dans data/raw/offres.csv ne sont pas retéléchargées ;
seules les nouvelles sont ajoutées. L'historique complet est conservé (le nettoyage Pandas
retire ensuite les offres expirées).

Courtoisie web : robots.txt lu pour chaque site, délai respecté, User-Agent honnête.
Si un site refuse notre robot, on ne le contourne pas : on l'ignore.

Utilisation :
    python -m data_pipeline.scraper_jobs                      # toutes les sources
    python -m data_pipeline.scraper_jobs --sources radiookapi # une seule source
"""
import argparse
import csv
import os
import re

from dotenv import load_dotenv

from .sources import mediacongo, radiookapi, reliefweb
from .sources.commun import COLONNES, Telechargeur, date_iso
# Rétro-compatibilité : les tests et l'ancien code importent ces fonctions depuis ce module
from .sources.mediacongo import parser_detail, parser_liste  # noqa: F401

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CSV_SORTIE = os.path.join(BASE_DIR, "data", "raw", "offres.csv")
ANCIEN_CSV = os.path.join(BASE_DIR, "data", "raw", "jobs_mediacongo.csv")  # format du 1er scraping

SOURCES = {"mediacongo": mediacongo, "radiookapi": radiookapi, "reliefweb": reliefweb}


def normaliser_date(texte):
    """'05.11.2026', '05/11/2026 - 08:53' ou '2026-11-05' -> '2026-11-05' (format ISO)."""
    texte = (texte or "").strip()
    iso = re.match(r"(\d{4})-(\d{2})-(\d{2})", texte)
    if iso:
        return iso.group(0)
    fr = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", texte)
    return date_iso(*fr.groups()) if fr else ""


def charger_existantes(chemin=CSV_SORTIE):
    """Offres déjà collectées (fichier actuel, ou ancien fichier mediacongo du premier scraping)."""
    for fichier, source_par_defaut in ((chemin, ""), (ANCIEN_CSV, "mediacongo")):
        if os.path.exists(fichier):
            with open(fichier, encoding="utf-8", newline="") as f:
                lignes = list(csv.DictReader(f))
            for ligne in lignes:
                ligne["source"] = ligne.get("source") or source_par_defaut
                ligne["date_publication"] = normaliser_date(ligne.get("date_publication"))
            return lignes
    return []


def fusionner(existantes, nouvelles):
    """Ajoute les nouvelles offres ; une référence déjà connue n'est jamais dupliquée."""
    par_reference = {o["reference"]: o for o in existantes}
    for offre in nouvelles:
        offre["date_publication"] = normaliser_date(offre.get("date_publication"))
        offre["date_limite"] = normaliser_date(offre.get("date_limite"))
        par_reference.setdefault(offre["reference"], offre)
    return sorted(par_reference.values(), key=lambda o: o.get("date_publication", ""), reverse=True)


def enregistrer_csv(offres, chemin=CSV_SORTIE):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLONNES, extrasaction="ignore")
        writer.writeheader()
        for offre in offres:
            writer.writerow({col: offre.get(col, "") for col in COLONNES})


def collecter_tout(noms_sources, afficher=print):
    existantes = charger_existantes()
    connues = {o["reference"] for o in existantes}
    afficher(f"{len(existantes)} offres déjà connues")
    toutes_nouvelles = []
    for nom in noms_sources:
        module = SOURCES[nom]
        afficher(f"\n== {nom} ==")
        try:
            # ReliefWeb est une API : pas de robots.txt de site à lire
            telecharger = None if module is reliefweb else Telechargeur(module.SITE, afficher=afficher)
            if telecharger:
                afficher(f"  robots.txt lu : {telecharger.delai} s entre deux pages")
            nouvelles = module.collecter(telecharger, connues, afficher=afficher)
        except Exception as erreur:  # une source en panne ne doit pas bloquer les autres
            afficher(f"  source ignorée suite à une erreur : {erreur}")
            nouvelles = []
        toutes_nouvelles.extend(nouvelles)
        connues.update(o["reference"] for o in nouvelles)
    return fusionner(existantes, toutes_nouvelles), len(toutes_nouvelles)


if __name__ == "__main__":
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    parser = argparse.ArgumentParser(description="Collecte d'offres d'emploi en RDC (multi-sources)")
    parser.add_argument("--sources", nargs="+", choices=list(SOURCES), default=list(SOURCES))
    args = parser.parse_args()

    offres, nb_nouvelles = collecter_tout(args.sources)
    enregistrer_csv(offres)
    par_source = {}
    for o in offres:
        par_source[o["source"]] = par_source.get(o["source"], 0) + 1
    print(f"\n{nb_nouvelles} nouvelles offres. Total : {len(offres)} offres {par_source}")
    print(f"Fichier : {os.path.relpath(CSV_SORTIE, BASE_DIR)}")
