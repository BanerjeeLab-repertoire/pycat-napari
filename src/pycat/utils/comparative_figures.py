"""**Comparative figures that SHOW the replicate structure, not hide it.**

Increment 3's visible layer, drawn from the consolidated table. The one design decision that matters:
a comparative figure must make pseudoreplication *visible*, not paper over it. So every condition is
drawn twice — the object cloud (light, many points) **and** the replicate means on top (dark, few
points) — because the honest test (`comparative_stats.compare_conditions`) runs on those few dark
points, and the figure should let the eye see that. A p-value annotation that came from the objects
would contradict what the picture shows; here they agree by construction.

Static matplotlib (Agg-safe, renders headlessly). Interactive brushing and a PyQtGraph render are a
later increment — they need a viewer, and this ships the part that can be verified without one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pycat.utils.comparative_stats import compare_conditions, aggregate_to_replicate


def _measurement_frame(long_df, measurement, condition_col, value_col):
    df = pd.DataFrame(long_df)
    if 'measurement' in df.columns:
        df = df[df['measurement'] == measurement]
    df = df[[condition_col] + [c for c in (value_col,) if c in df.columns]].copy()
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce')
    return df.dropna(subset=[value_col])


def condition_comparison_figure(long_df, measurement, *, condition_col, replicate_col,
                                value_col='value', order=None, parametric=False, ax=None):
    """A per-condition comparison: object cloud + replicate means + the honest stats annotation.

    Returns the matplotlib ``Figure``. The x-axis is the conditions; each shows a box of its objects,
    the individual objects jittered behind it (light), and the **replicate means as large dark
    points** — the units the test actually compares. The title carries the test, the p-value, and n at
    both levels, straight from ``compare_conditions``; a non-inferential result is labelled as such,
    never given a significance marker.
    """
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    objs = _measurement_frame(long_df, measurement, condition_col, value_col)
    reps = aggregate_to_replicate(long_df, measurement, condition_col=condition_col,
                                  replicate_col=replicate_col, value_col=value_col)
    result = compare_conditions(long_df, measurement, condition_col=condition_col,
                                replicate_col=replicate_col, value_col=value_col,
                                parametric=parametric)

    conditions = order or sorted(objs[condition_col].astype(str).unique())
    fig = ax.figure if ax is not None else plt.figure(figsize=(1.6 * len(conditions) + 2, 4.5))
    ax = ax or fig.add_subplot(111)
    rng = np.random.default_rng(0)

    for i, cond in enumerate(conditions):
        ov = objs.loc[objs[condition_col].astype(str) == str(cond), value_col].to_numpy()
        rv = reps.loc[reps[condition_col].astype(str) == str(cond), 'value'].to_numpy()
        if ov.size:
            ax.scatter(np.full(ov.size, i) + rng.uniform(-0.12, 0.12, ov.size), ov,
                       s=6, color='#c7d3e8', alpha=0.5, zorder=1)      # the object cloud, light
            ax.boxplot(ov, positions=[i], widths=0.5, showfliers=False,
                       medianprops=dict(color='#34495e'), zorder=2)
        if rv.size:
            ax.scatter(np.full(rv.size, i), rv, s=70, color='#c0392b', edgecolor='white',
                       linewidth=0.8, zorder=3, label='replicate mean' if i == 0 else None)  # the units tested

    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels(conditions)
    ax.set_xlabel(condition_col)
    ax.set_ylabel(f"{measurement} ({value_col})")
    ax.legend(loc='best', fontsize=8, frameon=False)

    ax.set_title(_annotation(result), fontsize=9, loc='left')
    fig.tight_layout()
    return fig


def _annotation(result) -> str:
    """One honest caption. Never a star without an inferential p-value behind it."""
    ns = " · ".join(f"{c}: n={g['n_replicates']} rep / {g['n_objects']} obj"
                    for c, g in result.groups.items())
    if result.inferential:
        star = '*' if (result.p_value is not None and result.p_value < 0.05) else 'n.s.'
        head = f"{result.measurement}: {result.test}, p = {result.p_value:.3g} [{star}]"
    else:
        head = f"{result.measurement}: NO TEST — {result.note.split('.')[0]}"
    return head + "\n" + ns


def dose_response_figure(long_df, measurement, *, dose_col, replicate_col,
                         value_col='value', ax=None):
    """Replicate-mean dose-response: mean ± SEM across replicates at each dose, points = replicates.

    The error bar is over REPLICATES, not objects — the same anti-pseudoreplication rule as the
    comparison. Doses are read as numbers so the x-axis is quantitative and ordered.
    """
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    reps = aggregate_to_replicate(long_df, measurement, condition_col=dose_col,
                                  replicate_col=replicate_col, value_col=value_col)
    reps = reps.assign(_dose=pd.to_numeric(reps[dose_col], errors='coerce')).dropna(subset=['_dose'])

    fig = ax.figure if ax is not None else plt.figure(figsize=(5, 4))
    ax = ax or fig.add_subplot(111)

    summary = reps.groupby('_dose')['value'].agg(['mean', 'std', 'size']).reset_index()
    summary['sem'] = summary['std'] / np.sqrt(summary['size'].clip(lower=1))
    ax.errorbar(summary['_dose'], summary['mean'], yerr=summary['sem'].fillna(0),
                marker='o', capsize=3, color='#34495e', zorder=2, label='replicate mean ± SEM')
    ax.scatter(reps['_dose'], reps['value'], s=30, color='#c0392b', alpha=0.6, zorder=1)

    ax.set_xlabel(dose_col)
    ax.set_ylabel(f"{measurement} ({value_col})")
    ax.set_title(f"{measurement} dose-response (n over replicates, not objects)", fontsize=9, loc='left')
    ax.legend(loc='best', fontsize=8, frameon=False)
    fig.tight_layout()
    return fig
