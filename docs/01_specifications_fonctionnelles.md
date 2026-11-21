# Spécifications fonctionnelles

## 1. Contexte

InduSense 4.0 est le projet fil rouge de la formation « Concevoir et implémenter une solution
d'IA ». Le cas d'usage est la **maintenance prédictive** : anticiper les pannes de machines à
partir de leur télémétrie, de leurs incidents et de leur historique de maintenance.

Le **sprint 1** vise à *cadrer et fiabiliser les données* avant toute modélisation
(compétences C1, C2, C3). Un modèle entraîné sur des données mal comprises produit des
prédictions fausses avec assurance : il faut d'abord savoir **ce que contiennent les fichiers,
quelle est leur qualité, et ce qu'on en attend**.

Le pipeline InduSense est l'outil de ce sprint. Il est conçu pour rester **générique** :
il ne contient aucun nom de fichier, de colonne ni de vocabulaire propre au cas d'usage, et
peut être réutilisé sur d'autres données.

## 2. Objectifs

| # | Objectif | Compétence |
|---|---|---|
| O1 | Inventorier chaque fichier de données reçu (carte d'identité) | C1 |
| O2 | Mesurer la qualité des données selon des dimensions reconnues et en garder la trace | C1, C3 |
| O3 | Repérer les données personnelles et appeler une décision de minimisation | C2 |
| O4 | Formaliser l'attendu de chaque jeu de données dans un contrat versionné | C3 |
| O5 | Contrôler automatiquement les données contre leur contrat | C3 |
| O6 | Rendre chaque exécution traçable et rejouable | C3 |

## 3. Périmètre

**Dans le périmètre**

- Fichiers déposés dans le dossier de données : CSV/TSV et scripts SQL (`CREATE TABLE` + `INSERT`).
- Inventaire, profilage, constats de qualité, cartographie des relations entre jeux de données.
- Contrats de données : brouillon généré, contrôle de structure, validation des données.
- Rapports Markdown/JSON horodatés et manifeste d'exécution.

**Hors périmètre** (sprints suivants ou autres outils)

- Nettoyage, correction ou transformation des données : l'audit **décrit**, il ne modifie rien.
- Construction des couches Bronze / Silver / Gold.
- Modèle de prédiction, tableaux de bord.
- Ordonnancement automatique des exécutions (exécution manuelle à ce stade).
- Connexion directe à une base de données : seuls des fichiers sont lus.

## 4. Acteurs

| Acteur | Rôle vis-à-vis du pipeline |
|---|---|
| Utilisateur technique (apprenant, data engineer) | dépose les fichiers, lance le pipeline, rédige les contrats, interprète les constats dans le journal de bord |
| Référent métier | explique la signification des valeurs, valide les bornes, les hypothèses et les décisions |
| Responsable d'un jeu de données (owner) | garant du contrat de son jeu de données — **à définir** pour chaque contrat |
| Évaluateur (formation) | vérifie la démarche à partir des rapports, du journal de bord et de l'historique Git |
| Agent IA (Claude Code) | assiste l'utilisateur technique selon `CLAUDE.md` et les skills de `.claude/skills/` ; n'interprète pas sans source, pose des questions |

## 5. Fonctions

### F1 — Découverte des sources

Le pipeline traite tout fichier du dossier de données dont le nom correspond à un motif de
`discovery.patterns` (par défaut `*.csv`, `*.tsv`, `*.sql`, `*.sql.txt`). Aucun nom de fichier
n'est déclaré. L'identifiant d'une source est le nom du fichier sans extension.

*Critères d'acceptation* : un fichier déposé est traité sans modification de la config ; un
fichier d'une autre extension est ignoré ; deux fichiers de même base reçoivent deux
identifiants distincts (avertissement affiché). — tests `test_io.py`.

### F2 — Carte d'identité d'un fichier

Pour chaque fichier : chemin, taille, encodage, heures de début et de fin de traitement,
nombre de lignes et de colonnes, type inféré de chaque colonne, min / max / moyenne / médiane
des colonnes numériques et dates. Un script SQL donne une section par table, avec le type SQL
déclaré à côté du type inféré.

*Critères d'acceptation* : format de la carte du TP1 (`- Source:`, `- Filesize :`,
`- ProcessBeginAt:`…) — test `test_pipeline.py`.

### F3 — Audit qualité

Pour chaque jeu de données : profil des colonnes et **constats** classés par dimension de
qualité (complétude, unicité, validité, cohérence, exactitude, représentativité, conformité)
et par sévérité (🔴 bloquant, 🟠 à traiter, 🔵 information).

Règles fonctionnelles :

- un constat énonce un **fait mesuré** (« valeur max 1900 répétée 93 fois ») ;
- une **piste d'explication** n'apparaît que si la spécification métier du projet en fournit
  une ; elle est affichée dans une colonne séparée (« Piste (spéc. métier) ») ;
- sans spécification métier, le rapport ne contient que des faits ;
- un constat attendu pour une colonne, validé par le métier, peut être **neutralisé** dans le
  contrat avec une justification obligatoire ; il reste visible dans une section dédiée.

*Critères d'acceptation* — tests `test_findings_spec.py`, `test_profiling.py`.

### F4 — Détection des données personnelles

Une colonne est signalée « donnée personnelle possible » si son nom correspond à un motif ou
si la majorité de ses valeurs a un format d'e-mail ou de téléphone. Le signalement appelle une
décision (`privacy` dans le contrat : garder, supprimer, pseudonymiser, agréger, à décider).

### F5 — Cartographie des relations

Relations entre jeux de données : déclarées dans les contrats (`references`) ou détectées
automatiquement (même nom de colonne). Pour chacune : cardinalité, taux d'inclusion, valeurs
orphelines. Diagramme entité-relation Mermaid.

### F6 — Brouillon de contrat

À partir de l'observé, le pipeline génère un brouillon de contrat par jeu de données (types,
complétude, motifs d'identifiants, bornes et modalités observées, données personnelles). Le
brouillon n'écrase jamais un contrat existant ; il est relu, complété (sens métier, unités,
bornes **métier**) puis promu manuellement dans `configs/contracts/`.

### F7 — Contrôle de structure des contrats

Vérifie la forme de chaque contrat (champs obligatoires, types connus, clés autorisées,
exceptions d'audit justifiées). Code retour non nul en cas d'erreur, pour une intégration
continue.

### F8 — Validation des données contre les contrats

Contrôle schéma (colonnes absentes ou en trop), nombre de lignes, doublons, clé primaire,
complétude, type, unicité, format, liste de valeurs, bornes, intégrité référentielle. Verdict
par jeu de données : conforme / non conforme. Code retour non nul si un jeu est non conforme.

Règle de gestion : **un contrat ne s'assouplit pas pour faire passer des données**. Une
violation se qualifie (donnée fautive, contrat faux, écart tolérable) et toute modification du
contrat est justifiée.

### F9 — Traçabilité

Chaque exécution écrit un manifeste : étapes, empreintes SHA-256 des fichiers lus, de la
configuration et de la spécification des constats, versions des outils, fichiers produits,
synthèse des verdicts.

### F10 — Journal de bord

Le notebook `notebooks/01_audit_journal_de_bord.ipynb` exécute le pipeline et accueille
l'interprétation : pour chaque constat, *observation → hypothèse → vérification → décision →
justification*. Il importe le package : aucune logique métier n'y est dupliquée.

## 6. Exigences non fonctionnelles

| Exigence | Traduction |
|---|---|
| Généricité | aucun nom de fichier, de colonne, de seuil ou de vocabulaire métier dans le code (`src/`) ; tout est en configuration, contrat ou spécification |
| Non-interprétation | les rapports énoncent des faits ; les hypothèses viennent du métier |
| Reproductibilité | mêmes fichiers + même configuration ⇒ mêmes constats ; dépendances figées (`uv.lock`) |
| Traçabilité | manifeste par exécution ; historique Git des contrats et de la configuration |
| Non-altération | les sources de `datas/` sont en lecture seule ; aucune donnée n'est modifiée |
| Confidentialité | `datas/` et les rapports générés ne sont pas versionnés (`.gitignore`) |
| Qualité du code | fonctions courtes, docstrings en français, un test pytest par règle |

## 7. État constaté sur les données actuelles (21/11/2026)

À titre d'illustration — ce n'est pas une exigence :

| Jeu de données | Source | Lignes | Verdict du contrat |
|---|---|---|---|
| `machine` | `machine.sql` | 15 | conforme |
| `maintenance` | `machine.sql` | 115 | non conforme : `maintenance_type` contient `proactive` / `reactive` au lieu de `preventive` / `corrective` |
| `releves_incidents` | `releves_incidents.csv` | 1 245 | conforme |
| `telemetry` | `telemetry.csv` | 135 626 | non conforme : 1 346 doublons (machine, heure), valeurs manquantes sur `pressure_bar` et `rotation_mean_rpm` |

Ces écarts relèvent de décisions métier (voir le mode opératoire, procédure « Traiter un échec
de validation ») ; aucun contrat n'a été assoupli.

## 8. Points ouverts

- Responsable (owner) de chaque jeu de données : à définir.
- Hypothèses de `configs/findings.yaml` (activées le 21/11/2026) : à confirmer avec le métier.
- Décisions sur les non-conformités de `maintenance` et `telemetry` : à prendre avec le métier.
