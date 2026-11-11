"""Étape 1 de /speak-smart : reformuler le texte brut d'un CV en un pitch oral fluide.

Le LLM (OpenAI) reçoit un prompt STRICT (rôle, tâche, contraintes) : il ne doit rien inventer
et doit écrire un texte qu'une voix de synthèse peut lire sans buter (pas de puces, de symboles,
de sigles ni de chiffres). Le résultat est ensuite nettoyé par nettoyer_pour_la_voix(), parce
qu'on ne fait jamais entièrement confiance à un LLM.
"""
import os
import re
import unicodedata

PROMPT_VERSION = "v2"  # à incrémenter dès qu'on modifie le prompt (renvoyé dans l'en-tête X-Prompt-Version)
MODELE = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LONGUEUR_MAX_PITCH = 1500  # caractères : environ 1 minute de parole, la voix reste rapide à générer

PROMPT_SYSTEME = """RÔLE
Tu es un coach en recrutement qui aide des candidats de la République démocratique du Congo
à se présenter oralement devant un recruteur.

TÂCHE
À partir du texte brut d'un CV (extrait d'un PDF ou d'un Word, souvent désordonné),
écris le pitch oral que le candidat prononcera pour se présenter.

CONTRAINTES
- Utilise UNIQUEMENT les informations présentes dans le CV : n'invente aucun diplôme,
  employeur, poste, date, chiffre, compétence ou qualité.
- N'ajoute aucun jugement de valeur absent du CV (par exemple « solide », « excellent »,
  « significative »).
- Ne relie pas une compétence à un employeur si le CV ne fait pas lui-même ce lien.
- Respecte les dates du CV : une période qui a une date de fin (par exemple 2022-2025) est
  terminée et s'écrit au passé avec ses deux dates ; seul un poste marqué « depuis »,
  « à ce jour » ou « actuellement » s'écrit au présent.
- Écris en français, à la première personne (« Je m'appelle… », « J'ai travaillé… »).
- Entre 80 et 150 mots, en 4 à 6 phrases fluides, dans cet ordre :
  1. qui je suis (nom) ;
  2. ma formation : le diplôme ou la formation le plus récent, OBLIGATOIRE si le CV en contient un ;
  3. mon expérience principale ;
  4. mes compétences ;
  5. mes langues : TOUTES celles du CV, avec le niveau écrit dans le CV pour chacune ; si le CV
     n'indique pas de niveau, cite la langue sans niveau (n'écris pas « couramment ») ;
  6. ce que je recherche, seulement si le CV l'indique.
- Le texte sera lu par une voix de synthèse : pas de listes, de titres, d'emojis ni de symboles
  (|, @, •, #, /, &, %, +) ; écris les nombres et les années en toutes lettres ; remplace les
  sigles par leur forme développée quand le CV la donne, sinon épelle-les sans points.
- Ignore les coordonnées (téléphone, e-mail, adresse, liens) : elles ne se disent pas dans un pitch.
- Réponds uniquement avec le texte du pitch, sans introduction, sans guillemets ni commentaire."""

# Remplacements appliqués après le LLM : ce que la voix lirait mal ou pas du tout
REMPLACEMENTS = [
    (re.compile(r"https?://\S+|www\.\S+"), " "),                 # liens
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), " "),          # adresses e-mail
    (re.compile(r"(?:\+|\b00)\d{2,3}[\d .-]{6,}\d|\b0\d{8,9}\b"), " "),  # téléphones (+243..., 08...)
    (re.compile(r"[*_#`>|•●▪◦→]+"), " "),                        # Markdown et puces
    (re.compile(r"\s*&\s*"), " et "),
    (re.compile(r"\s*%"), " pour cent"),
    (re.compile(r"\s*\+\s*"), " plus "),
    (re.compile(r"\s*/\s*"), " "),
    (re.compile(r"[«»\"“”]"), ""),                               # guillemets
]


class ReformulationImpossible(Exception):
    """OpenAI ne répond pas (clé absente ou invalide, plus de crédit, réseau...) ou répond à vide."""


def nettoyer_pour_la_voix(texte):
    """Rend le pitch lisible par la voix de synthèse : sans symboles, emojis ni retours à la ligne."""
    texte = unicodedata.normalize("NFC", texte or "")
    for motif, remplacement in REMPLACEMENTS:
        texte = motif.sub(remplacement, texte)
    # Retire les emojis et pictogrammes (catégorie Unicode "So" = autres symboles)
    texte = "".join(c for c in texte if unicodedata.category(c) != "So")
    texte = re.sub(r"\s+", " ", texte).strip()
    texte = re.sub(r"\s+([,.])", r"\1", texte)          # pas d'espace avant une virgule ou un point
    texte = re.sub(r"([,.])(?:\s*[,.])+", r"\1", texte)  # ponctuation orpheline laissée par un retrait
    if len(texte) > LONGUEUR_MAX_PITCH:
        # On coupe à la fin de la dernière phrase complète pour ne pas couper un mot
        coupe = texte[:LONGUEUR_MAX_PITCH]
        fin = max(coupe.rfind("."), coupe.rfind("!"), coupe.rfind("?"))
        texte = coupe[: fin + 1] if fin > 0 else coupe
    return texte


def reformuler(texte_cv):
    """Envoie le CV au LLM et renvoie (pitch nettoyé, nombre de tokens consommés)."""
    from openai import OpenAI, OpenAIError  # import ici : les tests remplacent cette fonction

    cle = os.getenv("OPENAI_API_KEY", "")
    if not cle:
        raise ReformulationImpossible("La clé OPENAI_API_KEY est absente du fichier .env.")

    client = OpenAI(api_key=cle, timeout=60)
    try:
        reponse = client.chat.completions.create(
            model=MODELE,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEME},
                {"role": "user", "content": f"Texte brut du CV :\n\n{texte_cv}"},
            ],
            temperature=0.3,   # un peu de fluidité, mais pas de créativité
            max_tokens=500,
        )
        contenu = reponse.choices[0].message.content
        tokens = reponse.usage.total_tokens if reponse.usage else 0
    except OpenAIError as erreur:
        raise ReformulationImpossible(f"Erreur OpenAI : {erreur.__class__.__name__}") from erreur
    except (IndexError, AttributeError) as erreur:
        raise ReformulationImpossible("La réponse d'OpenAI était inutilisable.") from erreur

    pitch = nettoyer_pour_la_voix(contenu)
    if len(pitch) < 20:
        raise ReformulationImpossible("OpenAI a renvoyé un pitch vide.")
    return pitch, tokens
