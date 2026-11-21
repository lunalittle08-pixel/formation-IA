"""Cartographie : relations entre jeux de données et intégrité référentielle.

Deux sources de relations :
- déclarées dans les contrats (`references: machine.machine_code`) ;
- détectées automatiquement (même nom de colonne dans deux jeux de données).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from indusense.io.readers import Dataset


@dataclass
class Relation:
    parent: str             # jeu de données « référentiel »
    parent_column: str
    child: str
    child_column: str
    origin: str             # contrat | auto
    cardinality: str        # 1-n | 1-1 | n-n
    inclusion_rate: float   # part des valeurs enfant présentes côté parent
    n_orphans: int
    orphans: list[str] = field(default_factory=list)


def _values(df: pd.DataFrame, col: str) -> set[str]:
    return set(df[col].astype(str).str.strip()) - {""}


def measure(parent: Dataset, pcol: str, child: Dataset, ccol: str, origin: str) -> Relation:
    pv, cv = _values(parent.df, pcol), _values(child.df, ccol)
    p_unique = parent.df[pcol].is_unique
    c_unique = child.df[ccol].is_unique
    card = "1-1" if p_unique and c_unique else ("1-n" if p_unique else "n-n")
    orphans = sorted(cv - pv)
    return Relation(parent.id, pcol, child.id, ccol, origin, card,
                    round(1 - len(orphans) / len(cv), 4) if cv else 1.0,
                    len(orphans), orphans[:10])


def map_relations(datasets: list[Dataset],
                  declared: list[tuple[str, str, str, str]]) -> list[Relation]:
    """`declared` : (dataset enfant, colonne enfant, dataset parent, colonne parent)."""
    by_id = {d.id: d for d in datasets}
    rels, seen = [], set()
    for child, ccol, parent, pcol in declared:
        if child in by_id and parent in by_id and ccol in by_id[child].df \
                and pcol in by_id[parent].df:
            rels.append(measure(by_id[parent], pcol, by_id[child], ccol, "contrat"))
            seen.add(frozenset({(child, ccol), (parent, pcol)}))
    for i, a in enumerate(datasets):
        for b in datasets[i + 1:]:
            for col in sorted(set(a.df.columns) & set(b.df.columns)):
                key = frozenset({(a.id, col), (b.id, col)})
                if key in seen or col in {"created_at", "updated_at"}:
                    continue
                # Le parent est le côté où la colonne est unique (référentiel) ;
                # en n-n, on garde le sens qui révèle le plus d'orphelines
                if b.df[col].is_unique and not a.df[col].is_unique:
                    rels.append(measure(b, col, a, col, "auto"))
                elif a.df[col].is_unique and not b.df[col].is_unique:
                    rels.append(measure(a, col, b, col, "auto"))
                else:
                    r1, r2 = measure(a, col, b, col, "auto"), measure(b, col, a, col, "auto")
                    rels.append(r1 if r1.n_orphans >= r2.n_orphans else r2)
    return rels


def mermaid_er(profiles: list, relations: list[Relation]) -> str:
    """Diagramme entité-relation Mermaid (PK = clé candidate)."""
    def ent(name: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in name).upper()

    lines = ["erDiagram"]
    for p in profiles:
        lines.append(f"    {ent(p.dataset_id)} {{")
        for c in p.columns:
            pk = " PK" if c.is_key_candidate else ""
            lines.append(f"        {c.type} {ent(c.name).lower()}{pk}")
        lines.append("    }")
    sym = {"1-n": "||--o{", "1-1": "||--||", "n-n": "}o--o{"}
    for r in relations:
        lines.append(f'    {ent(r.parent)} {sym[r.cardinality]} {ent(r.child)} : "{r.child_column}"')
    return "\n".join(lines)
