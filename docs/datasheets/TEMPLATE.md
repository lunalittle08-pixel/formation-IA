# Datasheet — <nom du jeu de données>

_D'après « Datasheets for Datasets » (Gebru et al.). Une datasheet par jeu de données,
mise à jour à chaque audit. Les faits chiffrés viennent des artefacts (`artifacts/ingestion/`)._

| Champ | Valeur |
|---|---|
| Identifiant (contrat) | `configs/contracts/<id>.yaml` — version x.y.z |
| Responsable (owner) | |
| Dernier audit | `artifacts/ingestion/audits/AAAAMMJJ/<id>_HHMMSS.md` |
| Empreinte de la version auditée | SHA-256 : |

## 1. Motivation
- Pour quel besoin métier ce jeu de données existe-t-il ? Quel KPI sert-il (ex. rappel des pannes) ?

## 2. Composition
- Que représente une ligne ? Combien de lignes, quelle période, quelles entités ?
- Variables clés, unités, cible éventuelle.

## 3. Collecte et provenance
- Qui produit la donnée, comment (capteur, saisie opérateur, export SQL), à quelle fréquence ?
- Transformations subies avant réception.

## 4. Qualité (synthèse de l'audit)
| Dimension | Constat | Décision |
|---|---|---|
| Complétude | | |
| Unicité | | |
| Validité | | |
| Cohérence | | |
| Exactitude | | |
| Représentativité | | |

## 5. Risques éthiques et conformité (C2)
- Données personnelles et décision de minimisation.
- Biais de représentativité (machines, équipes, périodes sous-représentées).
- Usages à proscrire.

## 6. Limites connues
-

## 7. Historique
| Date | Version contrat | Changement |
|---|---|---|
