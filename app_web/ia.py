"""Assistant DRH intelligent (Sprint 3) : connecteur OpenAI + prompt engineering.

Deux services :
  1. classer_offres()  : compare UN CV à TOUTES les offres scrapées -> score (%) + raison pour chacune
                         (un seul appel à OpenAI pour toutes les offres)
  2. analyser_offre()  : analyse détaillée CV / une offre -> résumé du profil, score, points forts,
                         compétences manquantes, suggestions honnêtes pour améliorer le CV

Sans clé OPENAI_API_KEY (ou dans les tests), un MODE DÉMO calcule un score simple par
mots-clés communs : l'application reste utilisable gratuitement, et la page indique
toujours quel mode a produit l'analyse.

Les réponses sont mises en cache dans la table `analyses` (voir obtenir_ou_calculer).
"""
import csv
import hashlib
import json
import os
import re
import unicodedata

from flask import current_app

from . import db
from .models import Analyse

PROMPT_VERSION = "v3"  # à incrémenter dès qu'on modifie un prompt : l'ancien cache est alors ignoré
TAILLE_DESCRIPTION_CLASSEMENT = 700   # caractères par offre envoyés pour le classement
TAILLE_DESCRIPTION_DETAIL = 6000      # caractères envoyés pour l'analyse détaillée


class IAIndisponible(Exception):
    """OpenAI ne répond pas (clé invalide, plus de crédit, réseau...)."""


# ---------- Offres (préparées au Sprint 2) ----------

def charger_offres(chemin=None):
    """Lit data/processed/jobs_clean.csv -> liste de dicts prêts à afficher et à envoyer au LLM."""
    chemin = chemin or current_app.config["OFFRES_CSV"]
    offres = []
    with open(chemin, encoding="utf-8", newline="") as f:
        for ligne in csv.DictReader(f):
            offres.append({
                "source": ligne.get("source") or "mediacongo",
                "reference": ligne["reference"],
                "intitule": ligne["intitule"],
                "entreprise": ligne["entreprise"],
                "lieu": ligne["lieu"],
                "province": ligne.get("province", ""),
                "date_limite": ligne.get("date_limite", ""),
                "description": ligne.get("description", ""),
                "description_disponible": ligne.get("description_disponible") == "True",
                "url": ligne.get("url", ""),
                "competences": [
                    col.removeprefix("comp_").replace("_", " ")
                    for col, val in ligne.items() if col.startswith("comp_") and val == "1"
                ],
            })
    return offres


def trouver_offre(reference, offres):
    return next((o for o in offres if o["reference"] == reference), None)


# ---------- Outils ----------

def mode_actuel():
    return "openai" if current_app.config.get("OPENAI_API_KEY") else "demo"


def empreinte(*parties):
    """SHA-256 de tout ce qui influence la réponse : si une partie change, l'empreinte change."""
    contenu = "\x1f".join(str(p) for p in parties)
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def borner_score(valeur):
    try:
        return max(0, min(100, int(round(float(valeur)))))
    except (TypeError, ValueError):
        return 0


def liste_de_textes(valeur, maximum=6, longueur=300):
    """Garde au plus `maximum` éléments texte non vides, chacun coupé à `longueur` caractères."""
    if not isinstance(valeur, list):
        return []
    return [str(v).strip()[:longueur] for v in valeur if str(v).strip()][:maximum]


# ---------- Appel à OpenAI ----------

PROMPT_SYSTEME = """Tu es un recruteur RH expérimenté qui travaille en République Démocratique du Congo.
Tu évalues des candidatures de façon juste, précise et bienveillante.

Règles strictes :
- Base-toi UNIQUEMENT sur le texte du CV et de l'offre fournis. N'invente jamais d'expérience,
  de diplôme, de compétence ou de chiffre absent du CV.
- Barème du score de compatibilité (0 à 100) : expérience pertinente 40 points,
  compétences techniques 30, formation 15, langues et savoir-être 15.
  Additionne réellement ces points pour chaque offre : utilise toute l'échelle et
  différencie les offres entre elles (deux offres différentes ont rarement le même score).
  Compte aussi les compétences TRANSFÉRABLES (outils, gestion, langues, rédaction...).
- Si une offre n'a pas de description (annonce publiée en image), juge sur le titre,
  l'entreprise et le lieu, et reste prudent (score modéré).
- Réponds en français, uniquement avec un objet JSON valide, sans texte autour."""


def appeler_llm(message_utilisateur):
    """Envoie le prompt à OpenAI et renvoie (dict JSON, nombre de tokens consommés)."""
    from openai import OpenAI, OpenAIError  # import ici : inutile en mode démo

    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"], timeout=60)
    try:
        reponse = client.chat.completions.create(
            model=current_app.config["OPENAI_MODEL"],
            messages=[
                {"role": "system", "content": PROMPT_SYSTEME},
                {"role": "user", "content": message_utilisateur},
            ],
            response_format={"type": "json_object"},  # oblige le modèle à répondre en JSON
            temperature=0.2,                           # réponses stables et peu « créatives »
        )
        contenu = reponse.choices[0].message.content
        tokens = reponse.usage.total_tokens if reponse.usage else 0
        return json.loads(contenu), tokens
    except OpenAIError as erreur:
        current_app.logger.error("Erreur OpenAI : %s", erreur)
        raise IAIndisponible("Le service d'IA est momentanément indisponible. Réessayez plus tard.") from erreur
    except (json.JSONDecodeError, IndexError, AttributeError) as erreur:
        raise IAIndisponible("La réponse de l'IA était inutilisable. Réessayez.") from erreur


# ---------- 1. Classement de toutes les offres ----------

def classer_offres(texte_cv, offres):
    """Renvoie (classement trié, tokens, mode). Classement : [{reference, score, raison}, ...]."""
    if mode_actuel() == "demo":
        classement = [
            {"reference": o["reference"], "score": score_demo(texte_cv, o),
             "raison": "Mode démo : part des mots-clés de l'offre présents dans le CV."}
            for o in offres
        ]
        return sorted(classement, key=lambda c: c["score"], reverse=True), 0, "demo"

    lignes_offres = "\n\n".join(
        f"[{o['reference']}] {o['intitule']} — {o['entreprise']} ({o['lieu']})\n"
        + (o["description"][:TAILLE_DESCRIPTION_CLASSEMENT] if o["description_disponible"]
           else "(annonce sans description : juger sur le titre)")
        for o in offres
    )
    message = (
        "Voici le CV d'un candidat, puis une liste d'offres d'emploi.\n"
        "Donne pour CHAQUE offre un score de compatibilité (0-100) et une raison courte (une phrase).\n"
        'Format attendu : {"classement": [{"reference": "OEM...", "score": 0, "raison": "..."}]}\n\n'
        f"=== CV ===\n{texte_cv}\n\n=== OFFRES ===\n{lignes_offres}"
    )
    donnees, tokens = appeler_llm(message)

    # Validation : on ne garde que les références connues, scores bornés, raisons raccourcies
    connues = {o["reference"] for o in offres}
    resultats = {}
    for item in donnees.get("classement", []) if isinstance(donnees, dict) else []:
        if isinstance(item, dict) and item.get("reference") in connues:
            resultats[item["reference"]] = {
                "reference": item["reference"],
                "score": borner_score(item.get("score")),
                "raison": str(item.get("raison", "")).strip()[:300],
            }
    # Offre oubliée par le LLM : on l'affiche quand même, sans l'inventer
    for o in offres:
        resultats.setdefault(o["reference"], {
            "reference": o["reference"], "score": 0, "raison": "Offre non évaluée par l'IA.",
        })
    classement = sorted(resultats.values(), key=lambda c: c["score"], reverse=True)
    return classement, tokens, "openai"


# ---------- 2. Analyse détaillée CV / une offre ----------

def analyser_offre(texte_cv, offre):
    """Renvoie (analyse, tokens, mode). Analyse : dict validé (voir valider_analyse)."""
    if mode_actuel() == "demo":
        return analyse_demo(texte_cv, offre), 0, "demo"

    description = (offre["description"][:TAILLE_DESCRIPTION_DETAIL] if offre["description_disponible"]
                   else "(annonce publiée en image : pas de description disponible)")
    message = (
        "Analyse la compatibilité de ce CV avec cette offre d'emploi.\n"
        "Pour les suggestions : propose 3 à 5 modifications CONCRÈTES du CV pour mieux correspondre à "
        "CETTE offre (reformuler, mettre en avant, chiffrer, utiliser les mots-clés de l'offre, réorganiser). "
        "Chaque suggestion doit s'appuyer sur un élément PRÉCIS déjà présent dans le CV, que tu cites. "
        "Ne conseille jamais d'ajouter ou de suggérer une expérience, une compétence ou un diplôme "
        "absent du CV : pour une compétence manquante, conseille seulement de l'acquérir (formation, "
        "certification) ou de la mentionner si le candidat la possède vraiment.\n"
        "Format attendu :\n"
        '{"resume_profil": "3 phrases maximum", "score": 0, '
        '"points_forts": ["..."], "competences_manquantes": ["..."], '
        '"suggestions_cv": [{"section": "ex : Expérience", "conseil": "..."}]}\n\n'
        f"=== CV ===\n{texte_cv}\n\n"
        f"=== OFFRE [{offre['reference']}] ===\n{offre['intitule']} — {offre['entreprise']} "
        f"({offre['lieu']})\n{description}"
    )
    donnees, tokens = appeler_llm(message)
    return valider_analyse(donnees), tokens, "openai"


def valider_analyse(donnees):
    """Ne fait jamais confiance au LLM : types, bornes et longueurs vérifiés."""
    donnees = donnees if isinstance(donnees, dict) else {}
    suggestions = []
    for s in donnees.get("suggestions_cv", []) if isinstance(donnees.get("suggestions_cv"), list) else []:
        if isinstance(s, dict) and str(s.get("conseil", "")).strip():
            suggestions.append({
                "section": str(s.get("section", "Général")).strip()[:60] or "Général",
                "conseil": str(s["conseil"]).strip()[:500],
            })
    return {
        "resume_profil": str(donnees.get("resume_profil", "")).strip()[:800],
        "score": borner_score(donnees.get("score")),
        "points_forts": liste_de_textes(donnees.get("points_forts")),
        "competences_manquantes": liste_de_textes(donnees.get("competences_manquantes")),
        "suggestions_cv": suggestions[:6],
    }


# ---------- Mode démo (sans OpenAI) ----------

MOTS_VIDES = set("""
a au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma mais me meme mes moi mon
ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre
vous est sont etre avoir sera plus tres tout tous toute toutes cette dont leurs ainsi afin aussi comme
the and for with you your our are will this that from have has job poste offre candidat profil
""".split())


def mots_cles(texte):
    texte = unicodedata.normalize("NFKD", texte.lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return {m for m in re.findall(r"[a-z0-9+#]{3,}", texte) if m not in MOTS_VIDES}


def score_demo(texte_cv, offre):
    mots_offre = mots_cles(offre["intitule"] + " " + offre["description"] + " " + " ".join(offre["competences"]))
    if not mots_offre:
        return 0
    communs = mots_offre & mots_cles(texte_cv)
    part = len(communs) / len(mots_offre)
    if offre.get("description_disponible", True):
        score = 100 * part * 2  # x2 : une longue description n'est jamais couverte à 100 % par un CV
    else:
        score = 60 * part  # annonce en image : on ne connaît que le titre -> 60 % au maximum (prudence)
    return borner_score(score)


def analyse_demo(texte_cv, offre):
    mots_cv = mots_cles(texte_cv)
    competences = offre["competences"]
    presentes = [c for c in competences if mots_cles(c) <= mots_cv]
    manquantes = [c for c in competences if c not in presentes]
    return {
        "resume_profil": "Mode démo : aucune clé OpenAI configurée. Le score compte les mots-clés "
                         "de l'offre retrouvés dans votre CV.",
        "score": score_demo(texte_cv, offre),
        "points_forts": [f"Compétence retrouvée : {c}" for c in presentes],
        "competences_manquantes": manquantes,
        "suggestions_cv": [{"section": "Compétences",
                            "conseil": f"Si vous maîtrisez « {c} », mentionnez-le explicitement."}
                           for c in manquantes[:3]],
    }


# ---------- Cache des analyses ----------

def chercher_en_cache(document_id, type_analyse, reference, cle):
    return Analyse.query.filter_by(
        document_id=document_id, type=type_analyse, offre_reference=reference, empreinte=cle
    ).order_by(Analyse.cree_le.desc()).first()


def enregistrer(document, type_analyse, reference, cle, resultat, tokens, mode):
    analyse = Analyse(
        user_id=document.user_id, document_id=document.id, type=type_analyse,
        offre_reference=reference, empreinte=cle, resultat=json.dumps(resultat, ensure_ascii=False),
        mode=mode, modele=current_app.config["OPENAI_MODEL"] if mode == "openai" else None, tokens=tokens,
    )
    db.session.add(analyse)
    db.session.commit()
    return analyse


def cle_classement(texte_cv, offres):
    signature_offres = [(o["reference"], o["intitule"], o["description"]) for o in offres]
    return empreinte("classement", PROMPT_VERSION, mode_actuel(), current_app.config["OPENAI_MODEL"],
                     texte_cv, signature_offres)


def cle_detail(texte_cv, offre):
    return empreinte("detail", PROMPT_VERSION, mode_actuel(), current_app.config["OPENAI_MODEL"],
                     texte_cv, offre["reference"], offre["intitule"], offre["description"])


# ---------- 3. Simulation d'entretien VOCAL (temps réel) ----------
#
# Même principe que l'ancienne version (dont la voix et le rythme étaient plus naturels) :
# 1. Le navigateur prépare une connexion audio WebRTC (micro + haut-parleur) et envoie son « offre SDP »
#    au site Flask.
# 2. Le site la transmet à l'API « live » d'OpenAI avec la consigne de la recruteuse (CV + offre) :
#    la clé OpenAI reste sur le serveur. OpenAI répond par un « SDP » que le navigateur utilise pour se
#    connecter directement : micro -> recruteuse IA -> haut-parleur.
# 3. À la fin (6 questions posées, bouton, ou limite de sécurité de 10 min), le navigateur renvoie la transcription ; le serveur demande
#    un bilan noté sur 100 au LLM.

URL_SESSION_VOCALE = "https://api.openai.com/v1/live/sessions"
MODELE_VOCAL = os.getenv("OPENAI_MODELE_VOCAL", "gpt-live-1")            # conversation vocale naturelle
MODELE_ASSISTANT = os.getenv("OPENAI_MODELE_ASSISTANT", "gpt-5.6-terra")  # réfléchit aux questions
VOIX = os.getenv("VOIX_ENTRETIEN", "")  # vide = voix par défaut du modèle (celle de l'ancienne version)
# L'entretien se compte en QUESTIONS (comme un vrai premier entretien RH), pas en minutes :
# Amina pose ces 6 questions dans l'ordre, puis conclut ; l'entretien se termine alors tout seul.
QUESTIONS_ENTRETIEN = [
    "Présentez-vous en quelques minutes.",
    "Pourquoi ce poste et notre entreprise vous intéressent-ils ?",
    "Parlez-moi d'une réalisation concrète de votre parcours : quel était votre rôle exact et quel "
    "a été le résultat ? (choisis-la à partir du CV)",
    "Racontez une situation difficile que vous avez vécue au travail ou pendant vos études, "
    "et comment vous l'avez gérée.",
    "Une mise en situation liée aux missions du poste (invente un cas réaliste à partir de l'offre) : "
    "comment vous y prendriez-vous ?",
    "Avez-vous des questions à nous poser ?",
]
NB_QUESTIONS = len(QUESTIONS_ENTRETIEN)
# Limite de sécurité discrète (non affichée) : coupe un entretien oublié, car chaque minute de voix coûte
DUREE_MAX_SECONDES = 10 * 60
LIMITE_PAR_HEURE = 6                    # entretiens par utilisateur et par heure (chaque minute coûte)
MIN_REPONSES_BILAN = 1                  # en dessous : pas de bilan du tout (rien n'a été entendu)
# Pour recevoir une NOTE, l'entretien doit être assez long : on ne juge pas quelqu'un sur une présentation.
# En dessous, le bilan donne un retour et des conseils, mais « Non noté ».
MIN_REPONSES_NOTE = 3                   # au moins 3 réponses sur les 6 questions
MIN_CRITERES_EVALUES = 2                # une note calculée sur un seul critère ne voudrait rien dire

CONSIGNE_RECRUTEUSE = """Tu es Amina, assistante DRH chez {entreprise}, bienveillante mais exigeante.
Tu reçois le candidat pour un premier entretien d'embauche au poste ci-dessous, à voix haute, en
français. Tu représentes {entreprise} : présente-toi toujours comme assistante DRH de
{entreprise} (jamais comme une recruteuse « à Kinshasa », ni comme une intelligence artificielle).

Poste visé : {poste}

{contexte}

L'entretien compte EXACTEMENT {nb_questions} questions, à poser dans cet ordre (reformule-les à l'oral
avec tes mots, en les adaptant au CV et au poste) :
{questions}

Déroulé :
- commence par dire bonjour et te présenter (Amina, assistante DRH chez {entreprise}, le poste
  pour lequel tu reçois le candidat), puis pose la question 1 ;
- une seule question à la fois ; ne coupe jamais le candidat au milieu d'une phrase ;
- n'ajoute AUCUNE autre question et ne pose pas de sous-question : chaque réponse du candidat te fait
  passer à la question suivante (seule exception : s'il n'a pas été entendu, demande-lui de répéter) ;
- après la réponse à la dernière question, réponds en une phrase à ses éventuelles questions, remercie-le,
  puis termine OBLIGATOIREMENT par cette phrase exacte :
  « L'entretien est terminé, vous allez recevoir votre bilan. »
  Ensuite, ne dis plus rien et ne pose plus aucune question (la conversation sera coupée) ;
- DÈS QUE LE CANDIDAT A FINI SA RÉPONSE ET MARQUE UNE PAUSE, RÉAGIS TOUT DE SUITE : ne reste jamais
  silencieuse après une réponse, n'attends pas qu'il te relance (il ne doit jamais avoir à dire « allô ») ;
- garde un ton oral naturel et des tours de parole courts (comme dans l'ancienne version, préférée à l'écoute) ;
- avant d'enchaîner, montre que tu as écouté : reprends en quelques mots un élément de sa réponse,
  puis pose la question suivante ;
- ne présume jamais qu'une expérience est vraie si elle n'apparaît pas au CV ;
- vouvoie toujours le candidat (« vous »), jamais de tutoiement ;
- ne donne pas de note pendant l'entretien.
Tu parles UNIQUEMENT en français, même si le candidat utilise une autre langue."""

# Ouverture : sans cela, le modèle attend que le candidat parle. Méthode documentée par OpenAI
# (« Greet before the caller speaks ») : un message développeur à la création de la session,
# puis la même consigne envoyée par le navigateur (session.commentary.append) dès session.started.
def nom_entreprise(offre):
    """Nom de l'employeur tel qu'Amina le dira (repli si l'annonce ne le précise pas)."""
    nom = str(offre.get("entreprise") or "").strip()
    return nom if nom and nom.lower() not in ("non précisé", "non precise", "nan") else "l'entreprise qui recrute"


def consigne_ouverture(offre):
    return (f"Commence l'entretien maintenant, sans attendre que le candidat parle : dis bonjour, présente-toi "
            f"en une ou deux phrases (« Amina, assistante DRH chez {nom_entreprise(offre)} », et le poste "
            f"« {offre['intitule']} »), annonce que l'entretien compte {NB_QUESTIONS} questions, puis pose "
            f"la question 1 (le candidat se présente) en le vouvoyant, et tais-toi pour lui laisser la parole.")


# Relance : l'API « live » n'a pas de réglage de fin de tour. Si Amina reste silencieuse après une réponse,
# le navigateur lui envoie ce message (session.commentary.append) au bout de quelques secondes de silence.
CONSIGNE_RELANCE = ("Le candidat a terminé sa réponse. Réagis maintenant à voix haute, sans attendre : "
                    "reprends en quelques mots un élément de sa réponse, puis pose ta question suivante. "
                    "Ne reste pas silencieuse.")
DELAI_RELANCE_SECONDES = 3
# Envoyée par le navigateur quand le candidat a répondu à la dernière question
CONSIGNE_CONCLUSION = ("Le candidat a répondu à la dernière question. Ne pose plus aucune question : "
                       "réponds en une phrase s'il t'a posé une question, remercie-le, puis dis exactement : "
                       "« L'entretien est terminé, vous allez recevoir votre bilan. » Ensuite, tais-toi.")
# Le navigateur reconnaît cette phrase et coupe l'entretien dès que plus personne ne parle depuis ce délai
# (5 s : le texte arrive un peu avant la voix, on laisse Amina finir sa phrase)
DELAI_FIN_SECONDES = 5


def liste_questions():
    return "\n".join(f"{i}. {q}" for i, q in enumerate(QUESTIONS_ENTRETIEN, 1))

CONSIGNE_ASSISTANT = ("Aide la recruteuse à préparer des questions pertinentes à partir du CV et du poste. "
                      "Ne crée aucun fait absent du CV. Rédige toujours en français.")

CRITERES = {  # barème de la note sur 100 : clé -> (libellé, points maximum)
    "pertinence": ("Pertinence des réponses", 30),
    "exemples": ("Exemples concrets et résultats", 30),
    "clarte": ("Clarté et structure à l'oral", 20),
    "adequation": ("Adéquation avec le poste", 20),
}

CONSIGNE_BILAN = """Tu es une recruteuse RH expérimentée et rigoureuse. Poste visé : {poste}

Transcription de l'entretien vocal de simulation (durée : {duree}) :
{transcription}

Repères mesurés : {statistiques}

Évalue UNIQUEMENT les réponses du candidat, critère par critère, avec ce barème :
- pertinence (sur 30) : les réponses répondent-elles vraiment aux questions posées ?
- exemples (sur 30) : situations réelles, rôle exact, résultats chiffrés ou vérifiables ?
- clarte (sur 20) : réponses structurées, compréhensibles, ni trop courtes ni décousues ?
- adequation (sur 20) : le parcours et les compétences citées correspondent-ils au poste visé ?
Pour chaque critère, donne un nombre entier précis (pas seulement des multiples de 5), utilise toute l'échelle (0 = absent, maximum = excellent) et justifie en UNE phrase
qui s'appuie sur ce que le candidat a réellement dit. Une réponse vague, hors sujet ou très courte
fait baisser le critère concerné.
CRITÈRE NON ÉVALUABLE : si l'entretien n'a pas permis d'en juger (par exemple, aucune question ne
demandait d'exemple, ou le poste n'a pas encore été abordé), mets "evalue": false et "points": null,
avec une justification qui explique pourquoi. Ne pénalise JAMAIS le candidat pour une question
qui ne lui a pas été posée.
TRANSCRIPTION AUTOMATIQUE : le texte vient d'une reconnaissance vocale qui fait des erreurs, surtout
sur les noms propres, les sigles et les mots rares (un nom peut devenir des mots sans rapport). Un
passage incompréhensible ou absurde est très probablement une erreur de transcription : ne le
compte ni comme une réponse confuse ni comme un manque de clarté, et ne le cite pas. Juge la clarté
sur la structure et la longueur des réponses, pas sur l'orthographe ou les mots mal reconnus.
REMARQUES RELIÉES À CET ENTRETIEN (obligatoire, comme pour la note) :
- points_forts (1 à 3) : chacun contient une "citation" COPIÉE MOT POUR MOT d'une réponse du candidat
  (5 à 25 mots, sans la reformuler) et un "pourquoi" : ce que cette réponse montre pour le poste visé.
- a_ameliorer (1 à 3) : chacun contient la "question" de la recruteuse (copiée), la "reponse" du candidat
  (extrait COPIÉ MOT POUR MOT) et "mieux" : ce qu'une meilleure réponse aurait contenu POUR CE POSTE,
  de façon concrète, sans inventer de faits sur le parcours du candidat.
- conseils (3) : chacun rattaché à un "critere" (pertinence, exemples, clarte ou adequation), en priorité
  aux critères où tu as mis le moins de points, et aux exigences du poste visé. INTERDIT : un conseil
  passe-partout qui ne mentionne ni une réponse du candidat ni une exigence du poste (« faites des
  recherches sur l'entreprise », « soyez plus clair »...).
- Ne cite jamais un passage incompréhensible (erreur de transcription). Les citations sont vérifiées :
  une citation qui ne figure pas dans la transcription est supprimée.
Adresse-toi directement au candidat avec « vous » dans TOUTES les phrases (jamais « le candidat » ni « il »). Réponds en français, uniquement avec un objet JSON :
{{"criteres": {{"pertinence": {{"evalue": true, "points": 0, "justification": "..."}},
               "exemples": {{"evalue": true, "points": 0, "justification": "..."}},
               "clarte": {{"evalue": true, "points": 0, "justification": "..."}},
               "adequation": {{"evalue": true, "points": 0, "justification": "..."}}}},
"message": "une phrase brève qui résume l'entretien",
"deroulement": "rapport de 3 à 4 phrases : comment s'est passé l'entretien",
"points_forts": [{{"citation": "mots exacts du candidat", "pourquoi": "..."}}],
"a_ameliorer": [{{"question": "question de la recruteuse", "reponse": "mots exacts du candidat", "mieux": "..."}}],
"conseils": [{{"critere": "exemples", "conseil": "..."}}]}}"""


class EntretienRefuse(Exception):
    """Entretien impossible (pas de clé, limite atteinte...) : message à montrer tel quel."""


def creer_session_vocale(texte_cv, offre, sdp):
    """Transmet l'offre WebRTC du navigateur à OpenAI et renvoie la réponse SDP (str)."""
    import requests

    cle = current_app.config.get("OPENAI_API_KEY")
    if not cle:
        raise EntretienRefuse("L'entretien vocal nécessite une clé OpenAI (OPENAI_API_KEY dans .env).")
    if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > 20000:
        raise EntretienRefuse("Connexion audio invalide : rechargez la page et réessayez.")
    session = {
        "model": MODELE_VOCAL,
        "instructions": CONSIGNE_RECRUTEUSE.format(
            entreprise=nom_entreprise(offre),
            poste=f"{offre['intitule']} — {offre['entreprise']} ({offre['lieu']})",
            contexte=contexte_entretien(texte_cv, offre),
            nb_questions=NB_QUESTIONS, questions=liste_questions(),
        ),
        "input": [{"type": "message", "role": "developer",
                   "content": [{"type": "input_text", "text": consigne_ouverture(offre)}]}],
        "delegation": {"type": "responses",
                       "responses": {"model": MODELE_ASSISTANT, "instructions": CONSIGNE_ASSISTANT}},
    }
    if VOIX:
        session["audio"] = {"output": {"voice": VOIX}}
    try:
        reponse = requests.post(URL_SESSION_VOCALE, timeout=60, headers={"Authorization": f"Bearer {cle}"},
                                json={"session": session, "transport": {"type": "webrtc", "sdp": sdp}})
        resultat = reponse.json()
    except (requests.RequestException, ValueError) as erreur:
        current_app.logger.error("Session vocale OpenAI impossible : %s", erreur)
        raise IAIndisponible("Impossible de démarrer l'entretien vocal pour le moment. Réessayez plus tard.") from erreur
    if not reponse.ok:
        code = str((resultat.get("error") or {}).get("code") or "")
        current_app.logger.error("Session vocale refusée (%s) : %s", reponse.status_code, resultat.get("error"))
        if code == "insufficient_quota":
            raise IAIndisponible("Crédit OpenAI épuisé : rechargez le compte OpenAI.")
        raise IAIndisponible("La recruteuse vocale est indisponible pour le moment. Réessayez dans une minute.")
    sdp_reponse = (resultat.get("transport") or {}).get("sdp")
    if not sdp_reponse:
        raise IAIndisponible("Réponse vocale incomplète d'OpenAI. Réessayez.")
    return sdp_reponse


def contexte_entretien(texte_cv, offre):
    description = (offre["description"][:TAILLE_DESCRIPTION_DETAIL] if offre["description_disponible"]
                   else "(annonce publiée en image : pas de description disponible)")
    return (f"CV du candidat :\n{texte_cv}\n\nOffre [{offre['reference']}] : {offre['intitule']} — "
            f"{offre['entreprise']} ({offre['lieu']})\n{description}")


def nettoyer_transcription(echanges):
    """Ne garde que des tours de parole valides : [{"role": "recruteuse"|"candidat", "texte": "..."}]."""
    propres = []
    for e in echanges if isinstance(echanges, list) else []:
        if isinstance(e, dict) and e.get("role") in ("recruteuse", "candidat"):
            texte = str(e.get("texte", "")).strip()[:3000]
            if texte:
                propres.append({"role": e["role"], "texte": texte})
    return propres[:200]


def mention(note):
    """Cotation lisible à partir de la note sur 100."""
    if note >= 85:
        return "Excellent"
    if note >= 70:
        return "Très bien"
    if note >= 55:
        return "Bien"
    if note >= 40:
        return "Passable"
    return "À retravailler"


def statistiques_entretien(echanges, duree_secondes):
    """Chiffres calculés à partir de la transcription (pas par le LLM : ils sont exacts)."""
    reponses = [e["texte"] for e in echanges if e["role"] == "candidat"]
    questions = [e["texte"] for e in echanges if e["role"] == "recruteuse"]
    mots_candidat = sum(len(t.split()) for t in reponses)
    mots_total = mots_candidat + sum(len(t.split()) for t in questions)
    minutes = max(duree_secondes, 1) / 60
    return {
        "duree_secondes": int(duree_secondes),
        "interventions_recruteuse": len(questions),
        "reponses_candidat": len(reponses),
        "mots_prononces": mots_candidat,
        "mots_par_reponse": round(mots_candidat / len(reponses)) if reponses else 0,
        "part_de_parole_pct": round(100 * mots_candidat / mots_total) if mots_total else 0,
        "mots_par_minute": round(mots_candidat / minutes),
    }


def entretien_assez_long(stats):
    """Assez d'échanges pour juger le candidat ? (sinon : bilan « Non noté »)"""
    return stats["reponses_candidat"] >= MIN_REPONSES_NOTE


def lire_critere(detail, maximum):
    """Un critère renvoyé par le LLM -> (évalué ?, points bornés ou None)."""
    if detail.get("evalue") is False or detail.get("points") is None:
        return False, None
    try:
        points = int(round(float(detail.get("points"))))
    except (TypeError, ValueError):
        return False, None
    return True, max(0, min(points, maximum))


def bilan_entretien(offre, echanges, duree_secondes):
    """Bilan de l'entretien à partir de la transcription. Renvoie (bilan, tokens).

    La note sur 100 est calculée ICI, jamais par le LLM : c'est la somme des points des critères
    évalués, ramenée sur 100 (un critère que l'entretien n'a pas permis d'observer ne compte pas).
    Pas de note (None, « Non noté ») si l'entretien est trop court ou si moins de 2 critères
    ont pu être évalués."""
    stats = statistiques_entretien(echanges, duree_secondes)
    assez_long = entretien_assez_long(stats)
    transcription = "\n".join(
        f"{'Recruteuse' if e['role'] == 'recruteuse' else 'Candidat'} : {e['texte']}" for e in echanges
    )
    minutes, secondes = divmod(int(duree_secondes), 60)
    message = CONSIGNE_BILAN.format(
        poste=f"{offre['intitule']} — {offre['entreprise']}",
        duree=f"{minutes} min {secondes:02d} s", transcription=transcription[-20000:],
        statistiques=(f"{stats['reponses_candidat']} réponses, {stats['mots_prononces']} mots prononcés par le "
                      f"candidat, {stats['mots_par_reponse']} mots par réponse en moyenne, "
                      f"{stats['part_de_parole_pct']} % du temps de parole"
                      + ("" if assez_long else
                         ". ENTRETIEN TROP COURT POUR ÊTRE NOTÉ : ne parle pas de note, donne seulement "
                         "un retour bienveillant sur ce qui a été dit et des conseils")),
    )
    donnees, tokens = appeler_llm(message)
    donnees = donnees if isinstance(donnees, dict) else {}
    brut = donnees.get("criteres") if isinstance(donnees.get("criteres"), dict) else {}
    criteres = []
    for cle, (libelle, maximum) in CRITERES.items():
        detail = brut.get(cle) if isinstance(brut.get(cle), dict) else {}
        evalue, points = lire_critere(detail, maximum)
        criteres.append({
            "critere": libelle, "evalue": evalue, "points": points, "sur": maximum,
            "justification": str(detail.get("justification", "")).strip()[:300],
        })
    evalues = [c for c in criteres if c["evalue"]]
    if assez_long and len(evalues) >= MIN_CRITERES_EVALUES:
        # Somme des critères évalués, ramenée sur 100 : un critère non abordé ne pénalise pas
        note = round(100 * sum(c["points"] for c in evalues) / sum(c["sur"] for c in evalues))
        mention_note = mention(note)
    else:
        note, mention_note = None, "Non noté"
    if not assez_long:
        criteres = []   # trop court : un détail de note n'aurait pas de sens
    message = str(donnees.get("message", "")).strip()[:300]
    if note is None:
        raison = (f"Entretien trop court pour être noté ({min(stats['reponses_candidat'], NB_QUESTIONS)} "
                  f"réponse{'s' if stats['reponses_candidat'] > 1 else ''} sur {NB_QUESTIONS} questions ; "
                  f"il faut au moins {MIN_REPONSES_NOTE} réponses)."
                  if not assez_long else
                  "Pas assez d'éléments pour une note : la plupart des critères n'ont pas pu être observés.")
        message = f"{raison} {message}".strip()
    return {
        "note": note,
        "mention": mention_note,
        "criteres": criteres,
        "message": message,
        "deroulement": str(donnees.get("deroulement", "")).strip()[:1200],
        **remarques_reliees(donnees, echanges, criteres),
        "statistiques": stats,
    }, tokens


# ---------- Remarques du bilan reliées à l'entretien (citations vérifiées) ----------

def normaliser_pour_citation(texte):
    """Minuscules, sans accents ni ponctuation : « Vous avez dit… » et « vous avez dit » se comparent."""
    texte = unicodedata.normalize("NFKD", str(texte or "").lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texte).split())


def citation_verifiee(citation, texte_normalise, mots_min=3):
    """Vrai si la citation (au moins 3 mots) figure telle quelle dans le texte (après normalisation)."""
    c = normaliser_pour_citation(citation)
    return len(c.split()) >= mots_min and f" {c} " in f" {texte_normalise} "


def remarques_reliees(donnees, echanges, criteres):
    """Points forts, points à améliorer et conseils, gardés SEULEMENT s'ils sont reliés à l'entretien.

    - un point fort doit citer mot pour mot une réponse du candidat ;
    - un point à améliorer doit citer une réponse du candidat (et la question, si elle est retrouvée) ;
    - un conseil doit être rattaché à un critère du barème (on affiche ses points).
    Le LLM peut inventer une citation : le serveur la cherche dans la transcription, sinon il l'écarte."""
    candidat = normaliser_pour_citation(" ".join(e["texte"] for e in echanges if e["role"] == "candidat"))
    recruteuse = normaliser_pour_citation(" ".join(e["texte"] for e in echanges if e["role"] == "recruteuse"))
    liste = lambda cle: [x for x in donnees.get(cle, []) if isinstance(x, dict)] if isinstance(donnees.get(cle), list) else []
    court = lambda x, n=300: str(x or "").strip().strip("«»\"“” ")[:n]

    points_forts = []
    for item in liste("points_forts"):
        citation, pourquoi = court(item.get("citation")), court(item.get("pourquoi"))
        if pourquoi and citation_verifiee(citation, candidat):
            points_forts.append(f"« {citation} » : {pourquoi}")

    a_ameliorer = []
    for item in liste("a_ameliorer"):
        question, reponse, mieux = court(item.get("question")), court(item.get("reponse")), court(item.get("mieux"))
        if not mieux or not citation_verifiee(reponse, candidat, mots_min=2):
            continue
        if citation_verifiee(question, recruteuse):
            a_ameliorer.append(f"À la question « {question} », vous avez répondu « {reponse} ». {mieux}")
        else:
            a_ameliorer.append(f"Vous avez répondu « {reponse} ». {mieux}")

    par_cle = {cle: c for cle, c in zip(CRITERES, criteres)} if criteres else {}
    conseils = []
    for item in liste("conseils"):
        cle, conseil = str(item.get("critere", "")).strip().lower(), court(item.get("conseil"))
        if cle not in CRITERES or not conseil:
            continue
        c = par_cle.get(cle)
        etiquette = (f"{CRITERES[cle][0]} ({c['points']}/{c['sur']})" if c and c.get("evalue")
                     else CRITERES[cle][0])
        conseils.append(f"{etiquette} : {conseil}")
    if not conseils:
        # Aucun conseil exploitable : on s'appuie sur le critère le plus faible et sa justification (vraie note)
        evalues = [c for c in criteres if c.get("evalue") and c.get("justification")]
        if evalues:
            faible = min(evalues, key=lambda c: c["points"] / c["sur"])
            conseils.append(f"Priorité : {faible['critere']} ({faible['points']}/{faible['sur']}) : {faible['justification']}")

    return {"points_forts": points_forts[:3], "a_ameliorer": a_ameliorer[:3], "conseils": conseils[:3]}
