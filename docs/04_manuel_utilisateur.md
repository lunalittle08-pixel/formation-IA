# Manuel utilisateur

Ce manuel explique comment utiliser le pipeline et lire ce qu'il produit. Pour les procédures
pas à pas (ajouter une source, traiter un échec…), voir le [mode opératoire](05_mode_operatoire.md).
Pour l'installation, voir le [dossier d'exploitation](06_dossier_exploitation.md).

## 1. En bref

```powershell
uv sync                        # une fois : installe l'environnement
uv run indusense run           # traite tous les fichiers de datas/
uv run pytest                  # vérifie que l'outil fonctionne
```

Toutes les commandes se lancent depuis la racine du projet (le dossier qui contient
`pyproject.toml`). Elles sont identiques sous Windows (PowerShell), macOS et Linux.

> `uv run indusense …` lance la commande du projet. Attention à ne pas confondre avec
> `uv sync` : `uv run sync` chercherait un programme nommé `sync` et échouerait.

## 2. Les commandes

| Commande | Ce qu'elle fait |
|---|---|
| `uv run indusense run` | tout : cartes d'identité, audit, brouillons de contrats, validation |
| `uv run indusense identity` | cartes d'identité seulement |
| `uv run indusense audit` | audit qualité et cartographie seulement |
| `uv run indusense contract draft` | brouillons de contrats seulement |
| `uv run indusense contract check` | vérifie la forme des contrats (sans lire les données) |
| `uv run indusense validate` | contrôle des données contre les contrats |

Options, à placer **avant** la commande :

| Option | Effet | Exemple |
|---|---|---|
| `--source <id>` | ne traite que ce fichier ; répétable. `<id>` = nom du fichier sans extension | `uv run indusense --source telemetry audit` |
| `--config <fichier>` | utilise une autre configuration | `uv run indusense --config configs/autre.yaml run` |
| `-v` | affiche plus de détails | `uv run indusense -v run` |

## 3. Ce qui s'affiche

```text
INFO    | lecture de machine.sql
INFO    | lecture de releves_incidents.csv
INFO    | lecture de telemetry.csv
INFO    | écrit artifacts/ingestion/identities/20261121/machine_184823.md
...
INFO    | manifeste : …\artifacts\ingestion\runs\20261121_184823\manifest.json
Non conformes : maintenance, telemetry
```

- `INFO` : déroulement normal ; chaque fichier produit est listé.
- `WARNING` : à regarder, mais le traitement continue (ex. aucune source trouvée,
  spécification des constats introuvable, deux fichiers de même nom).
- `ERROR` : un contrat est mal formé.
- `Non conformes : …` : ces jeux de données ne respectent pas leur contrat. **Ce n'est pas un
  plantage** : c'est le résultat du contrôle (voir § 4.4).

## 4. Lire les rapports

Tous les rapports sont dans `artifacts/ingestion/`, rangés par jour (`AAAAMMJJ/`) et
horodatés (`_HHMMSS`). Les plus récents portent l'heure la plus élevée.

### 4.1 Carte d'identité — `identities/`

Inventaire d'un fichier : chemin, taille, encodage, heures de traitement, nombre de lignes et
de colonnes, type de chaque colonne, statistiques des colonnes numériques et dates. Pour un
script SQL, une section par table, avec le type déclaré dans le script.

Types possibles : `IDENTITY` (identifiant), `INTEGER`, `FLOAT`, `BOOLEAN`, `DATETIME`, `TIME`,
`CATEGORY` (peu de valeurs différentes), `TEXT` (texte libre), `EMPTY` (vide).

### 4.2 Audit qualité — `audits/<jeu>_HHMMSS.md`

1. **Inventaire** : source, empreinte, encodage, séparateur, taille, doublons exacts.
2. **Synthèse** : nombre de constats 🔴 bloquants, 🟠 à traiter, 🔵 pour information.
3. **Profil des colonnes** : type, % de manquants, valeurs distinctes, clé candidate, min/max,
   valeurs hors bornes IQR, suspicion de donnée personnelle.
4. **Constats par dimension de qualité** :

| | Dimension | Colonne | Constat | Piste (spéc. métier) |
|---|---|---|---|---|
| 🟠 | exactitude | `rotation_mean_rpm` | valeur max 1900.0 répétée 93 fois | saturation capteur ou code d'erreur ? |

   - **Constat** : un fait mesuré, toujours vérifiable.
   - **Piste (spéc. métier)** : une hypothèse à vérifier, issue de `configs/findings.yaml`.
     Ce n'est **pas une conclusion**. La colonne n'apparaît que si la spécification en fournit.
5. **Constats neutralisés par le contrat** (si présents) : constats attendus pour une colonne,
   avec la justification écrite dans le contrat.

Les dimensions :

| Dimension | Question |
|---|---|
| complétude | manque-t-il des valeurs ? |
| unicité | y a-t-il des doublons ? |
| validité | les valeurs ont-elles un sens (constantes, négatives, redondantes) ? |
| cohérence | une même valeur est-elle écrite de plusieurs façons ? |
| exactitude | des valeurs sont-elles extrêmes ou suspectes ? |
| représentativité | toutes les catégories et entités sont-elles assez présentes ? |
| conformité | y a-t-il des données personnelles (RGPD) ? |

Le fichier `.json` du même nom contient le profil complet, pour un traitement par programme.

### 4.3 Cartographie — `audits/cartographie_HHMMSS.md`

Diagramme entité-relation (Mermaid, affiché par GitHub et VS Code) et tableau des relations :
origine (contrat ou détection automatique), cardinalité, taux d'inclusion, valeurs orphelines
(valeurs d'un jeu absentes du jeu de référence).

### 4.4 Validation — `validations/validation_HHMMSS.md`

Un verdict par jeu de données (✅ conforme / ❌ non conforme), puis le détail des violations :
règle, colonne, message, nombre de lignes concernées, exemples. Un jeu est non conforme dès
qu'une violation de sévérité erreur (🔴) existe.

### 4.5 Brouillons de contrats — `contracts/drafts/<run_id>/`

Contrats générés à partir de ce que contiennent les données. **Ils décrivent l'observé, pas
l'attendu** : ne jamais les copier tels quels dans `configs/contracts/` (voir mode opératoire).

### 4.6 Manifeste — `runs/<run_id>/manifest.json`

Trace de l'exécution : étapes, empreintes des fichiers lus, de la configuration et de la
spécification des constats, versions, fichiers produits, verdicts. C'est la seule sortie
versionnée dans Git.

## 5. Le journal de bord

Le notebook [notebooks/01_audit_journal_de_bord.ipynb](../notebooks/01_audit_journal_de_bord.ipynb)
relance le pipeline et sert à **interpréter** : pour chaque constat 🔴/🟠, remplir

> **Observation** — ce que montre l'audit (chiffre à l'appui)
> **Hypothèse** — fournie par le métier ou la spécification ; sinon, une question à poser
> **Vérification** — comment la confirmer, auprès de qui
> **Décision** — garder / minimiser / transformer / supprimer, et dans quelle couche
> **Justification** — métier, réglementaire ou technique

Ouvrir le notebook dans VS Code ou Jupyter, avec le noyau de l'environnement du projet
(`.venv`).

## 6. Questions fréquentes

**« Non conformes : … » s'affiche. Est-ce une erreur ?**
Non. Les données ne respectent pas leur contrat ; le détail est dans le rapport de validation.
La suite se décide avec le métier (mode opératoire, procédure P5). Ne pas assouplir le contrat
pour faire disparaître le message.

**`WARNING | aucune source trouvée dans …`**
Le dossier de données est vide ou ses fichiers n'ont pas une extension prévue par
`discovery.patterns` dans `configs/pipeline.yaml`.

**`WARNING | source introuvable`**
Le fichier a été déplacé ou supprimé pendant l'exécution.

**`WARNING | spécification des constats introuvable`**
`audit.findings_spec` pointe vers un fichier absent : les constats sont produits sans
hypothèse (catalogue par défaut). Vérifier le chemin.

**Le programme s'arrête sur `FindingsSpecError`.**
`configs/findings.yaml` contient une erreur : le message indique la règle et la clé en cause
(règle inconnue, variable de gabarit mal orthographiée, sévérité invalide…).

**`ERROR | contrat <id> : …`**
Le contrat est mal formé ; lancer `uv run indusense contract check` pour la liste complète.

**Un fichier n'est pas lu correctement (colonnes collées, caractères accentués cassés).**
Imposer le séparateur ou l'encodage par une règle `discovery.overrides` (procédure P3).

**Un jeu de données apparaît « sans contrat ».**
Il n'existe pas de fichier `configs/contracts/<id>.yaml` dont `contract.id` vaut l'identifiant
du jeu. Créer le contrat (procédure P4).

**Où sont mes anciens rapports ?**
Dans `artifacts/ingestion/`, rangés par date. Ils ne sont pas versionnés : s'ils ont été
supprimés, relancer `uv run indusense run` pour les régénérer.
