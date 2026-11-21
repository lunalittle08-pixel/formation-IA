---
name: data-audit
description: Audite la qualité des données InduSense (complétude, unicité, validité, cohérence, exactitude, représentativité, conformité RGPD) et cartographie les relations entre jeux de données, selon le guide docs/methodo/data_audit.md. À utiliser pour « auditer », « profiler », « analyser la qualité », « cartographier » des données, ou avant de construire la couche Bronze.
---

# Audit qualité et cartographie

Référence méthodologique : `docs/methodo/data_audit.md` (CRISP-DM « Data Understanding »,
dimensions de qualité ISO/IEC 25012 et DAMA, Datasheets for Datasets, RGPD).

## Étapes
1. S'assurer que les sources sont déclarées dans `configs/pipeline.yaml` (skill `data-identity-card`).
2. Lancer :
   ```bash
   uv run indusense audit
   ```
   Sorties dans `artifacts/ingestion/audits/AAAAMMJJ/` :
   - `<jeu>_HHMMSS.md` et `.json` : profil des colonnes et constats par dimension ;
   - `cartographie_HHMMSS.md` : diagramme Mermaid (ERD) et relations, avec orphelines.
3. **Interpréter chaque constat** 🔴/🟠 avec ce gabarit, dans le notebook de journal de bord
   (`notebooks/`) :
   > **Observation** — ce que montre l'audit (chiffre à l'appui)
   > **Hypothèse** — cause probable (erreur de saisie, saturation capteur, valeur réelle…)
   > **Vérification** — comment la confirmer, et auprès de qui (métier, fiche technique)
   > **Décision** — garder / minimiser / transformer / supprimer, et dans quelle couche
   > **Justification** — métier, réglementaire (C2) ou technique
4. **Reporter les décisions** dans le contrat concerné (`configs/contracts/<jeu>.yaml`) :
   bornes métier `min`/`max`, `allowed_values`, `privacy`, `on_fail`. Voir le skill `data-contract`.

## Points d'attention (ce que le jury vérifie)
- Un outlier n'est pas une erreur par défaut : en maintenance prédictive, une valeur extrême
  peut être le signal d'une panne. Ne jamais proposer de suppression sans validation métier.
- Un min ou max « tout rond » et répété (ex. 32.00, 80.00, 1900.00) évoque un écrêtage capteur.
- Une entité sous-représentée (machine, équipe) est un risque de biais : le noter dans la datasheet.
- Toute donnée personnelle (nom, badge, texte libre) appelle une décision de minimisation.
- Ne rien nettoyer dans l'audit : il décrit, il ne transforme pas.
- Ne pas modifier un seuil de `audit:` dans la config pour faire disparaître un constat.

## Restitution
Synthèse en 5 à 10 lignes : les 3 constats les plus importants, leur impact sur le cas d'usage
(prédiction de pannes), les décisions à faire valider et par qui.
