# Mode opératoire

Procédures pas à pas. Chaque procédure indique quand l'utiliser, les étapes et le résultat
attendu. Les commandes se lancent depuis la racine du projet.

| # | Procédure | Quand |
|---|---|---|
| P1 | Première installation | nouveau poste, nouveau clone du dépôt |
| P2 | Exécution complète | après un dépôt ou une modification de données, de configuration ou de contrat |
| P3 | Ajouter une nouvelle source | un nouveau fichier de données arrive |
| P4 | Créer ou faire évoluer un contrat | jeu de données sans contrat, ou attendu qui change |
| P5 | Traiter un échec de validation | « Non conformes : … » |
| P6 | Faire évoluer la spécification des constats | hypothèse validée par le métier, seuil à ajuster |
| P7 | Neutraliser un constat pour une colonne | constat attendu et expliqué par le métier |
| P8 | Versionner et publier | après chaque incrément |
| P9 | Régénérer les rapports | rapports supprimés, ou nouveau poste |

---

## P1 — Première installation

1. Installer `uv` (voir le [dossier d'exploitation](06_dossier_exploitation.md), § 2).
2. Récupérer le projet :
   ```powershell
   git clone https://github.com/lunalittle08-pixel/formation-IA.git
   cd formation-IA
   ```
3. Installer l'environnement : `uv sync`.
4. Déposer les fichiers de données dans `datas/` (ils ne sont pas dans le dépôt).
5. Vérifier : `uv run pytest` → tous les tests passent.

**Résultat** : dossier `.venv/` créé, tests au vert.

## P2 — Exécution complète

1. `uv run indusense contract check` → chaque contrat `OK`.
2. `uv run indusense run`.
3. Lire, dans cet ordre : validation (`validations/`), audits (`audits/`), cartographie.
4. Reporter interprétations et décisions dans le journal de bord (notebook).

**Résultat** : nouveaux rapports datés dans `artifacts/ingestion/`, un manifeste dans
`runs/<run_id>/`. Code retour 1 si un jeu est non conforme (voir P5).

## P3 — Ajouter une nouvelle source

1. Copier le fichier dans `datas/`. Ne jamais modifier un fichier déjà présent : `datas/` est
   en lecture seule.
2. Vérifier que son extension figure dans `discovery.patterns` de `configs/pipeline.yaml`
   (`*.csv`, `*.tsv`, `*.sql`, `*.sql.txt` par défaut). Sinon, ajouter le motif.
3. Produire sa carte d'identité : `uv run indusense --source <id> identity`
   (`<id>` = nom du fichier sans extension).
4. Contrôler la carte : encodage, nombre de colonnes, types. Si la lecture est incorrecte
   (une seule colonne, accents cassés), imposer les options **par motif** :
   ```yaml
   discovery:
     overrides:
       - match: "export_*.csv"
         encoding: cp1252
         separator: ";"
   ```
   Ne jamais écrire le nom exact d'un fichier si un motif suffit.
5. Lancer l'audit : `uv run indusense --source <id> audit`, puis lire le rapport.
6. Créer son contrat (P4).

**Résultat** : la source apparaît dans les rapports et le manifeste, sans modification du code.

## P4 — Créer ou faire évoluer un contrat

**Créer**

1. `uv run indusense contract draft` → brouillon dans
   `artifacts/ingestion/contracts/drafts/<run_id>/<id>.yaml`.
2. Copier le brouillon dans `configs/contracts/<id>.yaml`, sur une branche `contract/<id>`.
3. Compléter avec le métier — **poser les questions plutôt que supposer** :
   - `owner`, `description` du jeu et de chaque colonne, `unit` ;
   - remplacer les bornes **observées** `min`/`max` par les bornes **métier** ;
   - vérifier `allowed_values` (liste observée ≠ liste autorisée) ;
   - `references` vers les jeux de référence ;
   - `privacy` pour chaque colonne `pii: true` (keep, drop, pseudonymize, aggregate) ;
   - supprimer le bloc `observed` une fois relu.
4. `uv run indusense contract check` → `OK`.
5. `uv run indusense validate`, puis P5 si non conforme.
6. Passer `status: active` et `version: 1.0.0` quand le métier a validé.

**Faire évoluer**

- Version sémantique : correctif de description → `x.y.Z` ; règle ajoutée ou resserrée →
  `x.Y.0` ; colonne supprimée ou renommée, type changé → `X.0.0`.
- Chaque modification est justifiée (commentaire dans le YAML et message de commit).
- **Ne jamais assouplir une règle pour faire passer des données** (voir P5).

## P5 — Traiter un échec de validation

1. Ouvrir le dernier `artifacts/ingestion/validations/AAAAMMJJ/validation_HHMMSS.md`.
2. Pour chaque violation 🔴, qualifier avec le métier :

| Qualification | Signification | Action |
|---|---|---|
| donnée fautive | la donnée viole une règle juste | garder le contrat ; signaler à la source ; traiter dans une couche ultérieure (jamais dans `datas/`) |
| contrat faux | la règle ne reflète pas la réalité métier | corriger le contrat sur branche `contract/…`, avec justification et nouvelle version |
| écart tolérable | connu et accepté | `on_fail: warning` sur la colonne, avec commentaire justifiant la décision |

3. Noter la décision dans le journal de bord (tableau « Décisions sur les violations »).
4. Relancer `uv run indusense validate`.

**Exemple** (données du 21/11/2026) : `maintenance_type` contient `proactive`/`reactive`
alors que le contrat attend `preventive`/`corrective`. Question à poser au métier : s'agit-il
des mêmes notions sous d'autres termes ? Selon la réponse : contrat faux (mettre à jour la
liste) ou donnée à traduire dans une couche ultérieure.

## P6 — Faire évoluer la spécification des constats

Fichier : `configs/findings.yaml`. Liste des règles, variables et seuils :
`src/indusense/profiling/default_findings.yaml` et
[spécifications techniques § 4.3](03_specifications_techniques.md).

1. Sur une branche (`feat/…` ou `fix/…`).
2. Ajouter ou modifier **uniquement** ce qui diffère du défaut :
   ```yaml
   rules:
     repeated_extreme:
       hypothesis: "limite de mesure de l'appareil ?"   # validée avec <qui>, <date>
       params: {min_repeats: 10}                        # justification du seuil
   ```
3. `uv run pytest` (le test `test_spec_du_projet_valide` charge le fichier) puis
   `uv run indusense audit`.
4. Vérifier le rapport : l'hypothèse apparaît dans la colonne « Piste (spéc. métier) ».
5. Commit avec justification.

Une hypothèse est une piste fournie par le métier : ne pas y écrire une supposition non
vérifiée.

## P7 — Neutraliser un constat pour une colonne

Quand un constat est attendu pour **une** colonne et que le métier l'a expliqué (ex. une
valeur répétée qui correspond à un état connu), on ne change pas le seuil global : on déclare
une exception dans le contrat.

1. Repérer l'identifiant de la règle : champ `rule` dans le `.json` de l'audit, ou tableau des
   règles des spécifications techniques.
2. Dans `configs/contracts/<jeu>.yaml`, sur la colonne :
   ```yaml
   - name: <colonne>
     audit:
       <règle>: {enabled: false, reason: "<explication, validée par qui, quand>"}
   ```
3. `uv run indusense contract check` → `OK` (une exception sans `reason` est refusée).
4. `uv run indusense audit` : le constat passe dans « Constats neutralisés par le contrat »,
   avec la justification.

## P8 — Versionner et publier

Règles du projet (`CLAUDE.md`) : jamais de travail direct sur `main`, une branche par
incrément, commits fréquents au format `type(portée): message`.

```powershell
git checkout main
git pull                                   # récupérer la dernière version
git checkout -b feat/<sujet>               # ou contract/<jeu>, fix/<sujet>, docs/<sujet>
# … modifications …
uv run pytest
git add <fichiers>
git commit -m "feat(audit): <ce qui a été fait>"
git checkout main
git merge --no-ff feat/<sujet>
git push
```

Préfixes de branche prévus par `CLAUDE.md` : `feat/…`, `contract/…`, `fix/…`. Types de commit
utilisés dans l'historique : `feat`, `fix`, `docs`, `chore` ; la portée entre parenthèses
précise la zone touchée (`feat(audit)`, `docs(readme)`…).

Ne jamais versionner `datas/` ni de fichier contenant des secrets.

## P9 — Régénérer les rapports

Les rapports (`identities/`, `audits/`, `validations/`, brouillons) ne sont pas versionnés.

1. Supprimer éventuellement l'ancien contenu de `artifacts/ingestion/` (les manifestes
   versionnés restent récupérables dans Git).
2. `uv run indusense run`.
3. Versionner le nouveau manifeste (P8).
