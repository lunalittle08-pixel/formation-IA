"""Orchestration du chargement des couches : bronze -> silver -> gold.

    bronze : fichiers de datas/ -> bronze.<jeu>   (lecture brute, types inférés, empreinte, PII)
    silver : bronze.<jeu>       -> silver.<jeu> + silver.<jeu>_rejects + bilan Markdown/JSON
    gold   : configs/gold/<nom>.sql (SELECT sur le Silver) -> gold.<nom>

Chaque couche ne lit que la couche précédente : on peut rejouer le Silver sans relire les
fichiers, et le Gold sans refaire le Silver. Chaque exécution écrit un manifeste.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import Engine

from indusense.config import Settings, all_sources
from indusense.contracts.model import load_contracts
from indusense.layers.anonymize import columns_to_pseudonymize, undecided_pii_columns
from indusense.layers.bronze import build_bronze_frame, name_errors
from indusense.layers.database import (SAMPLE_SALT, EnvSettings, create_table_as, list_tables,
                                       load_table, make_engine, make_table, read_text_table,
                                       text_table)
from indusense.layers.gold import gold_queries, query_errors, strip_sql
from indusense.layers.settings import LayerSettings
from indusense.layers.silver import build_silver, render_report
from indusense.pipeline import Run, load_and_profile, write_manifest

log = logging.getLogger("indusense")
LAYERS = ("bronze", "silver", "gold")


class LayerError(RuntimeError):
    """Chargement impossible (configuration ou données) : message explicite pour l'utilisateur."""


def check_salt(salt: str) -> None:
    if not salt:
        raise LayerError("ANONYMISATION_SALT absent du .env : pseudonymisation impossible")
    if salt == SAMPLE_SALT:
        log.warning("sel d'exemple de env.sample utilisé : acceptable en formation, jamais en réel")


# ------------------------------------------------------------------ bronze
def load_bronze(run: Run, engine: Engine, env: EnvSettings, cfg: LayerSettings,
                contracts: dict, source_ids: list[str] | None) -> list:
    check_salt(env.anonymisation_salt)
    specs = [s for s in all_sources(run.settings) if not source_ids or s.id in source_ids]
    loaded = load_and_profile(specs, run.settings, contracts)
    summary = run.summary.setdefault("bronze", {})
    for ls in loaded:
        for ds, profile in zip(ls.src.datasets, ls.profiles):
            contract = contracts.get(ds.id)
            if contract is None:      # sans contrat, on ne sait pas quelles colonnes protéger
                log.error("%s : aucun contrat, chargement refusé (protection par défaut)", ds.id)
                run.summary.setdefault("refused", []).append(f"bronze.{ds.id}")
                continue
            errors = name_errors(list(ds.df.columns), cfg)
            if errors:
                log.error("%s : %s", ds.id, " ; ".join(errors))
                run.summary.setdefault("refused", []).append(f"bronze.{ds.id}")
                continue
            pii = columns_to_pseudonymize(contract, cfg.pseudonymize_privacy)
            absent = [c for c in pii if c not in ds.df.columns]
            if absent:   # écart contrat/données : c'est la validation qui le traite
                log.warning("%s : colonnes du contrat absentes des données : %s", ds.id, absent)
            pii = [c for c in pii if c in ds.df.columns]
            privacy = {c["name"]: c.get("privacy") for c in contract["schema"]["columns"]}
            for col in undecided_pii_columns(contract, cfg.pseudonymize_privacy):
                log.warning("%s.%s : donnée personnelle laissée en clair (privacy=%s), décision à prendre",
                            ds.id, col, privacy[col])
            types = {c.name: c.type for c in profile.columns}
            frame = build_bronze_frame(ds.df, types, pii, env.anonymisation_salt, cfg)
            comment = (f"Bronze {ds.id} | source {ls.src.spec.path.name} sha256={ls.src.sha256} "
                       f"| run {run.run_id} | pseudonymisées : {', '.join(pii) or 'aucune'}")
            table = text_table(ds.id, list(frame.columns), cfg.bronze_schema, comment)
            n = load_table(engine, table, frame, cfg.chunk_size)
            if n != len(frame):
                raise LayerError(f"{ds.id} : {n} lignes en base pour {len(frame)} attendues")
            log.info("%s.%s : %d lignes, %d colonnes (pseudonymisées : %s)",
                     cfg.bronze_schema, ds.id, n, frame.shape[1], ", ".join(pii) or "aucune")
            run.outputs.append(f"postgresql:{cfg.bronze_schema}.{ds.id}")
            summary[ds.id] = {"rows": n, "columns": frame.shape[1], "pseudonymized": pii}
    return loaded


# ------------------------------------------------------------------ silver
def load_silver(run: Run, engine: Engine, cfg: LayerSettings, contracts: dict,
                dataset_ids: list[str] | None) -> None:
    summary = run.summary.setdefault("silver", {})
    tables = list_tables(engine, cfg.bronze_schema)
    if not tables:
        log.warning("aucune table dans le schéma %s : lancer d'abord `indusense load bronze`",
                    cfg.bronze_schema)
    for name in tables:
        if dataset_ids and name not in dataset_ids:
            continue
        contract = contracts.get(name)
        if contract is None:
            log.error("%s : aucun contrat, Silver impossible (pas de types ni de règles attendus)", name)
            run.summary.setdefault("refused", []).append(f"silver.{name}")
            continue
        bronze = read_text_table(engine, cfg.bronze_schema, name)
        result = build_silver(name, bronze, contract, cfg, run.settings.inference)
        r = result.report
        for col in r["dropped_columns_not_in_contract"]:
            log.warning("%s.%s : colonne absente du contrat, non reprise au Silver", name, col)
        comment = (f"Silver {name} | depuis {cfg.bronze_schema}.{name} | contrat v{r['contract_version']} "
                   f"| run {run.run_id}")
        n = load_table(engine, make_table(name, result.column_types, cfg.silver_schema, comment),
                       result.valid, cfg.chunk_size)
        rej_name = f"{name}{cfg.rejects_suffix}"
        rej_table = text_table(rej_name, list(result.rejects.columns), cfg.silver_schema,
                               f"Quarantaine Silver {name} | run {run.run_id}")
        n_rej = load_table(engine, rej_table, result.rejects, cfg.chunk_size)
        if (n, n_rej) != (len(result.valid), len(result.rejects)):
            raise LayerError(f"{name} : nombre de lignes en base incohérent")
        log.info("%s.%s : %d lignes ; quarantaine : %d ; doublons exacts retirés : %d",
                 cfg.silver_schema, name, n, n_rej, r["exact_duplicates_removed"])
        run.write(run.out_path("layers", f"silver_{name}"), render_report(result, cfg.silver_schema, rej_name))
        run.write(run.out_path("layers", f"silver_{name}", ext="json"),
                  json.dumps(r, ensure_ascii=False, indent=2))
        run.outputs += [f"postgresql:{cfg.silver_schema}.{name}", f"postgresql:{cfg.silver_schema}.{rej_name}"]
        summary[name] = {k: r[k] for k in ("rows_bronze", "rows_silver", "rows_rejected",
                                           "exact_duplicates_removed")}


# ------------------------------------------------------------------ gold
def load_gold(run: Run, engine: Engine, cfg: LayerSettings) -> None:
    summary = run.summary.setdefault("gold", {})
    queries = gold_queries(cfg.gold_queries_dir)
    if not queries:
        log.info("aucune requête dans %s : rien à construire au Gold pour l'instant",
                 cfg.gold_queries_dir)
    for name, query in queries.items():
        errors = query_errors(name, query)
        if errors:
            log.error("gold %s : %s", name, " ; ".join(errors))
            run.summary.setdefault("refused", []).append(f"gold.{name}")
            continue
        comment = f"Gold {name} | requête {cfg.gold_queries_dir.name}/{name}.sql | run {run.run_id}"
        n = create_table_as(engine, cfg.gold_schema, name, strip_sql(query), comment)
        log.info("%s.%s : %d lignes", cfg.gold_schema, name, n)
        run.outputs.append(f"postgresql:{cfg.gold_schema}.{name}")
        summary[name] = {"rows": n}


# ------------------------------------------------------------------ point d'entrée
def run_layers(settings: Settings, layers: list[str], source_ids: list[str] | None = None,
               env: EnvSettings | None = None, engine: Engine | None = None) -> Run:
    """Charge les couches demandées, dans l'ordre bronze -> silver -> gold ; écrit le manifeste."""
    env = env or EnvSettings()
    cfg = LayerSettings.from_raw(settings.raw, settings.root)
    if "bronze" in layers:
        check_salt(env.anonymisation_salt)      # avant toute lecture : on échoue tôt
    engine = engine or make_engine(env)
    with engine.connect():                      # échoue tout de suite si la base est injoignable
        pass
    run = Run(settings)
    run.summary["refused"] = []
    contracts = load_contracts(settings.contracts_dir)
    loaded = []
    if "bronze" in layers:
        loaded = load_bronze(run, engine, env, cfg, contracts, source_ids)
    if "silver" in layers:
        load_silver(run, engine, cfg, contracts, source_ids)
    if "gold" in layers:
        load_gold(run, engine, cfg)
    log.info("manifeste : %s", write_manifest(run, loaded, [f"load:{x}" for x in layers]))
    return run
