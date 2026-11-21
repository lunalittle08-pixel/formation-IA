"""Profil d'un jeu de données : types, statistiques et constats qualité.

Les constats suivent les dimensions de qualité du guide data audit :
complétude, unicité, validité, cohérence, exactitude, représentativité, conformité.
Les détecteurs ci-dessous MESURENT ; textes, sévérités et seuils viennent des règles
(`findings_spec.py` : catalogue par défaut, spécification métier, exceptions des contrats).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from indusense.config import Settings
from indusense.io.readers import Dataset
from indusense.profiling.findings_spec import FindingRule, apply_override
from indusense.profiling.types import (NUMERIC_TYPES, TEMPORAL_TYPES, infer_type,
                                       missing_mask, present_values)

DATASET = "(jeu de données)"


@dataclass
class Finding:
    dimension: str      # complétude, unicité, validité, cohérence, exactitude, représentativité, conformité
    severity: str       # info | warning | error
    column: str         # nom de colonne, ou "(jeu de données)"
    message: str        # FAIT mesuré
    rule: str = ""      # identifiant de la règle (findings_spec)
    hypothesis: str = ""  # piste fournie par la spécification métier, jamais par le code
    suppressed: str = ""  # justification si le contrat neutralise ce constat pour la colonne

    @property
    def active(self) -> bool:
        return not self.suppressed


class FindingSink:
    """Collecte les constats en appliquant règles et exceptions de colonne des contrats."""

    def __init__(self, rules: dict[str, FindingRule], overrides: dict[str, dict] | None = None):
        self.rules = rules
        self.overrides = overrides or {}
        self.findings: list[Finding] = []

    def rule(self, rule_id: str, column: str = DATASET) -> FindingRule:
        """Règle effective pour une colonne (exception du contrat appliquée)."""
        return apply_override(self.rules[rule_id], self.overrides.get(column, {}).get(rule_id))

    def emit(self, rule_id: str, column: str = DATASET, **values) -> None:
        """Enregistre un constat. Une règle désactivée par la spec ne produit rien ;
        une règle neutralisée par un contrat est conservée avec sa justification."""
        r = self.rule(rule_id, column)
        if not r.enabled and not r.reason:
            return
        self.findings.append(Finding(
            dimension=r.dimension, severity=r.severity, column=column,
            message=r.render(r.message, values), rule=rule_id,
            hypothesis=r.render(r.hypothesis, values),
            suppressed="" if r.enabled else r.reason))


@dataclass
class ColumnProfile:
    name: str
    type: str
    n_rows: int
    n_missing: int
    pct_missing: float
    n_unique: int
    pct_unique: float
    is_key_candidate: bool
    pii_suspected: str = ""
    declared_type: str | None = None
    examples: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)       # valeurs brutes (min, max, mean, median...)
    top_values: dict = field(default_factory=dict)  # catégories : effectifs


@dataclass
class DatasetProfile:
    dataset_id: str
    table: str | None
    n_rows: int
    n_cols: int
    n_duplicate_rows: int
    columns: list[ColumnProfile]
    findings: list[Finding]
    composite_key: list[str] = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def active_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.active]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- statistiques
def _stats(col_type: str, converted: pd.Series | None) -> dict:
    if converted is None or col_type not in NUMERIC_TYPES | TEMPORAL_TYPES:
        return {}
    s = converted.dropna()
    if s.empty:
        return {}
    out = {"min": s.min(), "max": s.max()}
    if col_type in NUMERIC_TYPES:            # moyenne et médiane : numériques seulement
        out.update(mean=float(s.mean()), median=float(s.median()), std=float(s.std()),
                   q1=float(s.quantile(0.25)), q3=float(s.quantile(0.75)),
                   skew=float(s.skew()) if len(s) > 2 else 0.0)
    return out


# ---------------------------------------------------------------- constats colonne
def _completeness(p: ColumnProfile, sink: FindingSink) -> None:
    """Colonne vide, taux de manquants, valeur constante, identifiant incomplet."""
    if p.type == "EMPTY":
        sink.emit("empty_column", p.name)
    elif p.pct_missing > sink.rule("missing_high", p.name).params["threshold_pct"]:
        sink.emit("missing_high", p.name, pct=p.pct_missing)
    elif p.pct_missing > sink.rule("missing_moderate", p.name).params["threshold_pct"]:
        sink.emit("missing_moderate", p.name, pct=p.pct_missing)
    if p.n_unique == 1 and p.n_rows > 1:
        sink.emit("constant_column", p.name, value=p.examples[0] if p.examples else "")
    if (p.type == "IDENTITY" and not p.is_key_candidate and p.n_missing
            and p.n_unique == p.n_rows - p.n_missing):
        sink.emit("identity_incomplete", p.name, missing=p.n_missing)


def _accuracy(p: ColumnProfile, converted: pd.Series, sink: FindingSink) -> None:
    """Valeurs hors bornes IQR, extrêmes répétés, asymétrie, négatifs isolés."""
    x = converted.dropna()
    factor = sink.rule("iqr_outliers", p.name).params["factor"]
    q1, q3 = p.stats["q1"], p.stats["q3"]
    low, high = q1 - factor * (q3 - q1), q3 + factor * (q3 - q1)
    n_out = int(((x < low) | (x > high)).sum())
    p.stats["n_outliers_iqr"] = n_out
    if n_out:
        sink.emit("iqr_outliers", p.name, count=n_out, low=low, high=high)
    rep = sink.rule("repeated_extreme", p.name).params
    for bound in ("min", "max"):
        v = p.stats[bound]
        n_rep = int((x == v).sum())
        if n_rep >= rep["min_repeats"] and x.nunique() > rep["min_distinct"]:
            sink.emit("repeated_extreme", p.name, bound=bound, value=v, count=n_rep)
    skew = p.stats.get("skew", 0)
    if abs(skew) > sink.rule("skewed_distribution", p.name).params["min_abs_skew"] and n_out:
        sink.emit("skewed_distribution", p.name, skew=skew)
    n_neg = int((x < 0).sum())
    if 0 < n_neg < sink.rule("isolated_negatives", p.name).params["max_share"] * len(x):
        sink.emit("isolated_negatives", p.name, count=n_neg, share=n_neg / len(x))


def _label_groups(values: pd.Series, limit: int = 5) -> list[list[str]]:
    """Modalités qui ne diffèrent que par la casse ou les espaces : [['Matin', 'matin'], ...]."""
    variants = pd.Series(values.drop_duplicates().tolist(), dtype=str)
    keys = variants.str.strip().str.lower()
    groups = [sorted(variants[keys == k]) for k in keys.drop_duplicates()]
    return [g for g in groups if len(g) > 1][:limit]


def _categories(p: ColumnProfile, values: pd.Series, settings: Settings,
                sink: FindingSink) -> None:
    """Cohérence et représentativité des modalités (catégories, identifiants peu variés)."""
    low_card_id = (p.type == "IDENTITY" and not p.is_key_candidate
                   and p.n_unique <= settings.inference.category_max_unique)
    if p.type != "CATEGORY" and not low_card_id:
        return
    counts = values.value_counts()
    groups = _label_groups(values) if p.type == "CATEGORY" else []
    if groups:
        sink.emit("inconsistent_labels", p.name, groups=groups)
    ratio = counts.iloc[0] / counts.iloc[-1] if len(counts) > 1 else 0
    if (p.type == "CATEGORY" and len(counts) > 1
            and ratio >= sink.rule("class_imbalance", p.name).params["ratio"]):
        sink.emit("class_imbalance", p.name, rare=counts.index[-1],
                  rare_count=int(counts.iloc[-1]), major=counts.index[0],
                  major_count=int(counts.iloc[0]), ratio_observed=ratio)
    elif len(counts) > 2:
        threshold = sink.rule("underrepresented", p.name).params["ratio"] * counts.median()
        low = counts[counts < threshold]
        if len(low):
            sink.emit("underrepresented", p.name,
                      values={k: int(v) for k, v in low.head(5).items()}, median=counts.median())


def _column_findings(p: ColumnProfile, values: pd.Series, converted: pd.Series | None,
                     settings: Settings, sink: FindingSink) -> None:
    """Tous les détecteurs d'une colonne."""
    _completeness(p, sink)
    if p.type in NUMERIC_TYPES and converted is not None and p.stats:
        _accuracy(p, converted, sink)
    _categories(p, values, settings, sink)
    if p.pii_suspected:
        sink.emit("pii_suspected", p.name, reasons=p.pii_suspected)


def _share_matching(sample: pd.Series, pattern: str) -> float:
    """Part des valeurs de l'échantillon qui correspondent entièrement au motif."""
    return float(sample.str.fullmatch(re.compile(pattern)).mean()) if len(sample) else 0.0


def _pii(name: str, values: pd.Series, rule: FindingRule) -> str:
    """Raisons de suspecter une donnée personnelle (nom de colonne, format des valeurs)."""
    prm = rule.params
    reasons = []
    if re.search(prm["name_pattern"], name, re.IGNORECASE):
        reasons.append("nom de colonne")
    sample = values.head(int(prm["sample_rows"]))
    if _share_matching(sample, prm["email_pattern"]) > prm["min_share"]:
        reasons.append("format email")
    if _share_matching(sample, prm["phone_pattern"]) > prm["min_share"]:
        reasons.append("format téléphone")
    return ", ".join(reasons)


# ---------------------------------------------------------------- profil complet
def profile_column(name: str, s: pd.Series, settings: Settings,
                   declared_type: str | None = None,
                   sink: FindingSink | None = None) -> tuple[ColumnProfile, list[Finding]]:
    """Profil et constats d'une colonne (sink partagé par le jeu de données, ou local)."""
    sink = sink or FindingSink(settings.audit.rules)
    start = len(sink.findings)
    cfg = settings.inference
    miss = missing_mask(s, cfg)
    values = present_values(s, cfg)
    col_type, converted = infer_type(name, s, cfg)
    n_rows, n_present = len(s), len(values)
    n_unique = int(values.nunique())
    discrete = col_type in {"IDENTITY", "CATEGORY"} or (
        col_type == "INTEGER" and converted is not None)
    p = ColumnProfile(
        name=name, type=col_type, n_rows=n_rows, n_missing=int(miss.sum()),
        pct_missing=round(miss.mean() * 100, 2) if n_rows else 0.0,
        n_unique=n_unique,
        pct_unique=round(n_unique / n_present * 100, 2) if n_present else 0.0,
        is_key_candidate=bool(discrete and miss.sum() == 0 and n_unique == n_rows and n_rows > 1),
        pii_suspected=_pii(name, values, sink.rule("pii_suspected", name)),
        declared_type=declared_type,
        examples=list(values.drop_duplicates().head(3)),
        stats=_stats(col_type, converted),
    )
    if col_type in {"CATEGORY", "BOOLEAN"}:
        p.top_values = {k: int(v) for k, v in values.value_counts().head(30).items()}
    _column_findings(p, values, converted, settings, sink)
    return p, sink.findings[start:]


def profile_dataset(ds: Dataset, settings: Settings,
                    overrides: dict[str, dict] | None = None) -> DatasetProfile:
    """Profil d'un jeu de données ; `overrides` = exceptions par colonne tirées du contrat."""
    sink = FindingSink(settings.audit.rules, overrides)
    n_dup = int(ds.df.duplicated().sum())
    if n_dup:
        sink.emit("duplicate_rows", count=n_dup)
    columns = []
    for name in ds.df.columns:
        declared = (ds.declared_types or {}).get(name)
        p, _ = profile_column(name, ds.df[name], settings, declared, sink)
        columns.append(p)
    _redundancy(ds, columns, settings, sink)
    composite = _entity_time_key(ds, columns, settings, sink)
    for target in settings.audit.targets:
        if target in ds.df.columns:
            dist = ds.df[target].value_counts(normalize=True).round(4).to_dict()
            sink.emit("target_distribution", target, distribution=dist)
    return DatasetProfile(dataset_id=ds.id, table=ds.table, n_rows=len(ds.df),
                          n_cols=ds.df.shape[1], n_duplicate_rows=n_dup,
                          columns=columns, findings=sink.findings, composite_key=composite)


def _redundancy(ds: Dataset, columns: list[ColumnProfile], settings: Settings,
                sink: FindingSink) -> None:
    """Paires de colonnes numériques très corrélées."""
    from indusense.profiling.types import to_numeric
    num_cols = [c.name for c in columns if c.type in NUMERIC_TYPES]
    if len(num_cols) < 2:
        return
    cfg = settings.inference
    frame = pd.DataFrame({c: to_numeric(ds.df[c].where(~missing_mask(ds.df[c], cfg), ""))
                          for c in num_cols})
    corr = frame.corr().abs()
    min_corr = sink.rule("redundant_columns").params["min_corr"]
    for i, a in enumerate(num_cols):
        for b in num_cols[i + 1:]:
            if corr.loc[a, b] >= min_corr:
                sink.emit("redundant_columns", f"{a} / {b}", corr=corr.loc[a, b])


def _entity_time_key(ds: Dataset, columns: list[ColumnProfile], settings: Settings,
                     sink: FindingSink) -> list[str]:
    """Séries temporelles (entité, horodatage) : clé composite, doublons, écarts par entité.

    Cas visé : une mesure par (identifiant d'entité, horodatage). Ne s'applique que s'il
    n'existe pas de clé simple.
    """
    if any(c.is_key_candidate for c in columns):
        return []
    entities = [c.name for c in columns if c.type in {"IDENTITY", "CATEGORY"}
                and 1 < c.n_unique <= settings.inference.category_max_unique]
    times = [c.name for c in columns if c.type == "DATETIME"]
    if not entities or not times:
        return []
    ent, tcol = entities[0], times[0]
    n_dup = int(ds.df.duplicated(subset=[ent, tcol]).sum())
    if n_dup:
        sink.emit("composite_key_duplicates", f"{ent} + {tcol}", count=n_dup,
                  entity=ent, time=tcol)
    from indusense.profiling.types import to_datetime
    t = to_datetime(ds.df[tcol].astype(str))
    frame = pd.DataFrame({"e": ds.df[ent], "t": t}).dropna().drop_duplicates()
    steps = frame.sort_values("t").groupby("e")["t"].diff().dropna()
    if not steps.empty:
        _time_regularity(frame, steps.median(), tcol, sink)
    return [ent, tcol]


def _time_regularity(frame: pd.DataFrame, global_step, tcol: str, sink: FindingSink) -> None:
    """Entités mesurées moins souvent que les autres, et écarts anormaux par entité."""
    ordered = frame.sort_values("t").groupby("e")["t"]
    per_entity = ordered.apply(lambda x: x.diff().median())
    step_factor = sink.rule("sparse_entities", tcol).params["step_factor"]
    sparse = per_entity[per_entity > step_factor * global_step]
    if len(sparse):
        sink.emit("sparse_entities", tcol, entities=list(sparse.index), step=sparse.iloc[0],
                  global_step=global_step)
    # Écart = plus de gap_factor × le pas habituel DE L'ENTITÉ (une entité peut mesurer moins souvent)
    gap_factor = sink.rule("time_gaps", tcol).params["gap_factor"]
    gaps = ordered.apply(lambda x: int((x.diff() > gap_factor * x.diff().median()).sum()))
    if gaps.sum():
        sink.emit("time_gaps", tcol, count=int(gaps.sum()), max_count=int(gaps.max()),
                  entity=gaps.idxmax())
