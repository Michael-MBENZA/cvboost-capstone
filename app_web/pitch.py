"""Pitch audio (Sprint 4) : le site appelle l'API FastAPI /speak-smart (api_service/).

Le site ne fait ni la reformulation ni la synthèse vocale lui-même : il envoie le texte du CV
à l'API (par le réseau interne de Docker en production) et range le .wav reçu.
Le candidat n'a besoin que du site : il ne voit jamais l'API ni sa clé.
"""
import hashlib
import os
import uuid
from urllib.parse import unquote

import requests
from flask import current_app

from . import db
from .models import Pitch

DELAI_MAX = 110  # secondes, sous le délai de Gunicorn (120 s) ; LLM + voix prennent en général 10 à 40 s


class PitchIndisponible(Exception):
    """L'API /speak-smart est arrêtée, en erreur, ou a mis trop de temps à répondre."""


def dossier_pitchs():
    dossier = os.path.join(current_app.config["UPLOAD_FOLDER"], "pitchs")
    os.makedirs(dossier, exist_ok=True)
    return dossier


def empreinte_cv(texte_cv):
    """SHA-256 du texte du CV + version du prompt : même CV = même pitch, sans repayer OpenAI."""
    version = current_app.config["PITCH_PROMPT_VERSION"]
    return hashlib.sha256(f"{version}\n{texte_cv}".encode("utf-8")).hexdigest()


def dernier_pitch(document, texte_cv):
    return (Pitch.query.filter_by(document_id=document.id, empreinte=empreinte_cv(texte_cv))
            .order_by(Pitch.cree_le.desc()).first())


def demander_pitch(texte_cv):
    """Appelle POST /speak-smart. Renvoie (contenu wav en octets, texte du pitch, tokens)."""
    url = current_app.config["API_PITCH_URL"].rstrip("/") + "/speak-smart"
    entetes = {}
    if current_app.config["API_PITCH_CLE"]:
        entetes["X-API-Key"] = current_app.config["API_PITCH_CLE"]
    try:
        reponse = requests.post(url, json={"texte_cv": texte_cv}, headers=entetes, timeout=DELAI_MAX)
    except requests.RequestException as erreur:
        current_app.logger.error("API pitch injoignable (%s) : %s", url, erreur)
        raise PitchIndisponible("Le service de pitch audio ne répond pas. Réessayez plus tard.") from erreur

    if reponse.status_code != 200 or not reponse.content:
        current_app.logger.error("API pitch : statut %s, %s", reponse.status_code, reponse.text[:200])
        raise PitchIndisponible("Le pitch audio n'a pas pu être généré. Réessayez plus tard.")
    texte = unquote(reponse.headers.get("X-Pitch-Texte", ""))
    tokens = int(reponse.headers.get("X-Pitch-Tokens", "0") or 0)
    return reponse.content, texte, tokens


def generer(document, texte_cv):
    """Demande le pitch à l'API, range le .wav (nom UUID) et l'enregistre en base."""
    contenu, texte, tokens = demander_pitch(texte_cv)
    nom = f"{uuid.uuid4().hex}.wav"
    with open(os.path.join(dossier_pitchs(), nom), "wb") as fichier:
        fichier.write(contenu)
    pitch = Pitch(user_id=document.user_id, document_id=document.id, empreinte=empreinte_cv(texte_cv),
                  texte=texte, fichier=nom, tokens=tokens)
    db.session.add(pitch)
    db.session.commit()
    return pitch


def chemin_audio(pitch):
    return os.path.join(dossier_pitchs(), pitch.fichier)
