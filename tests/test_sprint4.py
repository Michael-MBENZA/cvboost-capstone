"""Sprint 4 : API FastAPI /speak-smart (reformulation LLM + voix VITS).

OpenAI et la voix VITS sont SIMULÉS : les tests ne paient rien et n'ont besoin ni d'internet
ni de PyTorch. La vraie chaîne est vérifiée à part, à la main (voir docs/JOURNAL.md).
"""
import io
import os
import wave
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from api_service import main as api
from api_service.services import llm_reformulator, tts_engine

TEXTE_CV = ("Michael Mbenza, Kinshasa. Tél : +243 812 345 678. michael@exemple.cd. "
            "Formation développeur IA, Kadea Academy (2026). Expérience : assistant informatique "
            "à la CENI (2023-2025). Compétences : Python, Flask, SQL. Langues : français, lingala, anglais B2.")
PITCH = "Je m'appelle Michael Mbenza. Je suis développeur en intelligence artificielle à Kinshasa."


def wav_minuscule():
    """Un vrai petit fichier WAV (0,1 s de silence) fabriqué avec la bibliothèque standard."""
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 2205)
    return tampon.getvalue()


class FauxMoteur:
    """Remplace la voix VITS : écrit un WAV de silence, ou échoue si on le demande."""

    nom_modele = "faux-modele"

    def __init__(self, echoue=False):
        self.echoue = echoue
        self.textes = []
        self.charge = True

    def synthetiser(self, texte, chemin):
        self.textes.append(texte)
        if self.echoue:
            raise tts_engine.SyntheseImpossible("panne simulée")
        with open(chemin, "wb") as fichier:
            fichier.write(wav_minuscule())
        return chemin


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Client de l'API avec un dossier audio temporaire et une fausse voix."""
    monkeypatch.setattr(api, "DOSSIER_AUDIO", str(tmp_path / "temp_audio"))
    monkeypatch.setenv("PRECHARGER_VOIX", "0")   # ne charge pas le vrai modèle au démarrage
    monkeypatch.delenv("API_PITCH_CLE", raising=False)
    moteur = FauxMoteur()
    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: (lambda texte: (PITCH, 321))
    api.app.dependency_overrides[api.obtenir_moteur_vocal] = lambda: moteur
    with TestClient(api.app) as client:
        client.moteur = moteur
        yield client
    api.app.dependency_overrides.clear()


# ---------- API FastAPI ----------

def test_speak_smart_renvoie_un_wav_et_le_texte(api_client, tmp_path):
    reponse = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})
    assert reponse.status_code == 200
    assert reponse.headers["content-type"] == "audio/wav"
    assert reponse.content[:4] == b"RIFF" and reponse.content[8:12] == b"WAVE"
    assert unquote(reponse.headers["X-Pitch-Texte"]) == PITCH
    assert reponse.headers["X-Pitch-Tokens"] == "321"
    assert api_client.moteur.textes == [PITCH]          # la voix lit bien le texte reformulé
    # Le fichier temporaire est supprimé après l'envoi
    assert os.listdir(tmp_path / "temp_audio") == []


def test_texte_trop_court_ou_absent_refuse(api_client):
    assert api_client.post("/speak-smart", json={"texte_cv": "trop court"}).status_code == 422
    assert api_client.post("/speak-smart", json={}).status_code == 422
    assert api_client.post("/speak-smart", json={"texte_cv": "x" * 15001}).status_code == 422
    assert api_client.moteur.textes == []                # rien n'a été généré


def test_panne_openai_donne_502(api_client):
    def panne(texte):
        raise llm_reformulator.ReformulationImpossible("Erreur OpenAI : APIConnectionError")

    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: panne
    reponse = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})
    assert reponse.status_code == 502
    assert "OpenAI" in reponse.json()["detail"]
    assert api_client.moteur.textes == []                # pas de synthèse sans texte


def test_panne_de_la_voix_ne_laisse_pas_de_fichier(api_client, tmp_path):
    api.app.dependency_overrides[api.obtenir_moteur_vocal] = lambda: FauxMoteur(echoue=True)
    reponse = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})
    assert reponse.status_code == 503
    assert os.listdir(tmp_path / "temp_audio") == []


def test_cle_api_exigee_si_configuree(api_client, monkeypatch):
    monkeypatch.setenv("API_PITCH_CLE", "secret-de-test")
    assert api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV}).status_code == 401
    mauvaise = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV}, headers={"X-API-Key": "faux"})
    assert mauvaise.status_code == 401
    bonne = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV}, headers={"X-API-Key": "secret-de-test"})
    assert bonne.status_code == 200


def test_health(api_client):
    reponse = api_client.get("/health")
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "ok"
    assert reponse.json()["voix_chargee"] is True


def test_reformulation_sans_cle_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(llm_reformulator.ReformulationImpossible):
        llm_reformulator.reformuler(TEXTE_CV)


def test_reformulation_avec_faux_openai(monkeypatch):
    """Simule le SDK OpenAI : vérifie le prompt envoyé et le nettoyage de la réponse."""
    envoye = {}

    class FauxClient:
        def __init__(self, **kwargs):
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            envoye.update(kwargs)
            message = type("M", (), {"content": "**Je m'appelle Michael** 🚀\n- Python & SQL"})
            choix = type("C", (), {"message": message})
            return type("R", (), {"choices": [choix], "usage": type("U", (), {"total_tokens": 250})})

    import openai
    monkeypatch.setattr(openai, "OpenAI", FauxClient)
    monkeypatch.setenv("OPENAI_API_KEY", "cle-de-test")
    pitch, tokens = llm_reformulator.reformuler(TEXTE_CV)
    assert pitch == "Je m'appelle Michael - Python et SQL"
    assert tokens == 250
    assert envoye["messages"][0]["content"] == llm_reformulator.PROMPT_SYSTEME
    assert TEXTE_CV in envoye["messages"][1]["content"]


def test_nettoyer_pour_la_voix():
    nettoyer = llm_reformulator.nettoyer_pour_la_voix
    sans_coordonnees = nettoyer("J'habite à Kinshasa, +243 812 345 678, moi@site.cd, https://site.cd.")
    assert sans_coordonnees.startswith("J'habite à Kinshasa")
    for coordonnee in ("243", "@", "http", "site.cd"):
        assert coordonnee not in sans_coordonnees
    assert nettoyer("Réussite à 90% • Python & SQL") == "Réussite à 90 pour cent Python et SQL"
    assert nettoyer("De 2023 à 2025, puis 2026.") == "De 2023 à 2025, puis 2026."  # années conservées
    assert nettoyer("Ligne 1\n\nLigne 2") == "Ligne 1 Ligne 2"
    long = nettoyer("Une phrase. " * 300)
    assert len(long) <= llm_reformulator.LONGUEUR_MAX_PITCH and long.endswith(".")


def test_le_prompt_est_strict():
    prompt = llm_reformulator.PROMPT_SYSTEME
    for exigence in ("UNIQUEMENT", "n'invente", "première personne", "voix de synthèse", "coordonnées"):
        assert exigence in prompt
