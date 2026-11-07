# Projet-Capstone-Final-CVBoost-

Contexte
La startup TalentIA commercialise sa plateforme phare CVBoost. Après plusieurs mois de développement et d'expérimentations techniques, l'entreprise souhaite unifier l'ensemble des briques de l'application en une solution globale, performante, industrielle et prête pour la production.

Votre mission finale est de livrer l'architecture complète de CVBoost : une plateforme web sécurisée appuyée par une pipeline de données robuste, un jeu de données NLP préparé sans fuite, un évaluateur intelligent de profils (Assistant DRH), un orchestrateur audio multimodal (FastAPI + Text-to-Speech), et un déploiement Cloud automatisé via Docker et CI/CD sur Microsoft Azure.

Objectifs Pédagogiques Globaux
À l'issue de ce projet d'intégration finale, les apprenants sauront :

Développement Web & Sécurité : Consolider une application web (Flask), sécuriser l'authentification et gérer le stockage persistant de documents.
Data Engineering & Analytics : Construire une pipeline ETL complète (Logs, Web Scraping, SQL analytique, Preprocessing Pandas).
NLP & Préparation de données : Préparer des jeux de données sans fuite (Hugging Face, SHA-256, assertions).
Extraction & Prompt Engineering (IA DRH) : Extraire les données d'un document PDF (pypdf) et interroger dynamiquement un LLM (OpenAI) pour évaluer un profil de candidat.
Architecture Hybride & Multimodale : Exposer une API (FastAPI) combinant LLM et synthèse vocale (Coqui TTS) pour générer des pitchs candidats.
MLOps, Conteneurisation & Cloud : Dockeriser l'application, automatiser le déploiement via GitHub Actions sur Azure et superviser l'application en production.
Spécifications des Sprints d'Intégration
Sprint 1 : Socle Web & Gestion Utilisateurs (Module 1)
Application Flask & Authentification : Inscription, connexion sécurisée (hashage Werkzeug, Flask-Login) et gestion de sessions.
Persistance & Fichiers : Modélisation des tables User et Document (SQLAlchemy). Téléversement sécurisé de CV (PDF/DOCX, validation de taille à 5 Mo, renommage UUID).
Interface Dynamique : Templating Jinja2 avec composants réutilisables (base.html), navigation dynamique et espace historique utilisateur.
Sprint 2 : Ingestion de Données, Scraping & Analytics (Module 2)
Audit & Logs : Script d'I/O pour parser les fichiers de logs d'upload, détecter les tentatives de fraude et extraire les métadonnées.
Base Analytique SQL : Base SQLite/PostgreSQL dédiée aux KPI (analytics.db), calcul des statistiques d'usage via requêtes avancées (GROUP BY, HAVING).
Collecteur d'Offres (Web Scraping) : Robot BeautifulSoup récupérant des offres d'emploi réelles (intitulé, entreprise, description, salaire) avec respect de la courtoisie web (User-Agent, délais).
Data Cleaning & Normalisation (Pandas) : Nettoyage du dataset d'offres (doublons, valeurs manquantes, conversion des salaires, encodage des compétences) pour le rendre "IA-Ready".
Sprint 3 : Moteur NLP & Assistant DRH Intelligent (Module 3)
Pipeline Anti-Fuite (Hugging Face) : Déduplication stricte par hashage SHA-256 et séparation étanche des sous-ensembles d'entraînement (80% Train, 10% Validation, 10% Test) avec assertions automatisées.
L'Assistant DRH Intelligent (Nouveau Intégré) :
Parsing PDF (pypdf) : Extraction automatisée et propre du texte brut à partir des CV téléversés dans le dossier uploads/.
Intégration OpenAI & Prompt Engineering : Création d'un module ia.py pour envoyer le texte du CV et l'offre d'emploi à l'API OpenAI (ex: gpt-3.5-turbo ou gpt-4o-mini).
Scoring & Résumé : Le LLM doit générer un résumé du profil du candidat et calculer un pourcentage de matching (compatibilité) avec l'offre cible, affiché ensuite sur l'interface web. Sécurisation obligatoire de la clé via python-dotenv (fichier .env).
L'Assistant DRH Intelligent :
Le simulateur d'entretien : Ajoutez une fonctionnalité sur l'interface permettant à l'utilisateur de déclencher une "Simulation d'entretien".
Parsing PDF (pypdf) : Lorsque la fonctionnalité est déclenchée, le système doit extraire automatiquement et proprement le texte brut du CV téléversé.
Intégration OpenAI & Prompt Engineering : Le module ia.py envoie le texte extrait du CV et l'offre d'emploi cible à l'API OpenAI.
Scoring & Résumé : Le LLM agit comme un recruteur : il génère un résumé du profil, calcule un pourcentage de matching et (idéalement) pose une première question d'entretien. L'ensemble est affiché sur l'interface. La sécurisation de la clé d'API via python-dotenv est obligatoire.
Sprint 4 : API Hybride, Pitch Vocal & Déploiement Cloud (Module 4)
Orchestrateur FastAPI (/speak-smart) :
Étape 1 : Reformulation du CV brut en un discours fluide via un prompt strict envoyé à un LLM (OpenAI / Azure OpenAI).
Étape 2 : Transmission du texte nettoyé à un modèle vocal (Coqui TTS / VITS) pour générer une présentation audio .wav.
Conteneurisation (Docker) : Rédaction du Dockerfile optimisé et configuration d'un docker-compose.yml isolant les services web, API et base de données.
CI/CD & Cloud Azure : Pipeline GitHub Actions pour tester et déployer automatiquement l'image Docker sur Azure App Service / Container Instances.
MLOps & Logs : Mise en place de métriques de santé, journalisation des erreurs et documentation des métriques audio (WER, CER, MOS).
Architecture Globale du Projet Industrialisé
cvboost_enterprise/
│
├── .github/
│   └── workflows/
│       └── deploy.yml            # Pipeline CI/CD GitHub Actions
│
├── app_web/                      # Application Flask (Sprint 1 & Assistant DRH)
│   ├── static/
│   ├── templates/
│   ├── models.py
│   ├── routes.py
│   ├── ia.py                     # Connecteur API OpenAI (Sprint 3)
│   └── config.py
│
├── data_pipeline/                # Engineering & Scraping (Sprint 2)
│   ├── logs_parser.py
│   ├── scraper_jobs.py
│   └── pandas_cleaning.py
│
├── ai_engine/                    # Dataset NLP sans fuite (Sprint 3)
│   └── prepare_dataset.py       
│
├── api_service/                  # Orchestrateur FastAPI (Sprint 4)
│   ├── main.py                   # Route POST /speak-smart
│   ├── services/
│   │   ├── llm_reformulator.py  
│   │   └── tts_engine.py        
│   └── temp_audio/
│
├── database/
│   ├── cvboost.db               
│   └── analytics.db             
│
├── Dockerfile                   
├── docker-compose.yml           
├── requirements.txt             
├── .env.example                 
└── README.md
