"""Collecteur d'offres d'emploi réelles (Sprint 2) : mediacongo.net.

Récupère pour chaque offre : intitulé, entreprise, lieu, date, description, salaire (si indiqué),
puis enregistre le tout dans data/raw/jobs_mediacongo.csv (données BRUTES, nettoyées ensuite
par pandas_cleaning.py).

Courtoisie web :
  - on lit robots.txt et on respecte son "Crawl-delay" (10 s chez mediacongo) ;
  - on s'identifie avec un User-Agent explicite ;
  - on limite le nombre d'offres téléchargées.

Utilisation :
    python -m data_pipeline.scraper_jobs              # 15 offres (environ 3 minutes)
    python -m data_pipeline.scraper_jobs --max 40     # plus d'offres (plus long)
"""
import argparse
import csv
import json
import os
import re
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CSV_SORTIE = os.path.join(BASE_DIR, "data", "raw", "jobs_mediacongo.csv")

SITE = "https://www.mediacongo.net/"
PAGE_LISTE = SITE + "emplois-search--tri-offres_recentes-page-{numero}.html"
USER_AGENT = "CVBoost-StudentBot/1.0 (projet pedagogique Kadea Academy; scraping respectueux)"
DELAI_PAR_DEFAUT = 10  # secondes, utilisé si robots.txt n'indique rien
COLONNES = ["reference", "intitule", "entreprise", "lieu", "date_publication",
            "date_limite", "salaire", "description", "url"]

# Mots qui annoncent un salaire, suivis (dans les 80 caractères) d'un nombre
MOTIF_SALAIRE = re.compile(
    r"(?:salaire|r[ée]mun[ée]ration|salary|package)[^.\n]{0,80}?\d[\d\s.,]*\s*(?:USD|\$|CDF|FC|EUR|€)?",
    re.IGNORECASE,
)


# ---------- Courtoisie : robots.txt ----------

def lire_robots(session):
    """Télécharge robots.txt et renvoie l'objet qui sait si une URL est autorisée."""
    robots = RobotFileParser()
    reponse = session.get(urljoin(SITE, "robots.txt"), timeout=20)
    robots.parse(reponse.text.splitlines())
    return robots


# ---------- Analyse HTML (sans réseau : testable hors ligne) ----------

def parser_liste(html):
    """Page de liste -> liste de dicts {reference, intitule, entreprise, lieu, date_publication, url}."""
    soup = BeautifulSoup(html, "html.parser")
    offres = []
    # Chaque offre est une ligne <tr> qui contient la référence <strong class="format_id_emploi">
    for ref in soup.select("strong.format_id_emploi"):
        ligne = ref.find_parent("tr")
        cellules = ligne.find_all("td") if ligne else []
        if len(cellules) < 5:
            continue
        lien = cellules[1].find("a")
        titre = cellules[1].find("strong")
        offres.append({
            "reference": ref.get_text(strip=True),
            "intitule": titre.get_text(strip=True) if titre else "",
            "entreprise": cellules[2].get_text(strip=True),
            "lieu": cellules[3].get_text(strip=True),
            "date_publication": cellules[4].get_text(strip=True),
            "url": urljoin(SITE, lien["href"]) if lien else "",
        })
    return offres


def parser_detail(html):
    """Page d'une offre -> {description, salaire, date_limite}.

    mediacongo publie les données de l'offre au format JSON-LD (schema.org/JobPosting)
    dans une balise <script>, plus fiable que la mise en page HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    resultat = {"description": "", "salaire": "", "date_limite": ""}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            donnees = json.loads(script.string or "", strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(donnees, dict) and donnees.get("@type") == "JobPosting":
            # La description est du HTML : on n'en garde que le texte
            texte = BeautifulSoup(donnees.get("description", ""), "html.parser").get_text(" ", strip=True)
            resultat["description"] = re.sub(r"\s+", " ", texte)
            resultat["date_limite"] = (donnees.get("validThrough") or "")[:10]
            salaire = donnees.get("baseSalary")
            if salaire:
                resultat["salaire"] = json.dumps(salaire, ensure_ascii=False)
            break

    # Pas de JSON-LD : on se rabat sur le texte visible de la page
    if not resultat["description"]:
        bloc = soup.select_one("div.article_ttext:last-of-type")
        if bloc:
            resultat["description"] = bloc.get_text(" ", strip=True)

    # Salaire écrit en clair dans la description ("Salaire : 1 500 USD")
    if not resultat["salaire"]:
        trouve = MOTIF_SALAIRE.search(resultat["description"])
        if trouve:
            resultat["salaire"] = trouve.group(0).strip()

    return resultat


# ---------- Collecte (réseau) ----------

def creer_session():
    """Session HTTP polie et robuste.

    - "Connection: close" : une nouvelle connexion par requête. Avec 10 s de pause entre
      deux pages, le serveur a déjà fermé l'ancienne connexion ; la réutiliser provoquait
      l'erreur "Remote end closed connection without response".
    - Retry : en cas de coupure réseau ou d'erreur serveur (429, 5xx), on réessaie
      jusqu'à 3 fois en attendant de plus en plus longtemps (2 s, 4 s, 8 s).
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Connection"] = "close"
    reessais = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=reessais))
    session.mount("http://", HTTPAdapter(max_retries=reessais))
    return session


def collecter(max_offres=15, pages_max=3, afficher=print):
    session = creer_session()

    robots = lire_robots(session)
    delai = robots.crawl_delay(USER_AGENT) or DELAI_PAR_DEFAUT
    afficher(f"robots.txt lu : délai de courtoisie = {delai} s entre deux pages")

    def telecharger(url):
        if not robots.can_fetch(USER_AGENT, url):
            afficher(f"  interdit par robots.txt, ignoré : {url}")
            return None
        time.sleep(delai)  # pause de courtoisie AVANT chaque requête
        reponse = session.get(url, timeout=30)
        reponse.raise_for_status()
        reponse.encoding = reponse.apparent_encoding  # évite les accents cassés
        return reponse.text

    # 1. Pages de liste
    offres = []
    for numero in range(1, pages_max + 1):
        try:
            html = telecharger(PAGE_LISTE.format(numero=numero))
        except requests.RequestException as erreur:
            afficher(f"Page {numero} inaccessible ({erreur}) : arrêt de la collecte des listes")
            break
        if html is None:
            break
        nouvelles = parser_liste(html)
        afficher(f"Page {numero} : {len(nouvelles)} offres trouvées")
        offres.extend(nouvelles)
        if len(offres) >= max_offres or not nouvelles:
            break

    # 2. Une page de détail par offre (description, salaire, date limite)
    offres = offres[:max_offres]
    for i, offre in enumerate(offres, start=1):
        afficher(f"  [{i}/{len(offres)}] {offre['intitule'][:60]}")
        try:
            html = telecharger(offre["url"])
            if html:
                offre.update(parser_detail(html))
        except requests.RequestException as erreur:
            afficher(f"    échec ({erreur}) : offre gardée sans description")
    return offres


def enregistrer_csv(offres, chemin=CSV_SORTIE):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLONNES, extrasaction="ignore")
        writer.writeheader()
        for offre in offres:
            writer.writerow({col: offre.get(col, "") for col in COLONNES})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collecte d'offres d'emploi sur mediacongo.net")
    parser.add_argument("--max", type=int, default=15, help="nombre maximal d'offres (défaut : 15)")
    args = parser.parse_args()

    resultats = collecter(max_offres=args.max)
    enregistrer_csv(resultats)
    avec_salaire = sum(1 for o in resultats if o.get("salaire"))
    print(f"\n{len(resultats)} offres enregistrées dans {os.path.relpath(CSV_SORTIE, BASE_DIR)}")
    print(f"dont {avec_salaire} avec un salaire indiqué")
