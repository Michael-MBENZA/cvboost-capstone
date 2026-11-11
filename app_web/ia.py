"""Assistant DRH intelligent (Sprint 3) : connecteur OpenAI + prompt engineering.

Deux services :
  1. classer_offres()  : compare UN CV à TOUTES les offres scrapées -> score (%) + raison pour chacune
                         (un seul appel à OpenAI pour toutes les offres)
  2. analyser_offre()  : analyse détaillée CV / une offre -> résumé du profil, score, points forts,
                         compétences manquantes, suggestions honnêtes pour améliorer le CV,
                         première question d'entretien

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

PROMPT_VERSION = "v2"  # à incrémenter dès qu'on modifie un prompt : l'ancien cache est alors ignoré
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
        '"suggestions_cv": [{"section": "ex : Expérience", "conseil": "..."}], '
        '"question_entretien": "une première question de recruteur adaptée à cette offre"}\n\n'
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
        "question_entretien": str(donnees.get("question_entretien", "")).strip()[:500],
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
        "question_entretien": f"Pourquoi souhaitez-vous rejoindre {offre['entreprise']} "
                              f"comme {offre['intitule']} ?",
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
# 3. À la fin (5 minutes ou bouton), le navigateur renvoie la transcription ; le serveur demande
#    un bilan noté sur 100 au LLM.

URL_SESSION_VOCALE = "https://api.openai.com/v1/live/sessions"
MODELE_VOCAL = os.getenv("OPENAI_MODELE_VOCAL", "gpt-live-1")            # conversation vocale naturelle
MODELE_ASSISTANT = os.getenv("OPENAI_MODELE_ASSISTANT", "gpt-5.6-terra")  # réfléchit aux questions
VOIX = os.getenv("VOIX_ENTRETIEN", "")  # vide = voix par défaut du modèle (celle de l'ancienne version)
DUREE_MAX_SECONDES = 5 * 60
LIMITE_PAR_HEURE = 6                    # entretiens par utilisateur et par heure (chaque minute coûte)
MIN_REPONSES_BILAN = 1

CONSIGNE_RECRUTEUSE = """Tu es Amina, recruteuse RH bienveillante mais exigeante, à Kinshasa (RD Congo).
Tu mènes à voix haute un entretien de simulation d'environ 5 minutes, en français.

Poste visé : {poste}

{contexte}

Déroulé :
- commence par dire bonjour et te présenter, puis invite le candidat à se présenter ;
- une seule question à la fois ; LAISSE AU CANDIDAT LE TEMPS DE RÉPONDRE : ne l'interromps jamais,
  attends qu'il ait vraiment fini (une hésitation ou un court silence ne veut pas dire qu'il a fini) ;
- parle posément, sans te presser, avec des phrases courtes ;
- avant d'enchaîner, montre que tu as écouté : reprends en quelques mots un élément de sa réponse,
  puis rebondis (rôle exact, réalisations, résultats chiffrés) ;
- si la réponse est floue ou si tu n'as pas bien entendu, demande poliment de préciser ou de répéter ;
- alterne motivation, parcours, compétences demandées par le poste et mises en situation ;
- ne présume jamais qu'une expérience est vraie si elle n'apparaît pas au CV : si l'information manque, demande ;
- vouvoie toujours le candidat (« vous »), jamais de tutoiement ;
- ne donne pas de note pendant l'entretien ; à la fin, remercie le candidat.
Tu parles UNIQUEMENT en français, même si le candidat utilise une autre langue."""

# Ouverture : sans cela, le modèle attend que le candidat parle. Méthode documentée par OpenAI
# (« Greet before the caller speaks ») : un message développeur à la création de la session,
# puis la même consigne envoyée par le navigateur (session.commentary.append) dès session.started.
CONSIGNE_OUVERTURE = ("Commence l'entretien maintenant, sans attendre que le candidat parle : dis bonjour, "
                      "présente-toi en une ou deux phrases (prénom, rôle), puis invite le candidat à se présenter "
                      "en le vouvoyant, et tais-toi pour lui laisser la parole.")

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
fait baisser le critère concerné. Ignore les petites erreurs de reconnaissance vocale.
Dans les points forts et les points à améliorer, cite entre guillemets de courts extraits des réponses
du candidat. Adresse-toi directement au candidat avec « vous » dans TOUTES les phrases (jamais « le candidat » ni « il »). Réponds en français, uniquement avec un objet JSON :
{{"criteres": {{"pertinence": {{"points": 0, "justification": "..."}},
               "exemples": {{"points": 0, "justification": "..."}},
               "clarte": {{"points": 0, "justification": "..."}},
               "adequation": {{"points": 0, "justification": "..."}}}},
"message": "une phrase brève qui résume l'entretien",
"deroulement": "rapport de 3 à 4 phrases : comment s'est passé l'entretien",
"points_forts": ["..."],
"a_ameliorer": ["..."],
"conseils": ["3 conseils concrets et actionnables pour les prochains entretiens"]}}"""


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
            poste=f"{offre['intitule']} — {offre['entreprise']} ({offre['lieu']})",
            contexte=contexte_entretien(texte_cv, offre),
        ),
        "input": [{"type": "message", "role": "developer",
                   "content": [{"type": "input_text", "text": CONSIGNE_OUVERTURE}]}],
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


def bilan_entretien(offre, echanges, duree_secondes):
    """Bilan noté sur 100 à partir de la transcription. Renvoie (bilan, tokens)."""
    stats = statistiques_entretien(echanges, duree_secondes)
    transcription = "\n".join(
        f"{'Recruteuse' if e['role'] == 'recruteuse' else 'Candidat'} : {e['texte']}" for e in echanges
    )
    minutes, secondes = divmod(int(duree_secondes), 60)
    message = CONSIGNE_BILAN.format(
        poste=f"{offre['intitule']} — {offre['entreprise']}",
        duree=f"{minutes} min {secondes:02d} s", transcription=transcription[-20000:],
        statistiques=(f"{stats['reponses_candidat']} réponses, {stats['mots_prononces']} mots prononcés par le "
                      f"candidat, {stats['mots_par_reponse']} mots par réponse en moyenne, "
                      f"{stats['part_de_parole_pct']} % du temps de parole"),
    )
    donnees, tokens = appeler_llm(message)
    donnees = donnees if isinstance(donnees, dict) else {}
    brut = donnees.get("criteres") if isinstance(donnees.get("criteres"), dict) else {}
    criteres = []
    for cle, (libelle, maximum) in CRITERES.items():
        detail = brut.get(cle) if isinstance(brut.get(cle), dict) else {}
        try:
            points = int(round(float(detail.get("points", 0))))
        except (TypeError, ValueError):
            points = 0
        criteres.append({
            "critere": libelle, "points": max(0, min(points, maximum)), "sur": maximum,
            "justification": str(detail.get("justification", "")).strip()[:300],
        })
    note = sum(c["points"] for c in criteres)  # la note est la SOMME des critères, calculée ici
    return {
        "note": note,
        "mention": mention(note),
        "criteres": criteres,
        "message": str(donnees.get("message", "")).strip()[:300],
        "deroulement": str(donnees.get("deroulement", "")).strip()[:1200],
        "points_forts": liste_de_textes(donnees.get("points_forts"), 4),
        "a_ameliorer": liste_de_textes(donnees.get("a_ameliorer"), 4),
        "conseils": liste_de_textes(donnees.get("conseils"), 4),
        "statistiques": stats,
    }, tokens
