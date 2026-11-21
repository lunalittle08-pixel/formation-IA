---
name: data-audit
description: Audite la qualité de jeux de données (complétude, unicité, validité, cohérence, exactitude, représentativité, conformité RGPD) et cartographie les relations entre jeux de données, selon le guide docs/methodo/data_audit.md. À utiliser pour « auditer », « profiler », « analyser la qualité », « cartographier » des données, ou avant de construire la couche Bronze.
---

# Audit qualité et cartographie

Référence méthodologique : `docs/methodo/data_audit.md` (CRISP-DM « Data Understanding »,
dimensions de qualité ISO/IEC 25012 et DAMA, Datasheets for Datasets, RGPD).

## Étapes
1. S'assurer que les fichiers sont dans le dossier de données (`paths.data_dir`) et couverts par
   `discovery.patterns` de `configs/pipeline.yaml` : ils sont découverts automatiquement.
2. Lancer :
   ```bash
   uv run indusense audit
   ```
   Sorties dans `artifacts/ingestion/audits/AAAAMMJJ/` :
   - `<jeu>_HHMMSS.md` et `.json` : profil des colonnes et constats par dimension ;
   - `cartographie_HHMMSS.md` : diagramme Mermaid (ERD) et relations, avec orphelines.
3. **Lire les constats** : la colonne « Constat » est un fait mesuré ; la colonne « Piste
   (spéc. métier) », si présente, vient de `configs/findings.yaml` (spécification métier).
   Textes, sévérités et seuils des règles : catalogue par défaut
   `src/indusense/profiling/default_findings.yaml`, surchargé par `configs/findings.yaml`.
4. **Interpréter chaque constat** 🔴/🟠 avec ce gabarit, dans le notebook de journal de bord
   (`notebooks/`) :
   > **Observation** — ce que montre l'audit (chiffre à l'appui)
   > **Hypothèse** — fournie par le métier ou la spécification ; à défaut, **poser la question**
   > à l'utilisateur plutôt que supposer une cause
   > **Vérification** — comment la confirmer, et auprès de qui (métier, fiche technique)
   > **Décision** — garder / minimiser / transformer / supprimer, et dans quelle couche
   > **Justification** — métier, réglementaire (C2) ou technique
5. **Reporter les décisions** dans le contrat concerné (`configs/contracts/<jeu>.yaml`) :
   bornes métier `min`/`max`, `allowed_values`, `privacy`, `on_fail`. Voir le skill `data-contract`.
   Un constat attendu pour UNE colonne (validé par le métier) se neutralise dans le contrat,
   jamais en changeant un seuil global :
   ```yaml
   - name: <colonne>
     audit:
       <règle>: {enabled: false, reason: "<décision, qui l'a validée, date>"}
   ```
   Le constat reste visible dans le rapport, section « Constats neutralisés par le contrat ».

## Points d'attention (ce que le jury vérifie)
- Ne rien interpréter sans source : la signification d'une valeur, d'une borne ou d'une unité
  se demande à l'utilisateur ou au métier. Une hypothèse validée s'ajoute dans
  `configs/findings.yaml` (`hypothesis`), pas dans le code.
- Un outlier n'est pas une erreur par défaut : une valeur extrême peut être le phénomène que le
  cas d'usage cherche à détecter. Ne jamais proposer de suppression sans validation métier.
- Un min ou max répété est un fait à faire expliquer (limite d'un appareil, valeur par défaut,
  valeur réelle…) : le demander, ne pas conclure.
- Une entité sous-représentée est un risque de biais : le noter dans la datasheet.
- Toute donnée personnelle (nom, badge, texte libre) appelle une décision de minimisation.
- Ne rien nettoyer dans l'audit : il décrit, il ne transforme pas.
- Ne pas modifier un seuil (catalogue ou `configs/findings.yaml`) pour faire disparaître un
  constat : toute modification est justifiée (commentaire + message de commit).

## Restitution
Synthèse en 5 à 10 lignes : les 3 constats les plus importants, leur impact sur le cas d'usage
(tel que décrit par l'utilisateur), les décisions à faire valider et par qui, et les questions
restées ouvertes.
