# Plan de déploiement : du Bronze au Silver

Plan établi le 27/11/2026 à partir des consignes du formateur
`14_bronze_vers_silver_guide_stagiaires-complete.md` (méthodes et outils) et
`14_bronze_vers_silver_guide_stagiaires-didactique.md` (artefacts à livrer), confrontées au dépôt
et aux données réellement chargées en base. Chaque étape est appliquée après validation, sur sa
propre branche, avec ses tests et la mise à jour des documents concernés.

Les questions ouvertes sont tenues dans le [registre des questions métier](10_registre_questions_metier.md)
(identifiants `Q-xx` cités ci-dessous).

## 1. Ce que demandent les consignes

Les deux guides décrivent le même processus :

1. **Sortie du Bronze, quatre artefacts** : manifeste de lot, portrait du Bronze, registre des
   questions au métier, bilan de réception.
2. **Contrat avant toute transformation** : grain (ce qu'est une ligne), clé, unités, temps,
   sens des absences, plages physiquement impossibles, personne qui valide.
3. **Six gestes Silver, chacun avec sa preuve** : convertir sans perdre l'original ; harmoniser
   noms, unités et temps ; distinguer copies, conflits et vrais événements ; qualifier trous et
   valeurs surprenantes ; décider d'une éventuelle estimation ; contrôler puis publier.
4. **Paquet de sortie Silver** : jeu versionné, contrat et dictionnaire, journal des
   transformations, registres copies / conflits / quarantaine, rapport de qualité et graphiques,
   questions et décisions.

Principes rappelés : une valeur rare n'est pas une erreur (c'est souvent le signal recherché) ;
une absence expliquée vaut mieux qu'un chiffre inventé ; les méthodes statistiques donnent des
**candidats à revue**, pas des suppressions ; la mise à l'échelle (`StandardScaler`…) appartient
au Gold, ajustée sur l'entraînement seulement.

## 2. Écarts constatés (27/11/2026)

| Consigne | État du dépôt | Écart |
|---|---|---|
| Conversion selon le contrat ; échec = `null` **avec motif**, jamais 0 | ✅ conversion par contrat, valeur d'origine au Bronze | ⚠ motif seulement compté dans le bilan, pas porté par la ligne Silver |
| Copies exactes : une occurrence gardée, **registre** des copies | ⚠ dédoublonnage sur `_row_hash`, compteur | ❌ pas de registre « gardée ↔ écartées » |
| Même clé, contenu différent = **conflit**, registre séparé | ⚠ mis en quarantaine (`primary_key`) | ❌ pas de registre des conflits |
| Qualifier : valide / manquant / invalide / **suspect** ; aberrants signalés, jamais supprimés | ⚠ règles bloquantes et avertissements | ❌ ni drapeau par ligne ni repérage IQR / MAD par groupe |
| Temps en UTC sans inventer de fuseau ; instant mesuré ≠ instant ingéré | ❌ horodatages sans fuseau ; `_ingested_at` non repris au Silver | Q-02 répondue : tout est en UTC, à déclarer dans les contrats |
| Imputation : absence expliquée par défaut, `was_missing`, méthode tracée | ✅ aucune imputation | ❌ pas d'indicateur ni de politique déclarée |
| Journal des transformations ligne à ligne | ❌ | à créer |
| Contrôles de sortie **bloquants**, rapprochement par lot et par statut | ⚠ cohérence du nombre de lignes écrites | ❌ publication non conditionnée ; pas de rapprochement par `batch_id` |
| Artefacts Bronze : portrait, registre des questions, bilan de réception | ⚠ audit, manifeste, registre des lots | ❌ portrait par lot, registre des questions (étape 0), bilan |
| Graphiques (manquants, séries, boîtes par groupe, corrélations) | ❌ aucune librairie installée | décision Q-08 |

**Faits mesurés qui orientent le plan** (lots du 26/11/2026, voir le registre) :

- `telemetry` : 0 copie exacte, **1 340 couples (machine, heure) en double** (2 686 lignes, 360
  jours, 15 machines), valeurs voisines mais différentes, lignes toujours consécutives : **réémissions** (Q-01 répondue : on garde le second relevé ; ordre et groupes de 3 lignes, Q-20) ;
- `maintenance` : 115 lignes sur 115 en quarantaine, valeurs `proactive` / `reactive` hors de
  la liste du contrat ; la liste du contrat ne correspondait pas à la contrainte `CHECK` de la
  source : contrat corrigé (0.4.0, Q-03), 0 ligne en quarantaine depuis ;
- `releves_incidents` : 215 incidents sur 1 245 marqués `type_alarme_capteur` (Q-15).

## 3. Étapes

L'ordre conseillé est **0 → 1 → 2 → 3 → 4 → 7 → 5 → 6 → 8** : les étapes 3, 4 et 7 produisent
des preuves dès maintenant ; 5 et 6 dépendent le plus des réponses métier. Le code reste
générique : groupes, méthodes et seuils vivent dans la configuration et les contrats.

| # | Branche | Objet | Questions bloquantes | Statut |
|---|---|---|---|---|
| 0 | `docs/registre-questions-metier` | registre des questions métier, ce plan | — | fait (à fusionner) |
| 1 | `feat/bronze-portrait` | indice de confiance par ligne (colonne technique), portrait du Bronze, bilan de réception | Q-19 | à faire |
| 2 | `contract/silver-v1` | extension du format des contrats | Q-10, Q-12, Q-13 | à faire |
| 3 | `feat/silver-copies-conflits` | registres des copies et des conflits, rapprochement | Q-20 (Q-01 répondue) | à faire |
| 4 | `feat/silver-qualification` | drapeaux qualité par ligne, suspects par `machine_id` (Q-05 répondue) | Q-21, Q-06, Q-08 | à faire |
| 5 | `feat/silver-temps` | UTC déclaré, instant d'ingestion, colonnes dérivées | — (Q-02 répondue : UTC) | à faire |
| 6 | `feat/silver-imputation` | indicateurs de manque, politique d'imputation | Q-06, Q-07 | à faire |
| 7 | `feat/silver-journal-publication` | journal des transformations, publication contrôlée | — | à faire |
| 8 | `docs/notebook-silver` | journal de bord Silver et graphiques | Q-08 | à faire |

### Étape 0 — Registre des questions métier (artefact 3)

Document versionné [10_registre_questions_metier.md](10_registre_questions_metier.md) : colonne
ou lignes concernées, constat mesuré, `batch_id`, question, impact, interlocuteur, statut, étape
bloquée. Il reprend les questions de ce plan et les `TODO` des contrats. Il est mis à jour à
chaque réponse (date, décision, document ou contrat modifié).

### Étape 1 — Indice de confiance, portrait du Bronze et bilan de réception (artefacts 2 et 4)

**Indice de confiance des manquants (décision Q-04)** : colonne technique de **chaque ligne** du
Bronze (nom dans `layers.bronze.technical_columns`, comme `_row_hash`), calculée au chargement à
partir des valeurs d'origine, sans rien modifier à la donnée. Sa définition exacte (colonnes
prises en compte, jetons d'absence, valeur non convertible) est fixée par Q-19. La structure des
tables change : migration Alembic générée, relue puis appliquée (`indusense db revision` puis
`db upgrade`, docs/08 §4 bis), écart avec `docs/08`
documenté, dictionnaire de données (section `transformations`) et tests mis à jour.

Puis une commande en lecture seule sur le Bronze (par exemple `indusense portrait`), produisant
dans `artifacts/ingestion/` :

- manquants **par colonne, par lot et par valeur d'une colonne de regroupement déclarée**
  (machine pour Indusense), en nombres et en proportions ;
- distribution de l'indice de confiance (colonne technique ci-dessus), par lot et par groupe ;
- `_row_hash` répétés **dans un lot et entre lots** ; clés en double à contenu divergent ;
- erreurs de conversion par lot, exemples de textes bruts et types ;
- bilan de réception : reçu, attendu si déclaré, colonnes inattendues, décision (à étudier ou à
  recharger).

Le registre des lots et le dictionnaire indiquent le **périmètre du checksum** (valeurs d'origine
des seules colonnes sources) et la version de la règle.

### Étape 2 — Contrat Silver (format v1)

Clés optionnelles contrôlées par `indusense contract check` :

- `grain` : ce qu'est une ligne ;
- `timezone` de la source, par colonne de date ;
- `na_tokens` par colonne (sinon ceux de la configuration) ;
- `quality.conflicts` : règle de résolution, `none` par défaut ;
- `quality.outliers` : méthode, colonnes de regroupement, seuils ;
- `imputation` : `none` par défaut ;
- `validated_by`, `open_questions` (renvoi aux `Q-xx`).

Sans réponse métier, la valeur reste `to_decide` et le comportement est le plus conservateur.
Les bornes et listes en attente (Q-10, Q-12, Q-13) sont reportées ici une fois tranchées.

### Étape 3 — Copies et conflits (geste 3)

- `silver.<jeu>_copies` : ligne gardée ↔ lignes écartées, avec lots et numéros de ligne ;
- `silver.<jeu>_conflicts` : même clé, contenu différent, **séparé de la quarantaine**, en attente
  de règle ; résolu seulement si le contrat déclare une règle ;
- rapprochement par lot en catégories mutuellement exclusives :
  `reçues = retenues + copies écartées + quarantaine + conflits`.

### Étape 4 — Qualification (gestes 1 et 4)

- colonne de drapeaux par ligne (`_quality`, JSONB `{colonne: motif}`) : valeur mise à `null`
  et son motif, valeur manquante, **suspect** ;
- repérage des suspects par IQR et médiane / MAD normalisée, **par groupe** déclaré au contrat ;
  séries constantes (MAD = 0) traitées à part ; seuils dans la configuration, notés « à
  étalonner » ;
- un suspect est marqué, jamais supprimé ni borné ; taux par colonne et par groupe au bilan.

### Étape 5 — Temps (geste 2)

- conversion en UTC **uniquement** si le contrat déclare le fuseau source ; valeur source gardée ;
  heures ambiguës ou inexistantes (changement d'heure) signalées ;
- `_ingested_at` et `_batch_id` repris au Silver à côté de l'instant mesuré ;
- colonnes dérivées déclarées au contrat (ex. `date` + `time` → instant), exécutées au Silver ;
- conversion d'unités seulement si une formule versionnée est déclarée.

### Étape 6 — Manquants et imputation (geste 5)

- indicateurs `was_missing` et durée des trous (d'après la cadence déclarée, Q-06) ;
- politique `imputation: none` par défaut ; une méthode n'est activée que sur décision du
  contrat, avec `x_source`, `x_estimated`, `x_imputation_method`, une durée maximale et sans
  traverser une maintenance ;
- imputations apprises (médiane, KNN, itérative) renvoyées au Gold, ajustées sur l'entraînement.

### Étape 7 — Journal et publication (geste 6)

- `silver._transformations_log` : run, version du code, version et empreinte du contrat, lien
  vers la ligne Bronze, colonne, ancienne et nouvelle valeur, motif, méthode ; jamais de donnée
  personnelle en clair ;
- contrôles de sortie automatisés (types, clé unique, temps valide, taux de `null`,
  rapprochement) ; **un contrôle obligatoire en échec bloque la publication** (tables de
  préparation puis bascule dans une transaction).

### Étape 8 — Journal de bord Silver

Notebook `03_silver_journal_de_bord` (importe le package) : carte des manquants par machine et
par jour, séries par machine avec suspects et maintenances, boîtes par groupe, nuage
température / rotation, Pearson et Spearman avec le nombre de paires, vues avant / après, et les
quatre travaux courts du guide.

## 4. Hors périmètre

Mise à l'échelle, fenêtres temporelles, labels et découpage entraînement / validation / test :
Gold, sprint suivant. `pandera` n'est pas retenu : les contrats YAML jouent déjà ce rôle (à
confirmer, Q-08).
