"""Évaluation de la voix VITS (MLOps) : WER et CER mesurés automatiquement.

Principe (« test aller-retour ») :
    phrase de référence ──► VITS (synthèse) ──► .wav ──► reconnaissance vocale (OpenAI) ──► phrase entendue
On compare la phrase entendue à la phrase de référence :
  - WER (Word Error Rate)      = (substitutions + suppressions + insertions de MOTS) / nombre de mots de référence
  - CER (Character Error Rate) = la même chose, caractère par caractère
0 % = parfaitement compris. Plus c'est bas, plus la voix est intelligible.
Le MOS (note humaine de 1 à 5) ne se calcule pas : voir api_service/METRIQUES_AUDIO.md.

Lancement (venv activé, depuis la racine du projet ; coûte moins d'un centime) :
    python -m api_service.evaluer_voix
    python -m api_service.evaluer_voix --dossier-audio docs/essais/mos   # garde les .wav pour le MOS
"""
import argparse
import json
import os
import re
import tempfile
import time
import unicodedata
import wave
from datetime import datetime, timezone

MODELE_TRANSCRIPTION = os.getenv("MODELE_TRANSCRIPTION", "gpt-4o-mini-transcribe")
FICHIER_RESULTATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluation_voix.json")

# Phrases typiques d'un pitch, choisies pour couvrir les difficultés : noms propres, lieux,
# sigles, mots anglais, nombres écrits en lettres, phrases longues.
PHRASES_TEST = [
    "Je m'appelle Grace Kabila Mukendi et j'habite à Kinshasa.",
    "J'ai obtenu une licence en gestion à l'Université de Kinshasa en deux mille vingt-quatre.",
    "J'ai travaillé comme assistante administrative à l'ONG Santé Pour Tous pendant trois ans.",
    "Je maîtrise Excel, Word et la gestion de caisse.",
    "J'ai organisé douze ateliers de formation pour les agents de santé communautaires.",
    "Je parle français, lingala et anglais à un niveau intermédiaire.",
    "Je recherche un poste d'assistante de programme dans une organisation humanitaire.",
    "Je suis développeur Python et j'ai construit une application web avec Flask.",
]


# ---------- Normalisation : on compare le sens, pas la typographie ----------

def chiffres_en_lettres(texte):
    """« 2024 » -> « deux mille vingt-quatre » (la reconnaissance vocale écrit souvent les nombres en chiffres)."""
    try:
        from num2words import num2words  # installé avec Coqui TTS
    except ImportError:
        return texte
    return re.sub(r"\d+", lambda m: num2words(int(m.group()), lang="fr"), texte)


def normaliser(texte):
    """Minuscules, sans accents ni ponctuation, nombres en lettres, espaces uniques.
    Ainsi « Université » / « universite » ou « 2024 » / « deux mille vingt-quatre » ne comptent pas comme des erreurs."""
    texte = chiffres_en_lettres(texte or "").lower()
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = re.sub(r"[^a-z0-9]+", " ", texte)     # apostrophes, tirets, ponctuation -> espace
    return re.sub(r"\s+", " ", texte).strip()


# ---------- Distance d'édition (Levenshtein) ----------

def distance_edition(reference, hypothese):
    """Nombre minimal de substitutions, suppressions et insertions pour passer de l'une à l'autre.
    Fonctionne sur des listes de mots (WER) comme sur des chaînes de caractères (CER)."""
    precedente = list(range(len(hypothese) + 1))
    for i, element_ref in enumerate(reference, start=1):
        courante = [i]
        for j, element_hyp in enumerate(hypothese, start=1):
            courante.append(min(
                precedente[j] + 1,                                   # suppression
                courante[j - 1] + 1,                                 # insertion
                precedente[j - 1] + (element_ref != element_hyp),    # substitution (0 si identique)
            ))
        precedente = courante
    return precedente[-1]


def wer(reference, hypothese):
    mots_ref, mots_hyp = normaliser(reference).split(), normaliser(hypothese).split()
    return distance_edition(mots_ref, mots_hyp) / len(mots_ref) if mots_ref else 0.0


def cer(reference, hypothese):
    car_ref, car_hyp = normaliser(reference), normaliser(hypothese)
    return distance_edition(car_ref, car_hyp) / len(car_ref) if car_ref else 0.0


# ---------- Reconnaissance vocale ----------

def transcrire_openai(chemin_wav):
    """Fait « écouter » le .wav par un modèle de reconnaissance vocale et renvoie le texte entendu."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=60)
    with open(chemin_wav, "rb") as fichier:
        reponse = client.audio.transcriptions.create(model=MODELE_TRANSCRIPTION, file=fichier, language="fr")
    return reponse.text


def duree_wav(chemin):
    with wave.open(chemin) as fichier:
        return fichier.getnframes() / fichier.getframerate()


# ---------- Évaluation complète ----------

def evaluer(moteur, transcrire, phrases, dossier_audio):
    """Synthétise chaque phrase, la fait transcrire, et calcule WER / CER par phrase et au global."""
    os.makedirs(dossier_audio, exist_ok=True)
    details, erreurs_mots, total_mots, erreurs_car, total_car = [], 0, 0, 0, 0
    for numero, phrase in enumerate(phrases, start=1):
        chemin = os.path.join(dossier_audio, f"phrase_{numero:02d}.wav")
        debut = time.perf_counter()
        moteur.synthetiser(phrase, chemin)
        duree_synthese = time.perf_counter() - debut
        entendu = transcrire(chemin)

        mots_ref, mots_hyp = normaliser(phrase).split(), normaliser(entendu).split()
        car_ref, car_hyp = normaliser(phrase), normaliser(entendu)
        d_mots, d_car = distance_edition(mots_ref, mots_hyp), distance_edition(car_ref, car_hyp)
        erreurs_mots, total_mots = erreurs_mots + d_mots, total_mots + len(mots_ref)
        erreurs_car, total_car = erreurs_car + d_car, total_car + len(car_ref)
        details.append({
            "numero": numero, "reference": phrase, "entendu": entendu,
            "wer_pct": round(100 * d_mots / max(len(mots_ref), 1), 1),
            "cer_pct": round(100 * d_car / max(len(car_ref), 1), 1),
            "duree_audio_s": round(duree_wav(chemin), 2),
            "duree_synthese_s": round(duree_synthese, 2),
        })
    # Global : on additionne les erreurs de toutes les phrases (une longue phrase pèse plus qu'une courte)
    return {
        "wer_global_pct": round(100 * erreurs_mots / max(total_mots, 1), 1),
        "cer_global_pct": round(100 * erreurs_car / max(total_car, 1), 1),
        "nombre_phrases": len(phrases),
        "details": details,
    }


# ---------- MOS : moyenne des notes humaines ----------

def calculer_mos(notes):
    """MOS = moyenne des notes de 1 à 5, avec l'intervalle de confiance à 95 %
    (plus il y a d'auditeurs et de phrases, plus l'intervalle est étroit, donc plus le MOS est fiable)."""
    notes = [float(n) for n in notes if 1 <= float(n) <= 5]
    if not notes:
        raise ValueError("Aucune note valide (les notes vont de 1 à 5).")
    moyenne = sum(notes) / len(notes)
    if len(notes) > 1:
        ecart_type = (sum((n - moyenne) ** 2 for n in notes) / (len(notes) - 1)) ** 0.5
        marge = 1.96 * ecart_type / len(notes) ** 0.5
    else:
        ecart_type = marge = 0.0
    return {"mos": round(moyenne, 2), "ecart_type": round(ecart_type, 2),
            "intervalle_95": [round(moyenne - marge, 2), round(moyenne + marge, 2)], "nombre_notes": len(notes)}


def lire_notes_mos(chemin_csv):
    """CSV avec une colonne « note » (une ligne par auditeur et par phrase)."""
    import csv
    with open(chemin_csv, encoding="utf-8", newline="") as fichier:
        return [ligne["note"] for ligne in csv.DictReader(fichier) if ligne.get("note", "").strip()]


def main():
    parser = argparse.ArgumentParser(description="Mesure le WER et le CER de la voix VITS de l'API.")
    parser.add_argument("--dossier-audio", help="garde les .wav dans ce dossier (pour le test MOS)")
    parser.add_argument("--sortie", default=FICHIER_RESULTATS, help="fichier JSON des résultats")
    parser.add_argument("--mos", metavar="NOTES.csv", help="calcule seulement le MOS à partir des notes des auditeurs")
    args = parser.parse_args()

    if args.mos:
        try:
            notes = lire_notes_mos(args.mos)
            print(json.dumps(calculer_mos(notes), ensure_ascii=False, indent=2))
        except FileNotFoundError:
            parser.exit(1, f"Fichier introuvable : {args.mos}\nCréez d'abord le CSV des notes des auditeurs "
                           "(colonnes auditeur,phrase,note) : voir api_service/METRIQUES_AUDIO.md\n")
        except (KeyError, ValueError) as erreur:
            parser.exit(1, f"Notes invalides ({erreur}) : il faut une colonne « note » avec des valeurs de 1 à 5.\n")
        return

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    from .services.tts_engine import moteur

    dossier = args.dossier_audio or tempfile.mkdtemp(prefix="cvboost_eval_")
    print(f"Synthèse de {len(PHRASES_TEST)} phrases avec {moteur.nom_modele}, transcription par {MODELE_TRANSCRIPTION}...")
    resultats = evaluer(moteur, transcrire_openai, PHRASES_TEST, dossier)
    resultats.update({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "modele_vocal": moteur.nom_modele,
        "modele_transcription": MODELE_TRANSCRIPTION,
    })
    for d in resultats["details"]:
        print(f"  {d['numero']:>2}. WER {d['wer_pct']:5.1f} %  CER {d['cer_pct']:5.1f} %  | entendu : {d['entendu']}")
    print(f"WER global : {resultats['wer_global_pct']} %   CER global : {resultats['cer_global_pct']} %")

    with open(args.sortie, "w", encoding="utf-8") as fichier:
        json.dump(resultats, fichier, ensure_ascii=False, indent=2)
    print(f"Résultats enregistrés dans {args.sortie}")
    if args.dossier_audio:
        print(f"Fichiers audio gardés dans {dossier} (à faire écouter pour le MOS)")
    else:
        import shutil
        shutil.rmtree(dossier, ignore_errors=True)


if __name__ == "__main__":
    main()
