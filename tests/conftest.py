"""Fixtures : un mini-projet temporaire (config + petits fichiers) pour chaque test."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from indusense.config import load_settings

ROOT = Path(__file__).resolve().parents[1]

INCIDENTS_CSV = """incident_id;date;time;machine_id;severity;shift;durée
INC-000001;2025-06-01;05:42;MACH-01;4;matin;12,5
INC-000002;2025-06-01;21:08;MACH-02;3;Matin;3,0
INC-000003;2025-06-02;05:43;MACH-99;2;nuit;
"""

MACHINE_SQL = """CREATE TABLE machine (machine_code VARCHAR(16) PRIMARY KEY, is_active BOOLEAN,
  created_at TIMESTAMPTZ);
INSERT INTO machine (machine_code, is_active, created_at) VALUES
  ('MACH-01', TRUE, '2025-05-01 08:00:00+02'), ('MACH-02', FALSE, NULL);
CREATE INDEX idx ON machine(machine_code);
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Copie la config réelle (découverte auto) et dépose 2 fichiers dans datas/."""
    (tmp_path / "configs" / "contracts").mkdir(parents=True)
    cfg = yaml.safe_load((ROOT / "configs" / "pipeline.yaml").read_text(encoding="utf-8"))
    (tmp_path / "configs" / "pipeline.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    data = tmp_path / "datas"
    data.mkdir()
    (data / "releves_incidents.csv").write_text(INCIDENTS_CSV, encoding="cp1252")
    (data / "machine.sql.txt").write_text(MACHINE_SQL, encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(project: Path):
    return load_settings(project / "configs" / "pipeline.yaml")


@pytest.fixture
def real_contracts(project: Path) -> Path:
    """Copie les contrats du projet dans le mini-projet."""
    for p in (ROOT / "configs" / "contracts").glob("*.yaml"):
        shutil.copy(p, project / "configs" / "contracts" / p.name)
    return project / "configs" / "contracts"
