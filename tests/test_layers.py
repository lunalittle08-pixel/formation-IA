"""Tests du chargement des couches bronze / silver / gold.

Les tests de construction (Bronze, Silver, requêtes Gold) n'ont pas besoin de base de données.
Le test de bout en bout s'exécute seulement si INDUSENSE_TEST_DB_URL est défini, par exemple :
    INDUSENSE_TEST_DB_URL=postgresql+psycopg://indusense_user:<mdp>@127.0.0.1:5434/indusense_db
Il écrit dans des schémas *_test : les vraies tables ne sont jamais touchées.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, time

import pandas as pd
import pytest

from indusense.io.readers import Dataset
from indusense.layers.anonymize import (
    columns_to_pseudonymize,
    pseudonymize,
    undecided_pii_columns,
)
from indusense.layers.bronze import (
    BatchMeta,
    build_bronze_frame,
    cast_column,
    name_errors,
    row_hash,
    source_columns,
)
from indusense.contracts.model import structure_errors
from indusense.layers.checks import bronze_issues
from indusense.layers.gold import query_errors, strip_sql
from indusense.layers.settings import LayerSettings
from indusense.layers.silver import build_silver
from indusense.layers.stage import ingested_frame

CFG = LayerSettings(pseudonymize_privacy=("pseudonymize", "drop"))
SALT = "sel-de-test"
META = BatchMeta(uuid.UUID(int=1), "datas/jeu.csv", "f" * 64,
                 datetime(2026, 1, 1, tzinfo=UTC))


# ------------------------------------------------------------------ pseudonymisation
def test_pseudonymisation_stable_salee_et_vide_conserve():
    a = pseudonymize("Martin Dupont", SALT)
    assert a == pseudonymize("Martin Dupont", SALT)          # même entrée -> même empreinte
    assert a != pseudonymize("Martin Dupont", "autre-sel")     # le sel change tout
    assert len(a) == 64 and "Martin" not in a
    assert pseudonymize("", SALT) == ""                        # un vide n'est pas une personne


def test_colonnes_a_pseudonymiser_selon_le_contrat():
    contract = {"schema": {"columns": [
        {"name": "nom", "pii": True, "privacy": "drop"},
        {"name": "badge", "pii": True, "privacy": "pseudonymize"},
        {"name": "commentaire", "pii": True, "privacy": "to_decide"},
        {"name": "mesure"}]}}
    actions = ("pseudonymize", "drop")
    assert columns_to_pseudonymize(contract, actions) == ["nom", "badge"]
    assert undecided_pii_columns(contract, actions) == ["commentaire"]


# ------------------------------------------------------------------ bronze
def test_empreinte_sans_ambiguite_de_separateur():
    assert row_hash(["a,b", "c"]) != row_hash(["a", "b,c"])


def test_vide_et_illisible_ne_sont_pas_confondus():
    raw = pd.Series(["6,5", "", "n/a", "7"], dtype=str)
    r = cast_column(raw, "FLOAT")
    assert r.typed.tolist() == [6.5, None, None, 7.0]
    assert r.errors.tolist()[1] is None                        # vide : absence, pas une erreur
    assert "n/a" in r.errors.tolist()[2]                       # illisible : motif tracé
    assert cast_column(pd.Series(["2", "2.5"]), "INTEGER").errors.notna().tolist() == [False, True]


def test_table_bronze_brut_type_et_colonnes_techniques():
    raw = pd.DataFrame({"id": ["1", "2"], "nom": ["Léa", ""], "val": ["6,5", "abc"]}, dtype=str)
    types = {"id": "IDENTITY", "nom": "CATEGORY", "val": "FLOAT"}
    bf = build_bronze_frame(raw, types, META, CFG)
    f = bf.frame

    assert list(f.columns)[:6] == ["id_raw", "id", "nom_raw", "nom", "val_raw", "val"]
    assert list(f.columns)[6:] == list(CFG.technical.values())
    assert f["val_raw"].tolist() == ["6,5", "abc"]             # valeur d'origine intacte
    assert f["val"].tolist() == [6.5, None]
    assert f["nom_raw"].tolist() == ["Léa", ""]                # le Bronze ne pseudonymise pas
    assert f["nom"].tolist() == ["Léa", None]
    assert f["_row_number"].tolist() == [1, 2]
    assert f["_source_hash"].tolist() == ["f" * 64] * 2
    assert f["_row_hash"].tolist() == [row_hash(["1", "Léa", "6,5"]), row_hash(["2", "", "abc"])]
    assert f["_cast_errors"].tolist()[0] == {} and list(f["_cast_errors"].tolist()[1]) == ["val"]
    assert bf.rows_with_cast_errors == 1
    stats = {c["source_name"]: c for c in bf.columns}
    assert (stats["val"]["blank"], stats["val"]["cast_failures"]) == (0, 1)
    assert (stats["nom"]["blank"], stats["nom"]["null_rate"]) == (1, 0.5)
    assert source_columns(list(f.columns), CFG) == ["id", "nom", "val"]


def test_collision_de_noms_refusee():
    assert name_errors(["x", "x_raw"], CFG)                    # x_raw existe déjà
    assert name_errors(["_row_number"], CFG)                   # nom réservé aux colonnes techniques
    assert not name_errors(["x", "y"], CFG)


def _contract(columns, extra=False, pk=None):
    return {"kind": "DataContract", "contract": {"id": "jeu", "version": "1.0.0", "status": "active"},
            "schema": {"primary_key": pk or [], "allow_extra_columns": extra,
                       "columns": [{"name": n, "type": "TEXT"} for n in columns]}}


def test_contrat_confronte_au_fichier_reel():
    ds = Dataset("jeu", pd.DataFrame({"duration_hours": ["1"], "autre": ["x"]}, dtype=str))
    errors, _ = bronze_issues(ds, _contract(["duration_hour", "autre"]), "datas/jeu.csv", CFG)
    assert any("'duration_hour' absente de datas/jeu.csv" in e and "'duration_hours'" in e
               for e in errors)                                # colonne nommée + suggestion
    errors, _ = bronze_issues(ds, _contract(["duration_hours"]), "datas/jeu.csv", CFG)
    assert any("absentes du contrat" in e for e in errors)     # colonne en plus non autorisée
    _, warnings = bronze_issues(ds, _contract(["duration_hours"], extra=True), "x", CFG)
    assert warnings
    assert bronze_issues(ds, None, "x", CFG)[0]                # pas de contrat : refus


def test_colonne_du_schema_sql_jamais_alimentee():
    ds = Dataset("t", pd.DataFrame({"a": ["1"], "b": [""]}, dtype=str), table="t",
                 provided_columns=["a"])
    errors, _ = bronze_issues(ds, _contract(["a", "b"]), "dump.sql:t", CFG)
    assert any("'b' existe au schéma" in e for e in errors)
    vide = Dataset("t", pd.DataFrame({"a": []}, dtype=str))
    assert any("aucune ligne" in e for e in bronze_issues(vide, _contract(["a"]), "x", CFG)[0])


def _excluding(columns, excluded):
    c = _contract(columns)
    c["schema"]["excluded_columns"] = [{"name": n, "reason": "DEFAULT de la base"} for n in excluded]
    return c


def test_colonne_sql_non_alimentee_exclue_par_le_contrat():
    ds = Dataset("t", pd.DataFrame({"a": ["1"], "b": [""]}, dtype=str), table="t",
                 provided_columns=["a"])
    contract = _excluding(["a"], ["b"])
    assert bronze_issues(ds, contract, "dump.sql:t", CFG) == ([], [])
    assert list(ingested_frame(ds.df, contract).columns) == ["a"]


def test_exclusion_refusee_si_la_source_fournit_des_valeurs():
    csv = Dataset("t", pd.DataFrame({"a": ["1"], "b": ["x"]}, dtype=str))
    sql = Dataset("t", pd.DataFrame({"a": ["1"], "b": ["x"]}, dtype=str), table="t",
                  provided_columns=["a", "b"])
    for ds in (csv, sql):
        errors, _ = bronze_issues(ds, _excluding(["a"], ["b"]), "src", CFG)
        assert any("perdrait des données" in e for e in errors)
    absente = Dataset("t", pd.DataFrame({"a": ["1"]}, dtype=str), table="t", provided_columns=["a"])
    assert any("exclue 'zz' absente" in e
               for e in bronze_issues(absente, _excluding(["a"], ["zz"]), "src", CFG)[0])


def test_structure_des_exclusions():
    c = _excluding(["a"], ["a"])
    c["schema"]["excluded_columns"].append({"name": "b"})
    errs = structure_errors(c)
    assert any("à la fois dans columns" in e for e in errs)
    assert any("b : exclusion sans reason" in e for e in errs)


# ------------------------------------------------------------------ silver
CONTRACT = {
    "contract": {"version": "1.0.0"},
    "schema": {"primary_key": ["id"], "columns": [
        {"name": "id", "type": "IDENTITY", "required": True, "pattern": r"^I\d$"},
        {"name": "nom", "type": "CATEGORY", "pii": True, "privacy": "drop"},
        {"name": "badge", "type": "CATEGORY", "pii": True, "privacy": "pseudonymize",
         "pattern": r"^OP\d{4}$"},
        {"name": "gravite", "type": "INTEGER", "required": True, "min": 1, "max": 5},
        {"name": "mesure", "type": "FLOAT"},
        {"name": "equipe", "type": "CATEGORY", "allowed_values": ["matin", "nuit"]},
        {"name": "heure", "type": "TIME"},
        {"name": "note", "type": "INTEGER", "on_fail": "warning"},
    ]}}


def _bronze(rows: list[list[str]]) -> pd.DataFrame:
    """Table Bronze relue comme le fait le Silver (tout en texte)."""
    cols = ["id", "nom", "badge", "gravite", "mesure", "equipe", "heure", "note", "hors_contrat"]
    raw = pd.DataFrame(rows, columns=cols, dtype=str)
    frame = build_bronze_frame(raw, {c: "TEXT" for c in cols}, META, CFG).frame
    return frame.astype(object).where(frame.notna(), "").astype(str)


def test_silver_typage_quarantaine_et_lignage():
    bronze = _bronze([
        ["I1", "Léa", "OP0001", "3", "6,5", "matin", "05:42", "7", "x"],    # valide
        ["I1", "Léa", "OP0001", "3", "6,5", "matin", "05:42", "7", "x"],    # doublon exact
        ["I2", "Ana", "OP0002", "9", "N/A", "nuit", "21:08", "abc", "x"],   # gravité > 5
        ["I3", "Ana", "OP0002", "2", "1.5", "Matin", "", "", "x"],          # équipe hors liste
        ["I4", "Max", "OP0003", "", "2", "nuit", "10:00", "", "x"],         # gravité manquante
        ["I5", "Max", "OP0003", "4", "2", "nuit", "10:00", "3.5", "x"],     # valide (note -> NULL)
        ["I5", "Max", "OP0003", "1", "2", "nuit", "10:00", "", "x"],        # même clé : conflit
    ])
    r = build_silver("jeu", bronze, CONTRACT, CFG, _inference(), SALT)

    assert r.report["exact_duplicates_removed"] == 1
    assert r.valid["id"].tolist() == ["I1"]                     # I5 en conflit de clé
    row = r.valid.iloc[0]
    assert row["gravite"] == 3 and row["mesure"] == 6.5 and row["heure"] == time(5, 42)
    assert "nom" not in r.valid.columns                        # privacy: drop -> retirée
    assert "hors_contrat" not in r.valid.columns               # absente du contrat -> retirée
    assert row["badge"] == pseudonymize("OP0001", SALT)        # pseudonyme, format non contrôlé
    assert (row["_source_hash"], row["_row_number"]) == ("f" * 64, 1)   # lien vers le Bronze
    assert r.report["pseudonymized_columns"] == ["nom", "badge"]
    assert "Ana" not in r.rejects["nom"].tolist()              # jamais en clair en quarantaine

    reasons = dict(zip(r.rejects["id"], r.rejects["reject_reasons"]))
    assert reasons["I2"] == "gravite:max"                      # note 'abc' : warning, pas un rejet
    assert reasons["I3"] == "equipe:allowed_values"            # « Matin » n'est pas corrigé
    assert reasons["I4"] == "gravite:required"
    assert reasons["I5"] == "id:primary_key"
    assert r.report["nulled_by_rule"] == {"note:type": 2}      # 'abc' et '3.5' -> NULL
    assert r.report["rows_silver"] + r.report["rows_rejected"] == 6


def _inference():
    from pathlib import Path

    from indusense.config import load_settings
    return load_settings(Path(__file__).resolve().parents[1] / "configs" / "pipeline.yaml").inference


# ------------------------------------------------------------------ gold
def test_requete_gold_controlee():
    assert not query_errors("incidents_par_machine", "-- commentaire\nSELECT 1;\n")
    assert query_errors("Mauvais-Nom", "SELECT 1")
    assert query_errors("x", "DROP TABLE silver.telemetry")
    assert query_errors("x", "SELECT 1; DELETE FROM silver.telemetry")
    assert strip_sql("-- c\nSELECT 1 ;\n") == "SELECT 1"


# ------------------------------------------------------------------ bout en bout
E2E_MACHINE = {
    "apiVersion": "indusense/v1", "kind": "DataContract",
    "contract": {"id": "machine", "version": "1.0.0", "status": "active"},
    "schema": {"primary_key": ["machine_code"], "allow_extra_columns": False, "columns": [
        {"name": "machine_code", "type": "IDENTITY", "required": True},
        {"name": "is_active", "type": "BOOLEAN"},
        {"name": "created_at", "type": "DATETIME", "declared_type": "TIMESTAMPTZ"}]}}


@pytest.fixture
def db(settings):
    """Base de test : schémas *_test vidés avant le test ; les vraies tables ne sont pas touchées."""
    import yaml
    from sqlalchemy import create_engine, text

    from indusense.layers.database import EnvSettings

    url = os.environ["INDUSENSE_TEST_DB_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        for schema in ("bronze_test", "silver_test", "gold_test"):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    settings.raw["layers"] = {"schemas": {"bronze": "bronze_test", "silver": "silver_test",
                                          "gold": "gold_test"}}
    contracts = settings.root / "configs" / "contracts"
    (contracts / "machine.yaml").write_text(yaml.safe_dump(E2E_MACHINE), encoding="utf-8")
    env = EnvSettings(database_url=url, anonymisation_salt=SALT, _env_file=None)
    return settings, env, engine


@pytest.mark.skipif(not os.environ.get("INDUSENSE_TEST_DB_URL"), reason="pas de base de test")
def test_bout_en_bout_postgresql(db):
    from indusense.layers.stage import run_layers

    settings, env, engine = db
    gold_dir = settings.root / "configs" / "gold"
    gold_dir.mkdir()
    (gold_dir / "machines_actives.sql").write_text(
        "SELECT machine_code FROM silver_test.machine WHERE is_active", encoding="utf-8")
    run = run_layers(settings, ["bronze", "silver", "gold"], env=env, engine=engine)

    # sans contrat correspondant, le fichier d'incidents est refusé (aucune table écrite)
    assert "bronze.releves_incidents" in run.summary["refused"]
    assert run.summary["bronze"]["machine"]["records_inserted"] == 2
    bronze = pd.read_sql_table("machine", engine, schema="bronze_test")
    assert bronze["is_active_raw"].tolist() == ["true", "false"]   # valeur d'origine
    assert bronze["is_active"].tolist() == [True, False]           # valeur typée au mieux
    assert bronze["created_at"].isna().tolist() == [False, True]   # NULL typé <=> source vide
    assert bronze["created_at_raw"].tolist()[1] == ""
    assert bronze["_cast_errors"].tolist() == [{}, {}]

    silver = pd.read_sql_table("machine", engine, schema="silver_test")
    assert silver["is_active"].tolist() == [True, False]
    assert run.summary["gold"]["machines_actives"]["rows"] == 1


@pytest.mark.skipif(not os.environ.get("INDUSENSE_TEST_DB_URL"), reason="pas de base de test")
def test_rejeu_sans_doublon_et_registre(db):
    from indusense.layers.stage import run_layers

    settings, env, engine = db
    run_layers(settings, ["bronze"], ["machine"], env=env, engine=engine)
    run = run_layers(settings, ["bronze"], ["machine"], env=env, engine=engine)
    assert run.summary["bronze"]["machine"]["records_inserted"] == 0
    assert run.summary["bronze"]["machine"]["records_already_present"] == 2
    assert len(pd.read_sql_table("machine", engine, schema="bronze_test")) == 2
    log = pd.read_sql_table("ingestion_batches_log", engine, schema="bronze_test")
    assert log["status"].tolist() == ["SUCCESS", "SUCCESS"]


@pytest.mark.skipif(not os.environ.get("INDUSENSE_TEST_DB_URL"), reason="pas de base de test")
def test_seuil_d_erreurs_de_conversion_annule_le_lot(db):
    import yaml

    from indusense.layers.stage import run_layers

    settings, env, engine = db
    contract = dict(E2E_MACHINE, schema=dict(E2E_MACHINE["schema"]))
    contract["schema"]["columns"] = [dict(c) for c in E2E_MACHINE["schema"]["columns"]]
    contract["schema"]["columns"][0]["type"] = "INTEGER"    # 'MACH-01' n'est pas un entier
    (settings.root / "configs" / "contracts" / "machine.yaml").write_text(
        yaml.safe_dump(contract), encoding="utf-8")
    run = run_layers(settings, ["bronze"], ["machine"], env=env, engine=engine)

    assert run.summary["bronze"]["machine"]["status"] == "FAILED"
    assert "bronze.machine" in run.summary["refused"]
    from sqlalchemy import inspect
    assert not inspect(engine).has_table("machine", schema="bronze_test")   # rien n'est écrit
    log = pd.read_sql_table("ingestion_batches_log", engine, schema="bronze_test")
    assert log["status"].tolist() == ["FAILED"] and "seuil" in log["message"].iloc[0]
