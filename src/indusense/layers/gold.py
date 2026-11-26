"""Couche Gold : jeux prêts pour l'usage (le modèle), définis par des requêtes SQL.

Chaque fichier `<nom>.sql` du dossier `layers.gold.queries_dir` contient UNE requête SELECT
qui lit le Silver ; elle devient la table `gold.<nom>`. Le code ne contient aucune règle
métier : jointures, agrégations et cibles sont décidées dans ces fichiers, versionnés et relus.
Un fichier `*.sql.example` n'est pas exécuté (modèle à copier).
"""
from __future__ import annotations

import re
from pathlib import Path

NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def gold_queries(queries_dir: Path) -> dict[str, str]:
    """{nom de table : requête}, triés par nom ; seuls les fichiers *.sql sont retenus."""
    if not queries_dir.exists():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(queries_dir.glob("*.sql"))}


def query_errors(name: str, query: str) -> list[str]:
    """Contrôles avant exécution : nom de table valide, une seule requête de lecture."""
    errors = []
    if not NAME_PATTERN.match(name):
        errors.append(f"nom de table invalide {name!r} (minuscules, chiffres, _)")
    body = strip_sql(query)
    if not re.match(r"(?is)^(select|with)\b", body):
        errors.append("la requête doit commencer par SELECT ou WITH")
    if ";" in body:
        errors.append("une seule requête par fichier (pas de « ; » au milieu)")
    return errors


def strip_sql(query: str) -> str:
    """Requête sans commentaires `--` ni « ; » final, prête à suivre CREATE TABLE ... AS."""
    lines = [re.sub(r"--.*$", "", line) for line in query.splitlines()]
    return "\n".join(lines).strip().rstrip(";").strip()
