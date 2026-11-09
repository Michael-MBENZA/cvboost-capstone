"""Extraction du texte brut des CV téléversés (Sprint 3, Assistant DRH).

- PDF  : pypdf (exigé par l'énoncé)
- DOCX : python-docx (paragraphes ET tableaux : beaucoup de CV sont mis en page en tableaux)

Le texte est ensuite nettoyé (espaces, lignes vides) pour être envoyé proprement à OpenAI.
Un CV scanné (une image dans un PDF) ne contient pas de texte : on lève CVIllisible
avec un message clair au lieu d'envoyer un texte vide au LLM.
"""
import re

from docx import Document as DocumentWord
from pypdf import PdfReader
from pypdf.errors import PdfReadError

LONGUEUR_MIN = 50          # en dessous : CV vide ou scanné
LONGUEUR_MAX = 15_000      # au-delà : on tronque (coût OpenAI et limite de contexte)


class CVIllisible(Exception):
    """Le fichier ne contient pas de texte exploitable."""


def extraire_pdf(chemin):
    try:
        lecteur = PdfReader(chemin)
        pages = [page.extract_text() or "" for page in lecteur.pages]
    except (PdfReadError, ValueError, OSError) as erreur:
        raise CVIllisible("Le PDF est illisible ou endommagé.") from erreur
    return "\n".join(pages)


def extraire_docx(chemin):
    try:
        doc = DocumentWord(chemin)
    except Exception as erreur:  # python-docx lève des erreurs variées sur un fichier abîmé
        raise CVIllisible("Le fichier DOCX est illisible ou endommagé.") from erreur
    lignes = [p.text for p in doc.paragraphs]
    for tableau in doc.tables:
        for rangee in tableau.rows:
            cellules = [c.text.strip() for c in rangee.cells if c.text.strip()]
            # Une cellule fusionnée apparaît plusieurs fois : on retire les répétitions
            lignes.append(" | ".join(dict.fromkeys(cellules)))
    return "\n".join(lignes)


def nettoyer(texte):
    """Espaces multiples -> un seul ; plus de 2 lignes vides -> une ligne vide."""
    texte = texte.replace("\x00", "")
    lignes = [re.sub(r"[ \t ]+", " ", l).strip() for l in texte.splitlines()]
    texte = "\n".join(lignes)
    return re.sub(r"\n{3,}", "\n\n", texte).strip()


def extraire_texte(chemin, type_fichier):
    """Point d'entrée : chemin du CV + 'pdf' ou 'docx' -> texte propre.

    Lève CVIllisible si le texte est vide ou trop court (CV scanné, fichier abîmé).
    """
    if type_fichier == "pdf":
        brut = extraire_pdf(chemin)
    elif type_fichier == "docx":
        brut = extraire_docx(chemin)
    else:
        raise CVIllisible(f"Format non pris en charge : {type_fichier}")

    texte = nettoyer(brut)
    if len(texte) < LONGUEUR_MIN:
        raise CVIllisible(
            "Aucun texte n'a pu être lu dans ce CV. S'il s'agit d'un document scanné (image), "
            "déposez plutôt la version PDF ou Word d'origine."
        )
    return texte[:LONGUEUR_MAX]
