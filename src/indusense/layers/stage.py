"""Orchestration du chargement des couches : bronze -> silver -> gold.

    bronze : fichiers de datas/ -> bronze.<jeu>   (x_raw + x typé, colonnes techniques, rejeu sans doublon)
    silver : bronze.<jeu>       -> silver.<jeu> + silver.<jeu>_rejects + bilan Markdown/JSON
    gold   : configs/gold/<nom>.sql (SELECT sur le Silver) -> gold.<nom>

Chaque couche ne lit que la couche précédente : on peut rejouer le Silver sans relire les
fichiers, et le Gold sans refaire le Silver. Chaque exécution écrit un manifeste.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, MetaData, Table

from indusense.config import Settings, all_sources
from indusense.contracts.model import load_contracts
from indusense.io.files import sha256
from indusense.layers.anonymize import undecided_pii_columns
from indusense.layers.bronze import BatchMeta, BronzeFrame, build_bronze_frame
from indusense.layers.checks import bronze_issues
from indusense.layers.database import (
    SAMPLE_SALT,
    MIGRATION_HINT,
    EnvSettings,
    SchemaMismatch,
    check_table,
    create_table_as,
    insert_new_rows,
    keyed_table,
    list_tables,
    load_table,
    make_engine,
    make_table,
    read_text_table,
    record_batch,
    registry,
    text_table,
)
from indusense.layers.dictionary import (
    render_markdown,
    silver_operations,
    transformations,
)
from indusense.layers.gold import gold_queries, query_errors, strip_sql
from indusense.layers.migrate import MigrationError, schema_status
from indusense.layers.settings import LayerSettings
from indusense.layers.silver import build_silver, render_report
from indusense.pipeline import Run, load_and_profile, write_manifest

log = logging.getLogger("indusense")
LAYERS = ("bronze", "silver", "gold")


class LayerError(RuntimeError):
    """Chargement impossible (configuration ou données) : message explicite pour l'utilisateur."""


class CastThresholdExceeded(RuntimeError):
    """Trop de lignes avec une erreur de conversion : le lot est annulé."""


def check_salt(salt: str) -> None:
    if not salt:
        raise LayerError("ANONYMISATION_SALT absent du .env : pseudonymisation impossible")
    if salt == SAMPLE_SALT:
        log.warning("sel d'exemple de env.sample utilisé : acceptable en formation, jamais en réel")


# ------------------------------------------------------------------ bronze
def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _cast_ratio_limit(contract: dict, cfg: LayerSettings) -> float:
    """Seuil du contrat (`source.max_cast_error_ratio`) s'il existe, sinon celui de la config."""
    return float((contract.get("source") or {}).get("max_cast_error_ratio", cfg.max_cast_error_ratio))


def _types(ds, contract: dict, profile) -> dict[str, str]:
    """Type de chaque colonne : celui du contrat ; à défaut (colonne en plus autorisée), l'inféré."""
    declared = {c["name"]: c["type"] for c in contract["schema"]["columns"]}
    inferred = {c.name: c.type for c in profile.columns}
    return {name: declared.get(name, inferred.get(name, "TEXT")) for name in ds.df.columns}


def bronze_frame(ds, contract: dict, profile, meta: BatchMeta, settings: Settings,
                 cfg: LayerSettings) -> BronzeFrame:
    """Table Bronze en mémoire d'un jeu de données (types : contrat, sinon inférés)."""
    declared_sql = {c["name"]: c.get("declared_type") for c in contract["schema"]["columns"]}
    return build_bronze_frame(ds.df, _types(ds, contract, profile), meta, cfg,
                              settings.inference.parse_rate_min, declared_sql)


def bronze_table(dataset_id: str, bf: BronzeFrame, contract: dict, source: str,
                 cfg: LayerSettings, metadata: MetaData | None = None) -> Table:
    """Description SQLAlchemy de la table Bronze : la même pour le chargement et les migrations."""
    version = contract["contract"].get("version")
    return keyed_table(dataset_id, bf.sql_types, cfg.lineage, cfg.bronze_schema,
                       f"Bronze {dataset_id} | source {source} | contrat v{version}", metadata)


def loadable_datasets(loaded: list, contracts: dict, settings: Settings, cfg: LayerSettings,
                      refused: list | None = None):
    """Jeux de données dont le contrat est confronté sans erreur au fichier réel.

    Produit (jeu, profil, contrat, chemin de la source, empreinte). Les refusés sont journalisés
    et ajoutés à `refused` (« bronze.<jeu> »).
    """
    for ls in loaded:
        for ds, profile in zip(ls.src.datasets, ls.profiles):
            source = _relative(ls.src.spec.path, settings.root)
            label = f"{source}:{ds.table}" if ds.table else source
            contract = contracts.get(ds.id)
            errors, warnings = bronze_issues(ds, contract, label, cfg)
            for w in warnings:
                log.warning("%s : %s", ds.id, w)
            if errors:                            # contrat contre fichier réel : rien n'est écrit
                for e in errors:
                    log.error("%s : %s", ds.id, e)
                if refused is not None:
                    refused.append(f"bronze.{ds.id}")
                continue
            yield ds, profile, contract, ls.src.spec.path, ls.src.sha256


def load_one_bronze(engine: Engine, ds, contract: dict, profile, source_path: Path,
                    source_hash: str, run: Run, cfg: LayerSettings) -> dict:
    """Charge un jeu de données au Bronze ; renvoie la ligne du registre (statut compris)."""
    started = datetime.now(UTC)
    source = _relative(source_path, run.settings.root)
    entry = {"batch_id": uuid.uuid4(), "dataset_id": ds.id, "source_file": source,
             "source_hash": source_hash, "contract_version": str(contract["contract"].get("version")),
             "contract_hash": sha256(Path(contract["_path"])) if contract.get("_path") else None,
             "records_read": len(ds.df), "records_inserted": 0, "records_already_present": 0,
             "rows_with_cast_errors": 0, "status": "FAILED", "message": None, "started_at": started}
    meta = BatchMeta(entry["batch_id"], source, source_hash, started)
    bf = bronze_frame(ds, contract, profile, meta, run.settings, cfg)
    entry["rows_with_cast_errors"] = bf.rows_with_cast_errors
    limit = _cast_ratio_limit(contract, cfg)
    ratio = bf.rows_with_cast_errors / len(ds.df) if len(ds.df) else 0.0
    table = bronze_table(ds.id, bf, contract, source, cfg)
    try:
        if ratio > limit:
            raise CastThresholdExceeded(f"{bf.rows_with_cast_errors} lignes avec erreur de conversion "
                                        f"({ratio:.2%}) > seuil {limit:.2%} : rien n'est écrit")
        with engine.begin() as conn:            # une transaction : tout le lot ou rien
            check_table(conn, table)            # structure : migrations, jamais ici
            inserted = insert_new_rows(conn, table, bf.frame, cfg.chunk_size)
        entry.update(records_inserted=inserted, records_already_present=len(ds.df) - inserted,
                     status="SUCCESS")
    except (CastThresholdExceeded, SchemaMismatch) as e:
        entry["message"] = str(e)
    entry["completed_at"] = datetime.now(UTC)
    record_batch(engine, registry(cfg.bronze_schema, cfg.registry_table), entry)
    _write_dictionary(run, ds.id, bf, contract, entry, limit, cfg)
    return entry


def _write_dictionary(run: Run, dataset_id: str, bf, contract: dict, entry: dict, limit: float,
                      cfg: LayerSettings) -> None:
    batch = {k: (str(v) if k in ("batch_id", "started_at", "completed_at") else v)
             for k, v in entry.items()}
    d = {"dataset_id": dataset_id, "batch": batch, "max_cast_error_ratio": limit,
         "columns": bf.columns,
         "transformations": transformations(bf, cfg),
         "silver_operations": silver_operations(contract, cfg)}
    run.write(run.out_path("layers", f"bronze_{dataset_id}"), render_markdown(d))
    run.write(run.out_path("layers", f"bronze_{dataset_id}", ext="json"),
              json.dumps(d, ensure_ascii=False, indent=2, default=str))


def load_bronze(run: Run, engine: Engine, cfg: LayerSettings, contracts: dict,
                source_ids: list[str] | None) -> list:
    specs = [s for s in all_sources(run.settings) if not source_ids or s.id in source_ids]
    loaded = load_and_profile(specs, run.settings, contracts)
    summary = run.summary.setdefault("bronze", {})
    refused = run.summary.setdefault("refused", [])
    for ds, profile, contract, path, source_hash in loadable_datasets(loaded, contracts,
                                                                       run.settings, cfg, refused):
        for col in undecided_pii_columns(contract):
            log.warning("%s.%s : donnée personnelle sans décision de protection (privacy à "
                        "trancher) ; elle restera en clair au Silver", ds.id, col)
        entry = load_one_bronze(engine, ds, contract, profile, path, source_hash, run, cfg)
        if entry["status"] != "SUCCESS":
            log.error("%s : lot %s refusé — %s", ds.id, entry["batch_id"], entry["message"])
            refused.append(f"bronze.{ds.id}")
        else:
            log.info("%s.%s : %d lues, %d insérées, %d déjà présentes, %d avec erreur de conversion",
                     cfg.bronze_schema, ds.id, entry["records_read"], entry["records_inserted"],
                     entry["records_already_present"], entry["rows_with_cast_errors"])
            run.outputs.append(f"postgresql:{cfg.bronze_schema}.{ds.id}")
        summary[ds.id] = {k: entry[k] for k in ("status", "records_read", "records_inserted",
                                                "records_already_present", "rows_with_cast_errors")}
        summary[ds.id]["batch_id"] = str(entry["batch_id"])
    return loaded


def require_migrated(engine: Engine, settings: Settings, cfg: LayerSettings) -> None:
    """Refuse le chargement si la base n'est pas à la dernière migration (rien n'est créé ici)."""
    try:
        applied, heads = schema_status(engine, settings, cfg)
    except MigrationError as e:
        raise LayerError(str(e)) from e
    if not heads:
        raise LayerError(f"aucune migration dans {cfg.migrations_dir} : {MIGRATION_HINT}")
    if applied != heads:
        raise LayerError(f"base à la révision {sorted(applied) or 'aucune'}, migrations à "
                         f"{sorted(heads)} : relire puis appliquer (uv run indusense db upgrade)")


# ------------------------------------------------------------------ silver
def load_silver(run: Run, engine: Engine, cfg: LayerSettings, contracts: dict,
                dataset_ids: list[str] | None, salt: str) -> None:
    summary = run.summary.setdefault("silver", {})
    technical = {cfg.registry_table, cfg.version_table}
    tables = [t for t in list_tables(engine, cfg.bronze_schema) if t not in technical]
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
        result = build_silver(name, bronze, contract, cfg, run.settings.inference, salt)
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
        log.info("%s.%s : %d lignes ; quarantaine : %d ; doublons stricts retirés : %d ; "
                 "pseudonymisées : %s", cfg.silver_schema, name, n, n_rej,
                 r["exact_duplicates_removed"], ", ".join(r["pseudonymized_columns"]) or "aucune")
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
    """Charge les couches demandées, dans l'ordre bronze -> silver -> gold ; écrit le manifeste.

    Le Bronze exige une base à la dernière migration : un changement de structure passe par
    `indusense db revision` (script généré, relu) puis `indusense db upgrade`.
    """
    env = env or EnvSettings()
    cfg = LayerSettings.from_raw(settings.raw, settings.root)
    if "silver" in layers:
        check_salt(env.anonymisation_salt)      # avant toute lecture : on échoue tôt
    engine = engine or make_engine(env)
    with engine.connect():                      # échoue tout de suite si la base est injoignable
        pass
    if "bronze" in layers:
        require_migrated(engine, settings, cfg)
    run = Run(settings)
    run.summary["refused"] = []
    contracts = load_contracts(settings.contracts_dir)
    loaded = []
    if "bronze" in layers:
        loaded = load_bronze(run, engine, cfg, contracts, source_ids)
    if "silver" in layers:
        load_silver(run, engine, cfg, contracts, source_ids, env.anonymisation_salt)
    if "gold" in layers:
        load_gold(run, engine, cfg)
    log.info("manifeste : %s", write_manifest(run, loaded, [f"load:{x}" for x in layers]))
    return run
