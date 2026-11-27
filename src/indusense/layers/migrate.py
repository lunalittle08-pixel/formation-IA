"""Migrations de schéma de la couche Bronze, avec Alembic (guide 01, étape 4).

La structure attendue (« métadonnée cible ») n'est écrite nulle part à la main : elle est
calculée à partir des contrats et des fichiers réels de `datas/`, par les mêmes fonctions que
le chargement (`stage.bronze_table`, `database.registry`). Le cycle est toujours le même :

1. comparer cette structure à la base ;
2. GÉNÉRER le script de migration (`revision`), sans l'appliquer ;
3. relire le script, en particulier les opérations destructrices (signalées) ;
4. l'appliquer (`upgrade`).

Le chargement ne crée ni ne modifie aucune table : il refuse une base en retard.
Les identifiants de connexion viennent du `.env` (jamais d'`alembic.ini`).
"""
from __future__ import annotations

import logging
import uuid
from logging.config import fileConfig
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.autogenerate import api as autogen
from alembic.config import Config
from alembic.operations import ops
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Engine, MetaData
from sqlalchemy.schema import CreateSchema

from indusense.config import Settings
from indusense.layers.database import EnvSettings, make_engine, registry
from indusense.layers.settings import LayerSettings

log = logging.getLogger("indusense")

# Opérations qui perdent des données ou en changent la forme : à relire avant application.
DESTRUCTIVE = (ops.DropTableOp, ops.DropColumnOp, ops.DropConstraintOp, ops.DropIndexOp)


class MigrationError(RuntimeError):
    """Migration impossible (dossier absent, révision inconnue...) : message pour l'utilisateur."""


# ------------------------------------------------------------------ structure cible
def target_metadata(settings: Settings, cfg: LayerSettings) -> MetaData:
    """Tables Bronze attendues (une par jeu de données chargeable) + registre des lots.

    Un jeu refusé (pas de contrat, contrat en écart avec le fichier) n'a pas de table attendue.
    """
    # Import local : stage importe ce module (vérification de la révision avant chargement).
    from indusense.config import all_sources
    from indusense.contracts.model import load_contracts
    from indusense.layers.bronze import BatchMeta
    from indusense.layers.stage import _relative, bronze_frame, bronze_table, loadable_datasets
    from indusense.pipeline import load_and_profile

    metadata = MetaData()
    contracts = load_contracts(settings.contracts_dir)
    loaded = load_and_profile(all_sources(settings), settings, contracts)
    meta = BatchMeta(uuid.uuid4(), "", "", datetime.now(UTC))   # valeurs sans effet sur les types
    for ds, profile, contract, path, _ in loadable_datasets(loaded, contracts, settings, cfg):
        bf = bronze_frame(ds, contract, profile, meta, settings, cfg)
        bronze_table(ds.id, bf, contract, _relative(path, settings.root), cfg, metadata)
    registry(cfg.bronze_schema, cfg.registry_table, metadata)
    return metadata


def only_schema(schema: str):
    """Filtre de comparaison : seul le schéma Bronze est géré (Silver et Gold sont recalculés)."""
    def include_name(name, type_, parent_names):
        return name == schema if type_ == "schema" else True
    return include_name


def destructive_operations(upgrade_ops: ops.UpgradeOps) -> list[str]:
    """Opérations destructrices d'un script généré, décrites pour la relecture humaine."""
    found = []

    def walk(op):
        if isinstance(op, DESTRUCTIVE):
            found.append(f"{type(op).__name__} {getattr(op, 'table_name', '')} "
                         f"{getattr(op, 'column_name', '') or getattr(op, 'constraint_name', '') or ''}"
                         .strip())
        elif isinstance(op, ops.AlterColumnOp) and op.modify_type is not None:
            found.append(f"AlterColumnOp {op.table_name}.{op.column_name} : type "
                         f"{op.existing_type} -> {op.modify_type}")
        for child in getattr(op, "ops", []):
            walk(child)

    walk(upgrade_ops)
    return found


# ------------------------------------------------------------------ env.py
def _needs_metadata(config: Config) -> bool:
    """La structure cible (lecture des fichiers) n'est utile qu'aux comparaisons."""
    if "needs_metadata" in config.attributes:
        return config.attributes["needs_metadata"]
    opts = config.cmd_opts
    if opts is None:
        return True
    name = getattr(getattr(opts, "cmd", (None,))[0], "__name__", "")
    return bool(getattr(opts, "autogenerate", False)) or name == "check"


def _settings(context) -> Settings:
    """Réglages passés par l'appelant, sinon la config désignée par `-x config=...`."""
    from indusense.config import DEFAULT_CONFIG, load_settings

    settings = context.config.attributes.get("settings")
    if settings is None:
        settings = load_settings(context.get_x_argument(as_dictionary=True).get("config",
                                                                                 DEFAULT_CONFIG))
    return settings


def _skip_empty_and_flag(config: Config):
    """Hook de génération : pas de script vide ; opérations destructrices signalées."""
    def process(context, revision, directives):
        if not getattr(config.cmd_opts, "autogenerate", config.attributes.get("autogenerate")):
            return
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []
            config.attributes["empty"] = True
            log.info("aucun écart entre la base et la structure attendue : pas de migration")
            return
        config.attributes["destructive"] = destructive_operations(script.upgrade_ops)
    return process


def run_env(context) -> None:
    """Corps de `migrations/env.py` : configure Alembic sur le schéma Bronze et lance."""
    config = context.config
    if "settings" not in config.attributes and config.config_file_name:
        fileConfig(config.config_file_name)      # appel direct `alembic ...` : journaux de l'ini
    settings = _settings(context)
    cfg = LayerSettings.from_raw(settings.raw, settings.root)
    options = dict(
        target_metadata=target_metadata(settings, cfg) if _needs_metadata(config) else None,
        include_schemas=True, include_name=only_schema(cfg.bronze_schema),
        version_table=cfg.version_table, version_table_schema=cfg.bronze_schema,
        compare_type=True, process_revision_directives=_skip_empty_and_flag(config))
    if context.is_offline_mode():           # --sql : script SQL à relire, sans connexion
        context.configure(dialect_name="postgresql", literal_binds=True, **options)
        with context.begin_transaction():
            context.run_migrations()
        return
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_online(context, connection, cfg, options)
        return
    with make_engine(EnvSettings()).connect() as connection:
        _run_online(context, connection, cfg, options)


def _run_online(context, connection, cfg: LayerSettings, options: dict) -> None:
    context.configure(connection=connection, **options)
    with context.begin_transaction():
        connection.execute(CreateSchema(cfg.bronze_schema, if_not_exists=True))
        context.run_migrations()


# ------------------------------------------------------------------ commandes
def alembic_config(settings: Settings, cfg: LayerSettings) -> Config:
    """Configuration Alembic du projet : `alembic.ini` s'il existe, dossier de la config."""
    ini = settings.root / "alembic.ini"
    config = Config(str(ini)) if ini.exists() else Config()
    if not (cfg.migrations_dir / "env.py").exists():
        raise MigrationError(f"dossier de migrations introuvable : {cfg.migrations_dir} (env.py)")
    config.set_main_option("script_location", str(cfg.migrations_dir))
    config.attributes["settings"] = settings
    return config


def _with_connection(settings: Settings, engine: Engine | None, action, needs_metadata: bool):
    """Exécute une commande Alembic sur une connexion (transaction validée à la fin)."""
    cfg = LayerSettings.from_raw(settings.raw, settings.root)
    config = alembic_config(settings, cfg)
    config.attributes["needs_metadata"] = needs_metadata
    engine = engine or make_engine(EnvSettings())
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            result = action(config)
    except CommandError as e:
        raise MigrationError(str(e)) from e
    return config, result


def revision(settings: Settings, message: str, engine: Engine | None = None) -> dict:
    """Génère (sans appliquer) le script qui amène la base à la structure attendue.

    Renvoie {"path": script ou None si aucun écart, "destructive": opérations à relire}.
    """
    def action(config):
        config.attributes["autogenerate"] = True
        return command.revision(config, message=message, autogenerate=True)

    config, script = _with_connection(settings, engine, action, needs_metadata=True)
    if config.attributes.get("empty") or script is None:
        return {"path": None, "destructive": []}
    return {"path": Path(script.path), "destructive": config.attributes.get("destructive", [])}


def upgrade(settings: Settings, target: str = "head", engine: Engine | None = None) -> None:
    """Applique les scripts (relus) jusqu'à `target`."""
    _with_connection(settings, engine, lambda c: command.upgrade(c, target), needs_metadata=False)


def downgrade(settings: Settings, target: str, engine: Engine | None = None) -> None:
    """Revient à la révision `target` (ex. « -1 ») : exécute les `downgrade()` des scripts."""
    _with_connection(settings, engine, lambda c: command.downgrade(c, target),
                     needs_metadata=False)


def differences(settings: Settings, engine: Engine | None = None) -> list:
    """Écarts entre la base et la structure attendue (liste vide : à jour). N'écrit rien."""
    cfg = LayerSettings.from_raw(settings.raw, settings.root)
    metadata = target_metadata(settings, cfg)
    engine = engine or make_engine(EnvSettings())
    with engine.connect() as connection:
        mc = MigrationContext.configure(connection, opts={
            "include_schemas": True, "include_name": only_schema(cfg.bronze_schema),
            "version_table": cfg.version_table, "version_table_schema": cfg.bronze_schema,
            "compare_type": True, "target_metadata": metadata})
        return autogen.compare_metadata(mc, metadata)


def schema_status(engine: Engine, settings: Settings, cfg: LayerSettings) -> tuple[set, set]:
    """(révisions appliquées en base, dernières révisions des scripts)."""
    try:
        script = ScriptDirectory.from_config(alembic_config(settings, cfg))
        heads = set(script.get_heads())
    except CommandError as e:
        raise MigrationError(str(e)) from e
    with engine.connect() as connection:
        mc = MigrationContext.configure(connection, opts={
            "version_table": cfg.version_table, "version_table_schema": cfg.bronze_schema})
        return set(mc.get_current_heads()), heads
