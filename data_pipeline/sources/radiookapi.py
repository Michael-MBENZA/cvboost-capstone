"""Source n°2 : Radio Okapi, rubrique « Offres d'emploi » (radio de l'ONU en RDC).

robots.txt : rubrique autorisée, avec « Crawl-delay: 10 ».
Particularité : la rubrique mélange de vraies offres d'emploi, des appels d'offres (marchés publics)
et des communiqués. On ne garde que les annonces qui ressemblent à un recrutement.
Titre habituel : « Employeur : Intitulé du poste ».
"""
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .commun import extraire_date_limite, extraire_salaire

NOM = "radiookapi"
SITE = "https://www.radiookapi.net/"
PAGE_LISTE = SITE + "offre-demploi?page={numero}"  # pagination Drupal : la 1re page est 0

MOTS_RECRUTEMENT = re.compile(
    r"recrut|offre d.?emploi|appel [àa] candidature|avis de vacance|poste|vacancy|"
    r"assistant|agent|charg[ée]|responsable|coordinat|officer|manager|comptable|chauffeur|"
    r"m[ée]decin|infirmi|ing[ée]nieur|technicien|consultant",
    re.IGNORECASE,
)
MOTS_HORS_SUJET = re.compile(
    r"appel d.?offres|soumission|acquisition|fourniture|travaux|manifestation d.?int[ée]r[êe]t|"
    r"[ée]tude prospective|communiqu[ée]|appelle|invite les",
    re.IGNORECASE,
)
MOTIF_LIEU = re.compile(r"(?:lieu de prestation|lieu d.?affectation|lieu de travail|lieu)\s*:\s*([^\n:]{2,60})",
                        re.IGNORECASE)


def est_offre_emploi(titre, resume=""):
    texte = f"{titre} {resume}"
    return bool(MOTS_RECRUTEMENT.search(texte)) and not MOTS_HORS_SUJET.search(titre)


def decouper_titre(titre):
    """« GiveDirectly (GD) : Agent Centre d'Appel de Goma » -> ('GiveDirectly (GD)', 'Agent Centre...')."""
    if ":" in titre:
        employeur, poste = titre.split(":", 1)
        return employeur.strip(), poste.strip()
    return "", titre.strip()


def parser_liste(html):
    """Page de la rubrique -> liste de dicts (seulement les vraies offres d'emploi)."""
    soup = BeautifulSoup(html, "html.parser")
    offres = []
    for bloc in soup.select("div.views-row"):
        lien = bloc.select_one(".views-field-title a")
        if not lien:
            continue
        titre = lien.get_text(" ", strip=True)
        resume = bloc.select_one(".views-field-body")
        resume = resume.get_text(" ", strip=True) if resume else ""
        if not est_offre_emploi(titre, resume):
            continue
        date_brute = bloc.select_one(".views-field-created")
        employeur, poste = decouper_titre(titre)
        chemin = lien["href"]
        offres.append({
            "reference": "RO-" + chemin.rstrip("/").rsplit("/", 1)[-1][:60],
            "intitule": poste,
            "entreprise": employeur,
            "lieu": "",
            "date_publication": date_brute.get_text(strip=True).split(" ")[0] if date_brute else "",
            "url": urljoin(SITE, chemin),
        })
    return offres


def parser_detail(html):
    """Page d'une annonce -> {description, lieu, date_limite, salaire}."""
    soup = BeautifulSoup(html, "html.parser")
    corps = soup.select_one(".field-name-body")
    texte = corps.get_text("\n", strip=True) if corps else ""
    lieu = MOTIF_LIEU.search(texte)
    return {
        "description": re.sub(r"\s+", " ", texte),
        "lieu": lieu.group(1).strip(" .;-") if lieu else "",
        "date_limite": extraire_date_limite(texte),
        "salaire": extraire_salaire(texte),
    }


def collecter(telecharger, deja_connues, max_offres=20, pages_max=3, afficher=print):
    nouvelles = []
    for numero in range(0, pages_max):
        try:
            html = telecharger(PAGE_LISTE.format(numero=numero))
        except requests.RequestException as erreur:
            afficher(f"  page {numero + 1} inaccessible ({erreur}) : arrêt")
            break
        if html is None:
            break
        liste = parser_liste(html)
        a_voir = [o for o in liste if o["reference"] not in deja_connues]
        afficher(f"  page {numero + 1} : {len(liste)} offres d'emploi, dont {len(a_voir)} nouvelles")
        nouvelles.extend(a_voir)
        if len(nouvelles) >= max_offres:
            break

    nouvelles = nouvelles[:max_offres]
    for i, offre in enumerate(nouvelles, start=1):
        afficher(f"    [{i}/{len(nouvelles)}] {offre['intitule'][:60]}")
        try:
            html = telecharger(offre["url"])
            if html:
                detail = parser_detail(html)
                offre["description"] = detail["description"]
                offre["date_limite"] = detail["date_limite"]
                offre["salaire"] = detail["salaire"]
                offre["lieu"] = offre["lieu"] or detail["lieu"]
        except requests.RequestException as erreur:
            afficher(f"      échec ({erreur}) : offre gardée sans description")
        offre["source"] = NOM
    return nouvelles
