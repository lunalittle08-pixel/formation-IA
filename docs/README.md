# Documentation InduSense — index

Documentation du pipeline d'ingestion et de qualité des données InduSense (version du package
`0.1.0`). Tous les documents sont en Markdown, versionnés avec le code : une évolution du code
s'accompagne de la mise à jour du document concerné, dans la même branche.

## Documents

| Document | Pour qui | Contenu |
|---|---|---|
| [Spécifications fonctionnelles](01_specifications_fonctionnelles.md) | toute personne qui découvre le projet, référent métier, évaluateur | besoin, périmètre, acteurs, fonctions attendues et critères d'acceptation |
| [Dossier d'architecture](02_dossier_architecture.md) | développeur, évaluateur | vue d'ensemble, découpage en modules, flux de données, décisions de conception |
| [Spécifications techniques](03_specifications_techniques.md) | développeur | règles exactes : lecture, inférence de types, constats, contrats, validation, formats de fichiers |
| [Manuel utilisateur](04_manuel_utilisateur.md) | utilisateur du pipeline | commandes, lecture des rapports, questions fréquentes |
| [Mode opératoire](05_mode_operatoire.md) | utilisateur du pipeline | procédures pas à pas (ajouter une source, traiter un échec, versionner…) |
| [Dossier d'exploitation](06_dossier_exploitation.md) | personne qui installe et fait tourner le pipeline | environnement, installation, exécution, codes retour, journaux, sauvegarde, incidents |
| [Guide des couches Bronze / Silver / Gold](07_guide_couches_bronze_silver_gold.md) | apprenant, développeur | PostgreSQL sous Docker, `.env`, pseudonymisation, empreinte de ligne, typage et quarantaine Silver, requêtes Gold, commande `indusense load` |
| [Bronze v2 : consignes du formateur](08_bronze_contrat_v2.md) | apprenant, développeur, évaluateur | `x_raw` + `x` typé, `_cast_errors`, colonnes techniques, rejeu sans doublon, registre des lots, contrôles contrat/fichier, dictionnaire de données, pseudonymisation au Silver (remplace la partie Bronze du guide 07) |

Documents complémentaires déjà présents :

- [docs/methodo/data_audit.md](methodo/data_audit.md) — guide méthodologique de la formation
  (CRISP-DM, dimensions de qualité, RGPD) ;
- [docs/datasheets/TEMPLATE.md](datasheets/TEMPLATE.md) — modèle de datasheet d'un jeu de données ;
- [notebooks/01_audit_journal_de_bord.ipynb](../notebooks/01_audit_journal_de_bord.ipynb) —
  journal de bord exécutable : interprétation des constats et décisions ;
- [notebooks/02_bronze_journal_de_bord.ipynb](../notebooks/02_bronze_journal_de_bord.ipynb) —
  intégration du Bronze pas à pas (méthode en 8 étapes, colonnes techniques, librairies,
  invariants vérifiés en base, RGPD, écarts avec les consignes du formateur) ;
- [README.md](../README.md) du projet — démarrage rapide.

## Ordre de lecture conseillé

1. Découvrir : README du projet → spécifications fonctionnelles.
2. Utiliser : manuel utilisateur → mode opératoire.
3. Faire évoluer : dossier d'architecture → spécifications techniques.
4. Installer et exploiter : dossier d'exploitation.

## Conventions

- **Fait / interprétation** : les rapports générés énoncent des faits mesurés ; les hypothèses
  viennent d'une spécification métier (`configs/findings.yaml`), jamais du code.
- **« À définir »** signale une information que le projet n'a pas encore fixée (responsable,
  fréquence d'exécution, hébergement…). Elle n'est pas inventée : elle reste à décider.
- Les chemins sont relatifs à la racine du projet.

## Points à définir (récapitulatif)

| Sujet | Document | Statut |
|---|---|---|
| Responsable (owner) de chaque jeu de données | contrats, spéc. fonctionnelles | À définir (`owner: TODO` dans les contrats) |
| Fréquence et déclenchement des exécutions | dossier d'exploitation | À définir (exécution manuelle aujourd'hui) |
| Environnement cible hors poste de développement | dossier d'exploitation | À définir |
| Sauvegarde des données sources `datas/` (non versionnées) | dossier d'exploitation | À définir |
| Contact support / escalade | dossier d'exploitation | À définir |
| Validation métier des hypothèses de `configs/findings.yaml` | spéc. fonctionnelles | Activées le 21/11/2026, à confirmer avec le métier |
