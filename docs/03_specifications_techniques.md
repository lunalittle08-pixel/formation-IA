# Spécifications techniques

Référence exacte du comportement du pipeline. En cas de doute, le code et ses tests
(`tests/`) font foi ; ce document doit être mis à jour dans la même branche que le code.

## 1. Configuration — `configs/pipeline.yaml`

Les chemins relatifs sont résolus depuis la racine du projet (parent du dossier `configs/`).
L'empreinte SHA-256 du fichier est enregistrée dans chaque manifeste.

| Clé | Type | Rôle |
|---|---|---|
| `paths.data_dir` | chemin | dossier des sources (lecture seule) |
| `paths.contracts_dir` | chemin | dossier des contrats |
| `paths.artifacts_dir` | chemin | racine des sorties |
| `paths.identities`, `audits`, `validations`, `drafts`, `runs` | nom | sous-dossiers de `artifacts_dir` |
| `discovery.patterns` | liste de motifs glob | fichiers traités dans `data_dir` |
| `discovery.overrides` | liste de règles | options de lecture imposées par motif (voir § 2) |
| `reading.candidate_separators` | liste | séparateurs testés pour les CSV |
| `reading.sniff_bytes` | entier | taille de l'échantillon pour détecter le séparateur |
| `relations.ignore_columns` | liste | colonnes exclues de la détection automatique de relations |
| `inference.na_tokens` | liste | jetons de valeur manquante (comparaison sans casse, après `strip`) |
| `inference.parse_rate_min` | 0–1 | part minimale de valeurs convertibles pour retenir un type |
| `inference.category_max_unique` | entier | au-delà, un texte n'est plus une catégorie |
| `inference.min_rows_for_identity` | entier | taille minimale pour conclure qu'une colonne unique est un identifiant |
| `inference.identity_name_pattern` | regex | nom de colonne qui désigne un identifiant |
| `inference.time_pattern` | regex | heure seule (`HH:MM[:SS]`) |
| `inference.bool_values` | liste | valeurs admises pour un booléen |
| `audit.findings_spec` | chemin, facultatif | spécification métier des constats (voir § 5) |
| `audit.targets` | liste | colonnes cibles dont on publie la distribution |

## 2. Découverte des sources — `config.py`

1. Pour chaque motif de `discovery.patterns`, liste les fichiers de `data_dir` (non récursif),
   sans doublon, triés par nom (insensible à la casse).
2. Identifiant = nom sans **aucune** extension (`machine.sql.txt` → `machine`). En cas de
   collision, le second fichier reçoit son nom complet, caractères non alphanumériques
   remplacés par `_` (`machine.sql` → `machine_sql`), avec un avertissement.
3. Options de lecture : pour chaque règle de `discovery.overrides` dont `match` (glob,
   insensible à la casse) couvre le nom du fichier, les clés `format`, `encoding`, `separator`,
   `sql_dialect` sont appliquées dans l'ordre ; la dernière règle qui correspond l'emporte.
   Clés inconnues ignorées.
4. Format `auto` : `sql` si l'une des extensions est `.sql`, sinon `csv`.

## 3. Lecture — `io/`

**Encodage** (`encoding: auto`) :

| Contenu | Libellé | Encodage Python |
|---|---|---|
| commence par `EF BB BF` | UTF-8 (BOM) | `utf-8-sig` |
| décodable en UTF-8, uniquement ASCII | ASCII (compatible UTF-8) | `utf-8` |
| décodable en UTF-8 | UTF-8 | `utf-8` |
| sinon, contient un octet `0x80`–`0x9F` | Windows-1252 | `cp1252` |
| sinon | ISO-8859-1 | `latin-1` |

Un encodage imposé (`encoding: cp1252`) est utilisé tel quel.

**CSV** : séparateur détecté par `csv.Sniffer` sur les `sniff_bytes` premiers octets parmi
`candidate_separators` (repli : `,`), sauf s'il est imposé. Lecture
`pandas.read_csv(dtype=str, keep_default_na=False, na_filter=False)` : aucune conversion,
aucune valeur manquante implicite.

**SQL** : analyse par `sqlglot` dans le dialecte `sql_dialect`. Un `Dataset` par table :
colonnes et types déclarés des `CREATE TABLE`, lignes des `INSERT … VALUES` (avec ou sans
liste de colonnes). `NULL` → chaîne vide, booléens → `true`/`false`. Les autres instructions
(`CREATE INDEX`…) sont ignorées.

**Empreinte** : SHA-256 du fichier, lu par blocs de 1 Mo.

## 4. Profilage — `profiling/types.py`, `profiling/profile.py`

### 4.1 Inférence du type logique

Sur les valeurs présentes (hors `na_tokens`, après `strip`), règles testées **dans l'ordre** :

| # | Type | Condition |
|---|---|---|
| 1 | `EMPTY` | aucune valeur présente |
| 2 | `BOOLEAN` | toutes les valeurs dans `bool_values` et au plus 2 valeurs distinctes |
| 3 | `IDENTITY` | le nom correspond à `identity_name_pattern` |
| 4 | `INTEGER` / `FLOAT` | ≥ `parse_rate_min` de valeurs numériques (espaces retirés, virgule → point) ; entier si toutes sans partie décimale |
| 5 | `TIME` | ≥ `parse_rate_min` de valeurs au format `time_pattern` |
| 6 | `DATETIME` | ≥ `parse_rate_min` de valeurs contenant `-`, `/` ou `:` et convertibles (ISO 8601 d'abord, sinon jour en premier) ; fuseau détecté → UTC |
| 7 | `IDENTITY` | valeurs toutes distinctes et au moins `min_rows_for_identity` lignes |
| 8 | `CATEGORY` | au plus `category_max_unique` valeurs distinctes |
| 9 | `TEXT` | sinon |

### 4.2 Profil d'une colonne

Lignes, manquants (nombre et %), valeurs distinctes (nombre et % des présentes), 3 exemples,
type SQL déclaré éventuel, statistiques (numériques : min, max, moyenne, médiane, écart-type,
Q1, Q3, asymétrie ; dates et heures : min, max), effectifs des 30 premières modalités
(catégories et booléens).

**Clé candidate** : type `IDENTITY`, `CATEGORY` ou `INTEGER`, aucune valeur manquante, toutes
les valeurs distinctes, plus d'une ligne.

### 4.3 Détecteurs et règles de constats

Chaque détecteur **mesure** et transmet des variables ; la règle du même nom fournit
dimension, sévérité, seuils (`params`) et gabarits. Valeurs par défaut :
`src/indusense/profiling/default_findings.yaml`.

| Règle | Dimension | Sév. défaut | Condition de déclenchement | `params` (défaut) | Variables |
|---|---|---|---|---|---|
| `empty_column` | complétude | error | type `EMPTY` | — | — |
| `missing_high` | complétude | error | % manquants > seuil | `threshold_pct: 60` | `pct` |
| `missing_moderate` | complétude | warning | sinon, % manquants > seuil | `threshold_pct: 5` | `pct` |
| `time_gaps` | complétude | info | écarts > facteur × pas médian de l'entité | `gap_factor: 2` | `count`, `max_count`, `entity` |
| `duplicate_rows` | unicité | warning | lignes identiques | — | `count` |
| `composite_key_duplicates` | unicité | warning | doublons sur (entité, horodatage) | — | `count`, `entity`, `time` |
| `identity_incomplete` | unicité | info | `IDENTITY` non clé, unique sur les présentes, avec manquants | — | `missing` |
| `constant_column` | validité | warning | une seule valeur distincte, plus d'une ligne | — | `value` |
| `isolated_negatives` | validité | warning | 0 < négatifs < part × présentes | `max_share: 0.05` | `count`, `share` |
| `redundant_columns` | validité | info | \|corrélation\| ≥ seuil entre deux numériques | `min_corr: 0.95` | `corr` |
| `inconsistent_labels` | cohérence | warning | modalités égales à la casse et aux espaces près | — | `groups` |
| `iqr_outliers` | exactitude | warning | valeurs hors [Q1 − f·IQR ; Q3 + f·IQR] | `factor: 1.5` | `count`, `low`, `high` |
| `repeated_extreme` | exactitude | warning | min ou max répété ≥ seuil, et plus de N valeurs distinctes | `min_repeats: 5`, `min_distinct: 20` | `bound`, `value`, `count` |
| `skewed_distribution` | exactitude | info | \|asymétrie\| > seuil et au moins un outlier IQR | `min_abs_skew: 1` | `skew` |
| `class_imbalance` | représentativité | warning | catégorie : effectif max / min ≥ rapport | `ratio: 10` | `rare`, `rare_count`, `major`, `major_count`, `ratio_observed` |
| `underrepresented` | représentativité | warning | sinon, > 2 modalités dont certaines < rapport × médiane | `ratio: 0.3` | `values`, `median` |
| `sparse_entities` | représentativité | warning | pas médian d'une entité > facteur × pas global | `step_factor: 1.5` | `entities`, `step`, `global_step` |
| `target_distribution` | représentativité | info | colonne listée dans `audit.targets` | — | `distribution` |
| `pii_suspected` | conformité | warning | nom ou format (e-mail, téléphone) | `name_pattern`, `email_pattern`, `phone_pattern`, `min_share: 0.5`, `sample_rows: 500` | `reasons` |

Notes :

- `iqr_outliers`, `repeated_extreme`, `skewed_distribution`, `isolated_negatives` : colonnes
  `INTEGER`/`FLOAT` seulement.
- `class_imbalance`, `underrepresented` : colonnes `CATEGORY`, et pour `underrepresented`
  aussi les `IDENTITY` non clés d'au plus `category_max_unique` valeurs (clés étrangères).
- `composite_key_duplicates`, `sparse_entities`, `time_gaps` : seulement si aucune colonne
  n'est clé candidate ; entité = première colonne `IDENTITY`/`CATEGORY` ayant entre 2 et
  `category_max_unique` valeurs, horodatage = première colonne `DATETIME`.
- Les variables d'un gabarit sont celles du détecteur **plus** les `params` de la règle.

## 5. Spécification des constats — trois niveaux

**Niveau 1 — catalogue par défaut** (`default_findings.yaml`, dans le package) : toutes les
règles, messages factuels, aucune `hypothesis`.

**Niveau 2 — spécification métier** (`audit.findings_spec`, ex. `configs/findings.yaml`) :
facultative. Si le chemin est déclaré mais absent : avertissement, catalogue par défaut seul.

```yaml
rules:
  <règle>:            # doit exister dans le catalogue
    severity: info | warning | error
    enabled: true | false
    params: {...}     # fusionnés avec ceux du catalogue
    message: "..."    # gabarit Python str.format
    hypothesis: "..." # piste fournie par le métier
```

Fusion : les `params` sont fusionnés clé par clé, les autres clés remplacent. Une règle
`enabled: false` ne produit aucun constat.

**Niveau 3 — exception de colonne** (contrat, clé `audit:` de la colonne) :

```yaml
- name: <colonne>
  audit:
    <règle>: {enabled: false, reason: "<justification>"}   # aussi : severity, params, hypothesis
```

`reason` est obligatoire. Un constat neutralisé (`enabled: false`) est **conservé** avec sa
justification et affiché dans la section « Constats neutralisés par le contrat » ; il n'est
pas compté dans la synthèse.

**Contrôles au chargement** (`FindingsSpecError`, arrêt du programme) : règle inconnue, clé
inconnue, sévérité ou dimension invalide, message manquant, variable de gabarit inconnue,
règle du catalogue absente. Les exceptions de contrat sont contrôlées par
`indusense contract check`.

## 6. Contrats de données — `contracts/`

### 6.1 Format

```yaml
apiVersion: indusense/v1
kind: DataContract
contract: {id, version, status: draft|active|deprecated, owner, description}
source: {path, format, table, encoding, separator}   # descriptif, non contrôlé
schema:
  primary_key: [colonnes]
  allow_extra_columns: false
  columns: [ {name, type, ...} ]
quality: {min_rows, max_duplicate_rows}
```

Le contrat est associé au jeu de données par `contract.id` (= identifiant du `Dataset` :
nom de fichier sans extension pour un CSV, nom de table pour un SQL).

### 6.2 Clés de colonne

| Clé | Contrôlée à la validation | Rôle |
|---|---|---|
| `name`, `type` | oui | nom, type logique attendu (§ 4.1) |
| `required` | oui | aucune valeur manquante |
| `max_missing_pct` | oui | part maximale de manquants |
| `unique` | oui | pas de doublon |
| `pattern` | oui | regex que chaque valeur doit respecter entièrement |
| `allowed_values` | oui | liste fermée de valeurs |
| `min`, `max` | oui | bornes, comparées après conversion dans le type |
| `references` | oui | `<jeu>.<colonne>` : intégrité référentielle |
| `on_fail` | — | `error` (défaut) ou `warning` : sévérité des violations de la colonne |
| `audit` | — | exceptions de constats (§ 5) |
| `description`, `unit`, `declared_type`, `observed` | non | documentation |
| `pii`, `privacy` | non (privacy ∈ keep, drop, pseudonymize, aggregate, to_decide) | décision de minimisation |

### 6.3 Contrôle de structure (`indusense contract check`)

`kind`, `contract.id`, `version`, `status` présents et valides ; colonnes non vides, sans
doublon, type connu, clés connues ; `privacy` valide ; clé primaire parmi les colonnes ;
exceptions `audit:` valides et justifiées.

### 6.4 Validation (`indusense validate`)

| Règle | Niveau | Sévérité |
|---|---|---|
| `schema` — colonnes attendues absentes | jeu | error |
| `schema` — colonnes non prévues (si `allow_extra_columns: false`) | jeu | error |
| `min_rows` | jeu | error |
| `duplicates` — lignes identiques > `max_duplicate_rows` | jeu | error |
| `primary_key` — doublons sur la clé | jeu | error |
| `required`, `max_missing_pct`, `type`, `unique`, `pattern`, `allowed_values`, `min`, `max` | colonne | `on_fail` |
| `references` — valeurs absentes du jeu parent | colonne | `on_fail` |

Verdict : **conforme** s'il n'y a aucune violation de sévérité `error`. Les jeux de données
sans contrat sont listés à part.

### 6.5 Brouillon (`indusense contract draft`)

Version `0.1.0`, statut `draft`, `owner` et descriptions `TODO`. Par colonne : type inféré,
`required` si aucun manquant (sinon `max_missing_pct` = 1,5 × observé, minimum 1 %), `unique`
et `pattern` (forme commune des valeurs) pour les identifiants, bornes et modalités
**observées**, `pii: true` + `privacy: to_decide` si suspicion, statistiques sous `observed`.

## 7. Cartographie — `profiling/relations.py`

- Relations **déclarées** : chaque `references` de contrat dont les deux jeux et colonnes sont
  présents.
- Relations **automatiques** : colonnes de même nom dans deux jeux, hors
  `relations.ignore_columns` et hors paires déjà déclarées. Parent = côté où la colonne est
  unique ; si aucun ou les deux, le sens qui révèle le plus d'orphelines.
- Mesures : cardinalité (`1-1`, `1-n`, `n-n`), taux d'inclusion (part des valeurs enfant
  présentes chez le parent), orphelines (10 premières conservées).
- Seules les relations déclarées produisent des violations `references`.

## 8. Sorties — `artifacts/ingestion/`

| Étape | Fichier | Contenu |
|---|---|---|
| identity | `identities/AAAAMMJJ/<fichier>_HHMMSS.md` (heure de début de lecture) | carte d'identité |
| audit | `audits/AAAAMMJJ/<jeu>_HHMMSS.md` et `.json` | rapport (inventaire avec taille, profil des colonnes, statistiques min / max / moyenne / médiane / écart-type / Q1 / Q3, constats) et profil complet (`DatasetProfile`) |
| audit | `audits/AAAAMMJJ/cartographie_HHMMSS.md` et `.json` | relations, diagramme Mermaid |
| draft | `contracts/drafts/<run_id>/<jeu>.yaml` | brouillon de contrat |
| validate | `validations/AAAAMMJJ/validation_HHMMSS.md` et `.json` | verdicts et violations |
| toutes | `runs/<run_id>/manifest.json` | manifeste du run |

`run_id` = `AAAAMMJJ_HHMMSS` du début du run. Chaque exécution produit de nouveaux fichiers ;
deux exécutions lancées dans la même seconde écriraient aux mêmes chemins (la seconde
remplace la première).

**Manifeste** :

```json
{
  "run_id": "20261121_194422",
  "started_at": "...", "finished_at": "...",
  "stages": ["identity", "audit", "draft", "validate"],
  "config": {"path": "configs/pipeline.yaml", "sha256": "..."},
  "findings_spec": {"path": "configs/findings.yaml", "sha256": "..."},
  "versions": {"indusense": "0.1.0", "python": "...", "pandas": "..."},
  "inputs": [{"path": "datas/...", "sha256": "...", "size_bytes": 0, "charset": "...",
              "datasets": {"<jeu>": [lignes, colonnes]}}],
  "outputs": ["artifacts/ingestion/..."],
  "summary": {"validation": {"<jeu>": true}, "without_contract": []}
}
```

`findings_spec` vaut `null` si aucune spécification métier n'est utilisée.

## 9. Interface en ligne de commande — `cli.py`

```
indusense [--config CHEMIN] [--source ID]... [-v] COMMANDE
```

| Commande | Étapes | Code retour |
|---|---|---|
| `identity` | identity | 0 |
| `audit` | audit | 0 |
| `contract draft` | draft | 0 |
| `contract check` | contrôle de structure (aucune lecture de données) | 1 si un contrat est invalide |
| `validate` | validate | 1 si un jeu est non conforme |
| `run` | identity, audit, draft, validate | 1 si un jeu est non conforme |

Chaque commande (sauf `contract check`) écrit un manifeste. `--source` (répétable) restreint
aux identifiants donnés. `-v` active les messages de débogage. Une erreur de configuration
(fichier YAML invalide, `FindingsSpecError`) interrompt le programme avec un message d'erreur
Python et un code retour non nul.

## 10. Tests — `tests/`

| Fichier | Couvre |
|---|---|
| `test_io.py` | encodage, taille, séparateur, découverte, identifiants, options par motif, lecture SQL |
| `test_profiling.py` | inférence de types, constats sur un jeu de test |
| `test_findings_spec.py` | catalogue complet et factuel, spécification métier, erreurs de spécification, exceptions de contrat |
| `test_relations.py` | colonnes exclues de la détection automatique |
| `test_contracts.py` | contrats du projet bien formés, clé inconnue, forme des identifiants, violations, `on_fail: warning`, dérive de schéma |
| `test_pipeline.py` | run complet (cartes, validation, manifeste), codes retour de la CLI |

Les tests travaillent sur un mini-projet temporaire (`conftest.py`) : ils ne lisent pas
`datas/`. Lancement : `uv run pytest`.
