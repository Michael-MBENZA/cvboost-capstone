# Métriques audio de la voix VITS (Sprint 4, MLOps)

Ce document présente les trois métriques de qualité de la voix de l'API `/speak-smart`
(modèle `tts_models/fr/css10/vits` de Coqui TTS), la façon dont elles sont mesurées et les
résultats obtenus.

| Métrique | Ce qu'elle mesure | Comment | Objectif |
|---|---|---|---|
| **WER** (*Word Error Rate*) | part des **mots** mal compris | automatique (`evaluer_voix.py`) | le plus bas possible (< 10 % = très intelligible) |
| **CER** (*Character Error Rate*) | part des **caractères** mal compris | automatique (`evaluer_voix.py`) | le plus bas possible (< 5 %) |
| **MOS** (*Mean Opinion Score*) | **naturel** de la voix perçu par des humains | auditeurs, note de 1 à 5 | ≥ 3,5 = acceptable, ≥ 4 = bon |

## 1. WER et CER : l'intelligibilité

**Principe (test « aller-retour ») :** une phrase de référence est lue par la voix VITS, le `.wav`
obtenu est « écouté » par un modèle de reconnaissance vocale (`gpt-4o-mini-transcribe`), puis on
compare la phrase entendue à la phrase de référence.

```
WER = (S + D + I) / N
  S = mots remplacés, D = mots oubliés, I = mots ajoutés, N = nombre de mots de la référence
CER = même calcul, caractère par caractère
```

Le nombre minimal S + D + I est la **distance d'édition** (Levenshtein), programmée dans
`distance_edition()`. Avant la comparaison, les deux textes sont **normalisés** : minuscules,
sans accents ni ponctuation, nombres écrits en lettres. « Université » et « universite », ou
« 2024 » et « deux mille vingt-quatre », ne comptent donc pas comme des erreurs.
Le résultat global additionne les erreurs de toutes les phrases : une longue phrase pèse plus
qu'une courte.

**Lancer la mesure** (venv activé, à la racine du projet ; coût inférieur à 1 centime) :

```bash
python -m api_service.evaluer_voix
```

Les résultats sont écrits dans `api_service/evaluation_voix.json`.

### Résultats du 2026-11-12 (8 phrases typiques d'un pitch)

| # | Phrase de référence | Entendu (différences) | WER | CER |
|---|---|---|---|---|
| 1 | Je m'appelle Grace Kabila **Mukendi** et j'habite à Kinshasa. | … Kabila **Mpandi** … | 9,1 % | 5,4 % |
| 2 | J'ai obtenu une licence en gestion à l'Université de Kinshasa en deux mille vingt-quatre. | identique | 0 % | 0 % |
| 3 | … à l'**ONG Santé** Pour **Tous** pendant trois ans. | … à l'**Onsanté** pour **tout** … | 20,0 % | 3,4 % |
| 4 | Je maîtrise Excel, Word et la gestion de caisse. | identique | 0 % | 0 % |
| 5 | J'ai organisé douze ateliers de formation pour les agents de santé communautaires. | identique | 0 % | 0 % |
| 6 | Je parle français, lingala et anglais à un niveau intermédiaire. | identique | 0 % | 0 % |
| 7 | Je recherche un poste d'assistante de programme dans une organisation humanitaire. | identique | 0 % | 0 % |
| 8 | Je suis développeur Python et j'ai construit une application web avec Flask. | identique | 0 % | 0 % |
| | **Global** | | **4,0 %** | **1,0 %** |

**Lecture :** la voix est **très intelligible** (96 % des mots bien compris). Les seules erreurs
portent sur les cas difficiles prévus :
- un **nom propre** rare (« Mukendi ») : le modèle CSS10 a été entraîné sur des livres audio
  français, sans noms congolais ;
- un **sigle** (« ONG ») prononcé comme un mot et collé au mot suivant. Le prompt de
  reformulation demande déjà de développer les sigles quand le CV donne leur forme longue ;
- « Tous » / « tout » : deux mots qui se prononcent de la même façon. Cette erreur vient de la
  reconnaissance vocale, pas de la voix.

**Limite de la méthode :** le WER mesure toute la chaîne voix + reconnaissance vocale. Une erreur
peut venir de l'une ou de l'autre (cas 3). Avec un meilleur modèle de transcription, le WER de la
voix seule serait plutôt inférieur ou égal à cette mesure.

## 2. MOS : le naturel, jugé par des humains

Le MOS est la référence pour juger une voix de synthèse. Il ne se calcule pas automatiquement de
façon fiable : ce sont des **auditeurs** qui donnent une note.

**Protocole :**
1. Générer et garder les fichiers audio :
   ```bash
   python -m api_service.evaluer_voix --dossier-audio docs/essais/mos
   ```
2. Faire écouter les 8 fichiers `phrase_XX.wav` à **au moins 4 auditeurs** francophones, au
   casque, dans un endroit calme, dans un ordre différent pour chacun.
3. Chaque auditeur note **chaque phrase** sur cette échelle (norme UIT-T P.800) :

   | Note | Qualité | Naturel de la voix |
   |---|---|---|
   | 5 | Excellente | impossible de distinguer d'une voix humaine |
   | 4 | Bonne | légèrement artificielle, mais agréable |
   | 3 | Moyenne | clairement synthétique, demande un peu d'attention |
   | 2 | Médiocre | désagréable, fatigante à écouter |
   | 1 | Mauvaise | très difficile à écouter |

4. Noter les résultats dans un fichier CSV (une ligne par auditeur et par phrase) :
   ```
   auditeur,phrase,note
   A,1,4
   A,2,3
   B,1,4
   ```
5. Calculer le MOS et son intervalle de confiance à 95 % :
   ```bash
   python -m api_service.evaluer_voix --mos notes_mos.csv
   ```

**Lecture de l'intervalle de confiance :** un MOS de 3,6 avec l'intervalle [3,3 ; 3,9] signifie
que la vraie note se situe très probablement entre 3,3 et 3,9. Avec peu de notes, l'intervalle
est large : il faut alors plus d'auditeurs.

**Résultat MOS :** à mesurer (protocole prêt). Pour situer : les voix VITS mono-locuteur
entraînées sur un corpus de taille moyenne obtiennent en général un MOS de l'ordre de 3,5 à 4.

## 3. Surveillance en production

En complément de ces mesures ponctuelles, l'API expose en continu :
- `GET /health` : la voix est-elle chargée ? la clé OpenAI est-elle présente ? (utilisé par le
  healthcheck Docker) ;
- `GET /metrics` : nombre de requêtes par route et par code HTTP, taux d'erreurs serveur, pitchs
  réussis, erreurs par type (`llm`, `voix`, `validation`, `inattendue`), tokens OpenAI consommés,
  secondes d'audio produites, durées moyenne et maximale (LLM, voix, total) ;
- `logs/api_erreurs.log` : chaque erreur avec la trace complète (rotation : 5 fichiers de 1 Mo).

Une hausse du taux d'erreurs `voix`, ou une durée de synthèse qui s'allonge, signale un problème
de la voix (mémoire, processeur) avant même que les utilisateurs ne s'en plaignent.
