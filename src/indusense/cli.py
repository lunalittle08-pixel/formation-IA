"""Interface en ligne de commande.

    uv run indusense identity                 # cartes d'identité (TP1)
    uv run indusense audit                    # audit qualité + cartographie
    uv run indusense contract draft           # brouillons de contrats
    uv run indusense contract check           # structure des contrats (CI)
    uv run indusense validate                 # données vs contrats (code retour 1 si erreur)
    uv run indusense run                      # toutes les étapes
    uv run indusense load bronze|silver|gold  # chargement d'une couche dans PostgreSQL
    uv run indusense load all                 # les trois couches, dans l'ordre
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
    ld.add_argument("--rebuild", action="store_true",
                    help="supprime et recrée les tables Bronze (changement de structure décidé)")
    c = sub.add_parser("contract", help="gestion des contrats")
    c.add_argument("action", choices=["draft", "check"])
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s | %(message)s")
    settings = load_settings(args.config)

    if args.cmd == "contract" and args.action == "check":
        return _contract_check(settings)
    if args.cmd == "load":
        # Import local : les autres commandes n'ont besoin ni de la base ni du .env
        from sqlalchemy.exc import OperationalError

        from indusense.layers.stage import LAYERS, LayerError, run_layers
        layers = list(LAYERS) if args.layer == "all" else [args.layer]
        try:
            run = run_layers(settings, layers, args.source, rebuild=args.rebuild)
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
