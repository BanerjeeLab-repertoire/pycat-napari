"""**Compare conditions without lying about the sample size.**

Increment 3 turns the consolidated table (increment 2) into cross-condition comparisons. The figures
are the visible part; **this is the part that has to be right**, because the easiest way to produce a
plausible-but-false result in imaging biology is pseudoreplication: treat 5 000 puncta from 3 cells as
5 000 independent observations, and any trivial difference becomes p < 10⁻⁹.

PyCAT's own doctrine, stated in `pixel_wise_corr_analysis_tools`:

    "Pixels within one cell are not biological replicates, and neither are objects within one cell...
     the n for any statistical claim is the number of [replicates], not the number of pixels."

So every comparison here does one thing before any test: **aggregate each condition×replicate to a
single value**, making the replicate the inferential unit. Then, and only then, it compares conditions
— and it reports the n at *both* levels (objects and replicates), names the test it used, and
**refuses to infer** when there are too few replicates rather than borrowing significance from the
pseudoreplicated objects. A comparison that cannot be made honestly is reported as descriptive, not
dressed up with a p-value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from pycat.utils.notify import show_warning as _warn


# Below this many replicates per group, there is no honest replicate-level inference to make. The n
# is the biological unit, and 1 gives no within-condition variance; 2 is the bare minimum and is
# itself flagged as underpowered.
_MIN_REPLICATES_FOR_INFERENCE = 2


@dataclass
class ComparisonResult:
    """The outcome of a condition comparison, carrying its own honesty.

    ``inferential`` is False when the test could not be run at the replicate level — the descriptive
    per-group summaries are still populated, but ``p_value`` is ``None`` and ``note`` says why. A
    consumer must not read a missing p-value as "no difference".
    """
    measurement: str
    condition_col: str
    groups: dict                       # cond -> {n_objects, n_replicates, mean, std, values}
    test: Optional[str] = None
    statistic: Optional[float] = None
    p_value: Optional[float] = None
    inferential: bool = False
    note: str = ""

    def summary(self) -> str:
        parts = [f"{self.measurement} by {self.condition_col}:"]
        for cond, g in self.groups.items():
            parts.append(f"  {cond}: mean {g['mean']:.4g} "
                         f"(n={g['n_replicates']} replicates, {g['n_objects']} objects)")
        if self.inferential:
            parts.append(f"  {self.test}: p = {self.p_value:.4g}")
        else:
            parts.append(f"  NO TEST — {self.note}")
        return "\n".join(parts)


def aggregate_to_replicate(long_df, measurement, *, condition_col, replicate_col,
                           value_col='value', agg='mean') -> pd.DataFrame:
    """Collapse per-object rows to ONE value per (condition, replicate). **The anti-pseudoreplication step.**

    Returns a tidy frame with columns ``[condition_col, replicate_col, value]`` — one row per
    biological unit, which is what a test may treat as independent. Rows of other measurements are
    dropped; non-finite values are dropped before aggregation.
    """
    df = pd.DataFrame(long_df)
    if 'measurement' in df.columns:
        df = df[df['measurement'] == measurement]
    for col in (condition_col, replicate_col, value_col):
        if col not in df.columns:
            raise KeyError(f"column {col!r} is not in the table — "
                           f"have {list(df.columns)}")
    df = df[[condition_col, replicate_col, value_col]].copy()
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce')
    df = df.dropna(subset=[value_col])

    grouped = (df.groupby([condition_col, replicate_col])[value_col]
               .agg(agg).reset_index().rename(columns={value_col: 'value'}))
    return grouped


def _n_objects_per_condition(long_df, measurement, condition_col, value_col='value') -> dict:
    df = pd.DataFrame(long_df)
    if 'measurement' in df.columns:
        df = df[df['measurement'] == measurement]
    vals = pd.to_numeric(df.get(value_col), errors='coerce')
    df = df.assign(**{value_col: vals}).dropna(subset=[value_col])
    return df.groupby(condition_col)[value_col].size().to_dict()


def compare_conditions(long_df, measurement, *, condition_col, replicate_col,
                       value_col='value', agg='mean', parametric=False) -> ComparisonResult:
    """Compare a measurement across conditions, at the REPLICATE level, honestly.

    Aggregates each condition×replicate to one value, then:
      * 2 conditions → Mann-Whitney U (or Welch's t if ``parametric``);
      * >2 conditions → Kruskal-Wallis (or one-way ANOVA if ``parametric``).

    Reports the test, the statistic, the p-value, and n at **both** levels. If any condition has fewer
    than ``_MIN_REPLICATES_FOR_INFERENCE`` replicates, **no test is run** — the result is descriptive
    with a note, never a pixel-level p-value dressed as a biological one.
    """
    from scipy import stats

    agg_df = aggregate_to_replicate(long_df, measurement, condition_col=condition_col,
                                    replicate_col=replicate_col, value_col=value_col, agg=agg)
    n_objects = _n_objects_per_condition(long_df, measurement, condition_col, value_col)

    groups = {}
    for cond, sub in agg_df.groupby(condition_col):
        vals = sub['value'].to_numpy()
        groups[str(cond)] = dict(
            n_objects=int(n_objects.get(cond, 0)),
            n_replicates=int(len(vals)),
            mean=float(np.mean(vals)) if len(vals) else float('nan'),
            std=float(np.std(vals, ddof=1)) if len(vals) > 1 else float('nan'),
            values=vals.tolist())

    result = ComparisonResult(measurement=measurement, condition_col=condition_col, groups=groups)

    if len(groups) < 2:
        result.note = f"only {len(groups)} condition — nothing to compare"
        return result

    underpowered = [c for c, g in groups.items()
                    if g['n_replicates'] < _MIN_REPLICATES_FOR_INFERENCE]
    if underpowered:
        result.note = (f"conditions {underpowered} have < {_MIN_REPLICATES_FOR_INFERENCE} "
                       f"replicates — the inferential unit is the replicate, not the object, so no "
                       f"test can be run. The {sum(g['n_objects'] for g in groups.values())} objects "
                       f"are pseudoreplicates; a p-value from them would be a lie. Descriptive only.")
        _warn(f"Comparative stats: {result.note}")
        return result

    samples = [np.asarray(g['values']) for g in groups.values()]
    if len(samples) == 2:
        if parametric:
            stat, p = stats.ttest_ind(*samples, equal_var=False)
            test = "Welch's t-test (replicate means)"
        else:
            stat, p = stats.mannwhitneyu(*samples, alternative='two-sided')
            test = "Mann-Whitney U (replicate means)"
    else:
        if parametric:
            stat, p = stats.f_oneway(*samples)
            test = "one-way ANOVA (replicate means)"
        else:
            stat, p = stats.kruskal(*samples)
            test = "Kruskal-Wallis (replicate means)"

    result.test = test
    result.statistic = float(stat)
    result.p_value = float(p)
    result.inferential = True
    min_n = min(g['n_replicates'] for g in groups.values())
    if min_n < 3:
        result.note = (f"smallest group has {min_n} replicates — the test ran but is underpowered; "
                       f"report the effect size, not just the p-value")
    return result
