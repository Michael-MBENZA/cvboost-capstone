"""Audit des logs d'upload (Sprint 2).

Lit logs/uploads.log ligne par ligne, extrait les métadonnées de chaque tentative,
détecte les tentatives de fraude et écrit :
  - data/processed/upload_events.csv : une ligne par tentative (pour analytics.py)
  - data/reports/audit_uploads.json  : le rapport d'audit (résumé + alertes)

Utilisation :
    python -m data_pipeline.logs_parser                        # lit logs/uploads.log
    python -m data_pipeline.logs_parser data/samples/uploads_sample.log
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_PAR_DEFAUT = os.path.join(BASE_DIR, "logs", "uploads.log")
CSV_SORTIE = os.path.join(BASE_DIR, "data", "processed", "upload_events.csv")
RAPPORT_SORTIE = os.path.join(BASE_DIR, "data", "reports", "audit_uploads.json")

# Une ligne valide ressemble à :
# 2026-11-08 09:56:41 | user=1 | ip=127.0.0.1 | statut=ACCEPTE | motif=OK | taille=248792 | fichier=mon cv.pdf
MOTIF_LIGNE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| "
    r"user=(?P<user>\S+) \| ip=(?P<ip>\S+) \| statut=(?P<statut>\w+) \| "
    r"motif=(?P<motif>\w+) \| taille=(?P<taille>\d+) \| fichier=(?P<fichier>.*)$"
)

# Extensions qui n'ont rien à faire dans un CV : programmes, scripts...
EXTENSIONS_DANGEREUSES = {"exe", "bat", "cmd", "com", "scr", "js", "vbs", "ps1", "sh", "php", "jar", "msi", "dll"}

# Règle "rafale" : X refus ou plus pour la même IP en moins de N minutes
SEUIL_RAFALE = 3
FENETRE_RAFALE = timedelta(minutes=5)


def lire_lignes(chemin):
    """I/O : lit le fichier de logs et renvoie ses lignes (sans les retours à la ligne)."""
    with open(chemin, encoding="utf-8") as f:
        return [ligne.rstrip("\n") for ligne in f if ligne.strip()]


def parser_ligne(ligne):
    """Transforme une ligne de log en dictionnaire de métadonnées, ou None si elle est mal formée."""
    m = MOTIF_LIGNE.match(ligne)
    if not m:
        return None
    evt = m.groupdict()
    evt["date"] = datetime.strptime(evt["date"], "%Y-%m-%d %H:%M:%S")
    evt["taille"] = int(evt["taille"])
    nom = evt["fichier"].lower()
    evt["extension"] = nom.rsplit(".", 1)[1] if "." in nom else ""
    # "cv.pdf.exe" -> 2 extensions : technique classique pour tromper l'utilisateur
    evt["double_extension"] = nom.count(".") >= 2 and nom.rsplit(".", 2)[1] in {"pdf", "docx", "doc"}
    return evt


def detecter_fraudes(evenements):
    """Renvoie la liste des alertes de fraude trouvées dans les événements."""
    alertes = []

    for evt in evenements:
        if evt["motif"] == "SIGNATURE_INVALIDE":
            alertes.append(_alerte(evt, "FAUX_FICHIER", "Le contenu ne correspond pas à l'extension (fichier déguisé)."))
        if evt["extension"] in EXTENSIONS_DANGEREUSES:
            alertes.append(_alerte(evt, "EXTENSION_DANGEREUSE", f"Tentative d'envoi d'un fichier .{evt['extension']}."))
        if evt["double_extension"]:
            alertes.append(_alerte(evt, "DOUBLE_EXTENSION", "Nom de fichier à double extension (ex : cv.pdf.exe)."))

    # Rafales : beaucoup de refus depuis la même IP en peu de temps
    refus_par_ip = defaultdict(list)
    for evt in evenements:
        if evt["statut"] == "REFUSE":
            refus_par_ip[evt["ip"]].append(evt)
    for ip, refus in refus_par_ip.items():
        refus.sort(key=lambda e: e["date"])
        for i in range(len(refus) - SEUIL_RAFALE + 1):
            debut, fin = refus[i], refus[i + SEUIL_RAFALE - 1]
            if fin["date"] - debut["date"] <= FENETRE_RAFALE:
                alertes.append(_alerte(
                    fin, "RAFALE_DE_REFUS",
                    f"{SEUIL_RAFALE} refus ou plus en moins de {int(FENETRE_RAFALE.total_seconds() // 60)} min depuis {ip}.",
                ))
                break  # une seule alerte rafale par IP suffit

    return alertes


def _alerte(evt, type_alerte, detail):
    return {
        "type": type_alerte,
        "date": evt["date"].strftime("%Y-%m-%d %H:%M:%S"),
        "user": evt["user"],
        "ip": evt["ip"],
        "fichier": evt["fichier"],
        "detail": detail,
    }


def resumer(evenements, lignes_invalides, alertes):
    """Statistiques globales du journal."""
    acceptes = [e for e in evenements if e["statut"] == "ACCEPTE"]
    motifs = defaultdict(int)
    for e in evenements:
        if e["statut"] == "REFUSE":
            motifs[e["motif"]] += 1
    return {
        "total_tentatives": len(evenements),
        "acceptees": len(acceptes),
        "refusees": len(evenements) - len(acceptes),
        "lignes_invalides": lignes_invalides,
        "refus_par_motif": dict(motifs),
        "utilisateurs_distincts": len({e["user"] for e in evenements}),
        "taille_moyenne_acceptee_ko": round(sum(e["taille"] for e in acceptes) / len(acceptes) / 1024, 1) if acceptes else 0,
        "nombre_alertes": len(alertes),
    }


def ecrire_csv(evenements, chemin=CSV_SORTIE):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    colonnes = ["date", "user", "ip", "statut", "motif", "taille", "extension", "double_extension", "fichier"]
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes, extrasaction="ignore")
        writer.writeheader()
        for e in evenements:
            writer.writerow({**e, "date": e["date"].strftime("%Y-%m-%d %H:%M:%S")})


def ecrire_rapport(rapport, chemin=RAPPORT_SORTIE):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)


def auditer(chemin_log):
    """Point d'entrée : lit, parse, détecte, et renvoie (événements, rapport)."""
    lignes = lire_lignes(chemin_log)
    evenements, invalides = [], 0
    for ligne in lignes:
        evt = parser_ligne(ligne)
        if evt is None:
            invalides += 1
        else:
            evenements.append(evt)
    alertes = detecter_fraudes(evenements)
    rapport = {"source": os.path.relpath(chemin_log, BASE_DIR), "resume": resumer(evenements, invalides, alertes), "alertes": alertes}
    return evenements, rapport


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else LOG_PAR_DEFAUT
    if not os.path.exists(chemin):
        sys.exit(f"Fichier introuvable : {chemin}")

    evenements, rapport = auditer(chemin)
    ecrire_csv(evenements)
    ecrire_rapport(rapport)

    r = rapport["resume"]
    print(f"Journal analysé : {rapport['source']}")
    print(f"  Tentatives : {r['total_tentatives']} (acceptées : {r['acceptees']}, refusées : {r['refusees']})")
    print(f"  Lignes invalides ignorées : {r['lignes_invalides']}")
    print(f"  Refus par motif : {r['refus_par_motif']}")
    print(f"  Alertes de fraude : {r['nombre_alertes']}")
    for a in rapport["alertes"]:
        print(f"    [{a['type']}] {a['date']} user={a['user']} ip={a['ip']} -> {a['fichier']}")
    print(f"Événements -> {os.path.relpath(CSV_SORTIE, BASE_DIR)}")
    print(f"Rapport    -> {os.path.relpath(RAPPORT_SORTIE, BASE_DIR)}")
