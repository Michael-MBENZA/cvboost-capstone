"""Orchestrateur FastAPI CVBoost (Sprint 4) : route POST /speak-smart.

    texte brut du CV ──► Étape 1 : LLM (reformulation stricte) ──► Étape 2 : VITS (Coqui) ──► pitch.wav

Lancement (depuis la racine du projet, venv activé) :
    uvicorn api_service.main:app --port 8000
Documentation interactive (Swagger) : http://127.0.0.1:8000/docs
"""
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

DOSSIER_API = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(DOSSIER_API, "..", ".env"))

from .services import llm_reformulator, tts_engine  # noqa: E402  (après load_dotenv)

DOSSIER_AUDIO = os.path.join(DOSSIER_API, "temp_audio")
LONGUEUR_MIN_CV = 50
LONGUEUR_MAX_CV = 15000  # comme app_web/extraction.py : maîtrise du coût et du contexte du LLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("cvboost.api")


@asynccontextmanager
async def demarrage(app):
    """Au démarrage : dossier audio prêt et modèle vocal chargé une fois pour toutes."""
    os.makedirs(DOSSIER_AUDIO, exist_ok=True)
    if os.getenv("PRECHARGER_VOIX", "1") == "1":
        debut = time.perf_counter()
        try:
            tts_engine.moteur.charger()
            logger.info("Modèle vocal %s chargé en %.1f s", tts_engine.moteur.nom_modele,
                        time.perf_counter() - debut)
        except tts_engine.SyntheseImpossible as erreur:
            # L'API démarre quand même : /health le signalera et /speak-smart renverra 503
            logger.error("%s", erreur)
    yield


app = FastAPI(
    title="CVBoost - Speak Smart",
    description="Transforme le texte brut d'un CV en pitch oral (LLM), puis en fichier audio .wav (VITS).",
    version="1.0.0",
    lifespan=demarrage,
)


class DemandePitch(BaseModel):
    texte_cv: str = Field(..., min_length=LONGUEUR_MIN_CV, max_length=LONGUEUR_MAX_CV,
                          description="Texte brut extrait du CV (PDF ou Word)")


# ---------- Dépendances (remplacées par des versions simulées dans les tests) ----------

def verifier_cle_api(x_api_key: str | None = Header(default=None)):
    """Si API_PITCH_CLE est définie dans .env, chaque appel doit envoyer l'en-tête X-API-Key."""
    attendue = os.getenv("API_PITCH_CLE", "")
    if attendue and not secrets.compare_digest(x_api_key or "", attendue):
        raise HTTPException(status_code=401, detail="Clé d'API manquante ou invalide.")


def obtenir_reformulateur():
    return llm_reformulator.reformuler


def obtenir_moteur_vocal():
    return tts_engine.moteur


def supprimer_fichier(chemin):
    try:
        os.remove(chemin)
    except FileNotFoundError:
        pass


# ---------- Routes ----------

@app.get("/health")
def sante(moteur=Depends(obtenir_moteur_vocal)):
    """Santé de l'API (utilisée plus tard par Docker et Azure pour savoir si le service répond)."""
    return {
        "statut": "ok",
        "voix_chargee": moteur.charge,
        "modele_vocal": moteur.nom_modele,
        "openai_configure": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.post("/speak-smart", response_class=FileResponse, dependencies=[Depends(verifier_cle_api)],
          responses={200: {"content": {"audio/wav": {}}, "description": "Pitch audio au format WAV"}})
def speak_smart(demande: DemandePitch,
                reformuler=Depends(obtenir_reformulateur),
                moteur=Depends(obtenir_moteur_vocal)):
    # Fonction « def » (pas « async def ») : FastAPI l'exécute dans un thread à part,
    # car l'appel à OpenAI et la synthèse vocale bloquent pendant plusieurs secondes.
    debut = time.perf_counter()

    # Étape 1 : reformulation par le LLM
    try:
        pitch, tokens = reformuler(demande.texte_cv)
    except llm_reformulator.ReformulationImpossible as erreur:
        logger.error("Étape 1 (LLM) : %s", erreur)
        raise HTTPException(status_code=502, detail="Le service de reformulation (OpenAI) est indisponible.")
    duree_llm = time.perf_counter() - debut

    # Étape 2 : synthèse vocale
    os.makedirs(DOSSIER_AUDIO, exist_ok=True)
    chemin = os.path.join(DOSSIER_AUDIO, f"{uuid.uuid4().hex}.wav")  # nom unique : pas de collision
    try:
        moteur.synthetiser(pitch, chemin)
    except tts_engine.SyntheseImpossible as erreur:
        supprimer_fichier(chemin)
        logger.error("Étape 2 (TTS) : %s", erreur)
        raise HTTPException(status_code=503, detail="La synthèse vocale a échoué.")
    duree_totale = time.perf_counter() - debut

    logger.info("Pitch généré : %d caractères, %d tokens, LLM %.1f s, total %.1f s",
                len(pitch), tokens, duree_llm, duree_totale)
    return FileResponse(
        chemin,
        media_type="audio/wav",
        filename="pitch.wav",
        # Le texte du pitch accompagne l'audio (en-tête HTTP : encodé, car il contient des accents)
        headers={
            "X-Pitch-Texte": quote(pitch),
            "X-Pitch-Tokens": str(tokens),
            "X-Prompt-Version": llm_reformulator.PROMPT_VERSION,
        },
        # Le fichier temporaire est supprimé APRÈS l'envoi de la réponse
        background=BackgroundTask(supprimer_fichier, chemin),
    )
