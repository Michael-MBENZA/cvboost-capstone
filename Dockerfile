# CVBoost : un seul Dockerfile, deux images (« cibles ») :
#   docker build --target web -t cvboost-web .   -> site Flask (Gunicorn), image légère
#   docker build --target api -t cvboost-api .   -> API /speak-smart (FastAPI + PyTorch CPU + Coqui TTS)

# ---------- Base commune ----------
FROM python:3.11-slim AS base

# Pas de fichiers .pyc, logs affichés tout de suite, pas de cache pip (image plus petite)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Utilisateur sans droits d'administrateur : le conteneur ne tourne jamais en root
RUN useradd --create-home --uid 1000 cvboost


# ---------- Site web (Flask) ----------
FROM base AS web

# 1) Dépendances d'abord : cette étape reste en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Puis le code et les offres nettoyées (Sprint 2)
COPY app_web/ app_web/
COPY run.py .
COPY data/processed/jobs_clean.csv data/processed/jobs_clean.csv

# Dossiers où le site écrit (base SQLite si pas de PostgreSQL, CV déposés, journaux)
RUN mkdir -p database uploads logs && chown -R cvboost:cvboost /app
USER cvboost

EXPOSE 5000
# Gunicorn : 2 processus ; 120 s de délai (les analyses OpenAI peuvent prendre ~20 s)
# --preload : l'application (et db.create_all) est chargée UNE fois avant de lancer les 2 processus.
# Sans lui, les 2 processus créaient les tables en même temps -> « table users already exists ».
CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "run:app"]


# ---------- API /speak-smart (FastAPI + voix VITS) ----------
FROM base AS api

# PyTorch version PROCESSEUR (~200 Mo) au lieu de la version GPU (plus de 2 Go) : pas de GPU sur Azure App Service
COPY api_service/requirements.txt api_service/requirements.txt
# Connexion lente : délai de 5 min, 20 essais, et cache des paquets déjà téléchargés
# conservé d'une construction à l'autre (hors de l'image : elle ne grossit pas)
RUN --mount=type=cache,target=/root/.cache/pip \
    unset PIP_NO_CACHE_DIR \
 && pip install --timeout 300 --retries 20 --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchaudio==2.8.0 \
 && pip install --timeout 300 --retries 20 -r api_service/requirements.txt

ENV NUMBA_CACHE_DIR=/tmp/numba
RUN mkdir -p api_service/temp_audio && chown -R cvboost:cvboost /app
USER cvboost

# Modèle de voix téléchargé UNE fois, pendant la construction : il fait partie de l'image
RUN python -c "from TTS.api import TTS; TTS('tts_models/fr/css10/vits')"

COPY --chown=cvboost:cvboost api_service/ api_service/

EXPOSE 8000
CMD ["uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000"]