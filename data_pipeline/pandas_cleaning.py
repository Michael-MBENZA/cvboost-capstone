"""Nettoyage et normalisation des offres d'emploi avec Pandas (Sprint 2).

Entrée  : data/raw/jobs_mediacongo.csv        (données brutes du scraper)
Sortie  : data/processed/jobs_clean.csv       (données "IA-Ready")

Étapes :
  1. nettoyage du texte (espaces, retours à la ligne)
  2. suppression des doublons
  3. valeurs manquantes (entreprise, lieu, description vide ou réduite au titre)
  4. normalisation du lieu (ville + province) et des dates
  5. conversion des salaires en USD mensuels
  6. encodage des compétences en colonnes 0/1 (one-hot)

Utilisation :
    python -m data_pipeline.pandas_cleaning
"""
import json
import os
import re
import sys
import unicodedata

import pandas as pd

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CSV_BRUT = os.path.join(BASE_DIR, "data", "raw", "jobs_mediacongo.csv")
CSV_PROPRE = os.path.join(BASE_DIR, "data", "processed", "jobs_clean.csv")

# Hypothèses de conversion (à mettre à jour si besoin)
TAUX_VERS_USD = {"USD": 1.0, "CDF": 1 / 2850, "EUR": 1.08}
LONGUEUR_MIN_DESCRIPTION = 100  # en dessous, la "description" n'est qu'un titre

PROVINCES_RDC = [
    "Kinshasa", "Kongo-Central", "Kwango", "Kwilu", "Mai-Ndombe", "Kasai", "Kasai-Central",
    "Kasai-Oriental", "Lomami", "Sankuru", "Maniema", "Sud-Kivu", "Nord-Kivu", "Ituri",
    "Haut-Uele", "Tshopo", "Bas-Uele", "Nord-Ubangi", "Mongala", "Sud-Ubangi", "Equateur",
    "Tshuapa", "Tanganyika", "Haut-Lomami", "Lualaba", "Haut-Katanga",
]
# Grandes villes -> province (pour les offres qui ne citent que la ville)
VILLES_PROVINCES = {
    "goma": "Nord-Kivu", "beni": "Nord-Kivu", "butembo": "Nord-Kivu", "bukavu": "Sud-Kivu",
    "uvira": "Sud-Kivu", "lubumbashi": "Haut-Katanga", "kolwezi": "Lualaba", "bunia": "Ituri",
    "kisangani": "Tshopo", "mbandaka": "Equateur", "matadi": "Kongo-Central", "boma": "Kongo-Central",
    "kananga": "Kasai-Central", "mbuji-mayi": "Kasai-Oriental", "tshikapa": "Kasai",
    "kalemie": "Tanganyika", "kindu": "Maniema", "kikwit": "Kwilu", "bandundu": "Kwilu",
    "mweka": "Kasai",
}
LIEUX_NATIONAUX = {"rd congo", "rdc", "republique democratique du congo", "drc", "national"}

# Compétences recherchées : nom de colonne -> expression régulière (texte sans accents, minuscules)
COMPETENCES = {
    "python": r"\bpython\b",
    "sql": r"\bsql\b",
    "excel": r"\bexcel\b",
    "informatique": r"informatique|\bit\b|developpe|software|logiciel",
    "comptabilite": r"comptab|accounting|accountant",
    "finance": r"financ",
    "gestion_projet": r"gestion de projet|project manag|chef de projet|coordinat",
    "suivi_evaluation": r"suivi.{0,3}evaluation|monitoring|\bm&e\b|\bmeal\b",
    "logistique": r"logisti|supply chain|approvisionnement",
    "passation_marches": r"passation|procurement|achats?\b",
    "ressources_humaines": r"ressources humaines|human resources|\brh\b|\bhr\b",
    "sante": r"sante|health|medical|laboratoire|medecin",
    "communication": r"communication",
    "anglais": r"anglais|english",
    "environnement": r"environnement|environmental|climat",
    "securite": r"securite|security|safety",
    "eau_assainissement": r"\beau\b|assainissement|hygiene|\bwash\b|\beah\b",
}


# ---------- Outils ----------

def sans_accents(texte):
    """'Hygiène Santé' -> 'hygiene sante' (minuscules, accents retirés) pour comparer facilement."""
    if not isinstance(texte, str):
        return ""
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def nettoyer_texte(serie):
    """Retire les espaces en trop et les retours à la ligne d'une colonne texte."""
    return serie.astype("string").str.replace(r"\s+", " ", regex=True).str.strip()


# ---------- 5. Conversion des salaires ----------

def convertir_salaire(valeur):
    """Texte ou JSON de salaire -> montant mensuel en USD (float), ou None si absent/illisible.

    Exemples : "Salaire : 1 500 USD par mois" -> 1500.0
               "2 850 000 FC"                 -> 1000.0
               '{"currency": "USD", "value": {"value": 24000, "unitText": "YEAR"}}' -> 2000.0
    """
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)) or str(valeur).strip() == "":
        return None
    texte = str(valeur)

    # Cas 1 : format JSON-LD schema.org (baseSalary)
    if texte.lstrip().startswith("{"):
        try:
            donnees = json.loads(texte)
            devise = (donnees.get("currency") or "USD").upper()
            bloc = donnees.get("value", {})
            montant = bloc.get("value") or bloc.get("minValue")
            unite = (bloc.get("unitText") or "MONTH").upper()
            if montant is None or devise not in TAUX_VERS_USD:
                return None
            mensuel = float(montant) / 12 if unite == "YEAR" else float(montant)
            return round(mensuel * TAUX_VERS_USD[devise], 2)
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return None

    # Cas 2 : texte libre
    normalise = sans_accents(texte)
    nombre = re.search(r"\d[\d\s.,]*", normalise)
    if not nombre:
        return None
    brut = nombre.group(0).strip().replace(" ", "")
    # "1.500" ou "1,500" (séparateur de milliers) -> 1500 ; "1500,50" -> 1500.50
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", brut):
        brut = re.sub(r"[.,]", "", brut)
    else:
        brut = brut.replace(",", ".")
    try:
        montant = float(brut)
    except ValueError:
        return None

    if re.search(r"\bcdf\b|\bfc\b|franc", normalise):
        devise = "CDF"
    elif re.search(r"\beur\b|€|euro", normalise):
        devise = "EUR"
    else:
        devise = "USD"  # "$", "USD" ou devise non précisée : l'USD domine les offres en RDC

    if re.search(r"\ban\b|annuel|par an|/an|year|annual", normalise):
        montant = montant / 12
    return round(montant * TAUX_VERS_USD[devise], 2)


# ---------- 4. Lieu ----------

def province_de(lieu):
    """'Beni (Nord-Kivu)' -> 'Nord-Kivu' ; 'RD Congo' -> 'National' ; inconnu -> 'Non précisé'."""
    texte = sans_accents(lieu)
    if not texte:
        return "Non précisé"
    if texte.strip() in LIEUX_NATIONAUX:
        return "National"
    for province in PROVINCES_RDC:
        if sans_accents(province) in texte:
            return province
    for ville, province in VILLES_PROVINCES.items():
        if ville in texte:
            return province
    return "Autre"


def ville_de(lieu):
    """'Beni (Nord-Kivu)' -> 'Beni' ; 'Tshikapa / Mweka' -> 'Tshikapa' (premier lieu cité)."""
    if not isinstance(lieu, str) or not lieu.strip():
        return "Non précisé"
    premier = re.split(r"[/,;(]", lieu)[0].strip()
    # "Nord-Kivu" ou "RD Congo" sont une province / le pays, pas une ville
    # (exception : Kinshasa est à la fois une ville et une province)
    nom = sans_accents(premier)
    if nom in LIEUX_NATIONAUX or (nom != "kinshasa" and nom in {sans_accents(p) for p in PROVINCES_RDC}):
        return "Non précisé"
    return premier or "Non précisé"


# ---------- Pipeline complet ----------

def nettoyer(df):
    """DataFrame brut -> DataFrame propre. Renvoie aussi un petit rapport (dict)."""
    rapport = {"lignes_brutes": len(df)}
    df = df.copy()

    # 1. Texte
    for col in ["reference", "intitule", "entreprise", "lieu", "description", "salaire", "url"]:
        if col in df.columns:
            df[col] = nettoyer_texte(df[col])

    # 2. Doublons : même référence, ou même poste publié deux fois (titre + entreprise + lieu)
    avant = len(df)
    df = df.drop_duplicates(subset="reference")
    cle = df["intitule"].map(sans_accents) + "|" + df["entreprise"].map(sans_accents) + "|" + df["lieu"].map(sans_accents)
    df = df[~cle.duplicated()]
    rapport["doublons_supprimes"] = avant - len(df)

    # 3. Valeurs manquantes
    rapport["valeurs_manquantes_avant"] = {
        col: int(df[col].isna().sum() + (df[col] == "").sum())
        for col in ["entreprise", "lieu", "description", "salaire"]
    }
    df["entreprise"] = df["entreprise"].replace("", pd.NA).fillna("Non précisé")
    df["lieu"] = df["lieu"].replace("", pd.NA).fillna("Non précisé")
    df["description"] = df["description"].fillna("")
    # Une description identique au titre ou très courte = annonce publiée en image/PDF
    df["description_disponible"] = (
        (df["description"].str.len() >= LONGUEUR_MIN_DESCRIPTION)
        & (df["description"].map(sans_accents) != df["intitule"].map(sans_accents))
    )
    df.loc[~df["description_disponible"], "description"] = ""

    # 4. Lieu et dates
    df["ville"] = df["lieu"].map(ville_de)
    df["province"] = df["lieu"].map(province_de)
    df["date_publication"] = pd.to_datetime(df["date_publication"], format="%d.%m.%Y", errors="coerce")
    df["date_limite"] = pd.to_datetime(df["date_limite"], format="%Y-%m-%d", errors="coerce")
    df["duree_candidature_jours"] = (df["date_limite"] - df["date_publication"]).dt.days

    # 5. Salaires
    df["salaire_usd_mensuel"] = df["salaire"].map(convertir_salaire).astype("Float64")
    df["salaire_indique"] = df["salaire_usd_mensuel"].notna()

    # 6. Compétences (one-hot) cherchées dans le titre + la description
    texte_complet = (df["intitule"].fillna("") + " " + df["description"]).map(sans_accents)
    for nom, motif in COMPETENCES.items():
        df[f"comp_{nom}"] = texte_complet.str.contains(motif, regex=True).astype(int)
    colonnes_comp = [f"comp_{nom}" for nom in COMPETENCES]
    df["nb_competences"] = df[colonnes_comp].sum(axis=1)

    df = df.drop(columns=["salaire"]).sort_values("date_publication", ascending=False)
    rapport["lignes_finales"] = len(df)
    rapport["offres_avec_description"] = int(df["description_disponible"].sum())
    rapport["offres_avec_salaire"] = int(df["salaire_indique"].sum())
    rapport["top_competences"] = (
        df[colonnes_comp].sum().sort_values(ascending=False).head(5)
        .rename(lambda c: c.removeprefix("comp_")).to_dict()
    )
    return df, rapport


if __name__ == "__main__":
    if not os.path.exists(CSV_BRUT):
        sys.exit(f"Fichier introuvable : {CSV_BRUT}\nLancez d'abord : python -m data_pipeline.scraper_jobs")

    brut = pd.read_csv(CSV_BRUT, dtype=str, keep_default_na=False)
    propre, rapport = nettoyer(brut)

    os.makedirs(os.path.dirname(CSV_PROPRE), exist_ok=True)
    propre.to_csv(CSV_PROPRE, index=False, encoding="utf-8", date_format="%Y-%m-%d")

    print(f"Lignes brutes            : {rapport['lignes_brutes']}")
    print(f"Doublons supprimés       : {rapport['doublons_supprimes']}")
    print(f"Valeurs manquantes avant : {rapport['valeurs_manquantes_avant']}")
    print(f"Lignes finales           : {rapport['lignes_finales']}")
    print(f"Avec vraie description   : {rapport['offres_avec_description']}")
    print(f"Avec salaire (USD/mois)  : {rapport['offres_avec_salaire']}")
    print(f"Top 5 compétences        : {rapport['top_competences']}")
    print(f"\nColonnes : {list(propre.columns)}")
    print(f"Fichier  : {os.path.relpath(CSV_PROPRE, BASE_DIR)}")
