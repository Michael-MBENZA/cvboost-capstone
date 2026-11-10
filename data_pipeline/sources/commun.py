"""Outils partagés par toutes les sources : session HTTP polie, robots.txt, extraction de dates et salaires."""
import re
import time
from datetime import date
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# On s'identifie honnêtement. Si un site refuse ce robot, on n'essaie PAS de se faire passer
# pour un navigateur : on renonce simplement à cette source.
USER_AGENT = "CVBoost-StudentBot/1.0 (projet pedagogique Kadea Academy; scraping respectueux)"
DELAI_PAR_DEFAUT = 10  # secondes entre deux pages, si robots.txt n'indique rien

# Colonnes du fichier brut commun à toutes les sources
COLONNES = ["source", "reference", "intitule", "entreprise", "lieu", "date_publication",
            "date_limite", "salaire", "description", "url"]

# Mots qui annoncent un salaire, suivis (dans les 80 caractères) d'un nombre
MOTIF_SALAIRE = re.compile(
    r"(?:salaire|r[ée]mun[ée]ration|salary|package)[^.\n]{0,80}?\d[\d\s.,]*\s*(?:USD|\$|CDF|FC|EUR|€)?",
    re.IGNORECASE,
)

MOIS = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
        "decembre": 12, "décembre": 12}


def creer_session():
    """Session HTTP : une connexion neuve par requête (« Connection: close ») et 3 réessais
    automatiques (attente de 2, 4 puis 8 s) en cas de coupure réseau ou d'erreur serveur."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Connection"] = "close"
    reessais = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                     allowed_methods=["GET"])
    session.mount("https://", HTTPAdapter(max_retries=reessais))
    session.mount("http://", HTTPAdapter(max_retries=reessais))
    return session


class Telechargeur:
    """Télécharge les pages d'UN site en respectant son robots.txt et son délai de courtoisie."""

    def __init__(self, site, session=None, afficher=print):
        self.session = session or creer_session()
        self.afficher = afficher
        self.robots = RobotFileParser()
        reponse = self.session.get(urljoin(site, "robots.txt"), timeout=30)
        self.robots.parse(reponse.text.splitlines() if reponse.ok else [])
        self.delai = self.robots.crawl_delay(USER_AGENT) or DELAI_PAR_DEFAUT

    def __call__(self, url):
        """Renvoie le HTML de la page, ou None si robots.txt l'interdit."""
        if not self.robots.can_fetch(USER_AGENT, url):
            self.afficher(f"  interdit par robots.txt, ignoré : {url}")
            return None
        time.sleep(self.delai)  # pause de courtoisie AVANT chaque requête
        reponse = self.session.get(url, timeout=30)
        reponse.raise_for_status()
        reponse.encoding = reponse.apparent_encoding  # évite les accents cassés
        return reponse.text


def extraire_salaire(texte):
    trouve = MOTIF_SALAIRE.search(texte or "")
    return trouve.group(0).strip() if trouve else ""


def date_iso(jour, mois, annee):
    try:
        return date(int(annee), int(mois), int(jour)).isoformat()
    except ValueError:
        return ""


def extraire_date_limite(texte):
    """Cherche la date limite de candidature dans un texte libre -> 'AAAA-MM-JJ' ou ''.

    Exemples reconnus : « Date limite : 20/11/2026 », « au plus tard le 5 décembre 2026 »,
    « Deadline: 30.11.2026 ».
    """
    texte = (texte or "").lower()
    contexte = re.search(r"(date limite|au plus tard|deadline|d[ée]lai|cl[ôo]tur)[^\n]{0,120}", texte)
    if not contexte:
        return ""
    zone = contexte.group(0)
    numerique = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", zone)
    if numerique:
        return date_iso(*numerique.groups())
    en_lettres = re.search(r"(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})", zone)
    if en_lettres and en_lettres.group(2) in MOIS:
        return date_iso(en_lettres.group(1), MOIS[en_lettres.group(2)], en_lettres.group(3))
    return ""
