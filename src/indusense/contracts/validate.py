"""Contrôle d'un jeu de données contre son contrat.

Chaque règle non respectée produit une `Violation`. Le jeu de données est
conforme s'il n'y a aucune violation de sévérité `error` (sévérité réglable
colonne par colonne avec `on_fail: warning`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from indusense.config import Settings
from indusense.io.readers import Dataset
from indusense.profiling.types import convert, missing_mask


@dataclass
class Violation:
    rule: str
    column: str
    severity: str
    message: str
    n_failed: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    dataset_id: str
    contract_version: str
    contract_status: str
    n_rows: int
    violations: list[Violation]

    @property
    def passed(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)


def _examples(s: pd.Series) -> list[str]:
    return [str(x) for x in s.drop_duplicates().head(5)]


def _check_column(spec: dict, raw: pd.Series, settings: Settings) -> list[Violation]:
    name, ctype = spec["name"], spec["type"]
    sev = spec.get("on_fail", "error")
    out: list[Violation] = []
    miss = missing_mask(raw, settings.inference)
    present = raw[~miss].astype(str).str.strip()

    def fail(rule: str, mask: pd.Series, msg: str, severity: str = sev):
        n = int(mask.sum())
        if n:
            out.append(Violation(rule, name, severity, msg, n, _examples(present[mask])))

    # Complétude
    if spec.get("required") and miss.any():
        out.append(Violation("required", name, sev, "valeurs manquantes sur une colonne obligatoire",
                             int(miss.sum())))
    if "max_missing_pct" in spec and miss.mean() * 100 > spec["max_missing_pct"]:
        out.append(Violation("max_missing_pct", name, sev,
                             f"{miss.mean() * 100:.2f} % de manquants > {spec['max_missing_pct']} %",
                             int(miss.sum())))
    if present.empty:
        return out

    # Validité du type
    converted = convert(present, ctype, settings.inference.parse_rate_min)
    if converted is not None:
        fail("type", converted.isna(), f"valeurs non convertibles en {ctype}")

    # Unicité
    if spec.get("unique"):
        fail("unique", present.duplicated(keep=False), "valeurs en double sur une colonne unique")

    # Format
    if spec.get("pattern"):
        rx = re.compile(spec["pattern"])
        fail("pattern", ~present.map(lambda v: bool(rx.fullmatch(v))),
             f"valeurs hors format {spec['pattern']}")

    # Domaine de valeurs
    if spec.get("allowed_values") is not None:
        allowed = {str(v) for v in spec["allowed_values"]}
        fail("allowed_values", ~present.isin(allowed), f"valeurs hors liste autorisée {sorted(allowed)}")

    # Bornes (même conversion que les données pour comparer des grandeurs homogènes)
    if converted is not None and ("min" in spec or "max" in spec):
        for key, op in (("min", lambda x, b: x < b), ("max", lambda x, b: x > b)):
            if key in spec and spec[key] is not None:
                bound = convert(pd.Series([str(spec[key])]), ctype).iloc[0]
                if pd.notna(bound):
                    fail(key, op(converted, bound).fillna(False),
                         f"valeurs {'<' if key == 'min' else '>'} {spec[key]}")
    return out


def validate_dataset(ds: Dataset, contract: dict, settings: Settings) -> ValidationResult:
    schema = contract.get("schema", {})
    quality = contract.get("quality", {})
    meta = contract.get("contract", {})
    df = ds.df
    violations: list[Violation] = []

    # Dérive de schéma : colonnes absentes ou inattendues
    expected = [c["name"] for c in schema.get("columns", [])]
    missing_cols = [c for c in expected if c not in df.columns]
    extra_cols = [c for c in df.columns if c not in expected]
    if missing_cols:
        violations.append(Violation("schema", ",".join(missing_cols), "error",
                                    "colonnes attendues absentes", len(missing_cols)))
    if extra_cols and not schema.get("allow_extra_columns", False):
        violations.append(Violation("schema", ",".join(extra_cols), "error",
                                    "colonnes non prévues par le contrat", len(extra_cols)))

    # Règles de niveau jeu de données
    if len(df) < quality.get("min_rows", 0):
        violations.append(Violation("min_rows", "(jeu de données)", "error",
                                    f"{len(df)} lignes < {quality['min_rows']}"))
    n_dup = int(df.duplicated().sum())
    if "max_duplicate_rows" in quality and n_dup > quality["max_duplicate_rows"]:
        violations.append(Violation("duplicates", "(jeu de données)", "error",
                                    f"{n_dup} lignes dupliquées > {quality['max_duplicate_rows']}", n_dup))
    pk = [c for c in schema.get("primary_key", []) or [] if c in df.columns]
    if pk:
        n_pk_dup = int(df.duplicated(subset=pk).sum())
        if n_pk_dup:
            violations.append(Violation("primary_key", ",".join(pk), "error",
                                        "clé primaire non unique", n_pk_dup))

    for spec in schema.get("columns", []):
        if spec["name"] in df.columns:
            violations += _check_column(spec, df[spec["name"]], settings)

    return ValidationResult(ds.id, str(meta.get("version", "?")), meta.get("status", "?"),
                            len(df), violations)


def reference_violations(relations, contracts: dict[str, dict]) -> dict[str, list[Violation]]:
    """Intégrité référentielle des relations déclarées dans les contrats."""
    out: dict[str, list[Violation]] = {}
    for r in relations:
        if r.origin != "contrat" or not r.n_orphans:
            continue
        spec = next((c for c in contracts[r.child]["schema"]["columns"]
                     if c["name"] == r.child_column), {})
        out.setdefault(r.child, []).append(Violation(
            "references", r.child_column, spec.get("on_fail", "error"),
            f"valeurs absentes de {r.parent}.{r.parent_column}", r.n_orphans, r.orphans[:5]))
    return out
