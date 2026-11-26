"""Couche Bronze : construction de la table en mémoire (pandas), sans base de données.

Pour chaque colonne source `x` : `x` (valeur brute, en texte, identique à la source,
sauf pseudonymisation) puis `x<suffixe>` (type inféré de `x`). En dernier : la colonne
d'empreinte de la ligne. Tout est séparé de la base pour pouvoir être testé seul.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from indusense.layers.anonymize import pseudonymize_series
from indusense.layers.settings import LayerSettings

PG_MAX_IDENTIFIER = 63      # longueur maximale d'un nom de colonne PostgreSQL


def type_column(name: str, cfg: LayerSettings) -> str:
    """Nom de la colonne de type associée à une colonne source."""
    return f"{name}{cfg.type_suffix}"


def source_columns(bronze_columns: list[str], cfg: LayerSettings) -> list[str]:
    """Colonnes sources d'une table Bronze (sans les colonnes de type ni l'empreinte)."""
    types = {type_column(c, cfg) for c in bronze_columns}
    return [c for c in bronze_columns if c not in types and c != cfg.hash_column]


def name_errors(columns: list[str], cfg: LayerSettings) -> list[str]:
    """Collisions de noms ou noms trop longs : on refuse plutôt que d'écraser une colonne."""
    errors = []
    bronze = [c for name in columns for c in (name, type_column(name, cfg))] + [cfg.hash_column]
    duplicates = sorted({c for c in bronze if bronze.count(c) > 1})
    if duplicates:
        errors.append(f"noms de colonnes en collision : {duplicates}")
    too_long = [c for c in bronze if len(c) > PG_MAX_IDENTIFIER]
    if too_long:
        errors.append(f"noms de plus de {PG_MAX_IDENTIFIER} caractères : {too_long}")
    return errors


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


def build_bronze_frame(raw: pd.DataFrame, types: dict[str, str], pii_columns: list[str],
                       salt: str, cfg: LayerSettings) -> pd.DataFrame:
    """Table Bronze : colonnes sources (pseudonymisées si besoin) + types + empreinte.

    L'empreinte est calculée APRÈS pseudonymisation : calculée sur le nom en clair et
    sans sel, elle permettrait de vérifier si « Martin Dupont » figure dans une ligne
    en essayant des noms, ce qui annulerait la protection.
    """
    source = raw.copy()
    for name in pii_columns:
        source[name] = pseudonymize_series(source[name], salt)
    bronze = pd.DataFrame(index=source.index)
    for name in source.columns:
        bronze[name] = source[name]
        bronze[type_column(name, cfg)] = types[name]
    bronze[cfg.hash_column] = hash_rows(source)
    return bronze
