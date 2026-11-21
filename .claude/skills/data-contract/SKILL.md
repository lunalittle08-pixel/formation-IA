---
name: data-contract
description: Crée, met à jour et fait respecter les contrats de données YAML d'InduSense (configs/contracts/), puis contrôle les données contre eux. À utiliser pour « créer un contrat », « valider les données », « contrôle qualité », quand une validation échoue, ou quand un fichier source change de structure.
---

# Contrats de données

Un contrat décrit ce qu'on **attend** d'un jeu de données : colonnes, types logiques, champs
obligatoires, unicité, formats, domaines de valeurs, bornes, relations, confidentialité.
Format : `configs/contracts/<id>.yaml` (inspiré de l'Open Data Contract Standard, simplifié).
Types logiques : `IDENTITY INTEGER FLOAT DATETIME TIME BOOLEAN CATEGORY TEXT EMPTY`.

## Créer un contrat pour une nouvelle source
1. `uv run indusense contract draft` génère un brouillon par jeu de données dans
   `artifacts/ingestion/contracts/drafts/<run_id>/`. Un brouillon décrit l'**observé**.
2. Copier le brouillon dans `configs/contracts/<id>.yaml`, puis le transformer en **attendu** :
   - remplacer chaque `TODO` (description, unité, owner) ;
   - remplacer les `min`/`max` observés par les bornes **métier ou physiques** (fiche capteur),
     et marquer `# TODO valider` ce qui n'est pas encore confirmé ;
   - nettoyer `allowed_values` (ne pas y laisser une modalité sale comme `Matin`) ;
   - décider `privacy` pour chaque `pii: true` (`drop`, `pseudonymize`, `aggregate`, `keep`) ;
   - déclarer les relations avec `references: <jeu>.<colonne>` ;
   - supprimer le bloc `observed` (c'est de l'information d'audit, pas une règle).
3. Vérifier la forme : `uv run indusense contract check` (doit afficher OK partout).
4. `status: draft` tant que des bornes sont à valider, `active` une fois validé (version 1.0.0).

## Contrôler les données
```bash
uv run indusense validate        # code retour 1 si une règle `error` échoue (utilisable en CI)
```
Rapport : `artifacts/ingestion/validations/AAAAMMJJ/validation_HHMMSS.md`.

## Quand une validation échoue
Ne **jamais** assouplir le contrat pour faire passer les données sans décision tracée.
Pour chaque violation, qualifier :
- **donnée fautive** (ex. `Matin` au lieu de `matin`, machine `MACH-99` inconnue) : le contrat
  reste tel quel ; la correction se fera en couche Silver, ou le producteur est alerté ;
- **contrat faux** (règle mal posée) : corriger le contrat, incrémenter la version et expliquer
  pourquoi dans le message de commit ;
- **tolérable** : passer la règle en `on_fail: warning`, avec un commentaire qui justifie.

## Versionnement (SemVer)
- PATCH (1.0.1) : description, commentaire, unité ;
- MINOR (1.1.0) : nouvelle colonne optionnelle, règle assouplie ;
- MAJOR (2.0.0) : colonne supprimée ou renommée, type changé, règle durcie.
Un contrat se modifie sur une branche dédiée (`contract/<id>-<objet>`), jamais directement sur `main`.
