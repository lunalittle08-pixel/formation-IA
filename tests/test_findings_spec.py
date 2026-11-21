"""Règles de constats : catalogue par défaut, spécification métier, exceptions de contrat."""
from pathlib import Path

import pandas as pd
import pytest
import yaml

from indusense.contracts.model import structure_errors
from indusense.io.readers import Dataset
from indusense.profiling.findings_spec import (DEFAULT_SPEC, DETECTOR_FIELDS, FindingsSpecError,
                                               column_overrides, load_rules, template_fields)
from indusense.profiling.profile import profile_dataset

ROOT = Path(__file__).resolve().parents[1]
# Valeur max (100) répétée 6 fois parmi plus de 20 valeurs distinctes
SERIE = [str(v) for v in range(1, 31)] + ["100"] * 6


def _write(path: Path, spec: dict) -> Path:
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return path


def _findings(settings, rule_id: str, overrides=None):
    ds = Dataset("t", pd.DataFrame({"mesure": SERIE}))
    return [f for f in profile_dataset(ds, settings, overrides).findings if f.rule == rule_id]


def test_catalogue_par_defaut_complet_et_coherent():
    """Chaque détecteur a sa règle, et chaque gabarit n'utilise que des variables connues."""
    rules = load_rules(None)
    assert set(rules) == set(DETECTOR_FIELDS)
    for r in rules.values():
        assert template_fields(r.message) <= DETECTOR_FIELDS[r.id] | set(r.params)


def test_catalogue_par_defaut_sans_hypothese():
    """Par défaut : des faits seuls, aucune piste d'explication."""
    assert all(not r.hypothesis for r in load_rules(None).values())
    raw = DEFAULT_SPEC.read_text(encoding="utf-8").lower()
    assert "capteur" not in raw and "machine" not in raw


def test_constat_factuel_sans_spec(settings):
    (f,) = _findings(settings, "repeated_extreme")
    assert f.message == "valeur max 100 répétée 6 fois" and f.hypothesis == ""


def test_spec_metier_ajoute_hypothese_et_seuil(tmp_path, settings):
    spec = _write(tmp_path / "f.yaml", {"rules": {"repeated_extreme": {
        "hypothesis": "plafond de {count} mesures ?", "params": {"min_repeats": 7}}}})
    rules = load_rules(spec)
    assert rules["repeated_extreme"].params == {"min_repeats": 7, "min_distinct": 20}
    assert rules["repeated_extreme"].hypothesis.startswith("plafond")


def test_spec_absente_catalogue_par_defaut(tmp_path, caplog):
    rules = load_rules(tmp_path / "absente.yaml")
    assert set(rules) == set(DETECTOR_FIELDS) and "introuvable" in caplog.text


@pytest.mark.parametrize("spec, attendu", [
    ({"rules": {"regle_inexistante": {"severity": "info"}}}, "règle inconnue"),
    ({"rules": {"repeated_extreme": {"message": "{cont} fois"}}}, "variables inconnues"),
    ({"rules": {"repeated_extreme": {"severity": "grave"}}}, "severity invalide"),
])
def test_spec_invalide_refusee(tmp_path, spec, attendu):
    with pytest.raises(FindingsSpecError, match=attendu):
        load_rules(_write(tmp_path / "f.yaml", spec))


def test_exception_de_contrat_tracee(settings):
    """Neutralisé par le contrat : le constat reste visible avec sa justification."""
    contract = {"schema": {"columns": [{"name": "mesure", "type": "FLOAT", "audit": {
        "repeated_extreme": {"enabled": False, "reason": "plafond documenté par le métier"}}}]}}
    (f,) = _findings(settings, "repeated_extreme", column_overrides(contract))
    assert not f.active and f.suppressed == "plafond documenté par le métier"


def test_exception_de_contrat_sans_justification_refusee():
    contract = {"kind": "DataContract", "contract": {"id": "t", "version": "0.1.0",
                                                      "status": "draft"},
                "schema": {"columns": [{"name": "mesure", "type": "FLOAT",
                                        "audit": {"repeated_extreme": {"enabled": False}}}]}}
    errs = structure_errors(contract, set(DETECTOR_FIELDS))
    assert any("reason" in e for e in errs)


def test_spec_du_projet_valide():
    """La spécification métier versionnée du projet se charge sans erreur."""
    assert load_rules(ROOT / "configs" / "findings.yaml")
