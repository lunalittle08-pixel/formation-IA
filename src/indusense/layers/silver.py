"""Couche Silver : du Bronze (texte) vers des données typées et contrôlées par le contrat.

Le Silver lit les valeurs d'origine du Bronze (colonnes `_raw`), pas sa colonne typée :
les règles de typage du Silver sont celles du contrat, pas le calcul « au mieux » du Bronze.

Règles, dans l'ordre, pour chaque jeu de données :
1. doublons stricts (même empreinte de ligne Bronze) : on garde la première ligne ;
1 bis. pseudonymisation (HMAC-SHA256 + sel) des colonnes personnelles du contrat
   (opération ANO : le Bronze les garde telles quelles, c'est ici qu'elles sont protégées) ;
2. espaces en début et fin retirés ; jetons de valeur manquante (`na_tokens`) -> NULL ;
3. conversion vers le type du CONTRAT (l'attendu), pas vers le type inféré ;
4. contrôles ligne à ligne : obligatoire, type, format, liste de valeurs, bornes, unicité, clé ;
   - règle en `on_fail: error` (défaut) : la ligne part en QUARANTAINE avec ses motifs ;
   - règle en `on_fail: warning` : la ligne reste ; une valeur non convertible devient NULL ;
5. colonnes `privacy: drop` retirées ; colonnes absentes du contrat retirées (signalées) ;
   colonnes `traceability: true` non reprises (ni contrôlées : elles restent au Bronze) ;
6. chaque ligne garde l'empreinte du fichier et le numéro de sa ligne Bronze (lignage).

Rien n'est corrigé ni deviné : une valeur est convertie, ou mise de côté avec son motif.
Les valeurs extrêmes mais dans les bornes du contrat sont conservées (c'est souvent le signal).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import BigInteger, Text

from indusense.config import InferenceSettings
from indusense.contracts.model import traceability_columns
from indusense.layers.anonymize import columns_to_pseudonymize, pseudonymize_series
from indusense.layers.bronze import raw_column, source_columns
from indusense.layers.settings import LayerSettings
from indusense.layers.sqltypes import CONVERTIBLE, sql_type, to_python
from indusense.profiling.types import convert, missing_mask


@dataclass
class SilverResult:
    dataset_id: str
    valid: pd.DataFrame                    # lignes conformes, valeurs typées
    rejects: pd.DataFrame                  # lignes en quarantaine (texte) + motifs
    column_types: dict[str, object]        # type SQL de chaque colonne de `valid`
    report: dict = field(default_factory=dict)


# ------------------------------------------------------------------ conversions
def _bound(value, col_type: str, converted: pd.Series):
    """Borne du contrat convertie comme les données (une date sans fuseau est lue en UTC
    si les données ont un fuseau, pour comparer des grandeurs homogènes)."""
    b = convert(pd.Series([str(value)]), col_type).iloc[0]
    tz = getattr(getattr(converted, "dt", None), "tz", None) if col_type == "DATETIME" else None
    if tz is not None and pd.notna(b) and b.tzinfo is None:
        b = b.tz_localize("UTC")
    return b


# ------------------------------------------------------------------ contrôles d'une colonne
def column_checks(spec: dict, raw: pd.Series, protected: bool,
                  inference: InferenceSettings) -> tuple[pd.Series | None, dict[str, pd.Series]]:
    """(valeurs converties ou None, {règle: masque des lignes en échec}).

    Une colonne pseudonymisée (`protected`) n'est pas contrôlée sur son format ni sur sa liste
    de valeurs : ses valeurs sont des empreintes, plus les valeurs d'origine.
    """
    ctype = spec["type"]
    miss = missing_mask(raw, inference)
    present = raw[~miss]
    none = pd.Series(False, index=raw.index)
    checks: dict[str, pd.Series] = {}
    if spec.get("required"):
        checks["required"] = miss

    converted = None
    if ctype in CONVERTIBLE and not protected:
        conv = convert(present, ctype, inference.parse_rate_min)
        bad = conv.isna()
        if ctype == "INTEGER":
            bad |= (conv % 1 != 0).fillna(False)
        checks["type"] = none | bad.reindex(raw.index, fill_value=False)
        converted = conv.where(~bad).reindex(raw.index)
        for key in ("min", "max"):
            if spec.get(key) is not None:
                b = _bound(spec[key], ctype, converted)
                if pd.notna(b):
                    out = (converted < b) if key == "min" else (converted > b)
                    checks[key] = out.fillna(False).astype(bool)

    if not protected:
        if spec.get("pattern"):
            rx = re.compile(spec["pattern"])
            checks["pattern"] = none | (~present.map(lambda v: bool(rx.fullmatch(v)))).reindex(
                raw.index, fill_value=False)
        if spec.get("allowed_values") is not None:
            allowed = {str(v) for v in spec["allowed_values"]}
            checks["allowed_values"] = none | (~present.isin(allowed)).reindex(raw.index, fill_value=False)
    if spec.get("unique"):
        checks["unique"] = none | present.duplicated(keep=False).reindex(raw.index, fill_value=False)
    return converted, checks


# ------------------------------------------------------------------ construction
def build_silver(dataset_id: str, bronze: pd.DataFrame, contract: dict, cfg: LayerSettings,
                 inference: InferenceSettings, salt: str) -> SilverResult:
    """Construit les lignes Silver, la quarantaine et le bilan à partir d'une table Bronze."""
    src_cols = source_columns(list(bronze.columns), cfg)
    n_in = len(bronze)
    bronze = bronze.drop_duplicates(subset=[cfg.col("row_hash")], keep="first")
    df = pd.DataFrame({c: bronze[raw_column(c, cfg)].astype(str).str.strip() for c in src_cols},
                      index=bronze.index)
    lineage = bronze[cfg.lineage]
    pseudonymized = [c for c in columns_to_pseudonymize(contract, cfg.pseudonymize_privacy)
                     if c in df.columns]
    for name in pseudonymized:
        df[name] = pseudonymize_series(df[name], salt)

    specs = {c["name"]: c for c in contract.get("schema", {}).get("columns", [])}
    missing_cols = [c for c in specs if c not in df.columns]
    extra_cols = [c for c in df.columns if c not in specs]
    traceability = [c for c in traceability_columns(contract) if c in df.columns]
    df = df.drop(columns=traceability)             # traçabilité : Bronze seulement, ni contrôlée ni reprise
    dropped = [n for n, s in specs.items() if s.get("privacy") == "drop" and n in df.columns]

    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    report_rejects: dict[str, int] = {}
    report_nulled: dict[str, int] = {}
    report_warnings: dict[str, int] = {}
    typed: dict[str, pd.Series] = {}
    types: dict[str, object] = {}

    for name in [c for c in df.columns if c in specs]:
        spec = specs[name]
        protected = spec.get("pii") and spec.get("privacy") in cfg.pseudonymize_privacy
        converted, checks = column_checks(spec, df[name], bool(protected), inference)
        severity = spec.get("on_fail", "error")
        for rule, mask in checks.items():
            n = int(mask.sum())
            if not n:
                continue
            key = f"{name}:{rule}"
            if severity == "error":
                report_rejects[key] = n
                for i in mask[mask].index:
                    reasons.at[i].append(key)
            elif rule == "type":
                report_nulled[key] = n          # valeur non convertible -> NULL, ligne gardée
            else:
                report_warnings[key] = n        # signalé, valeur gardée
        if name in dropped:
            continue
        if converted is not None:
            typed[name] = to_python(converted, spec["type"])
            types[name] = sql_type(spec["type"], converted, spec.get("declared_type"))
        else:
            miss = missing_mask(df[name], inference)
            typed[name] = df[name].where(~miss, None).astype(object)
            types[name] = Text

    # Clé primaire : parmi les lignes encore valides, deux lignes de même clé sont en conflit
    pk = [c for c in contract.get("schema", {}).get("primary_key", []) or [] if c in df.columns]
    ok = reasons.map(len) == 0
    if pk:
        conflict = df[ok].duplicated(subset=pk, keep=False).reindex(df.index, fill_value=False)
        if conflict.any():
            report_rejects[f"{','.join(pk)}:primary_key"] = int(conflict.sum())
            for i in conflict[conflict].index:
                reasons.at[i].append(f"{','.join(pk)}:primary_key")
            ok = reasons.map(len) == 0

    valid = pd.DataFrame(typed, index=df.index)[ok]
    rejects = df[~ok].copy()
    for col in cfg.lineage:
        valid[col], rejects[col] = lineage.loc[ok, col], lineage.loc[~ok, col]
    types[cfg.col("source_hash")] = Text
    types[cfg.col("row_number")] = BigInteger
    if len(valid):
        valid[cfg.col("row_number")] = valid[cfg.col("row_number")].astype(int)
    rejects[cfg.reasons_column] = reasons[~ok].map("; ".join)

    report = {
        "rows_bronze": n_in, "exact_duplicates_removed": n_in - len(df),
        "pseudonymized_columns": pseudonymized,
        "rows_silver": int(ok.sum()), "rows_rejected": int((~ok).sum()),
        "rejected_by_rule": report_rejects, "nulled_by_rule": report_nulled,
        "warnings_by_rule": report_warnings, "dropped_columns_privacy": dropped,
        "dropped_columns_not_in_contract": extra_cols,
        "dropped_columns_traceability": traceability, "contract_columns_missing": missing_cols,
        "contract_version": str(contract.get("contract", {}).get("version", "?")),
    }
    return SilverResult(dataset_id, valid.reset_index(drop=True), rejects.reset_index(drop=True),
                        types, report)


def render_report(result: SilverResult, schema: str, rejects_table: str) -> str:
    """Bilan Markdown du passage Bronze -> Silver (faits mesurés, aucune interprétation)."""
    r = result.report
    lines = [f"# Bilan Silver — {result.dataset_id}", "",
             f"- Contrat : version {r['contract_version']}",
             f"- Lignes Bronze : {r['rows_bronze']}",
             f"- Doublons exacts retirés : {r['exact_duplicates_removed']}",
             f"- Lignes Silver (`{schema}.{result.dataset_id}`) : {r['rows_silver']}",
             f"- Lignes en quarantaine (`{schema}.{rejects_table}`) : {r['rows_rejected']}",
             (f"- Colonnes pseudonymisées (HMAC-SHA256 + sel) : "
              f"{', '.join(r['pseudonymized_columns']) or 'aucune'}"),
             f"- Colonnes retirées (privacy: drop) : {', '.join(r['dropped_columns_privacy']) or 'aucune'}",
             (f"- Colonnes non reprises (traçabilité, restent au Bronze) : "
              f"{', '.join(r['dropped_columns_traceability']) or 'aucune'}"),
             (f"- Colonnes retirées (absentes du contrat) : "
              f"{', '.join(r['dropped_columns_not_in_contract']) or 'aucune'}"),
             (f"- Colonnes du contrat absentes des données : "
              f"{', '.join(r['contract_columns_missing']) or 'aucune'}"), ""]
    for title, key in (("Lignes mises en quarantaine, par règle (on_fail: error)", "rejected_by_rule"),
                       ("Valeurs non convertibles mises à NULL (on_fail: warning)", "nulled_by_rule"),
                       ("Avertissements, valeurs conservées (on_fail: warning)", "warnings_by_rule")):
        lines += [f"## {title}", ""]
        if r[key]:
            lines += ["| Colonne:règle | Lignes |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in sorted(r[key].items())]
        else:
            lines.append("Aucune.")
        lines.append("")
    return "\n".join(lines)
