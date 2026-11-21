import pandas as pd
import pytest

from indusense.config import all_sources
from indusense.io.readers import load_source
from indusense.profiling.profile import profile_dataset
from indusense.profiling.types import infer_type


@pytest.mark.parametrize("name, values, expected", [
    ("incident_id", ["INC-1", "INC-2"], "IDENTITY"),             # nom en _id
    ("machine_code", ["M1", "M1", "M2"], "IDENTITY"),            # nom en _code
    ("severity", ["2", "3", "5"], "INTEGER"),
    ("duree", ["12,5", "3,0", ""], "FLOAT"),                      # virgule décimale
    ("time", ["05:42", "21:08"], "TIME"),                         # heure seule, pas une date
    ("date", ["2025-06-01", "2025-06-02"], "DATETIME"),
    ("date_fr", ["01/06/2025", "02/06/2025"], "DATETIME"),
    ("flag", ["0", "1", "1"], "BOOLEAN"),
    ("shift", ["nuit", "matin", "nuit"], "CATEGORY"),
    ("nom", ["Ana", "Bob"], "CATEGORY"),                          # unique mais trop peu de lignes
    ("vide", ["", "NULL", "n/a"], "EMPTY"),
])
def test_infer_type(settings, name, values, expected):
    col_type, _ = infer_type(name, pd.Series(values), settings.inference)
    assert col_type == expected


def test_constats_audit(settings):
    spec = next(s for s in all_sources(settings) if s.id == "releves_incidents")
    (ds,) = load_source(spec, settings).datasets
    p = profile_dataset(ds, settings)
    messages = " ".join(f.message for f in p.findings)
    assert "modalités incohérentes" in messages                  # Matin / nuit
    assert p.column("incident_id").is_key_candidate
    assert p.column("severity").stats["max"] == 4
