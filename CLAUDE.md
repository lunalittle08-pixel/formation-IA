# InduSense 4.0 — instructions pour l'agent

Projet fil rouge de la formation « Concevoir et implémenter une solution d'IA » :
maintenance prédictive (pannes machines). Sprint 1 : fiabiliser les données (C1, C2, C3).

## Organisation
- `datas/` : sources brutes, **en lecture seule**. Ne jamais les modifier.
- `configs/pipeline.yaml` : chemins, découverte des sources, seuils d'inférence et d'audit. Les
  fichiers de `datas/` sont découverts automatiquement (id = nom sans extension) : aucun nom de
  fichier en dur, ni dans le code ni dans la config ; une option de lecture s'impose par motif
  (`discovery.overrides`).
- `configs/contracts/*.yaml` : contrats de données (l'attendu). Versionnés, modifiés sur branche.
- `src/indusense/` : package Python (logique réutilisable, testée).
- `notebooks/` : journal de bord (exploration, interprétation, décisions). Le notebook importe le
  package : pas de logique métier dupliquée dans les cellules.
- `artifacts/ingestion/` : sorties générées (cartes, audits, validations, manifestes). Jamais éditées à la main.

## Commandes
```bash
uv sync                            # installe les dépendances
uv run indusense run               # identity + audit + draft + validate
uv run indusense contract check    # structure des contrats
uv run pytest                      # tests
```

## Skills disponibles
- `data-identity-card` : inventaire d'un fichier (TP1).
- `data-audit` : audit qualité et cartographie, interprétation des constats.
- `data-contract` : création des contrats, validation, traitement des échecs.

## Règles de travail
- Git : jamais de travail sur `main`. Une branche par incrément (`feat/…`, `contract/…`, `fix/…`),
  commits fréquents au format `type(portée): message`, fusion dans `main` puis push de `main`.
- Les seuils de la config et les règles des contrats ne s'assouplissent pas pour faire passer
  des données : toute modification est justifiée (commentaire et message de commit).
- Pas de nettoyage dans l'audit ni dans le Bronze : on décrit, on trace, on décide.
- Données personnelles (noms, badges, texte libre) : signaler et proposer une décision de minimisation.
- Code : fonctions courtes à responsabilité unique, docstrings en français, test pytest pour toute
  nouvelle règle.
