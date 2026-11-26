"""Contrôles faits AVANT d'écrire quoi que ce soit au Bronze : le contrat face au fichier réel.

Chaque problème est renvoyé (liste complète, pas seulement le premier) avec la colonne et
la source concernées. Une seule erreur suffit à refuser le chargement du jeu de données.
"""
from __future__ import annotations

import difflib

from indusense.contracts.model import excluded_columns, structure_errors
from indusense.io.readers import Dataset
from indusense.layers.bronze import name_errors
from indusense.layers.settings import LayerSettings


def _suggestion(name: str, candidates: list[str]) -> str:
    close = difflib.get_close_matches(name, candidates, n=1)
    return f" Vouliez-vous dire '{close[0]}' ?" if close else ""


def _exclusion_errors(ds: Dataset, excluded: dict[str, str], source: str) -> list[str]:
    """Une exclusion n'est admise que pour une colonne du schéma qu'aucune donnée n'alimente.

    Exclure une colonne qui porte des valeurs de la source serait une perte de données.
    """
    errors = []
    present = list(ds.df.columns)
    for name in excluded:
        if name not in present:
            errors.append(f"colonne exclue '{name}' absente de {source} (contrat à mettre à jour)")
        elif ds.provided_columns is None or name in ds.provided_columns:
            errors.append(f"colonne exclue '{name}' : {source} fournit des valeurs pour elle ; "
                          "l'exclure perdrait des données")
    return errors


def bronze_issues(ds: Dataset, contract: dict | None, source: str,
                  cfg: LayerSettings) -> tuple[list[str], list[str]]:
    """(erreurs bloquantes, avertissements) pour un jeu de données et son contrat."""
    if contract is None:
        return [f"{source} : aucun contrat pour '{ds.id}' (on ne sait pas ce qui est attendu)"], []
    errors = [f"contrat {ds.id} : {e}" for e in structure_errors(contract)]
    warnings = []
    schema = contract.get("schema", {})
    declared = [c["name"] for c in schema.get("columns", [])]
    excluded = excluded_columns(contract)
    present = list(ds.df.columns)

    for name in declared:                                   # déclarée mais absente du fichier
        if name not in present:
            errors.append(f"colonne '{name}' absente de {source}.{_suggestion(name, present)}")
    errors += _exclusion_errors(ds, excluded, source)
    extra = [c for c in present if c not in declared and c not in excluded]
    if extra:                                               # présente mais non déclarée
        msg = f"colonnes de {source} absentes du contrat : {extra}"
        (warnings if schema.get("allow_extra_columns") else errors).append(msg)
    if ds.provided_columns is not None:                     # SQL : au schéma, jamais alimentée
        for name in present:
            if name not in ds.provided_columns and name not in excluded:
                errors.append(f"'{name}' existe au schéma de {source} mais aucune ligne ne la "
                              "fournit (sa valeur ne viendrait pas du fichier)")
    if ds.df.empty:
        errors.append(f"{source} : aucune ligne à ingérer")
    errors += name_errors(present, cfg)
    return errors, warnings
