"""Chargement de configs/pipeline.yaml en objets typés.

Toute valeur « magique » (seuils, chemins, motifs) vit dans la config, pas dans le code.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path("configs/pipeline.yaml")
# Options de lecture qu'une règle `overrides` peut imposer à une source découverte
SOURCE_OPTIONS = ("format", "encoding", "separator", "sql_dialect")

log = logging.getLogger("indusense")


@dataclass(frozen=True)
class SourceSpec:
    id: str
    path: Path
    format: str = "auto"          # csv | sql | auto
    encoding: str = "auto"
    separator: str = "auto"
    sql_dialect: str | None = None

    @property
    def resolved_format(self) -> str:
        if self.format != "auto":
            return self.format
        suffixes = [s.lower() for s in self.path.suffixes]
        return "sql" if ".sql" in suffixes else "csv"


@dataclass(frozen=True)
class InferenceSettings:
    na_tokens: frozenset[str]
    parse_rate_min: float
    category_max_unique: int
    min_rows_for_identity: int
    identity_name_pattern: re.Pattern
    time_pattern: re.Pattern
    bool_values: frozenset[str]


@dataclass(frozen=True)
class AuditSettings:
    missing_warn_pct: float
    missing_drop_pct: float
    outlier_iqr_factor: float
    repeated_extreme_min: int
    underrepresented_ratio: float
    imbalance_ratio: float
    redundant_corr: float
    pii_name_pattern: re.Pattern
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    root: Path
    config_path: Path
    config_sha256: str
    data_dir: Path
    contracts_dir: Path
    artifacts_dir: Path
    subdirs: dict[str, str]
    discovery_patterns: tuple[str, ...]
    overrides: tuple[dict, ...]
    candidate_separators: tuple[str, ...]
    sniff_bytes: int
    inference: InferenceSettings
    audit: AuditSettings
    raw: dict = field(repr=False, default_factory=dict)

    def artifact_dir(self, kind: str) -> Path:
        """Dossier d'un type d'artefact : identities, audits, validations, drafts, runs."""
        return self.artifacts_dir / self.subdirs[kind]


def _resolve(root: Path, p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (root / p)


def load_settings(config_path: Path | str = DEFAULT_CONFIG) -> Settings:
    config_path = Path(config_path).resolve()
    text = config_path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    # La racine du projet est le parent du dossier configs/
    root = config_path.parent.parent

    paths = cfg["paths"]
    inf = cfg["inference"]
    aud = cfg["audit"]
    discovery = cfg.get("discovery", {})
    return Settings(
        root=root,
        config_path=config_path,
        config_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        data_dir=_resolve(root, paths["data_dir"]),
        contracts_dir=_resolve(root, paths["contracts_dir"]),
        artifacts_dir=_resolve(root, paths["artifacts_dir"]),
        subdirs={k: paths[k] for k in ("identities", "audits", "validations", "drafts", "runs")},
        discovery_patterns=tuple(discovery.get("patterns", [])),
        overrides=tuple(discovery.get("overrides") or ()),
        candidate_separators=tuple(cfg["reading"]["candidate_separators"]),
        sniff_bytes=int(cfg["reading"]["sniff_bytes"]),
        inference=InferenceSettings(
            na_tokens=frozenset(t.lower() for t in inf["na_tokens"]),
            parse_rate_min=float(inf["parse_rate_min"]),
            category_max_unique=int(inf["category_max_unique"]),
            min_rows_for_identity=int(inf["min_rows_for_identity"]),
            identity_name_pattern=re.compile(inf["identity_name_pattern"], re.IGNORECASE),
            time_pattern=re.compile(inf["time_pattern"]),
            bool_values=frozenset(v.lower() for v in inf["bool_values"]),
        ),
        audit=AuditSettings(
            missing_warn_pct=float(aud["missing_warn_pct"]),
            missing_drop_pct=float(aud["missing_drop_pct"]),
            outlier_iqr_factor=float(aud["outlier_iqr_factor"]),
            repeated_extreme_min=int(aud["repeated_extreme_min"]),
            underrepresented_ratio=float(aud["underrepresented_ratio"]),
            imbalance_ratio=float(aud["imbalance_ratio"]),
            redundant_corr=float(aud["redundant_corr"]),
            pii_name_pattern=re.compile(aud["pii_name_pattern"], re.IGNORECASE),
            targets=tuple(aud.get("targets") or ()),
        ),
        raw=cfg,
    )


def source_id(path: Path) -> str:
    """Identifiant d'une source : nom du fichier sans AUCUNE extension (machine.sql.txt -> machine)."""
    return path.name.split(".")[0]


def discover_files(data_dir: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Fichiers de data_dir correspondant à au moins un motif, sans doublon, triés par nom."""
    found: dict[Path, Path] = {}
    for pattern in patterns:
        for p in data_dir.glob(pattern):
            if p.is_file():
                found.setdefault(p.resolve(), p)
    return sorted(found.values(), key=lambda p: p.name.lower())


def source_options(path: Path, overrides: tuple[dict, ...]) -> dict:
    """Options de lecture des règles dont le motif `match` couvre le nom du fichier.

    Les règles s'appliquent dans l'ordre : la dernière qui correspond l'emporte.
    """
    opts: dict = {}
    for rule in overrides:
        if fnmatch(path.name.lower(), str(rule.get("match", "")).lower()):
            opts.update({k: v for k, v in rule.items() if k in SOURCE_OPTIONS})
    return opts


def unique_id(path: Path, taken: set[str]) -> str:
    """Identifiant de source unique : nom complet normalisé si deux fichiers partagent la même base."""
    sid = source_id(path)
    if sid in taken:
        alt = re.sub(r"\W", "_", path.name)
        log.warning("identifiant %r déjà pris : %s sera identifié %r", sid, path.name, alt)
        sid = alt
    return sid


def all_sources(settings: Settings) -> list[SourceSpec]:
    """Sources découvertes dans data_dir : aucun nom de fichier n'est codé en dur."""
    specs, taken = [], set()
    for p in discover_files(settings.data_dir, settings.discovery_patterns):
        sid = unique_id(p, taken)
        taken.add(sid)
        specs.append(SourceSpec(id=sid, path=p, **source_options(p, settings.overrides)))
    return specs
