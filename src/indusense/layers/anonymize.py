"""Pseudonymisation des données personnelles : SHA-256 avec un sel secret.

On utilise HMAC-SHA256, la façon standard d'« ajouter un sel secret » à SHA-256 :
- même valeur + même sel  -> même empreinte (on peut encore compter ou regrouper par opérateur) ;
- sans le sel, impossible de retrouver le nom en essayant une liste de noms connus.

Au sens du RGPD, c'est une PSEUDONYMISATION (le détenteur du sel pourrait refaire le lien),
pas une anonymisation complète : les tables restent des données personnelles à protéger.
"""
from __future__ import annotations

import hashlib
import hmac

import pandas as pd

from indusense.contracts.model import PRIVACY_UNDECIDED


def pseudonymize(value: str, salt: str) -> str:
    """Empreinte HMAC-SHA256 (64 caractères hexadécimaux) d'une valeur ; vide reste vide."""
    if value == "":
        return ""
    return hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def pseudonymize_series(values: pd.Series, salt: str) -> pd.Series:
    """Applique `pseudonymize` à une colonne (calcul fait une seule fois par valeur distincte)."""
    mapping = {v: pseudonymize(v, salt) for v in values.unique()}
    return values.map(mapping)


def columns_to_pseudonymize(contract: dict, privacy_actions: tuple[str, ...]) -> list[str]:
    """Colonnes du contrat marquées `pii: true` dont l'action `privacy` est dans la liste."""
    return [c["name"] for c in contract.get("schema", {}).get("columns", [])
            if c.get("pii") and c.get("privacy") in privacy_actions]


def undecided_pii_columns(contract: dict) -> list[str]:
    """Colonnes personnelles sans décision de protection (`privacy` absent ou `to_decide`).

    `keep` est une décision (garder en clair, tracée dans le contrat) : elle n'est pas signalée.
    """
    return [c["name"] for c in contract.get("schema", {}).get("columns", [])
            if c.get("pii") and c.get("privacy", PRIVACY_UNDECIDED) == PRIVACY_UNDECIDED]
