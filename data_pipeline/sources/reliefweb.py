"""Source n°3 : ReliefWeb (OCHA, Nations unies), offres humanitaires et ONG en RDC.

Pas de scraping ici : ReliefWeb fournit une API OFFICIELLE et gratuite (JSON), qui est la manière
propre et autorisée de récupérer ses offres. Elle exige un « appname » approuvé par ReliefWeb :
  1. le demander sur https://apidoc.reliefweb.int/parameters#appname
  2. l'ajouter dans .env : RELIEFWEB_APPNAME=...
Sans appname, cette source est simplement ignorée.
"""
import os
import re

import requests
from bs4 import BeautifulSoup

from .commun import extraire_salaire

NOM = "reliefweb"
SITE = "https://reliefweb.int/"
API = "https://api.reliefweb.int/v2/jobs"
CHAMPS = ["title", "body", "source.name", "city.name", "date.created", "date.closing", "url_alias"]


def convertir(item):
    """Un résultat de l'API -> une offre au format commun."""
    champs = item.get("fields", {})
    description = BeautifulSoup(champs.get("body", ""), "html.parser").get_text(" ", strip=True)
    sources = champs.get("source") or [{}]
    villes = champs.get("city") or []
    return {
        "source": NOM,
        "reference": f"RW-{item.get('id')}",
        "intitule": champs.get("title", "").strip(),
        "entreprise": sources[0].get("name", ""),
        "lieu": ", ".join(v.get("name", "") for v in villes) or "RD Congo",
        "date_publication": (champs.get("date", {}).get("created") or "")[:10],
        "date_limite": (champs.get("date", {}).get("closing") or "")[:10],
        "salaire": extraire_salaire(description),
        "description": re.sub(r"\s+", " ", description),
        "url": champs.get("url_alias") or f"https://reliefweb.int/job/{item.get('id')}",
    }


def collecter(telecharger, deja_connues, max_offres=50, afficher=print, appname=None, session=None):
    appname = appname or os.getenv("RELIEFWEB_APPNAME", "")
    if not appname:
        afficher("  ignorée : pas de RELIEFWEB_APPNAME dans .env (voir data_pipeline/sources/reliefweb.py)")
        return []
    parametres = {
        "appname": appname,
        "filter[field]": "country.iso3",
        "filter[value]": "cod",  # code ISO de la RD Congo
        "sort[]": "date.created:desc",
        "limit": max_offres,
        "fields[include][]": CHAMPS,
    }
    try:
        reponse = (session or requests).get(API, params=parametres, timeout=60)
        reponse.raise_for_status()
    except requests.RequestException as erreur:
        afficher(f"  API ReliefWeb inaccessible ({erreur})")
        return []
    offres = [convertir(item) for item in reponse.json().get("data", [])]
    nouvelles = [o for o in offres if o["reference"] not in deja_connues]
    afficher(f"  API : {len(offres)} offres en RDC, dont {len(nouvelles)} nouvelles")
    return nouvelles
