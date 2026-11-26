"""Types logiques des contrats -> valeurs Python et types PostgreSQL.

Partagé par le Bronze (colonne typée calculée au mieux) et le Silver (colonne contrôlée).
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd
from sqlalchemy import BigInteger, Boolean, DateTime, Double, Text, Time

CONVERTIBLE = {"INTEGER", "FLOAT", "DATETIME", "TIME", "BOOLEAN"}


def to_python(values: pd.Series, col_type: str) -> pd.Series:
    """Valeurs converties -> objets Python que PostgreSQL comprend (None si manquant)."""
    def one(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        if col_type == "INTEGER":
            return int(v)
        if col_type == "FLOAT":
            return float(v)
        if col_type == "BOOLEAN":
            return bool(v)
        if col_type == "DATETIME":
            return v.to_pydatetime()
        if col_type == "TIME":
            return (datetime.min + v).time()   # noqa: DTZ901 (heure du jour seule)
        return v
    return values.map(one).astype(object)


def sql_type(col_type: str, converted: pd.Series | None = None, declared: str | None = None):
    """Type PostgreSQL d'un type logique.

    DATETIME -> TIMESTAMPTZ si les valeurs portent un fuseau, ou si le type SQL déclaré dans
    le contrat en prévoit un (utile quand la colonne est entièrement vide).
    """
    if col_type == "DATETIME":
        tz = converted is not None and getattr(converted.dt, "tz", None) is not None
        tz = tz or bool(declared and re.search(r"TZ|TIME ZONE", declared.upper()))
        return DateTime(timezone=tz)
    return {"INTEGER": BigInteger, "FLOAT": Double, "TIME": Time, "BOOLEAN": Boolean}.get(col_type, Text)
