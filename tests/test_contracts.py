import pandas as pd

from indusense.contracts.draft import value_shape
from indusense.contracts.model import load_contracts, structure_errors
from indusense.contracts.validate import validate_dataset
from indusense.io.readers import Dataset


def _contract(columns, **schema):
    return {"apiVersion": "indusense/v1", "kind": "DataContract",
            "contract": {"id": "t", "version": "1.0.0", "status": "active"},
            "schema": {"columns": columns, **schema}, "quality": {"max_duplicate_rows": 0}}


def test_contrats_du_projet_bien_formes(real_contracts):
    contracts = load_contracts(real_contracts)
    assert contracts, "aucun contrat trouvé"
    for cid, c in contracts.items():
        assert structure_errors(c) == [], cid


def test_structure_cle_inconnue():
    c = _contract([{"name": "a", "type": "INTEGER", "minimum": 0}])
    assert any("clés inconnues" in e for e in structure_errors(c))


def test_value_shape():
    assert value_shape("INC-000123") == r"^[A-Z]{3}-\d{6}$"


def test_validation_detecte_les_violations(settings):
    df = pd.DataFrame({"id": ["A-1", "A-1", "B2"], "sev": ["1", "9", ""],
                       "shift": ["nuit", "Matin", "nuit"]}, dtype=str)
    c = _contract([
        {"name": "id", "type": "IDENTITY", "unique": True, "pattern": r"^[A-Z]-\d$"},
        {"name": "sev", "type": "INTEGER", "required": True, "min": 1, "max": 5},
        {"name": "shift", "type": "CATEGORY", "allowed_values": ["nuit", "matin"]},
    ], primary_key=["id"])
    r = validate_dataset(Dataset("t", df), c, settings)
    rules = {(v.rule, v.column) for v in r.violations}
    assert {("unique", "id"), ("pattern", "id"), ("primary_key", "id"),
            ("required", "sev"), ("max", "sev"), ("allowed_values", "shift")} <= rules
    assert not r.passed


def test_on_fail_warning_ne_bloque_pas(settings):
    df = pd.DataFrame({"c": ["x", ""]}, dtype=str)
    c = _contract([{"name": "c", "type": "TEXT", "required": True, "on_fail": "warning"}])
    assert validate_dataset(Dataset("t", df), c, settings).passed


def test_derive_de_schema(settings):
    df = pd.DataFrame({"a": ["1"], "nouvelle": ["x"]}, dtype=str)
    c = _contract([{"name": "a", "type": "INTEGER"}, {"name": "b", "type": "TEXT"}])
    msgs = [v.message for v in validate_dataset(Dataset("t", df), c, settings).violations]
    assert "colonnes attendues absentes" in msgs and "colonnes non prévues par le contrat" in msgs
