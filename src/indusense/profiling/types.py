"""Inférence des types logiques d'une colonne brute (reprise du notebook TP1).

Vocabulaire commun à tout le pipeline (cartes, audit, contrats) :
EMPTY, BOOLEAN, IDENTITY, INTEGER, FLOAT, TIME, DATETIME, CATEGORY, TEXT.
"""
from __future__ import annotations

import pandas as pd

from indusense.config import InferenceSettings

LOGICAL_TYPES = ("EMPTY", "BOOLEAN", "IDENTITY", "INTEGER", "FLOAT", "TIME",
                 "DATETIME", "CATEGORY", "TEXT")
NUMERIC_TYPES = {"INTEGER", "FLOAT"}
TEMPORAL_TYPES = {"DATETIME", "TIME"}


def missing_mask(s: pd.Series, cfg: InferenceSettings) -> pd.Series:
    """True pour les cellules vides ou contenant un jeton de valeur manquante."""
    return s.isna() | s.astype(str).str.strip().str.lower().isin(cfg.na_tokens)


def present_values(s: pd.Series, cfg: InferenceSettings) -> pd.Series:
    return s[~missing_mask(s, cfg)].astype(str).str.strip()


# ------------------------------------------------------------------ convertisseurs
def to_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(" ", "", regex=False)
                          .str.replace(",", ".", regex=False), errors="coerce")


def to_datetime(s: pd.Series, parse_rate_min: float = 0.95) -> pd.Series:
    iso = pd.to_datetime(s, errors="coerce", format="ISO8601", utc=_has_tz(s))
    if iso.notna().mean() >= parse_rate_min:
        return iso
    return pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")


def _has_tz(s: pd.Series) -> bool:
    """Dates avec fuseau (ex. TIMESTAMPTZ PostgreSQL : +01:00, Z)."""
    return bool(s.head(200).str.contains(r"(?:[+-]\d{2}:?\d{2}|Z)$", regex=True).any())


def to_time(s: pd.Series) -> pd.Series:
    """Heures seules (HH:MM ou HH:MM:SS) en durée depuis minuit, sans date ajoutée."""
    s = s.where(s.str.count(":") == 2, s + ":00")
    return pd.to_timedelta(s, errors="coerce")


def to_bool(s: pd.Series) -> pd.Series:
    mapping = {"1": True, "true": True, "oui": True, "yes": True, "t": True,
               "0": False, "false": False, "non": False, "no": False, "f": False}
    return s.str.lower().map(mapping)


def convert(values: pd.Series, col_type: str, parse_rate_min: float = 0.95) -> pd.Series | None:
    """Convertit des valeurs présentes vers le type logique (NaN si invalide)."""
    if col_type in NUMERIC_TYPES:
        return to_numeric(values)
    if col_type == "DATETIME":
        return to_datetime(values, parse_rate_min)
    if col_type == "TIME":
        return to_time(values)
    if col_type == "BOOLEAN":
        return to_bool(values)
    return None


# ------------------------------------------------------------------ inférence
def infer_type(name: str, s: pd.Series, cfg: InferenceSettings) -> tuple[str, pd.Series | None]:
    """Renvoie (type inféré, série convertie ou None). Règles testées dans l'ordre."""
    values = present_values(s, cfg)
    if values.empty:
        return "EMPTY", None
    if values.str.lower().isin(cfg.bool_values).all() and values.nunique() <= 2:
        return "BOOLEAN", to_bool(values)

    num = to_numeric(values)
    numeric = num.notna().mean() >= cfg.parse_rate_min
    if cfg.identity_name_pattern.search(name):
        return "IDENTITY", (num if numeric else None)
    if numeric:
        is_int = bool((num.dropna() % 1 == 0).all())
        return ("INTEGER" if is_int else "FLOAT"), num

    # Heure seule : testée avant les dates, sinon pandas lui colle la date du jour
    if values.str.match(cfg.time_pattern).mean() >= cfg.parse_rate_min:
        return "TIME", to_time(values)

    # Une date contient forcément un séparateur : on évite de tester du texte libre
    if values.str.contains(r"[-/:]").mean() >= cfg.parse_rate_min:
        dates = to_datetime(values, cfg.parse_rate_min)
        if dates.notna().mean() >= cfg.parse_rate_min:
            return "DATETIME", dates

    if values.is_unique and len(values) >= cfg.min_rows_for_identity:
        return "IDENTITY", None
    if values.nunique() <= cfg.category_max_unique:
        return "CATEGORY", None
    return "TEXT", None
