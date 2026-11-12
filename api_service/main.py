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
import wave
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

DOSSIER_API = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(DOSSIER_API, "..", ".env"))

from .services import llm_reformulator, tts_engine  # noqa: E402  (après load_dotenv)
from .services.metriques import metriques  # noqa: E402

DOSSIER_AUDIO = os.path.join(DOSSIER_API, "temp_audio")
DOSSIER_LOGS = os.getenv("LOG_FOLDER", os.path.join(DOSSIER_API, "..", "logs"))
LONGUEUR_MIN_CV = 50
LONGUEUR_MAX_CV = 15000  # comme app_web/extraction.py : maîtrise du coût et du contexte du LLM

FORMAT_LOGS = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=FORMAT_LOGS)
logger = logging.getLogger("cvboost.api")


def brancher_journal_erreurs(dossier):
    """logs/api_erreurs.log : avertissements et erreurs de l'API, avec la trace complète des exceptions.
    Rotation : 5 fichiers de 1 Mo au maximum."""
    for ancien in [h for h in logger.handlers if h.get_name() == "cvboost-api-erreurs"]:
        logger.removeHandler(ancien)
        ancien.close()
    try:
        os.makedirs(dossier, exist_ok=True)
        handler = RotatingFileHandler(os.path.join(dossier, "api_erreurs.log"), maxBytes=1_000_000,
                                      backupCount=5, encoding="utf-8")
    except OSError as erreur:  # dossier non accessible en écriture : l'API fonctionne quand même
        logger.warning("Journal des erreurs indisponible (%s) : erreurs affichées seulement à l'écran", erreur)
        return
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(FORMAT_LOGS, "%Y-%m-%d %H:%M:%S"))
    handler.set_name("cvboost-api-erreurs")
    logger.addHandler(handler)


@asynccontextmanager
async def demarrage(app):
    """Au démarrage : journal des erreurs, dossier audio prêt et modèle vocal chargé une fois pour toutes."""
    brancher_journal_erreurs(DOSSIER_LOGS)
    metriques.remettre_a_zero()
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


@app.middleware("http")
async def compter_les_requetes(request: Request, call_next):
    """Chaque requête est comptée par route et par code HTTP (vu dans /metrics).
    Une exception non prévue est comptée comme une erreur 500 avant d'être traitée plus bas."""
    route = request.url.path
    try:
        reponse = await call_next(request)
    except Exception:
        metriques.requete(route, 500)
        raise
    metriques.requete(route, reponse.status_code)
    if reponse.status_code == 422:
        metriques.erreur("validation")
    return reponse


@app.exception_handler(Exception)
async def erreur_inattendue(request: Request, erreur: Exception):
    """Toute erreur non prévue (bug) : trace complète dans logs/api_erreurs.log, message neutre au client."""
    metriques.erreur("inattendue")
    logger.exception("Erreur inattendue sur %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erreur interne de l'API."})


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


def duree_audio(chemin_wav):
    """Durée d'un fichier WAV en secondes (0 si illisible)."""
    try:
        with wave.open(chemin_wav) as fichier:
            return fichier.getnframes() / fichier.getframerate()
    except (wave.Error, OSError, ZeroDivisionError):
        return 0.0


# ---------- Routes ----------

@app.get("/health")
def sante(moteur=Depends(obtenir_moteur_vocal)):
    """Santé de l'API (utilisée par Docker, et plus tard par l'hébergeur, pour savoir si le service répond)."""
    return {
        "statut": "ok",
        "voix_chargee": moteur.charge,
        "modele_vocal": moteur.nom_modele,
        "openai_configure": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.get("/metrics")
def metriques_api():
    """Métriques de fonctionnement (MLOps) : requêtes, erreurs, durées, tokens, secondes d'audio produites."""
    return metriques.instantane()


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
        metriques.erreur("llm")
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
        metriques.erreur("voix")
        logger.error("Étape 2 (TTS) : %s", erreur)
        raise HTTPException(status_code=503, detail="La synthèse vocale a échoué.")
    duree_totale = time.perf_counter() - debut
    secondes_audio = duree_audio(chemin)

    metriques.pitch(duree_llm, duree_totale - duree_llm, duree_totale, secondes_audio, tokens)
    logger.info("Pitch généré : %d caractères, %d tokens, %.1f s d'audio, LLM %.1f s, total %.1f s",
                len(pitch), tokens, secondes_audio, duree_llm, duree_totale)
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
