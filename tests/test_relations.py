"""Cartographie : colonnes exclues de la détection automatique de relations."""
import pandas as pd

from indusense.io.readers import Dataset
from indusense.profiling.relations import map_relations


def test_colonnes_ignorees_par_la_config():
    a = Dataset("a", pd.DataFrame({"k": ["1", "2"], "maj": ["x", "y"]}))
    b = Dataset("b", pd.DataFrame({"k": ["1", "1"], "maj": ["x", "x"]}))
    cols = {r.child_column for r in map_relations([a, b], [], frozenset({"maj"}))}
    assert cols == {"k"}
    assert {r.child_column for r in map_relations([a, b], [])} == {"k", "maj"}
