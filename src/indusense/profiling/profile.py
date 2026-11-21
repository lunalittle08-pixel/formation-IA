"""Profil d'un jeu de données : types, statistiques et constats qualité.

Les constats suivent les dimensions de qualité du guide data audit :
complétude, unicité, validité, cohérence, exactitude, représentativité, conformité.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from indusense.config import Settings
from indusense.io.readers import Dataset
from indusense.profiling.types import (NUMERIC_TYPES, TEMPORAL_TYPES, infer_type,
                                       missing_mask, present_values)

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
PHONE_RE = re.compile(r"^(\+33|0)[1-9](\s?\d{2}){4}$")


@dataclass
class Finding:
    dimension: str      # complétude, unicité, validité, cohérence, exactitude, représentativité, conformité
    severity: str       # info | warning | error
    column: str         # nom de colonne, ou "(jeu de données)"
    message: str


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
def _column_findings(p: ColumnProfile, values: pd.Series, converted: pd.Series | None,
                     settings: Settings) -> list[Finding]:
    a = settings.audit
    out: list[Finding] = []
    add = lambda dim, sev, msg: out.append(Finding(dim, sev, p.name, msg))

    # Complétude
    if p.type == "EMPTY":
        add("complétude", "error", "colonne entièrement vide")
    elif p.pct_missing > a.missing_drop_pct:
        add("complétude", "error", f"{p.pct_missing:.1f} % de manquants : candidate à la suppression")
    elif p.pct_missing > a.missing_warn_pct:
        add("complétude", "warning", f"{p.pct_missing:.1f} % de manquants : stratégie d'imputation à définir")
    if p.n_unique == 1 and p.n_rows > 1:
        add("validité", "warning", "valeur constante : aucune information")

    # Unicité des identifiants
    if p.type == "IDENTITY" and not p.is_key_candidate and p.n_unique == p.n_rows - p.n_missing:
        add("unicité", "info", "identifiant unique mais incomplet")

    # Exactitude : outliers, valeurs extrêmes répétées, asymétrie
    if p.type in NUMERIC_TYPES and converted is not None and p.stats:
        x = converted.dropna()
        q1, q3 = p.stats["q1"], p.stats["q3"]
        low, high = q1 - a.outlier_iqr_factor * (q3 - q1), q3 + a.outlier_iqr_factor * (q3 - q1)
        n_out = int(((x < low) | (x > high)).sum())
        p.stats["n_outliers_iqr"] = n_out
        if n_out:
            add("exactitude", "warning", f"{n_out} valeurs hors bornes IQR [{low:.4g} ; {high:.4g}] "
                                         "— erreur capteur ou phénomène réel ? (à valider métier)")
        for label in ("min", "max"):
            v = p.stats[label]
            rep = int((x == v).sum())
            if rep >= a.repeated_extreme_min and x.nunique() > 20:
                add("exactitude", "warning", f"valeur {label} {v} répétée {rep} fois "
                                             "(saturation capteur ou code d'erreur ?)")
        if abs(p.stats.get("skew", 0)) > 1 and n_out:
            add("exactitude", "info", f"distribution asymétrique (skew={p.stats['skew']:.2f}) : "
                                      "l'IQR surestime les outliers")
        n_neg = int((x < 0).sum())
        if 0 < n_neg < 0.05 * len(x):
            add("validité", "warning", f"{n_neg} valeurs négatives isolées (valeur sentinelle ?)")

    # Cohérence et représentativité des catégories (et des clés étrangères : machine, site...)
    low_card_id = (p.type == "IDENTITY" and not p.is_key_candidate
                   and p.n_unique <= settings.inference.category_max_unique)
    if p.type == "CATEGORY" or low_card_id:
        counts = values.value_counts()
        if p.type == "CATEGORY" and values.str.strip().str.lower().nunique() < values.nunique():
            add("cohérence", "warning", f"modalités incohérentes (casse/espaces) : {list(counts.index[:6])}")
        if p.type == "CATEGORY" and len(counts) > 1 and counts.iloc[0] / counts.iloc[-1] >= a.imbalance_ratio:
            add("représentativité", "warning",
                f"déséquilibre : '{counts.index[-1]}' ({counts.iloc[-1]}) vs '{counts.index[0]}' ({counts.iloc[0]})")
        elif len(counts) > 2:
            low = counts[counts < a.underrepresented_ratio * counts.median()]
            if len(low):
                add("représentativité", "warning",
                    f"sous-représentation : { {k: int(v) for k, v in low.head(5).items()} } "
                    f"(médiane {counts.median():.0f})")

    # Conformité (RGPD)
    if p.pii_suspected:
        add("conformité", "warning", f"donnée personnelle possible ({p.pii_suspected}) "
                                     "→ minimiser / pseudonymiser (C2)")
    return out


def _pii(name: str, values: pd.Series, settings: Settings) -> str:
    reasons = []
    if settings.audit.pii_name_pattern.search(name):
        reasons.append("nom de colonne")
    sample = values.head(500)
    if len(sample) and sample.str.fullmatch(EMAIL_RE).mean() > 0.5:
        reasons.append("format email")
    if len(sample) and sample.str.fullmatch(PHONE_RE).mean() > 0.5:
        reasons.append("format téléphone")
    return ", ".join(reasons)


# ---------------------------------------------------------------- profil complet
def profile_column(name: str, s: pd.Series, settings: Settings,
                   declared_type: str | None = None) -> tuple[ColumnProfile, list[Finding]]:
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
        pii_suspected=_pii(name, values, settings),
        declared_type=declared_type,
        examples=list(values.drop_duplicates().head(3)),
        stats=_stats(col_type, converted),
    )
    if col_type in {"CATEGORY", "BOOLEAN"}:
        p.top_values = {k: int(v) for k, v in values.value_counts().head(30).items()}
    return p, _column_findings(p, values, converted, settings)


def profile_dataset(ds: Dataset, settings: Settings) -> DatasetProfile:
    columns, findings = [], []
    for name in ds.df.columns:
        declared = (ds.declared_types or {}).get(name)
        p, f = profile_column(name, ds.df[name], settings, declared)
        columns.append(p)
        findings += f

    n_dup = int(ds.df.duplicated().sum())
    if n_dup:
        findings.insert(0, Finding("unicité", "warning", "(jeu de données)",
                                   f"{n_dup} lignes dupliquées à l'identique"))
    findings += _redundancy(ds, columns, settings)
    composite, ts_findings = _entity_time_key(ds, columns, settings)
    findings += ts_findings
    for target in settings.audit.targets:
        if target in ds.df.columns:
            dist = ds.df[target].value_counts(normalize=True).round(4).to_dict()
            findings.append(Finding("représentativité", "info", target,
                                    f"distribution de la cible : {dist}"))
    return DatasetProfile(dataset_id=ds.id, table=ds.table, n_rows=len(ds.df),
                          n_cols=ds.df.shape[1], n_duplicate_rows=n_dup,
                          columns=columns, findings=findings, composite_key=composite)


def _redundancy(ds: Dataset, columns: list[ColumnProfile], settings: Settings) -> list[Finding]:
    from indusense.profiling.types import to_numeric
    num_cols = [c.name for c in columns if c.type in NUMERIC_TYPES]
    if len(num_cols) < 2:
        return []
    cfg = settings.inference
    frame = pd.DataFrame({c: to_numeric(ds.df[c].where(~missing_mask(ds.df[c], cfg), ""))
                          for c in num_cols})
    corr = frame.corr().abs()
    out = []
    for i, a in enumerate(num_cols):
        for b in num_cols[i + 1:]:
            if corr.loc[a, b] >= settings.audit.redundant_corr:
                out.append(Finding("validité", "info", f"{a} / {b}",
                                   f"corrélation {corr.loc[a, b]:.3f} : colonnes probablement redondantes"))
    return out


def _entity_time_key(ds: Dataset, columns: list[ColumnProfile],
                     settings: Settings) -> tuple[list[str], list[Finding]]:
    """Séries temporelles (entité, horodatage) : clé composite, doublons, trous par entité.

    Ex. telemetry : une mesure par (machine_id, timestamp). Ne s'applique que s'il
    n'existe pas de clé simple.
    """
    if any(c.is_key_candidate for c in columns):
        return [], []
    entities = [c.name for c in columns if c.type in {"IDENTITY", "CATEGORY"}
                and 1 < c.n_unique <= settings.inference.category_max_unique]
    times = [c.name for c in columns if c.type == "DATETIME"]
    if not entities or not times:
        return [], []
    ent, tcol = entities[0], times[0]
    out: list[Finding] = []
    n_dup = int(ds.df.duplicated(subset=[ent, tcol]).sum())
    if n_dup:
        out.append(Finding("unicité", "warning", f"{ent} + {tcol}",
                           f"{n_dup} doublons sur la clé composite ({ent}, {tcol})"))
    from indusense.profiling.types import to_datetime
    t = to_datetime(ds.df[tcol].astype(str))
    frame = pd.DataFrame({"e": ds.df[ent], "t": t}).dropna().drop_duplicates()
    steps = frame.sort_values("t").groupby("e")["t"].diff().dropna()
    if not steps.empty:
        global_step = steps.median()
        per_entity = frame.sort_values("t").groupby("e")["t"].apply(
            lambda x: x.diff().median())
        sparse = per_entity[per_entity > 1.5 * global_step]
        if len(sparse):
            out.append(Finding("représentativité", "warning", tcol,
                               f"fréquence plus faible pour {list(sparse.index)} "
                               f"(pas {sparse.iloc[0]} vs {global_step} en général)"))
        # Trou = écart > 2 × le pas habituel DE L'ENTITÉ (une machine peut mesurer moins souvent)
        gaps = frame.sort_values("t").groupby("e")["t"].apply(
            lambda x: int((x.diff() > 2 * x.diff().median()).sum()))
        if gaps.sum():
            out.append(Finding("complétude", "info", tcol,
                               f"{int(gaps.sum())} trous temporels (> 2 × pas habituel de "
                               f"l'entité) ; max {int(gaps.max())} pour {gaps.idxmax()}"))
    return [ent, tcol], out
