"""Couche Bronze : construction de la table en mémoire (pandas), sans base de données.

Le Bronze recopie la source, il ne la corrige pas. Pour chaque colonne source `x` :
- `x<raw_suffix>` (`x_raw`) : la valeur d'origine, en texte, intacte ;
- `x` : la valeur convertie au mieux vers le type du contrat ; NULL si la case est vide
  (une absence n'est pas une erreur) ou si la conversion échoue (l'échec est alors tracé).
Puis les colonnes techniques : lot, fichier, empreinte du fichier, numéro de ligne,
date de chargement, empreinte de la ligne et erreurs de conversion (JSONB).
Tout est séparé de la base pour pouvoir être testé seul.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy import BigInteger, DateTime, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from indusense.layers.settings import LayerSettings
from indusense.layers.sqltypes import CONVERTIBLE, sql_type, to_python
from indusense.profiling.types import convert

PG_MAX_IDENTIFIER = 63      # longueur maximale d'un nom de colonne PostgreSQL


@dataclass(frozen=True)
class BatchMeta:
    """Ce qui identifie un lot d'ingestion : recopié sur chaque ligne."""
    batch_id: object          # uuid.UUID
    source_file: str
    source_hash: str          # SHA-256 des octets du fichier
    ingested_at: datetime     # UTC


@dataclass
class BronzeFrame:
    frame: pd.DataFrame                       # lignes prêtes à insérer
    sql_types: dict[str, object]              # nom de colonne -> type SQL, dans l'ordre
    columns: list[dict] = field(default_factory=list)   # statistiques par colonne source
    rows_with_cast_errors: int = 0            # lignes portant au moins une erreur de conversion


# ------------------------------------------------------------------ noms
def raw_column(name: str, cfg: LayerSettings) -> str:
    """Nom de la colonne qui garde la valeur d'origine."""
    return f"{name}{cfg.raw_suffix}"


def source_columns(bronze_columns: list[str], cfg: LayerSettings) -> list[str]:
    """Colonnes sources d'une table Bronze, retrouvées à partir de leurs colonnes `_raw`."""
    n = len(cfg.raw_suffix)
    return [c[:-n] for c in bronze_columns if c.endswith(cfg.raw_suffix) and c[:-n] in bronze_columns]


def physical_columns(columns: list[str], cfg: LayerSettings) -> list[str]:
    """Toutes les colonnes de la table Bronze, dans l'ordre où elles seront créées."""
    return [c for name in columns for c in (raw_column(name, cfg), name)] + list(cfg.technical.values())


def name_errors(columns: list[str], cfg: LayerSettings) -> list[str]:
    """Collisions de noms ou noms trop longs : on refuse plutôt que d'écraser une colonne."""
    errors = []
    physical = physical_columns(columns, cfg)
    duplicates = sorted({c for c in physical if physical.count(c) > 1})
    if duplicates:
        errors.append(f"noms de colonnes en collision dans la table Bronze : {duplicates}")
    too_long = [c for c in physical if len(c) > PG_MAX_IDENTIFIER]
    if too_long:
        errors.append(f"noms de plus de {PG_MAX_IDENTIFIER} caractères : {too_long}")
    return errors


# ------------------------------------------------------------------ empreintes
def row_hash(values: list[str]) -> str:
    """Empreinte SHA-256 d'une ligne.

    Les valeurs sont encodées en liste JSON : ["a,b", "c"] et ["a", "b,c"] donnent des
    textes différents, donc des empreintes différentes (une simple concaténation avec une
    virgule les confondrait).
    """
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_rows(df: pd.DataFrame) -> pd.Series:
    """Empreinte de chaque ligne, calculée sur les colonnes de `df` dans leur ordre."""
    return pd.Series([row_hash(list(r)) for r in df.itertuples(index=False, name=None)],
                     index=df.index, dtype=str)


# ------------------------------------------------------------------ conversion au mieux
@dataclass
class CastResult:
    typed: pd.Series          # objets Python (None si vide ou non convertible)
    errors: pd.Series         # motif de l'échec, ou None
    sql: object               # type SQL de la colonne typée
    converted: pd.Series | None = None   # valeurs converties (pour min/max)


def cast_column(raw: pd.Series, col_type: str, parse_rate_min: float = 0.95,
                declared: str | None = None) -> CastResult:
    """Convertit une colonne de texte vers `col_type`, valeur par valeur, sans rien perdre.

    Case vide -> NULL sans erreur. Valeur présente non convertible -> NULL + motif.
    Les types non convertibles (identifiant, catégorie, texte) gardent la valeur telle quelle.
    """
    blank = raw.astype(str).str.strip() == ""
    errors = pd.Series([None] * len(raw), index=raw.index, dtype=object)
    if col_type not in CONVERTIBLE:
        return CastResult(raw.astype(object).where(~blank, None), errors, Text)
    present = raw[~blank].astype(str).str.strip()
    conv = convert(present, col_type, parse_rate_min)
    bad = conv.isna()
    if col_type == "INTEGER":
        bad |= (conv % 1 != 0).fillna(False)
    for i in bad[bad].index:
        errors.at[i] = f"conversion {col_type} impossible : {raw.at[i]!r}"
    kept = conv.where(~bad)
    typed = to_python(kept, col_type).reindex(raw.index)
    typed = typed.astype(object).where(typed.notna(), None)
    return CastResult(typed, errors, sql_type(col_type, conv, declared), kept)


def _column_stats(name: str, raw: pd.Series, cast: CastResult, col_type: str,
                  cfg: LayerSettings) -> dict:
    """Ligne du dictionnaire de données : faits mesurés sur la colonne."""
    n = len(raw)
    blank = int((raw.astype(str).str.strip() == "").sum())
    failures = int(cast.errors.notna().sum())
    values = cast.converted.dropna() if cast.converted is not None else pd.Series(dtype=object)
    return {"source_name": name, "raw_column": raw_column(name, cfg), "typed_column": name,
            "type": col_type, "sql_type": str(cast.sql() if isinstance(cast.sql, type) else cast.sql),
            "blank": blank, "cast_failures": failures,
            "null_rate": round((blank + failures) / n, 4) if n else 0.0,
            "min": str(values.min()) if len(values) else None,
            "max": str(values.max()) if len(values) else None}


# ------------------------------------------------------------------ table
def build_bronze_frame(raw: pd.DataFrame, types: dict[str, str], meta: BatchMeta,
                       cfg: LayerSettings, parse_rate_min: float = 0.95,
                       declared: dict[str, str] | None = None) -> BronzeFrame:
    """Table Bronze : x_raw + x pour chaque colonne source, puis les colonnes techniques."""
    declared = declared or {}
    out: dict[str, pd.Series] = {}
    sql: dict[str, object] = {}
    stats = []
    errors_by_col: dict[str, pd.Series] = {}
    for name in raw.columns:
        cast = cast_column(raw[name], types[name], parse_rate_min, declared.get(name))
        out[raw_column(name, cfg)], sql[raw_column(name, cfg)] = raw[name], Text
        out[name], sql[name] = cast.typed, cast.sql
        errors_by_col[name] = cast.errors
        stats.append(_column_stats(name, raw[name], cast, types[name], cfg))

    frame = pd.DataFrame(out, index=raw.index)
    tech = {
        "batch_id": (meta.batch_id, Uuid), "source_file": (meta.source_file, Text),
        "source_hash": (meta.source_hash, Text),
        "row_number": (pd.Series(range(1, len(raw) + 1), index=raw.index), BigInteger),
        "ingested_at": (meta.ingested_at, DateTime(timezone=True)),
        "row_hash": (hash_rows(raw), Text),
    }
    for role, (value, typ) in tech.items():
        frame[cfg.col(role)], sql[cfg.col(role)] = value, typ
    frame[cfg.col("cast_errors")] = [
        {c: e.at[i] for c, e in errors_by_col.items() if e.at[i] is not None} for i in raw.index]
    sql[cfg.col("cast_errors")] = JSONB
    n_bad = int(frame[cfg.col("cast_errors")].map(bool).sum())
    return BronzeFrame(frame, sql, stats, n_bad)
