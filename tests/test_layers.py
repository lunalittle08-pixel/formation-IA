"""Tests du chargement des couches bronze / silver / gold.

Les tests de construction (Bronze, Silver, requêtes Gold) n'ont pas besoin de base de données.
Le test de bout en bout s'exécute seulement si INDUSENSE_TEST_DB_URL est défini, par exemple :
    INDUSENSE_TEST_DB_URL=postgresql+psycopg://indusense_user:<mdp>@127.0.0.1:5434/indusense_db
Il écrit dans des schémas *_test : les vraies tables ne sont jamais touchées.
"""
from __future__ import annotations

import os
from datetime import time

import pandas as pd
import pytest

from indusense.layers.anonymize import (columns_to_pseudonymize, pseudonymize,
                                        undecided_pii_columns)
from indusense.layers.bronze import (build_bronze_frame, name_errors, row_hash,
                                     source_columns)
from indusense.layers.gold import query_errors, strip_sql
from indusense.layers.settings import LayerSettings
from indusense.layers.silver import build_silver

CFG = LayerSettings(pseudonymize_privacy=("pseudonymize", "drop"))
SALT = "sel-de-test"


# ------------------------------------------------------------------ bronze
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


def test_empreinte_sans_ambiguite_de_separateur():
    assert row_hash(["a,b", "c"]) != row_hash(["a", "b,c"])


def test_table_bronze_ordre_types_et_empreinte():
    raw = pd.DataFrame({"id": ["1", "2"], "nom": ["Léa", ""], "val": ["6,5", "7"]}, dtype=str)
    types = {"id": "IDENTITY", "nom": "CATEGORY", "val": "FLOAT"}
    bronze = build_bronze_frame(raw, types, ["nom"], SALT, CFG)

    assert list(bronze.columns) == ["id", "id_type", "nom", "nom_type", "val", "val_type", "hash"]
    assert bronze["val"].tolist() == ["6,5", "7"]              # valeur brute à l'identique
    assert bronze["val_type"].tolist() == ["FLOAT", "FLOAT"]
    assert bronze.loc[0, "nom"] == pseudonymize("Léa", SALT) and bronze.loc[1, "nom"] == ""
    # empreinte = colonnes sources telles que stockées (nom pseudonymisé), dans l'ordre
    assert bronze.loc[0, "hash"] == row_hash(["1", pseudonymize("Léa", SALT), "6,5"])
    assert raw.loc[0, "nom"] == "Léa"                          # la source n'est pas modifiée
    assert source_columns(list(bronze.columns), CFG) == ["id", "nom", "val"]


def test_collision_de_noms_refusee():
    assert name_errors(["x", "x_type"], CFG)                   # x_type existe déjà
    assert name_errors(["hash"], CFG)
    assert not name_errors(["x", "y"], CFG)


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
    cols = ["id", "nom", "badge", "gravite", "mesure", "equipe", "heure", "note", "hors_contrat"]
    raw = pd.DataFrame(rows, columns=cols, dtype=str)
    types = {c: "TEXT" for c in cols}
    return build_bronze_frame(raw, types, ["nom", "badge"], SALT, CFG)


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
    r = build_silver("jeu", bronze, CONTRACT, CFG, _inference())

    assert r.report["exact_duplicates_removed"] == 1
    assert r.valid["id"].tolist() == ["I1"]                     # I5 en conflit de clé
    row = r.valid.iloc[0]
    assert row["gravite"] == 3 and row["mesure"] == 6.5 and row["heure"] == time(5, 42)
    assert "nom" not in r.valid.columns                        # privacy: drop -> retirée
    assert "hors_contrat" not in r.valid.columns               # absente du contrat -> retirée
    assert row["badge"] == pseudonymize("OP0001", SALT)        # pseudonyme, format non contrôlé
    assert row["bronze_hash"] == bronze.loc[0, "hash"]         # lien vers la ligne Bronze

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
@pytest.mark.skipif(not os.environ.get("INDUSENSE_TEST_DB_URL"), reason="pas de base de test")
def test_bout_en_bout_postgresql(settings, real_contracts):
    from sqlalchemy import create_engine

    from indusense.layers.bronze import hash_rows
    from indusense.layers.database import EnvSettings
    from indusense.layers.stage import run_layers

    url = os.environ["INDUSENSE_TEST_DB_URL"]
    gold_dir = settings.root / "configs" / "gold"
    gold_dir.mkdir()
    (gold_dir / "machines_actives.sql").write_text(
        "SELECT machine_code FROM silver_test.machine WHERE is_active", encoding="utf-8")
    settings.raw["layers"] = {"schemas": {"bronze": "bronze_test", "silver": "silver_test",
                                          "gold": "gold_test"}}
    env = EnvSettings(database_url=url, anonymisation_salt=SALT, _env_file=None)
    engine = create_engine(url)
    run = run_layers(settings, ["bronze", "silver", "gold"], env=env, engine=engine)

    assert run.summary["bronze"]["machine"]["rows"] == 2
    bronze = pd.read_sql_table("machine", engine, schema="bronze_test")
    cols = source_columns(list(bronze.columns), CFG)
    assert (hash_rows(bronze[cols]) == bronze["hash"]).all()   # empreintes vérifiables en relisant
    assert bronze["is_active"].tolist() == ["true", "false"]   # texte brut, pas converti

    silver = pd.read_sql_table("machine", engine, schema="silver_test")
    assert silver["is_active"].tolist() == [True, False]       # vrai booléen au Silver
    assert run.summary["gold"]["machines_actives"]["rows"] == 1
