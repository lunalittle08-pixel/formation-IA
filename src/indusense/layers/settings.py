"""Réglages des couches : section `layers:` de configs/pipeline.yaml (aucun secret ici)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Colonnes techniques du Bronze : rôle -> nom par défaut (modifiable dans la config).
TECHNICAL_DEFAULTS = {
    "batch_id": "_batch_id",          # lot d'ingestion (UUID)
    "source_file": "_source_file",    # fichier d'origine
    "source_hash": "_source_hash",    # SHA-256 des OCTETS du fichier
    "row_number": "_row_number",      # numéro de la ligne de données dans la source (1 = première)
    "ingested_at": "_ingested_at",    # horodatage du chargement (UTC)
    "row_hash": "_row_hash",          # SHA-256 des valeurs brutes de la ligne (doublons stricts)
    "cast_errors": "_cast_errors",    # JSONB : {colonne: motif} des conversions impossibles
}


@dataclass(frozen=True)
class LayerSettings:
    bronze_schema: str = "bronze"
    silver_schema: str = "silver"
    gold_schema: str = "gold"
    raw_suffix: str = "_raw"                      # bronze : x_raw (texte) + x (typé)
    technical: dict = field(default_factory=lambda: dict(TECHNICAL_DEFAULTS))
    registry_table: str = "ingestion_batches_log"  # bronze : une ligne par tentative de chargement
    max_cast_error_ratio: float = 0.0             # part max de lignes avec >= 1 erreur de conversion
    migrations_dir: Path = Path("migrations")     # bronze : scripts Alembic (env.py, versions/)
    version_table: str = "alembic_version"        # bronze : révision appliquée (dans le schéma Bronze)
    pseudonymize_privacy: tuple[str, ...] = ("pseudonymize",)   # silver
    rejects_suffix: str = "_rejects"              # silver : table de quarantaine
    reasons_column: str = "reject_reasons"        # silver : motifs de rejet
    gold_queries_dir: Path = Path("configs/gold")
    chunk_size: int = 5000

    def col(self, role: str) -> str:
        """Nom d'une colonne technique du Bronze à partir de son rôle."""
        return self.technical[role]

    @property
    def lineage(self) -> list[str]:
        """Colonnes recopiées au Silver pour retrouver la ligne Bronze (et donc la source)."""
        return [self.col("source_hash"), self.col("row_number")]

    @classmethod
    def from_raw(cls, raw: dict, root: Path) -> LayerSettings:
        """Lit la section `layers:` ; les clés absentes prennent la valeur par défaut."""
        g = raw.get("layers") or {}
        schemas = g.get("schemas") or {}
        bronze, silver, gold = (g.get(k) or {} for k in ("bronze", "silver", "gold"))
        qdir = Path(gold.get("queries_dir", cls.gold_queries_dir))
        migrations = bronze.get("migrations") or {}
        mdir = Path(migrations.get("dir", cls.migrations_dir))
        return cls(
            bronze_schema=schemas.get("bronze", cls.bronze_schema),
            silver_schema=schemas.get("silver", cls.silver_schema),
            gold_schema=schemas.get("gold", cls.gold_schema),
            raw_suffix=bronze.get("raw_suffix", cls.raw_suffix),
            technical={**TECHNICAL_DEFAULTS, **(bronze.get("technical_columns") or {})},
            registry_table=bronze.get("registry_table", cls.registry_table),
            max_cast_error_ratio=float(bronze.get("max_cast_error_ratio", cls.max_cast_error_ratio)),
            migrations_dir=mdir if mdir.is_absolute() else root / mdir,
            version_table=migrations.get("version_table", cls.version_table),
            pseudonymize_privacy=tuple(silver.get("pseudonymize_privacy", cls.pseudonymize_privacy)),
            rejects_suffix=silver.get("rejects_suffix", cls.rejects_suffix),
            reasons_column=silver.get("reasons_column", cls.reasons_column),
            gold_queries_dir=qdir if qdir.is_absolute() else root / qdir,
            chunk_size=int(g.get("chunk_size", cls.chunk_size)),
        )
