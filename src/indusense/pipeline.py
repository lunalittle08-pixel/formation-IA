"""Orchestration des étapes : identity → audit → contract draft → validate.

Chaque étape lit les mêmes objets (`SourceFile`, `DatasetProfile`) et écrit ses
artefacts horodatés. Un manifeste d'exécution trace les entrées (empreintes),
la configuration et les versions : le run est rejouable et vérifiable.
"""
from __future__ import annotations

import json
import logging
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from indusense import __version__
from indusense.config import Settings, SourceSpec, all_sources, source_id
from indusense.contracts.draft import draft_contract
from indusense.contracts.model import (declared_references, load_contracts, save_contract,
                                       structure_errors)
from indusense.contracts.validate import (ValidationResult, reference_violations,
                                          validate_dataset)
from indusense.io.readers import SourceFile, load_source
from indusense.profiling.profile import DatasetProfile, profile_dataset
from indusense.profiling.relations import Relation, map_relations
from indusense.reporting import markdown as md

log = logging.getLogger("indusense")
STAGES = ("identity", "audit", "draft", "validate")


@dataclass
class LoadedSource:
    src: SourceFile
    profiles: list[DatasetProfile]


@dataclass
class Run:
    settings: Settings
    started_at: datetime = field(default_factory=datetime.now)
    outputs: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        return f"{self.started_at:%Y%m%d_%H%M%S}"

    def out_path(self, kind: str, base: str, when: datetime | None = None, ext: str = "md") -> Path:
        """<artifacts>/<kind>/AAAAMMJJ/<base>_HHMMSS.<ext>"""
        when = when or self.started_at
        p = self.settings.artifact_dir(kind) / f"{when:%Y%m%d}" / f"{base}_{when:%H%M%S}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        self.outputs.append(str(path.relative_to(self.settings.root)).replace("\\", "/"))
        log.info("écrit %s", self.outputs[-1])
        return path


def file_base(path: Path) -> str:
    """Nom sans AUCUNE extension : machine.sql.txt -> machine."""
    return source_id(path)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ------------------------------------------------------------------ chargement
def load_and_profile(specs: list[SourceSpec], settings: Settings) -> list[LoadedSource]:
    loaded = []
    for spec in specs:
        if not spec.path.exists():
            log.warning("source introuvable : %s", spec.path)
            continue
        log.info("lecture de %s", spec.path.name)
        src = load_source(spec, settings)
        profiles = [profile_dataset(ds, settings) for ds in src.datasets]
        src.end = datetime.now()   # ProcessEndAt : après lecture, inférence et statistiques
        loaded.append(LoadedSource(src, profiles))
    return loaded


# ------------------------------------------------------------------ étapes
def stage_identity(run: Run, loaded: list[LoadedSource]) -> None:
    for ls in loaded:
        path = run.out_path("identities", file_base(ls.src.spec.path), when=ls.src.begin)
        run.write(path, md.render_identity(ls.src, ls.profiles))


def stage_audit(run: Run, loaded: list[LoadedSource], relations: list[Relation]) -> None:
    for ls in loaded:
        for p in ls.profiles:
            run.write(run.out_path("audits", p.dataset_id), md.render_audit(ls.src, p))
            run.write(run.out_path("audits", p.dataset_id, ext="json"), _json(p.to_dict()))
    profiles = [p for ls in loaded for p in ls.profiles]
    run.write(run.out_path("audits", "cartographie"), md.render_cartography(profiles, relations))
    run.write(run.out_path("audits", "cartographie", ext="json"),
              _json([asdict(r) for r in relations]))


def stage_draft(run: Run, loaded: list[LoadedSource]) -> None:
    """Brouillons dans artifacts/ : jamais d'écrasement des contrats de configs/."""
    drafts_dir = run.settings.artifact_dir("drafts") / run.run_id
    for ls in loaded:
        for ds, p in zip(ls.src.datasets, ls.profiles):
            path = save_contract(draft_contract(ls.src, ds, p), drafts_dir / f"{ds.id}.yaml")
            run.outputs.append(str(path.relative_to(run.settings.root)).replace("\\", "/"))
            log.info("brouillon de contrat : %s", run.outputs[-1])


def stage_validate(run: Run, loaded: list[LoadedSource], contracts: dict[str, dict],
                   relations: list[Relation]) -> list[ValidationResult]:
    results, missing = [], []
    ref_viol = reference_violations(relations, contracts)
    for ls in loaded:
        for ds in ls.src.datasets:
            contract = contracts.get(ds.id)
            if contract is None:
                missing.append(ds.id)
                continue
            r = validate_dataset(ds, contract, run.settings)
            r.violations += ref_viol.get(ds.id, [])
            results.append(r)
    run.write(run.out_path("validations", "validation"), md.render_validation(results, missing))
    run.write(run.out_path("validations", "validation", ext="json"),
              _json([{**asdict(r), "passed": r.passed} for r in results]))
    run.summary["validation"] = {r.dataset_id: r.passed for r in results}
    run.summary["without_contract"] = missing
    return results


def write_manifest(run: Run, loaded: list[LoadedSource], stages: list[str]) -> Path:
    manifest = {
        "run_id": run.run_id,
        "started_at": run.started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "stages": stages,
        "config": {"path": str(run.settings.config_path.relative_to(run.settings.root)),
                   "sha256": run.settings.config_sha256},
        "versions": {"indusense": __version__, "python": platform.python_version(),
                     "pandas": pd.__version__},
        "inputs": [{"path": str(ls.src.spec.path.relative_to(run.settings.root)).replace("\\", "/"),
                    "sha256": ls.src.sha256, "size_bytes": ls.src.size_bytes,
                    "charset": ls.src.charset,
                    "datasets": {p.dataset_id: [p.n_rows, p.n_cols] for p in ls.profiles}}
                   for ls in loaded],
        "outputs": run.outputs,
        "summary": run.summary,
    }
    path = run.settings.artifact_dir("runs") / run.run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(manifest), encoding="utf-8")
    return path


# ------------------------------------------------------------------ point d'entrée
def run_pipeline(settings: Settings, stages: list[str], source_ids: list[str] | None = None) -> Run:
    run = Run(settings)
    specs = [s for s in all_sources(settings) if not source_ids or s.id in source_ids]
    if not specs:
        log.warning("aucune source trouvée dans %s (motifs : %s)",
                    settings.data_dir, ", ".join(settings.discovery_patterns))
    loaded = load_and_profile(specs, settings)
    datasets = [ds for ls in loaded for ds in ls.src.datasets]

    contracts = load_contracts(settings.contracts_dir) if settings.contracts_dir.exists() else {}
    for cid, c in contracts.items():
        for err in structure_errors(c):
            log.error("contrat %s : %s", cid, err)
    relations = map_relations(datasets, declared_references(contracts))

    if "identity" in stages:
        stage_identity(run, loaded)
    if "audit" in stages:
        stage_audit(run, loaded, relations)
    if "draft" in stages:
        stage_draft(run, loaded)
    if "validate" in stages:
        stage_validate(run, loaded, contracts, relations)
    log.info("manifeste : %s", write_manifest(run, loaded, stages))
    return run
