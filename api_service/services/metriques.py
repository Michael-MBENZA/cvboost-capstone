"""Métriques de santé de l'API (MLOps) : compteurs et durées, exposés par GET /metrics.

Les valeurs sont gardées en mémoire (elles repartent de zéro au redémarrage de l'API) :
c'est suffisant pour surveiller le service en direct. Pour un historique long, on les enverrait
à un outil dédié (Azure Monitor, Prometheus + Grafana...).
"""
import threading
import time
from collections import Counter


class Duree:
    """Nombre de mesures, moyenne et maximum d'une durée (en secondes)."""

    def __init__(self):
        self.nombre = 0
        self.total = 0.0
        self.maximum = 0.0

    def ajouter(self, secondes):
        self.nombre += 1
        self.total += secondes
        self.maximum = max(self.maximum, secondes)

    def resume(self):
        moyenne = self.total / self.nombre if self.nombre else 0.0
        return {"mesures": self.nombre, "moyenne_s": round(moyenne, 2), "max_s": round(self.maximum, 2)}


class Metriques:
    def __init__(self):
        self._verrou = threading.Lock()   # plusieurs requêtes peuvent écrire en même temps
        self.remettre_a_zero()

    def remettre_a_zero(self):
        with self._verrou:
            self.demarre_le = time.time()
            self.requetes = Counter()      # (route, code HTTP) -> nombre
            self.pitchs_reussis = 0
            self.erreurs = Counter()       # type d'erreur -> nombre (llm, voix, validation, inattendue...)
            self.tokens = 0
            self.secondes_audio = 0.0
            self.durees = {"llm": Duree(), "voix": Duree(), "total": Duree()}

    def requete(self, route, code):
        with self._verrou:
            self.requetes[(route, code)] += 1

    def erreur(self, type_erreur):
        with self._verrou:
            self.erreurs[type_erreur] += 1

    def pitch(self, duree_llm, duree_voix, duree_totale, secondes_audio, tokens):
        with self._verrou:
            self.pitchs_reussis += 1
            self.tokens += tokens
            self.secondes_audio += secondes_audio
            self.durees["llm"].ajouter(duree_llm)
            self.durees["voix"].ajouter(duree_voix)
            self.durees["total"].ajouter(duree_totale)

    def instantane(self):
        """Toutes les métriques sous forme de dictionnaire (renvoyé en JSON par /metrics)."""
        with self._verrou:
            total_requetes = sum(self.requetes.values())
            erreurs_serveur = sum(n for (_, code), n in self.requetes.items() if code >= 500)
            return {
                "demarre_depuis_secondes": int(time.time() - self.demarre_le),
                "requetes_total": total_requetes,
                "requetes_par_route": {f"{route} {code}": n for (route, code), n in sorted(self.requetes.items())},
                "taux_erreurs_serveur_pct": round(100 * erreurs_serveur / total_requetes, 1) if total_requetes else 0.0,
                "pitchs_reussis": self.pitchs_reussis,
                "erreurs": dict(self.erreurs),
                "tokens_openai": self.tokens,
                "secondes_audio_generees": round(self.secondes_audio, 1),
                "durees": {nom: d.resume() for nom, d in self.durees.items()},
            }


metriques = Metriques()
