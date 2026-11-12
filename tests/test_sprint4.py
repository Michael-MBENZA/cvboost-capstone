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
    monkeypatch.setattr(api, "DOSSIER_LOGS", str(tmp_path / "logs"))   # journal des erreurs temporaire
    monkeypatch.setenv("PRECHARGER_VOIX", "0")   # ne charge pas le vrai modèle au démarrage
    monkeypatch.delenv("API_PITCH_CLE", raising=False)
    moteur = FauxMoteur()
    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: (lambda texte: (PITCH, 321))
    api.app.dependency_overrides[api.obtenir_moteur_vocal] = lambda: moteur
    # raise_server_exceptions=False : une erreur 500 est renvoyée comme à un vrai client (et non relancée)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        client.moteur = moteur
        client.journal = tmp_path / "logs" / "api_erreurs.log"
        yield client
    api.app.dependency_overrides.clear()
    api.brancher_journal_erreurs(str(tmp_path / "logs"))   # referme le fichier avant la suppression du dossier
    for handler in [h for h in api.logger.handlers if h.get_name() == "cvboost-api-erreurs"]:
        api.logger.removeHandler(handler)
        handler.close()


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


# ---------- MLOps : métriques, journal des erreurs, santé du site ----------

def test_metrics_compte_les_pitchs_et_les_erreurs(api_client):
    api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})           # réussi
    api_client.post("/speak-smart", json={"texte_cv": "court"})            # 422

    def panne(texte):
        raise llm_reformulator.ReformulationImpossible("Erreur OpenAI : APIConnectionError")

    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: panne
    api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})           # 502

    m = api_client.get("/metrics").json()
    assert m["pitchs_reussis"] == 1 and m["tokens_openai"] == 321
    assert m["erreurs"] == {"validation": 1, "llm": 1}
    assert m["requetes_par_route"]["/speak-smart 200"] == 1
    assert m["requetes_par_route"]["/speak-smart 502"] == 1
    assert m["secondes_audio_generees"] == 0.1                             # le petit WAV fait 0,1 s
    assert m["durees"]["total"]["mesures"] == 1
    assert m["taux_erreurs_serveur_pct"] == 33.3                           # 1 erreur 5xx sur 3 requêtes


def test_erreur_ecrite_dans_le_journal(api_client):
    def panne(texte):
        raise llm_reformulator.ReformulationImpossible("Erreur OpenAI : RateLimitError")

    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: panne
    api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})
    journal = api_client.journal.read_text(encoding="utf-8")
    assert "ERROR | cvboost.api | Étape 1 (LLM) : Erreur OpenAI : RateLimitError" in journal


def test_erreur_inattendue_500_journalisee_sans_details(api_client):
    def bug(texte):
        raise RuntimeError("bug de test")

    api.app.dependency_overrides[api.obtenir_reformulateur] = lambda: bug
    reponse = api_client.post("/speak-smart", json={"texte_cv": TEXTE_CV})
    assert reponse.status_code == 500
    assert reponse.json() == {"detail": "Erreur interne de l'API."}        # aucun détail technique au client
    journal = api_client.journal.read_text(encoding="utf-8")
    assert "Erreur inattendue sur POST /speak-smart" in journal and "RuntimeError: bug de test" in journal
    assert api_client.get("/metrics").json()["erreurs"]["inattendue"] == 1


def test_sante_du_site(client):
    reponse = client.get("/health")
    assert reponse.status_code == 200
    donnees = reponse.get_json()
    assert donnees["statut"] == "ok" and donnees["base_de_donnees"] == "ok"
    assert donnees["moteur_base"] == "sqlite"
    assert "email" not in str(donnees)                                     # aucune donnée personnelle


def test_sante_du_site_base_injoignable(app, client, monkeypatch):
    from app_web import db

    def base_en_panne(*args, **kwargs):
        raise RuntimeError("connexion refusée")

    monkeypatch.setattr(db.session, "execute", base_en_panne)
    reponse = client.get("/health")
    assert reponse.status_code == 503 and reponse.get_json()["base_de_donnees"] == "injoignable"
    journal = os.path.join(app.config["LOG_FOLDER"], "erreurs.log")
    assert "base de données injoignable : connexion refusée" in open(journal, encoding="utf-8").read()


def test_erreur_500_du_site_dans_erreurs_log(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False          # comportement réel : page 500, exception enregistrée
    app.add_url_rule("/test-bug", "test_bug", lambda: 1 / 0)
    reponse = app.test_client().get("/test-bug")
    assert reponse.status_code == 500
    journal = open(os.path.join(app.config["LOG_FOLDER"], "erreurs.log"), encoding="utf-8").read()
    assert "Exception on /test-bug [GET]" in journal and "ZeroDivisionError" in journal


# ---------- Métriques audio : WER, CER, MOS ----------

from api_service import evaluer_voix  # noqa: E402


def test_distance_edition():
    assert evaluer_voix.distance_edition("chat", "chats") == 1          # insertion
    assert evaluer_voix.distance_edition("chat", "chut") == 1           # substitution
    assert evaluer_voix.distance_edition(["a", "b", "c"], ["a", "c"]) == 1   # suppression d'un mot
    assert evaluer_voix.distance_edition("", "abc") == 3


def test_wer_et_cer():
    reference = "Je m'appelle Grace Kabila Mukendi et j'habite à Kinshasa."
    assert evaluer_voix.wer(reference, reference) == 0.0
    # 1 mot faux sur 11 (« Mukendi » -> « Mpandi ») : WER = 9,1 %
    assert round(100 * evaluer_voix.wer(reference, reference.replace("Mukendi", "Mpandi")), 1) == 9.1
    assert 0 < evaluer_voix.cer(reference, reference.replace("Mukendi", "Mpandi")) < 0.1
    # Accents, majuscules et ponctuation ne comptent pas comme des erreurs
    assert evaluer_voix.wer("L'Université, à Kinshasa !", "l universite a kinshasa") == 0.0


def test_nombres_normalises_en_lettres():
    pytest.importorskip("num2words")                    # installé avec Coqui (pas dans le requirements du site)
    assert evaluer_voix.wer("en deux mille vingt-quatre", "en 2024") == 0.0


def test_evaluation_complete_avec_faux_transcripteur(tmp_path):
    entendus = iter(["Je m'appelle Grace.", "Je parle lingala et français."])
    resultats = evaluer_voix.evaluer(FauxMoteur(), lambda chemin: next(entendus),
                                     ["Je m'appelle Grace.", "Je parle français et lingala."], str(tmp_path))
    assert resultats["details"][0]["wer_pct"] == 0.0
    assert resultats["details"][1]["wer_pct"] == 40.0   # 2 mots inversés = 2 substitutions sur 5 mots
    assert resultats["wer_global_pct"] == 22.2          # 2 erreurs sur 9 mots (« je m appelle grace » = 4 mots)
    assert resultats["details"][0]["duree_audio_s"] == 0.1


def test_calcul_du_mos():
    resultat = evaluer_voix.calculer_mos([4, 4, 3, 5, 4, "3", 9])    # 9 : hors échelle, ignoré
    assert resultat["mos"] == 3.83 and resultat["nombre_notes"] == 6
    bas, haut = resultat["intervalle_95"]
    assert bas < resultat["mos"] < haut
    with pytest.raises(ValueError):
        evaluer_voix.calculer_mos([0, 7])
