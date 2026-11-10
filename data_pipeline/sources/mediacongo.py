"""Source n°1 : mediacongo.net (offres variées, beaucoup d'ONG).

robots.txt : tout est autorisé, avec « Crawl-delay: 10 ».
Liste : tableau HTML ; détail : données JSON-LD schema.org/JobPosting dans la page.
"""
import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .commun import extraire_salaire

NOM = "mediacongo"
SITE = "https://www.mediacongo.net/"
PAGE_LISTE = SITE + "emplois-search--tri-offres_recentes-page-{numero}.html"


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
        resultat["salaire"] = extraire_salaire(resultat["description"])
    return resultat


def collecter(telecharger, deja_connues, max_offres=30, pages_max=3, afficher=print):
    """Renvoie les NOUVELLES offres (les références déjà connues ne sont pas retéléchargées)."""
    nouvelles = []
    for numero in range(1, pages_max + 1):
        try:
            html = telecharger(PAGE_LISTE.format(numero=numero))
        except requests.RequestException as erreur:
            afficher(f"  page {numero} inaccessible ({erreur}) : arrêt")
            break
        if html is None:
            break
        liste = parser_liste(html)
        a_voir = [o for o in liste if o["reference"] not in deja_connues]
        afficher(f"  page {numero} : {len(liste)} offres, dont {len(a_voir)} nouvelles")
        nouvelles.extend(a_voir)
        # Une page sans nouveauté : les suivantes (plus anciennes) sont déjà connues
        if not liste or not a_voir or len(nouvelles) >= max_offres:
            break

    nouvelles = nouvelles[:max_offres]
    for i, offre in enumerate(nouvelles, start=1):
        afficher(f"    [{i}/{len(nouvelles)}] {offre['intitule'][:60]}")
        try:
            html = telecharger(offre["url"])
            if html:
                offre.update(parser_detail(html))
        except requests.RequestException as erreur:
            afficher(f"      échec ({erreur}) : offre gardée sans description")
        offre["source"] = NOM
    return nouvelles
