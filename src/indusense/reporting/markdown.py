"""Rendu Markdown des livrables : carte d'identité, audit, validation."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from indusense.contracts.validate import ValidationResult
from indusense.io.files import get_source
from indusense.io.readers import SourceFile
from indusense.profiling.profile import DatasetProfile
from indusense.profiling.relations import Relation, mermaid_er

SEVERITY_ICON = {"error": "🔴", "warning": "🟠", "info": "🔵"}


# ------------------------------------------------------------------ formatage
def fmt_value(v, col_type: str) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M:%S") + (" UTC" if v.tzinfo else "")
    if isinstance(v, pd.Timedelta):
        t = int(v.total_seconds())
        return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
    if col_type == "INTEGER":
        return f"{int(v)}"
    if isinstance(v, (int, float)):
        return f"{v:.2f}"
    return str(v)


def md_table(header: list[str], rows: list[list]) -> list[str]:
    esc = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
    return (["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            + ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])


# ------------------------------------------------------------------ carte d'identité (TP1)
def _identity_table(p: DatasetProfile) -> list[str]:
    lines = [f"- Rows Number : {p.n_rows}", f"- Cols Number : {p.n_cols}", "",
             "### Column list", ""]
    has_declared = any(c.declared_type for c in p.columns)
    header = ["Colname", "Infered Type"] + (["Declared Type"] if has_declared else [])
    rows = [[c.name, c.type] + ([c.declared_type or ""] if has_declared else []) for c in p.columns]
    lines += md_table(header, rows)
    lines += ["", "### Numeric and date columns", ""]
    stats = [c for c in p.columns if c.stats]
    if stats:
        lines += md_table(
            ["Col name", "Min value", "Max value", "Mean value", "Median value"],
            [[c.name, fmt_value(c.stats["min"], c.type), fmt_value(c.stats["max"], c.type),
              fmt_value(c.stats.get("mean"), "FLOAT"), fmt_value(c.stats.get("median"), "FLOAT")]
             for c in stats])
    else:
        lines.append("_Aucune colonne numérique ou date._")
    return lines


def render_identity(src: SourceFile, profiles: list[DatasetProfile]) -> str:
    lines = [f"# Carte d'identité — {src.spec.path.name}", "",
             f"- Source: {get_source(src.spec.path)}",
             f"- Filesize : {src.size_label}",
             f"- ProcessBeginAt: {src.begin:%H:%M:%S}",
             f"- ProcessEndAt: {src.end:%H:%M:%S}",
             f"- Charset: {src.charset}"]
    if src.spec.resolved_format == "sql":
        lines.append(f"- Tables Number : {len(profiles)}")
        for p in profiles:
            lines += ["", f"## Table `{p.table}`", ""] + _identity_table(p)
    else:
        lines += _identity_table(profiles[0])
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ audit
def render_audit(src: SourceFile, p: DatasetProfile) -> str:
    title = p.dataset_id + (f" (table de {src.spec.path.name})" if p.table else "")
    counts = {s: sum(f.severity == s for f in p.findings) for s in ("error", "warning", "info")}
    lines = [f"# Audit qualité — {title}", "",
             f"_Généré le {datetime.now():%d/%m/%Y %H:%M}. Les constats sont produits par des "
             "règles automatiques (seuils dans `configs/pipeline.yaml`) : chacun doit être "
             "interprété et décidé avec le métier._", "",
             "## Inventaire", ""]
    lines += md_table(["Source", "SHA-256", "Encodage", "Séparateur", "Lignes", "Colonnes",
                       "Doublons exacts"],
                      [[src.spec.path.name, src.sha256[:12], src.charset,
                        repr(src.separator) if src.separator else "—",
                        p.n_rows, p.n_cols, p.n_duplicate_rows]])
    lines += ["", f"**Constats** : {SEVERITY_ICON['error']} {counts['error']} bloquant(s) · "
                  f"{SEVERITY_ICON['warning']} {counts['warning']} à traiter · "
                  f"{SEVERITY_ICON['info']} {counts['info']} pour information", "",
              "## Profil des colonnes", ""]
    lines += md_table(
        ["Colonne", "Type inféré", "% manq.", "Distinctes", "Clé ?", "Min", "Max",
         "Outliers IQR", "Donnée perso."],
        [[c.name, c.type, c.pct_missing, c.n_unique, "oui" if c.is_key_candidate else "",
          fmt_value(c.stats.get("min"), c.type) if c.stats else "",
          fmt_value(c.stats.get("max"), c.type) if c.stats else "",
          c.stats.get("n_outliers_iqr", ""), c.pii_suspected] for c in p.columns])
    lines += ["", "## Constats par dimension de qualité", ""]
    if p.findings:
        order = {"error": 0, "warning": 1, "info": 2}
        lines += md_table(["", "Dimension", "Colonne", "Constat"],
                          [[SEVERITY_ICON[f.severity], f.dimension, f"`{f.column}`", f.message]
                           for f in sorted(p.findings, key=lambda f: (order[f.severity], f.dimension))])
    else:
        lines.append("_Aucun constat._")
    lines += ["", "## Décisions (à compléter dans le notebook / le contrat)", "",
              "Pour chaque constat : **observation → hypothèse → vérification → décision → justification**.",
              "Les décisions se reportent dans le contrat (`configs/contracts/`) : bornes métier, "
              "`allowed_values`, `privacy`, `on_fail`.", ""]
    return "\n".join(lines)


def render_cartography(profiles: list[DatasetProfile], relations: list[Relation]) -> str:
    lines = ["# Cartographie des données", "",
             "PK = clé candidate (unique, sans manquant). Relations « contrat » : déclarées "
             "par `references` ; « auto » : même nom de colonne.", "",
             "```mermaid", mermaid_er(profiles, relations), "```", ""]
    if relations:
        lines += md_table(["Parent", "Enfant", "Origine", "Cardinalité", "Inclusion", "Orphelines"],
                          [[f"{r.parent}.{r.parent_column}", f"{r.child}.{r.child_column}",
                            r.origin, r.cardinality, f"{r.inclusion_rate:.1%}",
                            f"{r.n_orphans} {r.orphans[:5]}" if r.n_orphans else "0"]
                           for r in relations])
    else:
        lines.append("_Aucune relation détectée._")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ validation
def render_validation(results: list[ValidationResult], missing_contracts: list[str]) -> str:
    lines = ["# Contrôle des données contre leurs contrats", "",
             f"_Exécuté le {datetime.now():%d/%m/%Y %H:%M}._", ""]
    lines += md_table(["Jeu de données", "Contrat", "Lignes", "Erreurs", "Alertes", "Verdict"],
                      [[r.dataset_id, f"v{r.contract_version} ({r.contract_status})", r.n_rows,
                        sum(v.severity == "error" for v in r.violations),
                        sum(v.severity == "warning" for v in r.violations),
                        "✅ conforme" if r.passed else "❌ non conforme"] for r in results])
    if missing_contracts:
        lines += ["", f"⚠️ Sans contrat : {', '.join(missing_contracts)} "
                      "(lancer `indusense contract draft`)."]
    for r in results:
        if r.violations:
            lines += ["", f"## {r.dataset_id}", ""]
            lines += md_table(["", "Règle", "Colonne", "Message", "Nb", "Exemples"],
                              [[SEVERITY_ICON.get(v.severity, ""), v.rule, f"`{v.column}`",
                                v.message, v.n_failed, v.examples] for v in r.violations])
    return "\n".join(lines) + "\n"
