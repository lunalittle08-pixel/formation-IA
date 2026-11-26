"""Connexion à PostgreSQL, lecture et (re)chargement des tables des couches.

Les secrets (mot de passe, sel) viennent du fichier `.env` (jamais versionné),
lu par pydantic-settings ; les réglages non secrets viennent de configs/pipeline.yaml.
"""
from __future__ import annotations

import pandas as pd
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    DateTime,
    Engine,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    Uuid,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy import func as sqlfunc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL
from sqlalchemy.schema import CreateSchema
from sqlalchemy.types import TypeEngine

SAMPLE_SALT = "dev-salt-indusense-change-me"   # valeur d'exemple de env.sample


class EnvSettings(BaseSettings):
    """Variables du `.env` utiles au chargement (les autres sont ignorées)."""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "indusense_db"
    db_user: str = "indusense_user"
    db_password: str = ""
    database_url: str | None = None          # prioritaire si renseignée
    anonymisation_salt: str = ""

    def url(self) -> URL | str:
        """URL SQLAlchemy ; URL.create échappe les caractères spéciaux du mot de passe."""
        if self.database_url:
            return self.database_url
        return URL.create("postgresql+psycopg", username=self.db_user, password=self.db_password,
                          host=self.db_host, port=self.db_port, database=self.db_name)


def make_engine(env: EnvSettings) -> Engine:
    return create_engine(env.url(), pool_pre_ping=True)


def make_table(name: str, column_types: dict[str, TypeEngine | type], schema: str,
               comment: str) -> Table:
    """Description SQLAlchemy d'une table : nom de colonne -> type SQL, dans l'ordre."""
    return Table(name, MetaData(), *(Column(c, t) for c, t in column_types.items()),
                 schema=schema, comment=comment)


def keyed_table(name: str, column_types: dict, key: list[str], schema: str, comment: str) -> Table:
    """Table dont l'unicité est portée par la base (clé primaire), pas par le code."""
    return Table(name, MetaData(), *(Column(c, t) for c, t in column_types.items()),
                 PrimaryKeyConstraint(*key), schema=schema, comment=comment)


class SchemaMismatch(RuntimeError):
    """La table existe avec d'autres colonnes que celles attendues."""


def ensure_table(conn: Connection, table: Table, rebuild: bool = False) -> None:
    """Crée la table si besoin. Si elle existe avec d'autres colonnes : on refuse.

    Rien n'est modifié en silence : changer la structure d'une table qui contient déjà des
    lots est une décision (reconstruction explicite avec `rebuild`, ou migration).
    """
    conn.execute(CreateSchema(table.schema, if_not_exists=True))
    if rebuild:
        table.drop(conn, checkfirst=True)
    insp = inspect(conn)
    if not insp.has_table(table.name, schema=table.schema):
        table.create(conn)
        return
    existing = [c["name"] for c in insp.get_columns(table.name, schema=table.schema)]
    expected = [c.name for c in table.columns]
    if existing != expected:
        missing = [c for c in expected if c not in existing]
        unexpected = [c for c in existing if c not in expected]
        raise SchemaMismatch(f"{table.schema}.{table.name} existe avec une autre structure "
                             f"(attendues absentes : {missing} ; en trop : {unexpected}). "
                             "Si ce changement est décidé : relancer avec --rebuild")


def insert_new_rows(conn: Connection, table: Table, frame: pd.DataFrame, chunk_size: int) -> int:
    """INSERT ... ON CONFLICT DO NOTHING : une ligne déjà présente (même clé) est ignorée.

    Renvoie le nombre de lignes réellement insérées.
    """
    records = _records(frame)
    key = next(iter(table.primary_key.columns))
    # RETURNING : seules les lignes réellement insérées sont renvoyées, on les compte
    stmt = pg_insert(table).on_conflict_do_nothing().returning(key)
    inserted = 0
    for start in range(0, len(records), chunk_size):
        inserted += len(conn.execute(stmt, records[start:start + chunk_size]).all())
    return inserted


def registry(schema: str, name: str) -> Table:
    """Registre des lots : une ligne par tentative de chargement, réussie ou non."""
    return Table(
        name, MetaData(),
        Column("batch_id", Uuid, primary_key=True), Column("dataset_id", Text, nullable=False),
        Column("source_file", Text), Column("source_hash", Text), Column("contract_version", Text),
        Column("contract_hash", Text), Column("records_read", BigInteger),
        Column("records_inserted", BigInteger), Column("records_already_present", BigInteger),
        Column("rows_with_cast_errors", BigInteger), Column("status", Text, nullable=False),
        Column("message", Text), Column("started_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True)),
        schema=schema, comment="Registre des lots d'ingestion Bronze")


def record_batch(engine: Engine, table: Table, entry: dict) -> None:
    """Écrit une ligne du registre dans SA PROPRE transaction : un échec reste tracé."""
    with engine.begin() as conn:
        conn.execute(CreateSchema(table.schema, if_not_exists=True))
        table.create(conn, checkfirst=True)
        conn.execute(table.insert(), [entry])


def text_table(name: str, columns: list[str], schema: str, comment: str) -> Table:
    """Table dont toutes les colonnes sont en TEXT (Bronze : valeurs à l'identique)."""
    return make_table(name, {c: Text for c in columns}, schema, comment)


def _records(frame: pd.DataFrame) -> list[dict]:
    """Lignes à insérer ; les valeurs manquantes de pandas (NaN, NaT, NA) deviennent NULL."""
    clean = frame.astype(object).where(frame.notna(), None)
    return clean.to_dict(orient="records")


def load_table(engine: Engine, table: Table, frame: pd.DataFrame, chunk_size: int) -> int:
    """Recharge complète dans UNE transaction : tout passe ou rien ne change.

    DROP + CREATE + INSERT : relancer donne toujours le même résultat (idempotence).
    Renvoie le nombre de lignes relu en base, pour contrôle.
    """
    records = _records(frame)
    with engine.begin() as conn:
        conn.execute(CreateSchema(table.schema, if_not_exists=True))
        table.drop(conn, checkfirst=True)
        table.create(conn)
        for start in range(0, len(records), chunk_size):
            conn.execute(table.insert(), records[start:start + chunk_size])
        return conn.execute(select(sqlfunc.count()).select_from(table)).scalar_one()


def list_tables(engine: Engine, schema: str) -> list[str]:
    """Tables d'un schéma (liste vide si le schéma n'existe pas)."""
    insp = inspect(engine)
    return sorted(insp.get_table_names(schema=schema)) if schema in insp.get_schema_names() else []


def read_text_table(engine: Engine, schema: str, name: str) -> pd.DataFrame:
    """Relit une table en texte (valeurs telles que stockées, NULL -> chaîne vide)."""
    df = pd.read_sql_table(name, engine, schema=schema)
    return df.astype(object).where(df.notna(), "").astype(str)


def read_columns(engine: Engine, schema: str, name: str, columns: list[str]) -> pd.DataFrame:
    """Relit certaines colonnes d'une table en texte, dans l'ordre de la clé technique."""
    cols = ", ".join(f'"{c}"' for c in columns)
    df = pd.read_sql_query(text(f'SELECT {cols} FROM "{schema}"."{name}"'), engine)
    return df.astype(object).where(df.notna(), "").astype(str)


def create_table_as(engine: Engine, schema: str, name: str, query: str, comment: str) -> int:
    """Gold : (re)crée `schema.name` à partir d'une requête SELECT, dans une transaction."""
    target = f'"{schema}"."{name}"'
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema, if_not_exists=True))
        conn.execute(text(f"DROP TABLE IF EXISTS {target}"))
        conn.execute(text(f"CREATE TABLE {target} AS {query}"))
        # COMMENT n'accepte pas de paramètre lié : on échappe les apostrophes (règle SQL)
        literal = comment.replace("'", "''").replace(":", r"\:")   # \: : pas un paramètre SQLAlchemy
        conn.execute(text(f"COMMENT ON TABLE {target} IS '{literal}'"))
        return conn.execute(text(f"SELECT count(*) FROM {target}")).scalar_one()
