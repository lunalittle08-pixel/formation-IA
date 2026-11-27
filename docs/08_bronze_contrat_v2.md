# Bronze v2 : alignement sur les consignes du formateur

**Projet InduSense, Sprint 1.** Ce document décrit le Bronze **tel qu'il est implémenté** sur la
branche `feat/bronze-contrat-v2`. Il remplace les parties du
[guide des couches](07_guide_couches_bronze_silver_gold.md) qui traitent du Bronze (1.4, 2.2,
6.3, la partie Bronze de 6.7, 9.1 et 9.3). Le Silver et le Gold de ce guide restent valables,
à deux exceptions près, décrites en partie 6 : la pseudonymisation et le lignage.

Références du formateur : `01-INGESTION_PROCESS.md` (contrat Bronze v2),
`02-GUIDE_INGESTION_DONNEES.md` (méthode), `guide_ingestion_medallion_indusense.md` (architecture),
`13_RGPD_7_principes_Indusense.md` (RGPD).

---

## 1. Ce qui a changé et pourquoi

| Sujet | Avant | Maintenant | Consigne |
|---|---|---|---|
| Colonnes de valeur | `x` (texte) + `x_type` (**nom** du type) | `x_raw` (texte, intact) + `x` (**valeur convertie**) | « aucune perte » : un miroir texte et une colonne typée au mieux |
| Échec de conversion | invisible au Bronze | `x` à NULL et motif dans `_cast_errors` (JSONB) | distinguer *absent* et *illisible* |
| Traçabilité | une colonne `hash` | `_batch_id`, `_source_file`, `_source_hash`, `_row_number`, `_ingested_at`, `_row_hash`, `_cast_errors` | chaque ligne porte son fichier, son empreinte, son numéro et son lot |
| Rejeu | table vidée puis rechargée | clé primaire `(_source_hash, _row_number)` + `ON CONFLICT DO NOTHING` | idempotence portée par la base, pas par le code |
| Journal | aucun | table `bronze.ingestion_batches_log`, une ligne par tentative | registre des lots |
| Seuil | aucun | part maximale de lignes en erreur de conversion (`0.0` par défaut) ; au-delà, rien n'est écrit | tout ou rien |
| Pseudonymisation | **au Bronze** | **au Silver** (opération ANO) | le Bronze ne transforme rien |
| Contrôles | `contract check` (forme du contrat) | contrat confronté au **fichier réel** avant toute écriture | erreur localisée : colonne + fichier |
| Livrable | — | dictionnaire de données avec section `transformations` | rapport + dictionnaire |

**Écart assumé avec la consigne : le nom de fichier.** Le contrat du formateur indique le
chemin du fichier (`source.path`). Le dépôt garde la **découverte automatique**, qui rattache
un contrat au fichier par son identifiant (nom sans extension). C'est une décision du projet,
inscrite dans le `CLAUDE.md`.

---

## 2. La table Bronze

Pour chaque colonne source `x`, deux colonnes, puis les colonnes techniques :

```sql
-- exemple : bronze.releves_incidents
severity_raw   TEXT,     -- '4' : la valeur d'origine, intacte
severity       BIGINT,   -- 4   : convertie au mieux vers le type du contrat
...
_batch_id      UUID,     -- lot d'ingestion
_source_file   TEXT,     -- datas/releves_incidents.csv
_source_hash   TEXT,     -- SHA-256 des OCTETS du fichier
_row_number    BIGINT,   -- 1 = premier enregistrement de données
_ingested_at   TIMESTAMPTZ,
_row_hash      TEXT,     -- SHA-256 des valeurs d'origine de la ligne
_cast_errors   JSONB,    -- {} si tout est convertible
PRIMARY KEY (_source_hash, _row_number)
```

Le type de `x` vient du **contrat** (l'attendu). Une colonne en plus, autorisée par
`allow_extra_columns: true`, prend le type inféré.

### Les états d'une valeur

| Situation | `x_raw` | `x` | `_cast_errors` |
|---|---|---|---|
| Valeur présente et convertible | valeur d'origine | valeur typée | — |
| Case vide | `''` | `NULL` | — (une absence n'est pas une erreur) |
| Valeur non convertible (`n/a` dans une colonne numérique) | valeur d'origine | `NULL` | `{"x": "conversion FLOAT impossible : 'n/a'"}` |

Seule une case **vide** est une absence au Bronze. Les jetons `n/a`, `null`, `-` sont des valeurs
envoyées par la source : le Bronze les garde et trace l'échec. C'est le Silver qui applique la
liste `inference.na_tokens`.

### Deux empreintes, deux usages

- `_source_hash` : identité du **fichier**. Avec `_row_number`, elle forme la clé primaire. Le
  même fichier rechargé n'insère rien. Un fichier modifié (autre empreinte) est un nouveau lot.
- `_row_hash` : identité du **contenu** d'une ligne. Le Silver s'en sert pour retirer les
  doublons stricts (deux lignes identiques dans des positions différentes).

---

## 3. Le chargement, étape par étape

1. **Lire** chaque source découverte dans `datas/` (CSV ou dump SQL lu avec `sqlglot`, jamais
   exécuté).
2. **Confronter le contrat au fichier réel** (`layers/checks.py`). Chaque problème est listé,
   avec la colonne et la source :
   - pas de contrat pour ce jeu ;
   - contrat mal formé (`contract check`, y compris une clé primaire non déclarée) ;
   - colonne déclarée mais absente du fichier, avec une suggestion
     (« Vouliez-vous dire 'duration_hours' ? ») ;
   - colonne du fichier absente du contrat (bloquant si `allow_extra_columns: false`) ;
   - colonne d'un dump SQL présente au `CREATE TABLE` mais fournie par aucun `INSERT`, sauf si
     le contrat la déclare comme champ de traçabilité (`traceability: true`) : elle est alors
     créée et chargée vide (NULL) au Bronze, avec un avertissement, et n'est pas reprise au
     Silver (décision du formateur, 2026-11-26) ;
   - aucune ligne à ingérer ;
   - noms en collision (`x` et `x_raw` déjà présents, nom réservé aux colonnes techniques) ou
     trop longs pour PostgreSQL.

   Une seule erreur suffit : le jeu est refusé et rien n'est écrit.
3. **Construire** la table en mémoire (`layers/bronze.py`) et compter les lignes en erreur de
   conversion.
4. **Appliquer le seuil** : au-delà de `max_cast_error_ratio`, le lot est refusé avant toute
   écriture. Un contrat peut fixer sa propre valeur dans `source.max_cast_error_ratio`. Cette
   modification est une décision à justifier, jamais un moyen de faire passer des données.
5. **Écrire dans une transaction** : `INSERT ... ON CONFLICT DO NOTHING`. Le chargement ne
   crée ni ne modifie aucune table : elles sont créées par les migrations (section 4 bis). Si la
   table est absente ou a une autre structure, le lot est refusé avec la liste des écarts.
6. **Inscrire le lot au registre**, dans sa propre transaction, pour qu'un échec reste tracé :
   statut (`SUCCESS` ou `FAILED`), lignes lues, insérées et déjà présentes, lignes en erreur de
   conversion, empreinte du fichier, version et empreinte du contrat, message.
7. **Écrire le dictionnaire de données** (`artifacts/ingestion/layers/AAAAMMJJ/bronze_<jeu>_HHMMSS.md`
   et `.json`) :
   - une ligne par colonne : type, type SQL, vides, échecs de conversion, taux de NULL, min, max ;
   - la section `transformations` : ce que le Bronze a fait (CAST, COPY, HASH, TECH) ;
   - la section `silver_operations` : ce que le contrat confie au Silver (DEL, ANO, DROP, TODO).

---

## 4. Commandes

```bash
uv run indusense db upgrade                      # une fois, et après chaque nouvelle migration
uv run indusense load bronze                     # charge les sources ; rejouer n'insère rien
uv run indusense --source machine load bronze    # une seule source (option AVANT `load`)
uv run indusense load all                        # bronze -> silver -> gold
```

Le chargement vérifie d'abord que la base est à la dernière migration. Sinon il s'arrête, sans
rien écrire, et indique la commande à lancer.

---

## 4 bis. Les migrations de schéma (Alembic)

Règle du guide 01 (§1 « DDL revue », étape 4) : **un changement de structure passe par une
migration Alembic, jamais par un `CREATE TABLE` implicite**. Il n'y a plus d'option `--rebuild`.

**Ce qui est géré.** Le schéma Bronze : une table par jeu de données chargeable, plus le registre
des lots. La révision appliquée est rangée dans `bronze.alembic_version`. Le Silver et le Gold
ne sont pas concernés : ils sont recalculés entièrement à chaque exécution, à partir du Bronze.

**La structure attendue n'est écrite nulle part à la main.** Elle est calculée à partir des
contrats et des fichiers réels de `datas/`, par les mêmes fonctions que le chargement
(`layers/migrate.py` → `stage.bronze_table`). Ajouter un fichier, une colonne ou changer un type
dans un contrat produit donc un écart, que la migration montre.

**Le cycle** :

```bash
uv run indusense db check                              # 1. comparer : écarts base / attendu
uv run indusense db revision -m "motif du changement"  # 2. GÉNÉRER le script (non appliqué)
#                                                         3. RELIRE migrations/versions/<script>.py
uv run indusense db upgrade                            # 4. appliquer
uv run indusense db current                            # révision appliquée / dernière
uv run indusense db downgrade -1                       # annuler la dernière migration
```

- `revision` ne crée pas de script vide : sans écart, il le dit et s'arrête.
- `revision` **signale les opérations destructrices** (suppression de table, de colonne ou de
  contrainte, changement de type) : au Bronze, une suppression perd des lots chargés. On les
  relit avant `upgrade`, et on les justifie dans le message et dans le commit.
- Un script est un fichier versionné (`migrations/versions/`), relu comme du code, commité sur la
  branche du changement.
- Limite d'Alembic : un changement de **clé primaire** n'est pas détecté. Il faut l'écrire à la
  main dans le script.
- `alembic.ini` ne contient aucun identifiant : la connexion vient du `.env`. On peut aussi
  appeler `uv run alembic ...` directement (option `-x config=chemin/pipeline.yaml`).

**Exemple, changement de type dans un contrat** (`is_active` : `BOOLEAN` → `TEXT`) :
`db revision` produit un `op.alter_column(..., type_=sa.Text())`, le signale comme opération à
relire, et met à jour le commentaire de la table (`contrat v1.1.0`).

**Mise en place sur la base existante (2026-11-27).** Les tables Bronze existaient déjà. La
migration initiale a été générée contre une base vide temporaire, pour qu'elle crée vraiment
toutes les tables. La base de développement a ensuite été marquée à cette révision
(`alembic stamp`, sans DDL). Une seconde migration a mis à jour le commentaire de
`bronze.maintenance` (contrat v0.3.0 → v0.4.0). Une nouvelle base se construit avec
`uv run indusense db upgrade`.

---

## 5. Vérifier le résultat (pgAdmin)

```sql
-- Le registre : premier chargement puis rejeu
SELECT dataset_id, status, records_read, records_inserted, records_already_present,
       rows_with_cast_errors, message
FROM bronze.ingestion_batches_log ORDER BY started_at;

-- NULL typé <=> case vide (attendu : 0 ligne)
SELECT count(*) FROM bronze.telemetry
WHERE (temperature_c IS NULL) <> (trim(temperature_c_raw) = '' OR _cast_errors ? 'temperature_c');

-- Les erreurs de conversion, ligne par ligne
SELECT _row_number, _cast_errors FROM bronze.telemetry WHERE _cast_errors <> '{}'::jsonb;

-- Rejeu : aucune clé en double
SELECT _source_hash, _row_number, count(*) FROM bronze.telemetry
GROUP BY 1, 2 HAVING count(*) > 1;   -- attendu : 0 ligne
```

**Mesuré le 26/11/2026 sur les fichiers de `datas/`** :

| Jeu | Lignes lues | Insérées au 1er passage | Insérées au rejeu | Erreurs de conversion |
|---|---|---|---|---|
| `machine` | 15 | 15 | 0 | 0 |
| `maintenance` | 115 | 115 | 0 | 0 |
| `releves_incidents` | 1 245 | 1 245 | 0 | 0 |
| `telemetry` | 135 630 | 135 630 | 0 | 0 |

Le document du formateur annonce 135 626 lignes pour `telemetry`. Son fichier est donc
probablement une autre version que celui de `datas/`. Ce point est à vérifier avec lui.

---

## 6. Ce qui change pour le Silver

- Le Silver lit les colonnes `x_raw`, c'est-à-dire les valeurs d'origine. Il applique ses propres
  règles de typage, celles du contrat avec `na_tokens`, et non le calcul au mieux du Bronze.
- **Pseudonymisation (ANO)** : les colonnes `pii: true` dont `privacy` vaut `pseudonymize` ou
  `drop` sont pseudonymisées (HMAC-SHA256 + sel) **avant** les contrôles. Une colonne `drop` est
  donc pseudonymisée avant d'être retirée : elle n'apparaît jamais en clair dans la quarantaine.
- Le sel `ANONYMISATION_SALT` n'est plus exigé que pour le Silver.
- **Lignage** : chaque ligne Silver et chaque ligne en quarantaine garde `_source_hash` et
  `_row_number`. On retrouve ainsi sa ligne Bronze, et donc sa ligne dans le fichier.
- Les doublons stricts sont retirés grâce à `_row_hash`.

**Conséquence RGPD (principe 6 du document `13_RGPD`).** Le Bronze contient désormais les noms
et badges **en clair**, comme le fichier source. L'accès au schéma `bronze` doit donc être
réservé aux personnes qui chargent les données. Ce point est à faire valider dans la matrice
d'habilitation.

---

## 7. Les tests qui verrouillent ces garanties

| Garantie | Test (`tests/test_layers.py`) |
|---|---|
| Vide ≠ illisible ; entier non entier refusé | `test_vide_et_illisible_ne_sont_pas_confondus` |
| `x_raw` intact, `x` typé, colonnes techniques, `_cast_errors` | `test_table_bronze_brut_type_et_colonnes_techniques` |
| Collisions de noms refusées | `test_collision_de_noms_refusee` |
| Colonne absente nommée avec suggestion ; colonne en trop | `test_contrat_confronte_au_fichier_reel` |
| Colonne SQL jamais alimentée ; source vide | `test_colonne_du_schema_sql_jamais_alimentee` |
| Champ de traçabilité non alimenté admis au Bronze (avertissement) ; structure de `traceability` | `test_colonne_de_tracabilite_non_alimentee_admise_au_bronze`, `test_structure_de_traceability` |
| Champ de traçabilité non repris ni contrôlé au Silver | `test_silver_typage_quarantaine_et_lignage` |
| Pseudonymisation au Silver, jamais en clair en quarantaine, lignage | `test_silver_typage_quarantaine_et_lignage` |
| Bronze → Silver → Gold dans PostgreSQL ; NULL typé ⟺ source vide | `test_bout_en_bout_postgresql` |
| Rejeu sans doublon ; registre | `test_rejeu_sans_doublon_et_registre` |
| Seuil dépassé → rien n'est écrit, échec au registre | `test_seuil_d_erreurs_de_conversion_annule_le_lot` |
| Opérations destructrices signalées ; seul le schéma Bronze est comparé | `test_operations_destructrices_signalees`, `test_seul_le_schema_bronze_est_compare` |
| Chargement refusé tant que la migration n'est pas appliquée ; rien n'est créé | `test_chargement_refuse_sans_migration_appliquee` |
| Migration initiale, aucun script vide, évolution du contrat détectée et signalée | `test_migration_initiale_puis_evolution_du_contrat` |

Les tests sur PostgreSQL demandent une base de test (`INDUSENSE_TEST_DB_URL`). Ils écrivent
dans des schémas `*_test` et créent leurs tables comme en vrai : migration générée dans le
mini-projet, puis appliquée.

---

## 8. Ce qui reste à faire

**Bronze**

- **Horloge déclarée** (`timezone: UTC` dans le contrat, étiquetée et jamais convertie). Elle
  n'est pas encore dans nos contrats.
- **Unités** obligatoires pour les colonnes numériques.

**Silver** (guide 02, retenu face au guide Medallion), dans cet ordre :

1. fusion (`date` + `time`) ;
2. dédoublonnage par clé, avec un agrégateur choisi après mesure de la dispersion et une colonne
   qui compte les lignes agrégées ;
3. normalisation de la casse, avec un comptage des valeurs modifiées ;
4. imputation des trous courts seulement, avec `is_imputed`, après mesure de la longueur des
   trous ;
5. marquage des valeurs aberrantes, jamais supprimées ni bornées.

**Gold** : cible définie avec le métier, horizon, variables calculées sur le passé, découpage
chronologique avec purge, normalisation ajustée sur le jeu d'entraînement seul, export versionné
avec DVC.

**RGPD** : exclure le commentaire libre des variables du modèle et des exports. Décision Q-14
(27/11/2026) : `privacy: keep`, conservé au Silver, absent du Gold.
