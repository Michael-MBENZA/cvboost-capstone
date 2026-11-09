"""Tests du Sprint 3 : Assistant DRH (extraction du texte des CV)."""
import pytest
from docx import Document as DocumentWord

from app_web.extraction import CVIllisible, extraire_texte, nettoyer


def creer_pdf(chemin, lignes):
    """Fabrique un vrai petit PDF contenant du texte (sans bibliothèque externe)."""
    contenu = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lignes) + " ET"
    objets = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(contenu)} >>\nstream\n{contenu}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    sortie, positions = "%PDF-1.4\n", []
    for numero, objet in enumerate(objets, start=1):
        positions.append(len(sortie))
        sortie += f"{numero} 0 obj\n{objet}\nendobj\n"
    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n"
    sortie += "".join(f"{p:010d} 00000 n \n" for p in positions)
    sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n"
    chemin.write_bytes(sortie.encode("latin-1"))
    return chemin


def test_extraction_pdf(tmp_path):
    pdf = creer_pdf(tmp_path / "cv.pdf", [
        "Michael Mbenza - Developpeur IA",
        "Competences : Python, SQL, Flask, Pandas",
        "Experience : 3 ans en support informatique a Kinshasa",
    ])
    texte = extraire_texte(str(pdf), "pdf")
    assert "Python, SQL, Flask" in texte
    assert "Kinshasa" in texte


def test_extraction_docx_paragraphes_et_tableaux(tmp_path):
    doc = DocumentWord()
    doc.add_paragraph("Grace Kabila")
    doc.add_paragraph("Comptable senior   avec  8 ans d'expérience en ONG")
    tableau = doc.add_table(rows=2, cols=2)
    tableau.cell(0, 0).text, tableau.cell(0, 1).text = "Langues", "Français, Anglais, Lingala"
    tableau.cell(1, 0).text, tableau.cell(1, 1).text = "Logiciels", "Excel, Sage"
    chemin = tmp_path / "cv.docx"
    doc.save(chemin)

    texte = extraire_texte(str(chemin), "docx")
    assert "Comptable senior avec 8 ans" in texte          # espaces multiples nettoyés
    assert "Langues | Français, Anglais, Lingala" in texte  # le texte des tableaux est lu


def test_pdf_sans_texte_refuse(tmp_path):
    pdf = creer_pdf(tmp_path / "scan.pdf", [])  # comme un CV scanné : aucune ligne de texte
    with pytest.raises(CVIllisible, match="scanné"):
        extraire_texte(str(pdf), "pdf")


def test_fichier_endommage_refuse(tmp_path):
    faux = tmp_path / "abime.pdf"
    faux.write_bytes(b"%PDF-1.4 ceci n'est pas un vrai PDF")
    with pytest.raises(CVIllisible):
        extraire_texte(str(faux), "pdf")
    faux_docx = tmp_path / "abime.docx"
    faux_docx.write_bytes(b"PK\x03\x04 pas un vrai docx")
    with pytest.raises(CVIllisible):
        extraire_texte(str(faux_docx), "docx")


def test_nettoyer():
    assert nettoyer("  Bonjour   le\t monde \n\n\n\n Fin ") == "Bonjour le monde\n\nFin"
