import json
import re

from indusense.cli import main
from indusense.pipeline import STAGES, run_pipeline


def test_pipeline_complet(settings, real_contracts):
    run = run_pipeline(settings, list(STAGES))
    outs = run.outputs

    # Cartes d'identité au format du TP1 : identities/AAAAMMJJ/<fichier>_HHMMSS.md
    cards = [o for o in outs if "/identities/" in o]
    assert any(re.search(r"identities/\d{8}/releves_incidents_\d{6}\.md$", o) for o in cards)
    assert any(re.search(r"identities/\d{8}/machine_\d{6}\.md$", o) for o in cards)
    card = (settings.root / next(o for o in cards if "releves" in o)).read_text(encoding="utf-8")
    for field in ("- Source:", "- Filesize :", "- ProcessBeginAt:", "- ProcessEndAt:",
                  "- Charset: ISO-8859-1", "- Rows Number : 3", "- Cols Number : 7",
                  "| Colname | Infered Type |"):
        assert field in card

    # Audit : métriques de la carte d'identité (taille, moyenne, médiane) reprises dans le rapport
    audit = next(o for o in outs if re.search(r"audits/\d{8}/releves_incidents_\d{6}\.md$", o))
    audit_md = (settings.root / audit).read_text(encoding="utf-8")
    for field in ("| Taille |", "## Statistiques des colonnes numériques et dates",
                  "| Moyenne | Médiane | Écart-type | Q1 | Q3 |"):
        assert field in audit_md

    # Validation : MACH-99 absente du référentiel machine => violation de référence
    assert run.summary["validation"]["releves_incidents"] is False
    manifest = settings.artifact_dir("runs") / run.run_id / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(data["inputs"]) == 2 and data["config"]["sha256"]
    assert data["config"]["path"] == "configs/pipeline.yaml"       # séparateurs POSIX
    assert data["findings_spec"] is None                             # pas de spéc. dans le mini-projet


def test_cli_code_retour(project, real_contracts):
    cfg = str(project / "configs" / "pipeline.yaml")
    assert main(["--config", cfg, "contract", "check"]) == 0
    assert main(["--config", cfg, "validate"]) == 1     # données non conformes => échec CI
