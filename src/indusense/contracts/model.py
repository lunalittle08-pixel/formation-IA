"""Contrat de données : format YAML, chargement et contrôle de structure.

Format inspiré de l'Open Data Contract Standard (ODCS), simplifié pour la formation.
Un contrat décrit ce qu'on ATTEND d'un jeu de données (schéma, types, domaines de
valeurs, clés, relations, confidentialité). Il est versionné avec Git et ne doit
jamais être assoupli « pour que ça passe » sans décision tracée.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from indusense.profiling.findings_spec import column_override_errors
from indusense.profiling.types import LOGICAL_TYPES

CONTRACT_KIND = "DataContract"
API_VERSION = "indusense/v1"
STATUSES = {"draft", "active", "deprecated"}
PRIVACY_ACTIONS = {"keep", "drop", "pseudonymize", "aggregate", "to_decide"}
COLUMN_KEYS = {"name", "type", "description", "unit", "required", "unique", "min", "max",
               "allowed_values", "pattern", "max_missing_pct", "references", "pii",
               "privacy", "on_fail", "declared_type", "observed", "audit"}
# Colonne présente au schéma source mais sans valeur fournie par la source (ex. DEFAULT SQL).
EXCLUDED_KEYS = {"name", "reason", "declared_type"}


def load_contract(path: Path) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _plain(obj):
    """Convertit les scalaires numpy/pandas en types Python (sérialisables en YAML)."""
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        return obj.item()
    return obj


def save_contract(contract: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(_plain(contract), f, allow_unicode=True, sort_keys=False, width=110)
    return path


def load_contracts(contracts_dir: Path) -> dict[str, dict]:
    """Tous les contrats d'un dossier, indexés par `contract.id`."""
    out = {}
    for p in sorted(contracts_dir.glob("*.yaml")):
        c = load_contract(p)
        c["_path"] = str(p)
        out[c["contract"]["id"]] = c
    return out


def structure_errors(contract: dict, rule_ids: set[str] | None = None) -> list[str]:
    """Contrôle la forme du contrat (pas les données) : à lancer en CI.

    `rule_ids` : règles de constats connues, pour contrôler les clés `audit:` des colonnes.
    """
    errs = []
    if contract.get("kind") != CONTRACT_KIND:
        errs.append(f"kind doit valoir {CONTRACT_KIND}")
    meta = contract.get("contract", {})
    for k in ("id", "version", "status"):
        if not meta.get(k):
            errs.append(f"contract.{k} manquant")
    if meta.get("status") and meta["status"] not in STATUSES:
        errs.append(f"contract.status invalide : {meta['status']}")
    cols = contract.get("schema", {}).get("columns", [])
    if not cols:
        errs.append("schema.columns vide")
    names = [c.get("name") for c in cols]
    if len(names) != len(set(names)):
        errs.append("noms de colonnes en double dans schema.columns")
    for c in cols:
        if c.get("type") not in LOGICAL_TYPES:
            errs.append(f"{c.get('name')} : type inconnu {c.get('type')}")
        unknown = set(c) - COLUMN_KEYS
        if unknown:
            errs.append(f"{c.get('name')} : clés inconnues {sorted(unknown)}")
        if c.get("privacy") and c["privacy"] not in PRIVACY_ACTIONS:
            errs.append(f"{c.get('name')} : privacy invalide {c['privacy']}")
        if "audit" in c:
            errs += column_override_errors(c.get("name"), c["audit"], rule_ids or set())
    for pk in contract.get("schema", {}).get("primary_key", []) or []:
        if pk not in names:
            errs.append(f"clé primaire {pk} absente des colonnes")
    errs += _excluded_errors(contract.get("schema", {}).get("excluded_columns"), names)
    return errs


def _excluded_errors(excluded, names: list[str]) -> list[str]:
    """Forme de `schema.excluded_columns` : liste de {name, reason}, sans recouvrement avec columns."""
    if excluded is None:
        return []
    if not isinstance(excluded, list):
        return ["schema.excluded_columns doit être une liste"]
    errs = []
    for item in excluded:
        if not isinstance(item, dict) or not item.get("name"):
            errs.append("schema.excluded_columns : chaque entrée doit avoir un name")
            continue
        if not str(item.get("reason") or "").strip():
            errs.append(f"{item['name']} : exclusion sans reason (toute exclusion se justifie)")
        if item["name"] in names:
            errs.append(f"{item['name']} : à la fois dans columns et dans excluded_columns")
        unknown = set(item) - EXCLUDED_KEYS
        if unknown:
            errs.append(f"{item['name']} : clés inconnues {sorted(unknown)}")
    return errs


def excluded_columns(contract: dict) -> dict[str, str]:
    """Colonnes du schéma source volontairement non ingérées : {nom: justification}."""
    return {c["name"]: c.get("reason", "")
            for c in (contract.get("schema", {}).get("excluded_columns") or [])
            if isinstance(c, dict) and c.get("name")}


def declared_references(contracts: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(dataset enfant, colonne enfant, dataset parent, colonne parent)."""
    refs = []
    for cid, c in contracts.items():
        for col in c.get("schema", {}).get("columns", []):
            if col.get("references"):
                parent, pcol = col["references"].split(".", 1)
                refs.append((cid, col["name"], parent, pcol))
    return refs
