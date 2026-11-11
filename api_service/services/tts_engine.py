"""Étape 2 de /speak-smart : transformer le texte du pitch en fichier audio .wav.

Modèle : VITS français de Coqui TTS (`tts_models/fr/css10/vits`), open source, exécuté en local
sur le processeur (pas de GPU nécessaire, pas d'internet une fois le modèle téléchargé).

Le modèle met plusieurs secondes à se charger : on le charge UNE SEULE FOIS (au démarrage de
l'API ou au premier appel), puis on le réutilise. Un verrou (Lock) empêche deux requêtes de
l'utiliser en même temps, car il n'est pas prévu pour ça.
"""
import os
import threading

MODELE_TTS = os.getenv("MODELE_TTS", "tts_models/fr/css10/vits")


class SyntheseImpossible(Exception):
    """Le modèle vocal n'a pas pu être chargé ou n'a pas produit de fichier audio."""


class MoteurVocal:
    def __init__(self, nom_modele=MODELE_TTS):
        self.nom_modele = nom_modele
        self._tts = None
        self._verrou = threading.Lock()

    @property
    def charge(self):
        return self._tts is not None

    def charger(self):
        """Charge le modèle (téléchargé dans le cache de Coqui au tout premier lancement)."""
        if self._tts is None:
            # Import ici : PyTorch et Coqui sont lourds, les tests et /health n'en ont pas besoin
            from TTS.api import TTS
            try:
                self._tts = TTS(self.nom_modele, progress_bar=False)
            except Exception as erreur:  # Coqui lève des exceptions très variées
                raise SyntheseImpossible(f"Modèle vocal impossible à charger : {erreur}") from erreur
        return self._tts

    def synthetiser(self, texte, chemin_wav):
        """Écrit le texte lu par la voix dans chemin_wav. Renvoie le chemin."""
        with self._verrou:
            tts = self.charger()
            try:
                tts.tts_to_file(text=texte, file_path=chemin_wav)
            except Exception as erreur:
                raise SyntheseImpossible(f"Erreur de synthèse vocale : {erreur}") from erreur
        if not os.path.exists(chemin_wav) or os.path.getsize(chemin_wav) == 0:
            raise SyntheseImpossible("Le fichier audio n'a pas été créé.")
        return chemin_wav


# Un seul moteur pour toute l'API (le modèle n'est chargé qu'une fois)
moteur = MoteurVocal()
