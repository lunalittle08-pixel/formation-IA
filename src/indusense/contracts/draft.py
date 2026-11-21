"""Génère un BROUILLON de contrat à partir du profil observé.

Le brouillon décrit ce qu'on a vu, pas ce qui est attendu : il doit être relu,
complété (descriptions, unités, bornes métier, décisions RGPD) puis promu
manuellement dans configs/contracts/ avec `status: active`.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

from indusense.contracts.model import API_VERSION, CONTRACT_KIND
from indusense.io.readers import Dataset, SourceFile
from indusense.profiling.profile import ColumnProfile, DatasetProfile
from indusense.profiling.types import NUMERIC_TYPES


def value_shape(v: str) -> str:
    """Forme d'une valeur : 'INC-000123' -> '^[A-Z]{3}-\\d{6}$'."""
    out = []
    for m in re.finditer(r"[A-Z]+|[a-z]+|\d+|.", v):
        tok = m.group()
        if tok.isdigit():
            out.append(rf"\d{{{len(tok)}}}")
        elif tok.isupper():
            out.append(f"[A-Z]{{{len(tok)}}}")
        elif tok.islower():
            out.append(f"[a-z]{{{len(tok)}}}")
        elif tok in "-_ :/":
            out.append(tok)            # séparateurs lisibles tels quels
        else:
            out.append(re.escape(tok))
    return "^" + "".join(out) + "$"


def common_pattern(values: pd.Series) -> str | None:
    shapes = values.astype(str).map(value_shape).unique()
    return shapes[0] if len(shapes) == 1 else None


def _fmt(v):
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if isinstance(v, pd.Timedelta):
        t = int(v.total_seconds())
        return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
    if isinstance(v, float):
        return round(v, 4)
    return v


def draft_column(p: ColumnProfile, raw: pd.Series) -> dict:
    col: dict = {"name": p.name, "type": p.type, "description": "TODO: sens métier"}
    if p.type in NUMERIC_TYPES:
        col["unit"] = "TODO"
    col["required"] = p.n_missing == 0
    if not col["required"]:
        col["max_missing_pct"] = float(round(max(p.pct_missing * 1.5, 1.0), 1))
    if p.type == "IDENTITY":
        col["unique"] = p.is_key_candidate
        present = raw[raw.astype(str).str.strip() != ""]
        pat = common_pattern(present) if len(present) else None
        if pat:
            col["pattern"] = pat
    if p.type in NUMERIC_TYPES | {"DATETIME", "TIME"} and p.stats:
        # Bornes observées : à remplacer par les bornes MÉTIER (plage plausible fixée par le métier)
        col["min"], col["max"] = _fmt(p.stats["min"]), _fmt(p.stats["max"])
    if p.type in {"CATEGORY", "BOOLEAN"} and p.top_values:
        col["allowed_values"] = sorted(p.top_values)
    if p.pii_suspected:
        col["pii"] = True
        col["privacy"] = "to_decide"
    if p.declared_type:
        col["declared_type"] = p.declared_type
    col["observed"] = {"pct_missing": p.pct_missing, "n_unique": p.n_unique,
                       "examples": [str(e) for e in p.examples]}
    return col


def draft_contract(src: SourceFile, ds: Dataset, profile: DatasetProfile) -> dict:
    pk = [c.name for c in profile.columns if c.is_key_candidate and c.type == "IDENTITY"]
    try:
        rel_path = str(src.spec.path.relative_to(src.spec.path.parents[1]))
    except (ValueError, IndexError):
        rel_path = str(src.spec.path)
    return {
        "apiVersion": API_VERSION,
        "kind": CONTRACT_KIND,
        "contract": {
            "id": ds.id,
            "version": "0.1.0",
            "status": "draft",
            "owner": "TODO",
            "description": "TODO: à quoi sert ce jeu de données, qui le produit",
            "generated_on": date.today().isoformat(),
        },
        "source": {
            "path": rel_path.replace("\\", "/"),
            "format": src.spec.resolved_format,
            "table": ds.table,
            "encoding": src.encoding,
            "separator": src.separator,
            "sha256_at_draft": src.sha256,
        },
        "schema": {
            "primary_key": pk[:1] or profile.composite_key,
            "allow_extra_columns": False,
            "columns": [draft_column(p, ds.df[p.name]) for p in profile.columns],
        },
        "quality": {
            "min_rows": 1,
            "max_duplicate_rows": 0,
        },
    }
