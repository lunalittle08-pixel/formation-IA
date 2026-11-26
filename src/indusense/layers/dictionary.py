"""Dictionnaire de données et rapport d'ingestion du Bronze.

Le dictionnaire dit ce que contient chaque colonne (faits mesurés) ET ce qui a été fait à la
donnée (section `transformations`, obligatoire), ainsi que les opérations que le contrat
confie au Silver. Aucune interprétation : des faits, et des décisions déjà écrites ailleurs.
"""
from __future__ import annotations

from indusense.contracts.model import traceability_columns
from indusense.layers.bronze import BronzeFrame
from indusense.layers.settings import LayerSettings
from indusense.layers.sqltypes import CONVERTIBLE


def transformations(bf: BronzeFrame, cfg: LayerSettings) -> list[dict]:
    """Ce que le Bronze a fait à la donnée, colonne par colonne, en clair."""
    out = []
    for c in bf.columns:
        if c["type"] in CONVERTIBLE:
            detail = (f"conversion au mieux vers {c['type']} ({c['sql_type']}) ; valeur d'origine "
                      f"conservée dans {c['raw_column']} ; case vide -> NULL sans erreur ; "
                      f"échec -> NULL et motif dans {cfg.col('cast_errors')}")
            out.append({"code": "CAST", "target": c["typed_column"], "source": [c["raw_column"]],
                        "detail": detail})
        else:
            out.append({"code": "COPY", "target": c["typed_column"], "source": [c["raw_column"]],
                        "detail": "copie de la valeur d'origine (case vide -> NULL)"})
    out.append({"code": "HASH", "target": cfg.col("row_hash"), "source": [c["raw_column"] for c in bf.columns],
                "detail": "SHA-256 de la liste JSON des valeurs d'origine de la ligne"})
    out.append({"code": "TECH", "target": [cfg.col(r) for r in ("batch_id", "source_file", "source_hash",
                                                                 "row_number", "ingested_at")],
                "source": [], "detail": "colonnes de traçabilité ajoutées ; aucune valeur source modifiée"})
    return out


def silver_operations(contract: dict, cfg: LayerSettings) -> list[dict]:
    """Opérations que le contrat confie au Silver (déclarées ici, exécutées là-bas)."""
    ops = [{"code": "DEL", "target": "lignes", "source": [cfg.col("row_hash")],
            "detail": "doublons stricts (même empreinte de ligne) : une seule ligne gardée"}]
    ops += [{"code": "DROP", "target": name, "source": [name],
             "detail": "colonne de traçabilité (contrat : traceability) : gardée au Bronze, "
                       "non reprise au Silver"}
            for name in traceability_columns(contract)]
    for col in contract.get("schema", {}).get("columns", []):
        if not col.get("pii"):
            continue
        privacy = col.get("privacy")
        if privacy == "pseudonymize":
            ops.append({"code": "ANO", "target": col["name"], "source": [col["name"]],
                        "detail": "pseudonymisation HMAC-SHA256 avec sel secret"})
        elif privacy == "drop":
            ops.append({"code": "DROP", "target": col["name"], "source": [col["name"]],
                        "detail": "colonne personnelle retirée au Silver (pseudonymisée avant, "
                                  "y compris en quarantaine)"})
        else:
            ops.append({"code": "TODO", "target": col["name"], "source": [col["name"]],
                        "detail": f"donnée personnelle, privacy={privacy} : décision à prendre"})
    return ops


def render_markdown(d: dict) -> str:
    """Version lisible du dictionnaire et du rapport de lot."""
    b = d["batch"]
    lines = [f"# Bronze — {d['dataset_id']}", "",
             f"- Statut du lot : **{b['status']}**" + (f" — {b['message']}" if b.get("message") else ""),
             f"- Lot : `{b['batch_id']}`",
             f"- Source : `{b['source_file']}` (SHA-256 `{b['source_hash']}`)",
             f"- Contrat : version {b['contract_version']} (SHA-256 `{b['contract_hash']}`)",
             (f"- Lignes lues : {b['records_read']} ; insérées : {b['records_inserted']} ; "
              f"déjà présentes : {b['records_already_present']}"),
             (f"- Lignes avec au moins une erreur de conversion : {b['rows_with_cast_errors']} "
              f"(seuil : {d['max_cast_error_ratio']:.2%})"), "",
             "## Colonnes", "",
             "| Source | Brute | Typée | Type | Type SQL | Vides | Échecs conv. | Taux NULL | Min | Max |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in d["columns"]:
        lines.append(f"| {c['source_name']} | {c['raw_column']} | {c['typed_column']} | {c['type']} | "
                     f"{c['sql_type']} | {c['blank']} | {c['cast_failures']} | {c['null_rate']:.2%} | "
                     f"{c['min'] if c['min'] is not None else ''} | {c['max'] if c['max'] is not None else ''} |")
    for title, key in (("Transformations faites au Bronze", "transformations"),
                       ("Opérations déclarées pour le Silver", "silver_operations")):
        lines += ["", f"## {title}", "", "| Code | Cible | Source | Détail |", "|---|---|---|---|"]
        for t in d[key]:
            target = ", ".join(t["target"]) if isinstance(t["target"], list) else t["target"]
            lines.append(f"| {t['code']} | {target} | {', '.join(t['source'])} | {t['detail']} |")
    return "\n".join(lines) + "\n"
