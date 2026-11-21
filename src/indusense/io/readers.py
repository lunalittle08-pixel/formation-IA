"""Lecture des sources en jeux de données bruts (tout en texte).

Un fichier CSV donne un jeu de données ; un script SQL en donne un par table
(colonnes du CREATE TABLE, lignes des INSERT). Le reste du pipeline ne voit que des
`Dataset` : il traite donc CSV et SQL de la même façon.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import sqlglot
from sqlglot import exp

from indusense.config import Settings, SourceSpec
from indusense.io.files import human_size, resolve_charset, sha256


@dataclass
class Dataset:
    id: str                          # identifiant du contrat associé
    df: pd.DataFrame                 # valeurs brutes (str)
    table: str | None = None         # nom de table (sources SQL)
    declared_types: dict[str, str] | None = None   # types SQL déclarés


@dataclass
class SourceFile:
    spec: SourceSpec
    charset: str
    encoding: str
    separator: str | None
    size_bytes: int
    sha256: str
    begin: datetime
    datasets: list[Dataset] = field(default_factory=list)
    end: datetime | None = None

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes)


# --------------------------------------------------------------------------- CSV
def detect_separator(path: Path, encoding: str, candidates: tuple[str, ...],
                     sniff_bytes: int) -> str:
    with path.open(encoding=encoding, newline="") as f:
        sample = f.read(sniff_bytes)
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(candidates)).delimiter
    except csv.Error:
        return ","


def read_csv_raw(path: Path, encoding: str, sep: str) -> pd.DataFrame:
    """Lecture en texte brut : aucune conversion silencieuse de pandas."""
    return pd.read_csv(path, sep=sep, encoding=encoding, dtype=str,
                       keep_default_na=False, na_filter=False)


# --------------------------------------------------------------------------- SQL
def _literal_to_str(node: exp.Expression) -> str:
    """Valeur d'un INSERT en texte brut (NULL -> chaîne vide, comme dans un CSV)."""
    if isinstance(node, exp.Null):
        return ""
    if isinstance(node, exp.Literal):
        return node.this
    if isinstance(node, exp.Boolean):
        return "true" if node.this else "false"
    return node.sql()


def read_sql_script(path: Path, encoding: str, dialect: str | None) -> list[Dataset]:
    """Un Dataset par table : schéma des CREATE TABLE + lignes des INSERT."""
    declared: dict[str, dict[str, str]] = {}
    rows: dict[str, list[dict]] = {}
    for stmt in sqlglot.parse(path.read_text(encoding=encoding), read=dialect):
        if isinstance(stmt, exp.Create) and stmt.kind == "TABLE" and isinstance(stmt.this, exp.Schema):
            schema = stmt.this
            declared[schema.this.name] = {
                c.name: (c.args["kind"].sql(dialect=dialect) if c.args.get("kind") else "")
                for c in schema.expressions if isinstance(c, exp.ColumnDef)
            }
        elif isinstance(stmt, exp.Insert):
            target = stmt.this
            if isinstance(target, exp.Schema):            # INSERT INTO t (a, b) VALUES ...
                table, cols = target.this.name, [c.name for c in target.expressions]
            else:                                         # INSERT INTO t VALUES ...
                table, cols = target.name, list(declared.get(target.name, {}))
            values = stmt.expression
            if isinstance(values, exp.Values):
                for tup in values.expressions:
                    rows.setdefault(table, []).append(
                        dict(zip(cols, (_literal_to_str(v) for v in tup.expressions))))
    datasets = []
    for table in sorted(declared.keys() | rows.keys()):
        columns = list(declared.get(table, {})) or None
        df = pd.DataFrame(rows.get(table, []), columns=columns, dtype=str).fillna("")
        datasets.append(Dataset(id=table, df=df, table=table,
                                declared_types=declared.get(table)))
    return datasets


# --------------------------------------------------------------------------- entrée
def load_source(spec: SourceSpec, settings: Settings) -> SourceFile:
    """Lit une source (CSV ou SQL) et renvoie ses métadonnées + jeux de données."""
    begin = datetime.now()
    charset, encoding = resolve_charset(spec.path, spec.encoding)
    src = SourceFile(spec=spec, charset=charset, encoding=encoding, separator=None,
                     size_bytes=spec.path.stat().st_size, sha256=sha256(spec.path),
                     begin=begin)
    if spec.resolved_format == "sql":
        src.datasets = read_sql_script(spec.path, encoding, spec.sql_dialect)
    else:
        sep = spec.separator if spec.separator != "auto" else detect_separator(
            spec.path, encoding, settings.candidate_separators, settings.sniff_bytes)
        src.separator = sep
        src.datasets = [Dataset(id=spec.id, df=read_csv_raw(spec.path, encoding, sep))]
    return src
