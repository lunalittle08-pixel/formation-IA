"""Règles de constats d'audit : textes, sévérités et seuils hors du code.

Le code fournit des détecteurs génériques (des mesures) ; les règles disent quoi
signaler et comment le formuler. Trois niveaux, du plus général au plus précis :

1. catalogue par défaut livré avec le package (`default_findings.yaml`) : faits seuls ;
2. spécification métier du projet (`audit.findings_spec` dans pipeline.yaml), facultative ;
3. contexte d'une colonne dans son contrat (clé `audit:` de la colonne), justifié par `reason`.
"""
from __future__ import annotations

import logging
import string
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

log = logging.getLogger("indusense")

DEFAULT_SPEC = Path(__file__).with_name("default_findings.yaml")
SEVERITIES = {"info", "warning", "error"}
DIMENSIONS = {"complétude", "unicité", "validité", "cohérence", "exactitude",
              "représentativité", "conformité"}
RULE_KEYS = {"dimension", "severity", "enabled", "params", "message", "hypothesis"}
# Clés autorisées sous `audit: {<règle>: {...}}` d'une colonne de contrat
COLUMN_OVERRIDE_KEYS = {"enabled", "severity", "params", "hypothesis", "reason"}

# Mesures fournies par chaque détecteur : seules variables utilisables dans les gabarits
# (en plus des `params` de la règle). Toute règle du catalogue doit figurer ici.
DETECTOR_FIELDS: dict[str, set[str]] = {
    "empty_column": set(),
    "missing_high": {"pct"},
    "missing_moderate": {"pct"},
    "time_gaps": {"count", "max_count", "entity"},
    "duplicate_rows": {"count"},
    "composite_key_duplicates": {"count", "entity", "time"},
    "identity_incomplete": {"missing"},
    "constant_column": {"value"},
    "isolated_negatives": {"count", "share"},
    "redundant_columns": {"corr"},
    "inconsistent_labels": {"groups"},
    "iqr_outliers": {"count", "low", "high"},
    "repeated_extreme": {"bound", "value", "count"},
    "skewed_distribution": {"skew"},
    "class_imbalance": {"rare", "rare_count", "major", "major_count", "ratio_observed"},
    "underrepresented": {"values", "median"},
    "sparse_entities": {"entities", "step", "global_step"},
    "target_distribution": {"distribution"},
    "pii_suspected": {"reasons"},
}


class FindingsSpecError(ValueError):
    """Catalogue ou spécification de constats invalide."""


@dataclass(frozen=True)
class FindingRule:
    id: str
    dimension: str
    severity: str
    message: str
    hypothesis: str = ""
    enabled: bool = True
    params: dict = field(default_factory=dict)
    reason: str = ""            # justification d'une exception posée dans un contrat

    def render(self, template: str, values: dict) -> str:
        """Remplit un gabarit avec les mesures du détecteur et les params de la règle."""
        return template.format(**{**self.params, **values}) if template else ""


# ------------------------------------------------------------------ chargement
def _read_yaml(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def merge_rule(base: dict, override: dict) -> dict:
    """Surcharge une règle : les `params` fusionnent, les autres clés remplacent."""
    merged = {**base, **{k: v for k, v in override.items() if k != "params"}}
    merged["params"] = {**(base.get("params") or {}), **(override.get("params") or {})}
    return merged


def template_fields(template: str) -> set[str]:
    """Variables d'un gabarit : '{count} valeurs {low:.4g}' -> {'count', 'low'}."""
    return {f.split(".")[0].split("[")[0]
            for _, f, _, _ in string.Formatter().parse(template or "") if f}


def rule_errors(rule_id: str, rule: dict) -> list[str]:
    """Erreurs de structure d'une règle complète (après fusion)."""
    errs = []
    if rule_id not in DETECTOR_FIELDS:
        return [f"règle inconnue {rule_id!r} (détecteurs : {sorted(DETECTOR_FIELDS)})"]
    unknown = set(rule) - RULE_KEYS
    if unknown:
        errs.append(f"{rule_id} : clés inconnues {sorted(unknown)}")
    if rule.get("severity") not in SEVERITIES:
        errs.append(f"{rule_id} : severity invalide {rule.get('severity')!r}")
    if rule.get("dimension") not in DIMENSIONS:
        errs.append(f"{rule_id} : dimension invalide {rule.get('dimension')!r}")
    if not rule.get("message"):
        errs.append(f"{rule_id} : message manquant")
    allowed = DETECTOR_FIELDS[rule_id] | set(rule.get("params") or {})
    for key in ("message", "hypothesis"):
        bad = template_fields(rule.get(key, "")) - allowed
        if bad:
            errs.append(f"{rule_id}.{key} : variables inconnues {sorted(bad)} "
                        f"(disponibles : {sorted(allowed)})")
    return errs


def _to_rule(rule_id: str, rule: dict) -> FindingRule:
    return FindingRule(id=rule_id, dimension=rule["dimension"], severity=rule["severity"],
                       message=rule["message"], hypothesis=rule.get("hypothesis") or "",
                       enabled=bool(rule.get("enabled", True)), params=rule.get("params") or {})


def load_rules(project_spec: Path | None = None,
               default_spec: Path = DEFAULT_SPEC) -> dict[str, FindingRule]:
    """Catalogue par défaut, surchargé par la spécification métier si elle existe."""
    rules = _read_yaml(default_spec).get("rules", {})
    if project_spec is not None:
        if Path(project_spec).exists():
            for rule_id, override in (_read_yaml(project_spec).get("rules") or {}).items():
                if rule_id not in rules:
                    raise FindingsSpecError(f"{project_spec} : règle inconnue {rule_id!r}")
                rules[rule_id] = merge_rule(rules[rule_id], override or {})
        else:
            log.warning("spécification des constats introuvable : %s (catalogue par défaut)",
                        project_spec)
    errs = [e for rid, r in rules.items() for e in rule_errors(rid, r)]
    missing = set(DETECTOR_FIELDS) - set(rules)
    if missing:
        errs.append(f"règles absentes du catalogue : {sorted(missing)}")
    if errs:
        raise FindingsSpecError("; ".join(errs))
    return {rid: _to_rule(rid, r) for rid, r in rules.items()}


# ------------------------------------------------------------------ contrats
def column_override_errors(col_name: str, audit: dict, rule_ids: set[str]) -> list[str]:
    """Erreurs de la clé `audit:` d'une colonne de contrat."""
    if not isinstance(audit, dict):
        return [f"{col_name} : audit doit être un dictionnaire {{règle: {{...}}}}"]
    errs = []
    for rule_id, ov in audit.items():
        where = f"{col_name}.audit.{rule_id}"
        if rule_ids and rule_id not in rule_ids:
            errs.append(f"{where} : règle inconnue")
            continue
        if not isinstance(ov, dict):
            errs.append(f"{where} : doit être un dictionnaire")
            continue
        unknown = set(ov) - COLUMN_OVERRIDE_KEYS
        if unknown:
            errs.append(f"{where} : clés inconnues {sorted(unknown)}")
        if not str(ov.get("reason") or "").strip():
            errs.append(f"{where} : `reason` obligatoire (justification de l'exception)")
        if ov.get("severity") and ov["severity"] not in SEVERITIES:
            errs.append(f"{where} : severity invalide {ov['severity']!r}")
    return errs


def column_overrides(contract: dict | None) -> dict[str, dict[str, dict]]:
    """{colonne: {règle: surcharge}} tiré des clés `audit:` d'un contrat."""
    if not contract:
        return {}
    return {c["name"]: c["audit"] for c in contract.get("schema", {}).get("columns", [])
            if c.get("audit")}


def apply_override(rule: FindingRule, override: dict | None) -> FindingRule:
    """Règle effective pour une colonne, après l'exception éventuelle du contrat."""
    if not override:
        return rule
    return replace(rule,
                   enabled=bool(override.get("enabled", rule.enabled)),
                   severity=override.get("severity", rule.severity),
                   hypothesis=override.get("hypothesis", rule.hypothesis),
                   params={**rule.params, **(override.get("params") or {})},
                   reason=str(override.get("reason") or ""))
