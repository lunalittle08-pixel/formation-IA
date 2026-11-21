---
name: data-identity-card
description: Produit la carte d'identité (source, taille, encodage, lignes, colonnes, types, statistiques) d'un ou plusieurs fichiers de données InduSense, CSV ou script SQL. À utiliser quand un nouveau fichier arrive dans datas/, quand on demande « la carte d'identité », « l'inventaire » ou « ce que contient ce fichier ».
---

# Carte d'identité d'un fichier de données

## Quand l'utiliser
- Un fichier vient d'être déposé dans `datas/` (CSV, TSV, `.sql`, `.sql.txt`).
- On veut un premier inventaire factuel, avant tout audit ou transformation.

## Étapes
1. **Déclarer la source** si elle n'existe pas encore dans `configs/pipeline.yaml` (section `sources`) :
   `id` en snake_case, `path`, `format` (`csv` | `sql`), `encoding: auto`, `separator: auto`,
   `sql_dialect` pour un script SQL (`postgres` si on voit `TIMESTAMPTZ`, `SERIAL`…).
   La découverte automatique traite aussi les fichiers non déclarés, mais une source déclarée
   est traçable : préférer la déclaration.
2. **Générer** :
   ```bash
   uv run indusense identity                     # toutes les sources
   uv run indusense --source telemetry identity  # une seule
   ```
3. **Lire la carte produite** dans `artifacts/ingestion/identities/AAAAMMJJ/<fichier>_HHMMSS.md`
   et vérifier, sans rien corriger dans les données :
   - encodage et séparateur cohérents avec un aperçu du fichier ;
   - nombre de lignes plausible (en-tête non compté) ;
   - aucun type surprenant (ex. un montant en `TEXT` = décimales à virgule ou valeurs sales ;
     un `IDENTITY` inattendu = colonne unique sur un petit échantillon).
4. **Restituer** en 3 à 5 lignes : le fichier, ce qu'il contient, les types à vérifier.

## Règles
- Ne jamais modifier un fichier de `datas/` : les sources brutes sont en lecture seule.
- Ne pas changer un seuil d'inférence (`inference:` dans la config) pour « corriger » un type
  sans le signaler et le justifier : c'est une décision qui s'applique à toutes les sources.
- Le format de la carte suit la consigne du TP1 (Source, Filesize, ProcessBeginAt,
  ProcessEndAt, Charset, Rows Number, Cols Number, Column list, statistiques) : ne pas le modifier.
