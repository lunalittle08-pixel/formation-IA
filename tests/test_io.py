from pathlib import Path

import pytest

from indusense.config import all_sources, source_options
from indusense.io.files import detect_charset, human_size
from indusense.io.readers import detect_separator, load_source


@pytest.mark.parametrize("content, encoding, expected", [
    ("abc;def\n", "ascii", "ASCII (compatible UTF-8)"),
    ("défaut;été\n", "utf-8", "UTF-8"),
    ("défaut;été\n", "utf-8-sig", "UTF-8 (BOM)"),
    ("défaut;été\n", "latin-1", "ISO-8859-1"),
    ("coût 5 €\n", "cp1252", "Windows-1252"),   # € = 0x80 : propre à Windows-1252
])
def test_detect_charset(tmp_path: Path, content, encoding, expected):
    p = tmp_path / "f.csv"
    p.write_text(content, encoding=encoding)
    assert detect_charset(p) == expected


def test_human_size():
    assert human_size(512) == "0.50 Ko"
    assert human_size(1024 * 1024 * 3) == "3.00 Mo"


def test_separator_point_virgule(project: Path):
    p = project / "datas" / "releves_incidents.csv"
    assert detect_separator(p, "cp1252", (",", ";", "\t", "|"), 50_000) == ";"


def test_discovery_et_lecture_sql(settings):
    sources = {s.id: s for s in all_sources(settings)}
    assert set(sources) == {"machine", "releves_incidents"}   # ids = noms sans extension
    assert sources["machine"].sql_dialect == "postgres"        # règle overrides "*.sql*"
    src = load_source(sources["machine"], settings)
    (ds,) = src.datasets                       # CREATE INDEX ignoré : une seule table
    assert ds.id == "machine"
    assert list(ds.df.columns) == ["machine_code", "is_active", "created_at"]
    assert ds.df.loc[1, "created_at"] == ""    # NULL -> vide, comme dans un CSV
    assert ds.declared_types["created_at"] == "TIMESTAMPTZ"


def test_decouverte_sans_nom_en_dur(settings):
    """Un fichier quelconque déposé dans datas/ est découvert ; les autres extensions ignorées."""
    data = settings.data_dir
    (data / "Capteurs Ligne 2.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")
    (data / "notes.md").write_text("pas une donnée", encoding="utf-8")
    ids = [s.id for s in all_sources(settings)]
    assert "Capteurs Ligne 2" in ids and "notes" not in ids


def test_identifiants_en_collision(settings):
    """Deux fichiers de même base ne doivent pas partager un identifiant."""
    (settings.data_dir / "machine.csv").write_text("x\n1\n", encoding="utf-8")
    ids = [s.id for s in all_sources(settings)]
    assert len(ids) == len(set(ids)) and "machine" in ids


def test_overrides_par_motif(tmp_path: Path):
    """La dernière règle qui correspond l'emporte ; les clés inconnues sont ignorées."""
    rules = ({"match": "*.csv", "separator": ";", "encoding": "cp1252"},
             {"match": "export_*.csv", "separator": ",", "inconnu": 1})
    assert source_options(tmp_path / "EXPORT_jan.csv", rules) == {"separator": ",", "encoding": "cp1252"}
    assert source_options(tmp_path / "data.sql", rules) == {}
