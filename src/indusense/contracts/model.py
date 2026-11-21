"""Contrat de données : format YAML, chargement et contrôle de structure.

Format inspiré de l'Open Data Contract Standard (ODCS), simplifié pour la formation.
Un contrat décrit ce qu'on ATTEND d'un jeu de données (schéma, types, domaines de
valeurs, clés, relations, confidentialité). Il est versionné avec Git et ne doit
jamais être assoupli « pour que ça passe » sans décision tracée.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from indusense.profiling.types import LOGICAL_TYPES

CONTRACT_KIND = "DataContract"
API_VERSION = "indusense/v1"
STATUSES = {"draft", "active", "deprecated"}
PRIVACY_ACTIONS = {"keep", "drop", "pseudonymize", "aggregate", "to_decide"}
COLUMN_KEYS = {"name", "type", "description", "unit", "required", "unique", "min", "max",
               "allowed_values", "pattern", "max_missing_pct", "references", "pii",
               "privacy", "on_fail", "declared_type", "observed"}


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


def structure_errors(contract: dict) -> list[str]:
    """Contrôle la forme du contrat (pas les données) : à lancer en CI."""
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
    for pk in contract.get("schema", {}).get("primary_key", []) or []:
        if pk not in names:
            errs.append(f"clé primaire {pk} absente des colonnes")
    return errs


def declared_references(contracts: dict[str, dict]) -> list[tuple[str, str, str, str]]:
    """(dataset enfant, colonne enfant, dataset parent, colonne parent)."""
    refs = []
    for cid, c in contracts.items():
        for col in c.get("schema", {}).get("columns", []):
            if col.get("references"):
                parent, pcol = col["references"].split(".", 1)
                refs.append((cid, col["name"], parent, pcol))
    return refs
