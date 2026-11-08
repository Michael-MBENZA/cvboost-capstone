"""Base analytique des KPI (Sprint 2) : database/analytics.db.

1. Charge les données sources dans analytics.db :
   - upload_events : chaque tentative d'upload (issue de logs_parser.py)
   - documents     : les CV acceptés (copiés depuis cvboost.db, sans les noms de fichiers)
2. Calcule les KPI d'usage avec des requêtes SQL avancées (GROUP BY, HAVING).
3. Enregistre un instantané des KPI dans la table kpi_snapshots.

Utilisation :
    python -m data_pipeline.analytics                                   # logs/uploads.log
    python -m data_pipeline.analytics data/samples/uploads_sample.log   # journal d'exemple
"""
import os
import sqlite3
import sys
from datetime import datetime

from data_pipeline.logs_parser import auditer

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ANALYTICS_DB = os.path.join(BASE_DIR, "database", "analytics.db")
CVBOOST_DB = os.path.join(BASE_DIR, "database", "cvboost.db")
LOG_PAR_DEFAUT = os.path.join(BASE_DIR, "logs", "uploads.log")

SCHEMA = """
DROP TABLE IF EXISTS upload_events;
CREATE TABLE upload_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT    NOT NULL,
    jour      TEXT    NOT NULL,
    user_id   TEXT    NOT NULL,
    ip        TEXT    NOT NULL,
    statut    TEXT    NOT NULL,
    motif     TEXT    NOT NULL,
    taille    INTEGER NOT NULL,
    extension TEXT
);

DROP TABLE IF EXISTS documents;
CREATE TABLE documents (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    file_type   TEXT    NOT NULL,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    calcule_le  TEXT NOT NULL,
    nom         TEXT NOT NULL,
    valeur      REAL NOT NULL
);
"""

# ---------- Les requêtes KPI ----------
# Chaque entrée : (titre, requête SQL). Elles sont aussi utilisées par les tests.
REQUETES = {
    "tentatives_par_statut": (
        "Tentatives par statut",
        """
        SELECT statut, COUNT(*) AS nombre
        FROM upload_events
        GROUP BY statut
        ORDER BY nombre DESC
        """,
    ),
    "refus_par_motif": (
        "Refus par motif (en % des refus)",
        """
        SELECT motif,
               COUNT(*) AS nombre,
               ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM upload_events WHERE statut = 'REFUSE'), 1) AS pourcentage
        FROM upload_events
        WHERE statut = 'REFUSE'
        GROUP BY motif
        ORDER BY nombre DESC
        """,
    ),
    "activite_par_jour": (
        "Activité par jour",
        """
        SELECT jour,
               COUNT(*) AS tentatives,
               SUM(CASE WHEN statut = 'ACCEPTE' THEN 1 ELSE 0 END) AS acceptees,
               SUM(CASE WHEN statut = 'REFUSE'  THEN 1 ELSE 0 END) AS refusees
        FROM upload_events
        GROUP BY jour
        ORDER BY jour
        """,
    ),
    "ip_suspectes": (
        "IP suspectes (3 refus ou plus)",
        """
        SELECT ip, user_id, COUNT(*) AS refus
        FROM upload_events
        WHERE statut = 'REFUSE'
        GROUP BY ip, user_id
        HAVING COUNT(*) >= 3
        ORDER BY refus DESC
        """,
    ),
    "utilisateurs_actifs": (
        "Utilisateurs ayant fait au moins 2 tentatives",
        """
        SELECT user_id,
               COUNT(*) AS tentatives,
               ROUND(100.0 * SUM(CASE WHEN statut = 'ACCEPTE' THEN 1 ELSE 0 END) / COUNT(*), 1) AS taux_succes
        FROM upload_events
        GROUP BY user_id
        HAVING COUNT(*) >= 2
        ORDER BY tentatives DESC
        """,
    ),
    "extensions_tentees": (
        "Extensions tentées",
        """
        SELECT CASE WHEN extension = '' THEN '(aucune)' ELSE extension END AS extension,
               COUNT(*) AS nombre
        FROM upload_events
        GROUP BY extension
        ORDER BY nombre DESC
        """,
    ),
    "cv_par_type": (
        "CV stockés par type",
        """
        SELECT file_type, COUNT(*) AS nombre, ROUND(AVG(size_bytes) / 1024.0, 1) AS taille_moyenne_ko
        FROM documents
        GROUP BY file_type
        """,
    ),
    "utilisateurs_multi_cv": (
        "Utilisateurs ayant déposé plusieurs CV",
        """
        SELECT user_id, COUNT(*) AS nombre_cv
        FROM documents
        GROUP BY user_id
        HAVING COUNT(*) > 1
        ORDER BY nombre_cv DESC
        """,
    ),
}


def creer_schema(conn):
    conn.executescript(SCHEMA)


def charger_evenements(conn, evenements):
    conn.executemany(
        """INSERT INTO upload_events (date, jour, user_id, ip, statut, motif, taille, extension)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                e["date"].strftime("%Y-%m-%d %H:%M:%S"),
                e["date"].strftime("%Y-%m-%d"),
                e["user"], e["ip"], e["statut"], e["motif"], e["taille"], e["extension"],
            )
            for e in evenements
        ],
    )


def charger_documents(conn, chemin_cvboost):
    """Copie les CV acceptés depuis cvboost.db (sans les noms : données personnelles)."""
    if not os.path.exists(chemin_cvboost):
        return 0
    source = sqlite3.connect(chemin_cvboost)
    try:
        lignes = source.execute(
            "SELECT id, user_id, file_type, size_bytes, uploaded_at FROM documents"
        ).fetchall()
    except sqlite3.OperationalError:
        lignes = []  # la table n'existe pas encore
    finally:
        source.close()
    conn.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", lignes)
    return len(lignes)


def calculer_kpi(conn):
    """Exécute toutes les requêtes et renvoie {cle: (titre, colonnes, lignes)}."""
    resultats = {}
    for cle, (titre, sql) in REQUETES.items():
        curseur = conn.execute(sql)
        colonnes = [c[0] for c in curseur.description]
        resultats[cle] = (titre, colonnes, curseur.fetchall())
    return resultats


def enregistrer_snapshot(conn):
    """Garde une trace datée des KPI principaux (pour suivre leur évolution)."""
    total, acceptees = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN statut = 'ACCEPTE' THEN 1 ELSE 0 END) FROM upload_events"
    ).fetchone()
    acceptees = acceptees or 0
    kpis = {
        "tentatives_total": total,
        "taux_acceptation_pct": round(100.0 * acceptees / total, 1) if total else 0,
        "ip_suspectes": len(conn.execute(REQUETES["ip_suspectes"][1]).fetchall()),
        "cv_stockes": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
    }
    maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO kpi_snapshots (calcule_le, nom, valeur) VALUES (?, ?, ?)",
        [(maintenant, nom, valeur) for nom, valeur in kpis.items()],
    )
    return kpis


def construire(chemin_log, chemin_analytics=ANALYTICS_DB, chemin_cvboost=CVBOOST_DB):
    """Pipeline complet : logs + cvboost.db -> analytics.db -> KPI."""
    os.makedirs(os.path.dirname(chemin_analytics), exist_ok=True)
    evenements, _ = auditer(chemin_log)
    conn = sqlite3.connect(chemin_analytics)
    try:
        creer_schema(conn)
        charger_evenements(conn, evenements)
        nb_docs = charger_documents(conn, chemin_cvboost)
        resultats = calculer_kpi(conn)
        kpis = enregistrer_snapshot(conn)
        conn.commit()
    finally:
        conn.close()
    return len(evenements), nb_docs, resultats, kpis


def afficher_tableau(titre, colonnes, lignes):
    print(f"\n== {titre} ==")
    if not lignes:
        print("  (aucun résultat)")
        return
    largeurs = [max(len(str(x)) for x in [col] + [l[i] for l in lignes]) for i, col in enumerate(colonnes)]
    print("  " + " | ".join(str(c).ljust(w) for c, w in zip(colonnes, largeurs)))
    print("  " + "-+-".join("-" * w for w in largeurs))
    for ligne in lignes:
        print("  " + " | ".join(str(v).ljust(w) for v, w in zip(ligne, largeurs)))


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else LOG_PAR_DEFAUT
    if not os.path.exists(chemin):
        sys.exit(f"Fichier introuvable : {chemin}")

    nb_evt, nb_docs, resultats, kpis = construire(chemin)
    print(f"analytics.db construite : {nb_evt} tentatives, {nb_docs} CV stockés")
    for titre, colonnes, lignes in resultats.values():
        afficher_tableau(titre, colonnes, lignes)
    print("\n== Instantané des KPI (table kpi_snapshots) ==")
    for nom, valeur in kpis.items():
        print(f"  {nom} = {valeur}")
