"""Interface en ligne de commande.

    uv run indusense identity                 # cartes d'identité (TP1)
    uv run indusense audit                    # audit qualité + cartographie
    uv run indusense contract draft           # brouillons de contrats
    uv run indusense contract check           # structure des contrats (CI)
    uv run indusense validate                 # données vs contrats (code retour 1 si erreur)
    uv run indusense run                      # toutes les étapes
    uv run indusense load bronze|silver|gold  # chargement d'une couche dans PostgreSQL
    uv run indusense load all                 # les trois couches, dans l'ordre
    uv run indusense db revision -m "<motif>" # génère (sans appliquer) la migration du Bronze
    uv run indusense db upgrade               # applique les migrations relues
    uv run indusense db current|check         # révision appliquée / écarts restants
    uv run indusense db downgrade -1          # annule la dernière migration
    Options : --config configs/pipeline.yaml  --source <nom_fichier_sans_extension>
"""
from __future__ import annotations

import argparse
import logging
import sys

from indusense.config import DEFAULT_CONFIG, load_settings
from indusense.contracts.model import load_contracts, structure_errors
from indusense.pipeline import STAGES, run_pipeline


def _contract_check(settings) -> int:
    contracts = load_contracts(settings.contracts_dir)
    bad = 0
    for cid, c in contracts.items():
        errs = structure_errors(c, set(settings.audit.rules))
        status = "OK" if not errs else "; ".join(errs)
        print(f"{cid:25} v{c['contract'].get('version')} [{c['contract'].get('status')}] {status}")
        bad += bool(errs)
    return 1 if bad else 0


def _db(settings, args) -> int:
    """Commandes de migration : générer, relire (signalement), appliquer, vérifier."""
    from sqlalchemy.exc import OperationalError

    from indusense.layers import migrate
    from indusense.layers.database import EnvSettings, make_engine
    from indusense.layers.settings import LayerSettings

    try:
        if args.action == "revision":
            if not args.message:
                print("Erreur : motif obligatoire (-m \"...\") : il dit pourquoi la structure change",
                      file=sys.stderr)
                return 1
            result = migrate.revision(settings, args.message)
            if result["path"] is None:
                print("Aucun écart : pas de migration générée.")
                return 0
            print(f"Migration générée (NON appliquée) : {result['path']}")
            for op in result["destructive"]:
                print(f"  ATTENTION, opération destructrice à relire : {op}")
            print("Relire le script, puis : uv run indusense db upgrade")
        elif args.action == "upgrade":
            migrate.upgrade(settings, args.target or "head")
            print("Base à jour.")
        elif args.action == "downgrade":
            if not args.target:
                print("Erreur : révision visée obligatoire (ex. -1)", file=sys.stderr)
                return 1
            migrate.downgrade(settings, args.target)
        elif args.action == "current":
            cfg = LayerSettings.from_raw(settings.raw, settings.root)
            applied, heads = migrate.schema_status(make_engine(EnvSettings()), settings, cfg)
            print(f"appliquée : {sorted(applied) or 'aucune'} ; dernière : {sorted(heads) or 'aucune'}")
            return 0 if applied == heads else 1
        elif args.action == "check":
            diffs = migrate.differences(settings)
            for d in diffs:
                print(f"  écart : {d}")
            print("Aucun écart." if not diffs else f"{len(diffs)} écart(s) : uv run indusense db revision")
            return 1 if diffs else 0
    except migrate.MigrationError as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    except OperationalError as e:
        print(f"Erreur : connexion à PostgreSQL impossible. Détail : {e.orig}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="indusense", description="Pipeline d'ingestion InduSense")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--source", action="append", help="id de source = nom du fichier sans extension (répétable)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("identity", help="cartes d'identité des fichiers")
    sub.add_parser("audit", help="audit qualité et cartographie")
    sub.add_parser("validate", help="contrôle des données contre les contrats")
    sub.add_parser("run", help="toutes les étapes")
    ld = sub.add_parser("load", help="chargement des couches bronze / silver / gold (PostgreSQL)")
    ld.add_argument("layer", choices=["bronze", "silver", "gold", "all"])
    db = sub.add_parser("db", help="migrations de schéma du Bronze (Alembic)")
    db.add_argument("action", choices=["revision", "upgrade", "downgrade", "current", "check"])
    db.add_argument("target", nargs="?", default=None,
                    help="révision visée (upgrade : head par défaut ; downgrade : ex. -1)")
    db.add_argument("-m", "--message", help="motif de la migration (revision)")
    c = sub.add_parser("contract", help="gestion des contrats")
    c.add_argument("action", choices=["draft", "check"])
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s | %(message)s")
    # Alembic : on garde les migrations exécutées, pas la mécanique interne (plugins, dialecte)
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.migration").setLevel(logging.INFO)
    settings = load_settings(args.config)

    if args.cmd == "contract" and args.action == "check":
        return _contract_check(settings)
    if args.cmd == "load":
        # Import local : les autres commandes n'ont besoin ni de la base ni du .env
        from sqlalchemy.exc import OperationalError

        from indusense.layers.stage import LAYERS, LayerError, run_layers
        layers = list(LAYERS) if args.layer == "all" else [args.layer]
        try:
            run = run_layers(settings, layers, args.source)
        except LayerError as e:
            print(f"Erreur : {e}", file=sys.stderr)
            return 1
        except OperationalError as e:
            print("Erreur : connexion à PostgreSQL impossible. La base est-elle démarrée "
                  "(docker compose ps) ? DB_PORT et DB_PASSWORD du .env sont-ils corrects ?\n"
                  f"Détail : {e.orig}", file=sys.stderr)
            return 1
        if run.summary["refused"]:
            print(f"Refusés : {', '.join(run.summary['refused'])}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "db":
        return _db(settings, args)
    stages = {"identity": ["identity"], "audit": ["audit"], "validate": ["validate"],
              "run": list(STAGES), "contract": ["draft"]}[args.cmd]
    run = run_pipeline(settings, stages, args.source)
    if "validate" in stages:
        failed = [k for k, ok in run.summary.get("validation", {}).items() if not ok]
        if failed:
            print(f"Non conformes : {', '.join(failed)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
