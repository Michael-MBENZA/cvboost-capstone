"""Une source d'offres d'emploi = un module.

Chaque module expose :
  NOM        : identifiant court ("mediacongo", "radiookapi", "reliefweb")
  SITE       : adresse du site (pour robots.txt et l'affichage)
  collecter(telecharger, deja_connues, max_offres, afficher) -> liste de dicts au format COLONNES
"""
