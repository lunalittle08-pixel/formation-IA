"""Réglages des couches : section `layers:` de configs/pipeline.yaml (aucun secret ici)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LayerSettings:
    bronze_schema: str = "bronze"
    silver_schema: str = "silver"
    gold_schema: str = "gold"
    type_suffix: str = "_type"               # bronze : x -> x_type
    hash_column: str = "hash"                # bronze : empreinte de la ligne
    pseudonymize_privacy: tuple[str, ...] = ("pseudonymize",)
    lineage_column: str = "bronze_hash"      # silver : lien vers la ligne bronze d'origine
    rejects_suffix: str = "_rejects"         # silver : table de quarantaine
    reasons_column: str = "reject_reasons"   # silver : motifs de rejet
    gold_queries_dir: Path = Path("configs/gold")
    chunk_size: int = 5000

    @classmethod
    def from_raw(cls, raw: dict, root: Path) -> "LayerSettings":
        """Lit la section `layers:` ; les clés absentes prennent la valeur par défaut."""
        g = raw.get("layers") or {}
        schemas = g.get("schemas") or {}
        bronze, silver, gold = (g.get(k) or {} for k in ("bronze", "silver", "gold"))
        qdir = Path(gold.get("queries_dir", cls.gold_queries_dir))
        return cls(
            bronze_schema=schemas.get("bronze", cls.bronze_schema),
            silver_schema=schemas.get("silver", cls.silver_schema),
            gold_schema=schemas.get("gold", cls.gold_schema),
            type_suffix=bronze.get("type_suffix", cls.type_suffix),
            hash_column=bronze.get("hash_column", cls.hash_column),
            pseudonymize_privacy=tuple(bronze.get("pseudonymize_privacy", cls.pseudonymize_privacy)),
            lineage_column=silver.get("lineage_column", cls.lineage_column),
            rejects_suffix=silver.get("rejects_suffix", cls.rejects_suffix),
            reasons_column=silver.get("reasons_column", cls.reasons_column),
            gold_queries_dir=qdir if qdir.is_absolute() else root / qdir,
            chunk_size=int(g.get("chunk_size", cls.chunk_size)),
        )
