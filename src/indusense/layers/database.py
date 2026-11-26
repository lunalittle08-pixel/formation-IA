"""Connexion à PostgreSQL, lecture et (re)chargement des tables des couches.

Les secrets (mot de passe, sel) viennent du fichier `.env` (jamais versionné),
lu par pydantic-settings ; les réglages non secrets viennent de configs/pipeline.yaml.
"""
from __future__ import annotations

import pandas as pd
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (Column, Engine, MetaData, Table, Text, create_engine, inspect, select,
                        text)
from sqlalchemy import func as sqlfunc
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
