# Registre des questions métier

Artefact 3 de la sortie du Bronze (consignes du formateur, guide 14) : ce que l'équipe ne peut
pas décider seule. Une question n'est jamais remplacée par une supposition : tant qu'elle est
ouverte, le pipeline applique le comportement le plus conservateur (rien n'est corrigé, estimé
ni supprimé) et le signale.

**Tenue du registre.** Une ligne par question. À la réponse : passer le statut à `répondue`,
renseigner la décision, sa date, qui l'a prise, et le contrat, la configuration ou le document
modifié (dans une branche, avec un commit qui cite l'identifiant `Q-xx`). Une question n'est
pas supprimée : elle garde la trace de la décision.

**Lots de référence** (chargement du 26/11/2026, contrats en vigueur) :

| Jeu | `batch_id` | Source (empreinte) | Contrat |
|---|---|---|---|
| `machine` | `43409756-131e-416a-9684-2fdc44947aea` | `datas/machine.sql` (`9eb877ae8065…`) | 2.1.0 |
| `maintenance` | `462f4949-491f-4e75-9777-b471f676f008` | `datas/machine.sql` (`9eb877ae8065…`) | 0.3.0 |
| `releves_incidents` | `b1b4e8be-6405-42fc-a83c-ca4f9ee4ea7c` | `datas/releves_incidents.csv` (`be7204b3320a…`) | 1.0.0 |
| `telemetry` | `8ee87911-7a1e-4625-a0de-33f361e13e27` | `datas/telemetry.csv` (`68ec8dd09b69…`) | 0.2.0 |

Statuts : `ouverte`, `posée` (transmise à l'interlocuteur), `répondue`, `sans objet`.

## Questions

| Id | Jeu / colonne | Constat mesuré (exemple source) | Question | Impact | Interlocuteur | Étape | Statut |
|---|---|---|---|---|---|---|---|
| Q-01 | `telemetry` / clé `machine_id, timestamp` | 0 copie exacte ; 1 340 couples en double (2 à 3 lignes), 2 686 lignes, 360 jours, 15 machines. Ex. `MACH-01`, `2025-06-01 00:00:00` : température `46.348` et `46.332`, pression `198.203` et `198.206` | Deux lignes pour la même machine et la même heure : deux mesures distinctes, une réémission, une correction ? Quelle règle (aucune, priorité, agrégat) ? | règle `primary_key` : 2 639 lignes en quarantaine aujourd'hui ; sans règle, elles iront au registre des conflits | owner `telemetry` (équipe OT / supervision, à confirmer) | 3 | répondue |
| Q-02 | `telemetry.timestamp`, `releves_incidents.date` + `time`, `maintenance.maintenance_at` | `timestamp` et `date` / `time` sans fuseau ; contrat `telemetry` : « heure locale, sans fuseau ». `maintenance_at` : suffixe `+00` sur 115 lignes sur 115. Contrôle croisé (27/11/2026) : 25 maintenances `reactive` liées à un incident (`related_incident_id`) ; écart maintenance − incident de 40 h à 2 464 h, jamais négatif, sans valeur récurrente (aucun écart constant de quelques heures). `shift` des incidents : `matin` 6 h–13 h, `apres-midi` 14 h–21 h, `nuit` 22 h–5 h | Quel fuseau pour chaque source ? Que faire des heures ambiguës ou inexistantes au changement d'heure ? | conversion en UTC impossible sans réponse ; jointures temporelles entre jeux | owners des trois jeux | 5 | répondue |
| Q-03 | `maintenance.maintenance_type` | source : `CHECK (maintenance_type IN ('proactive','reactive'))` (`datas/machine.sql`, ligne 49) ; données : `proactive` 90, `reactive` 25 ; ancien contrat : `[preventive, corrective]` | ~~Vocabulaire officiel ?~~ Pas une question métier : la liste du contrat n'avait pas été vérifiée contre la contrainte de la source | 115 lignes sur 115 en quarantaine jusqu'à la correction ; 0 après | — | 2 | sans objet |
| Q-04 | tous les jeux (conception) | le guide 14 (didactique, §1) suppose au Bronze un indice de confiance sur les manquants **par ligne** ; le Bronze v2 (`docs/08`) n'en a pas | Indice calculé dans le rapport de portrait, ou colonne technique de chaque ligne du Bronze ? | ajout d'une colonne technique au Bronze, structure changée par migration Alembic (`db revision` relue, puis `db upgrade`) | porteur du projet | 1 | répondue |
| Q-05 | `telemetry` (mesures) | colonnes de `telemetry` : `machine_id`, `timestamp`, `temperature_c`, `pressure_bar`, `voltage_mean_v`, `rotation_mean_rpm`, `pieces_produced` ; aucune ne dit dans quel état était la machine au moment de la mesure (en production, à l'arrêt, en démarrage…), ni dans `telemetry` ni dans les autres sources | Les mesures ne disent pas si la machine était en production, à l'arrêt ou en démarrage. Avec quels groupes comparer les valeurs pour repérer les inhabituelles (par machine, par modèle, par ligne de production) ? Avec quelle méthode (IQR : hors de Q1 − 1,5×IQR à Q3 + 1,5×IQR ; MAD : écart à la médiane) et quel seuil de départ ? Une colonne permet-elle de déduire l'état (par exemple `pieces_produced = 0` signifie-t-il « à l'arrêt ») ? | qualification des suspects | owner `telemetry`, formateur | 4 | répondue |
| Q-06 | `telemetry.timestamp` | min `2025-06-01 00:00:00`, max `2026-06-08 23:00:00` ; contrat : « une mesure par machine et par heure » | Cadence attendue confirmée (horaire) ? Une absence peut-elle être normale (arrêt, maintenance) ? | détection et longueur des trous | owner `telemetry` | 4, 6 | ouverte |
| Q-07 | tous les jeux (conception) | aucune imputation aujourd'hui | Imputation au Silver : aucune pour l'instant (indicateurs `was_missing` seulement) ? Proposition : aucune | étape 6 réduite aux indicateurs | formateur | 6 | ouverte |
| Q-08 | projet (dépendances) | `scipy`, `matplotlib`, `seaborn`, `scikit-learn`, `pandera` non installés | Ajouter `scipy`, `matplotlib`, `seaborn` (puis `scikit-learn`) ? Ne pas ajouter `pandera` (rôle tenu par les contrats) ? | MAD, graphiques, IsolationForest / LOF | porteur du projet | 4, 8 | ouverte |
| Q-09 | `telemetry.temperature_c`, `telemetry.rotation_mean_rpm` | `80.0` répété 24 fois, `32.0` 2 fois ; `1900.0` répété 93 fois, `1100.0` 27 fois (valeurs rondes) | Ces valeurs correspondent-elles à une limite de mesure du capteur ? | suspect ou valeur valide | owner `telemetry` | 4 | ouverte |
| Q-10 | `telemetry` (bornes du contrat) | bornes provisoires : température 0–120 °C, pression 0–300 bar, tension 200–260 V, rotation 0–3 000 tr/min | Plages physiquement impossibles confirmées ? | règles bloquantes du Silver | owner `telemetry` | 2 | ouverte |
| Q-11 | `telemetry.pressure_bar`, `rotation_mean_rpm`, `temperature_c` | cases vides : 995 (pression), 958 (rotation), 894 (température) ; pression et rotation `required` | Que signifie une mesure absente (arrêt, panne de collecte, capteur) ? Doit-elle rejeter la ligne ? | règle `required` : 995 (pression) et 958 (rotation) lignes en quarantaine aujourd'hui | owner `telemetry` | 4, 6 | ouverte |
| Q-12 | `machine.criticality` | `LOW` 5, `MEDIUM` 7, `HIGH` 3 | Liste fermée des valeurs ? | règle `allowed_values` | owner `machine` | 2 | ouverte |
| Q-13 | `maintenance.duration_hours` | max observé `8.0` ; borne du contrat 72 h | Durée maximale admise d'une intervention ? | règle `max` | owner `maintenance` | 2 | ouverte |
| Q-14 | `releves_incidents.comment` | texte libre, `privacy: to_decide`, laissé en clair au Silver avec avertissement | Décision de minimisation : garder, pseudonymiser, retirer ? | RGPD | responsable du traitement | 2 | ouverte |
| Q-15 | `releves_incidents.type_alarme_capteur` | 215 incidents sur 1 245 | Ce type d'incident peut-il servir à qualifier les mesures `telemetry` de la machine à cet instant ? | qualification ; préparation du Gold | owner `releves_incidents` | 4 | ouverte |
| Q-16 | tous les contrats | `owner: TODO` dans les quatre contrats | Responsable de chaque jeu (et validateur des règles) ? | `validated_by` du contrat | porteur du projet | 2 | ouverte |
| Q-17 | schéma `bronze` | données personnelles en clair au Bronze, un seul utilisateur de base | Durée de conservation du Bronze et habilitations ? | RGPD (principes 5 et 6) | responsable du traitement | — | ouverte |
| Q-18 | registre des lots (conception) | colonnes différentes du modèle Medallion (`records_already_present`, pas de `PARTIAL`) ; unicité sur le fichier, pas sur le couple (fichier, contrat) | Écarts acceptés ? Recharger quand seul le contrat change ? | registre des lots, rejeu | formateur | 1 | ouverte |
| Q-19 | tous les jeux (indice de confiance, suite de Q-04) | colonnes de traçabilité toujours vides (`machine` : 3 sur 11, `maintenance` : 2 sur 11) ; jetons d'absence de la configuration (`""`, `NA`, `null`, `-`…) | Définition exacte de l'indice : quelles colonnes comptent (toutes les colonnes sources, ou hors champs de traçabilité) ? Un jeton `NA` compte-t-il comme absent ? Une valeur présente mais non convertible compte-t-elle comme absente ? | valeur de l'indice, qui ne doit pas pénaliser une ligne pour une colonne jamais alimentée | porteur du projet, formateur | 1 | ouverte |
| Q-20 | `telemetry` / clé `machine_id, timestamp` (suite de Q-01) | 1 340 groupes en double : 1 334 de 2 lignes, 6 de 3 lignes (ex. `MACH-02`, `2025-09-22 18:00:00`, lignes 11 796 à 11 798) ; les lignes d'un même groupe se suivent toujours dans le fichier | « Second relevé » = la ligne qui vient après dans le fichier (`_row_number`) ? Pour les groupes de 3 lignes : garder la dernière ? | règle de dédoublonnage du Silver (6 groupes) | porteur du projet, formateur | 3 | ouverte |
| Q-21 | `telemetry` (mesures, suite de Q-05) | groupes de comparaison décidés : par `machine_id` | Quelle méthode (IQR : hors de Q1 − 1,5×IQR à Q3 + 1,5×IQR ; MAD : écart à la médiane) et quel seuil de départ ? Une colonne permet-elle de déduire l'état de marche (par exemple `pieces_produced = 0` signifie-t-il « à l'arrêt ») ? | qualification des suspects | owner `telemetry`, formateur | 4 | ouverte |
| Q-22 | `telemetry.timestamp` (cadence future) | cadence actuelle : une mesure par machine et par heure (contrat ; Q-06) | Le métier prévoit-il de resserrer l'intervalle entre deux émissions (par exemple toutes les minutes), et à quelle échéance ? | volumes, durée des traitements, ressources pour réentraîner les modèles | métier (owner `telemetry`) | — | ouverte |
| Q-23 | `maintenance.related_incident_id` → `releves_incidents.incident_id` | 25 maintenances liées à un incident, tous retrouvés ; la machine de la maintenance (`machine_code`) est celle de l'incident (`machine_id`) dans 1 cas sur 25 | Une maintenance peut-elle porter sur une autre machine que celle de l'incident lié ? Sinon, quelle source fait foi (la machine, ou le lien) ? | jointures incidents ↔ maintenances (Gold, étiquettes de panne) | owners `maintenance` et `releves_incidents` | 2 | ouverte |

## Décisions prises

| Id | Date | Décision | Par | Trace |
|---|---|---|---|---|
| Q-01 | 27/11/2026 | doublons `telemetry` sur (`machine_id`, `timestamp`) : **réémission** (retry). On conserve le **second relevé** ; le premier est un raté d'émission. Ordre et groupes de 3 lignes : Q-20 | métier, transmis par le porteur du projet | à appliquer : dédoublonnage du Silver (étape 3), écarté tracé et compté |
| Q-02 | 27/11/2026 | **toutes les heures sont en UTC** (`telemetry`, `releves_incidents`, `maintenance`). Le contrôle croisé incidents ↔ maintenances correctives ne montre aucun décalage constant (voir constat) : rien à harmoniser | métier, transmis par le porteur du projet ; contrôle suggéré par le formateur | à appliquer : contrats `telemetry` (aujourd'hui « heure locale ») et `releves_incidents`, horloge déclarée `timezone: UTC` (étape 5) |
| Q-05 | 27/11/2026 | valeurs inhabituelles comparées **par `machine_id`**. Méthode, seuil, état de marche : Q-21 | métier, transmis par le porteur du projet | à appliquer : qualification des suspects (étape 4) |
| Q-03 | 27/11/2026 | `maintenance_type` : contrat aligné sur la contrainte de la source, `allowed_values: [proactive, reactive]` | constat sur la source (erreur du contrat) | contrat `maintenance` 0.4.0 ; Silver : 115 lignes, 0 en quarantaine |
| Q-04 | 27/11/2026 | indice de confiance des manquants : **colonne technique de chaque ligne du Bronze** (pas seulement dans un rapport) ; définition à fixer (Q-19) | porteur du projet | plan, étape 1 |
| — | 26/11/2026 | `is_active`, `created_at`, `updated_at` (`machine`, `maintenance`) : champs de traçabilité, chargés au Bronze (vides), non repris au Silver | formateur | contrats `machine` 2.1.0, `maintenance` 0.3.0 (`traceability: true`) |

## Notes pour plus tard

Points signalés par le métier, hors du périmètre du Bronze et du Silver. Ils ne changent rien
au pipeline actuel.

| Date | Sujet | Note | Quand en tenir compte |
|---|---|---|---|
| 27/11/2026 | inférence en continu | Le modèle inférera à chaque arrivée de nouvelles mesures. Deux mesures d'une même machine peuvent arriver dans la même seconde (voir Q-01) : le cas est à gérer au niveau de l'API d'inférence. | conception de l'API (déploiement du modèle) |
| 27/11/2026 | cadence des émissions | Un resserrement de l'intervalle entre deux émissions (par exemple toutes les minutes) aurait un fort impact sur le processus et sur les ressources nécessaires pour produire les nouveaux modèles. Question posée au métier : Q-22. | dimensionnement, MLOps |
