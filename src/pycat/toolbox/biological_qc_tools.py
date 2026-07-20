"""**Object-level biological QC — flag biological outliers, never filter them.**

PyCAT's imaging QC (``data_qc_tools``) answers *"can I trust this IMAGE?"* — saturation, focus, SNR,
drift, bleaching. Nothing answered *"can I trust this OBJECT?"*, yet the most common analysis errors are
object-level and pass imaging QC perfectly:

* a **cell touching the field edge** is truncated — its area, shape, and total intensity are wrong, and
  it silently biases every population statistic;
* an **oversegmented** nucleus doubles the apparent count;
* a **condensate outside its cell** is usually a segmentation error, not biology;
* an object with **extreme size or intensity** may be an aggregate or debris.

**The cardinal rule: this module FLAGS, it does not FILTER.** Excluding objects is the user's explicit
decision — the codebase's no-silent-gates contract, and the exact failure mode the filter-sensitivity
programme exists to catch. Provide the flags and the per-flag counts; let the analysis decide. Its honest
use is *"the effect holds when edge-touching cells are excluded"* — a stronger claim than an unqualified one.

**Flags are OBSERVATIONS, not verdicts.** "touches image border", "unusual size" — not "bad cell". A
mitotic or dead cell has legitimate, wildly different morphology; the flag is a hint for review. The one
exception is edge-touching: a truncated object is objectively a measurement artefact, stated definitively.

**Robust statistics only** — median/MAD, never mean/SD: a population containing outliers corrupts the very
estimator used to find them. ``k`` is a declared parameter, and every flag records the threshold it used.

(This is NOT a filter, and NOT the Measurement Reliability Index — that composes QC with segmentation
stability and benchmarking and is a much larger, separate construct.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Human-readable observation for each flag column — worded as a hint for review, never a verdict.
_FLAG_WORDS = {
    'qc_edge_touching':         'touches image border',
    'qc_size_outlier':          'unusual size',
    'qc_shape_outlier':         'unusual morphology',
    'qc_intensity_outlier':     'unusual intensity',
    'qc_containment_violation': 'outside parent object',
}


def _mad_outlier_mask(values, k):
    """Robust outlier mask: ``|x − median| > k · (1.4826 · MAD)``. The 1.4826 makes MAD a consistent
    estimator of σ for a normal core, so ``k`` reads like a number of SDs. Fewer than 3 finite values,
    or a zero-MAD (constant) population, flags NOTHING — there is no spread to be an outlier of."""
    v = np.asarray(values, dtype=float)
    finite = np.isfinite(v)
    if finite.sum() < 3:
        return np.zeros(v.shape, dtype=bool)
    med = np.median(v[finite])
    mad = np.median(np.abs(v[finite] - med))
    scaled = 1.4826 * mad
    if scaled <= 0:
        return np.zeros(v.shape, dtype=bool)
    return finite & (np.abs(v - med) > float(k) * scaled)


def flag_edge_touching(labels, *, border_px=0) -> pd.Series:
    """Objects whose mask reaches the field edge (within ``border_px``) — TRUNCATED, so their area,
    shape, and total intensity are wrong. Objectively a measurement artefact, so this one is definitive.

    Returns a boolean Series indexed by label id (background 0 excluded). ``border_px`` widens the band,
    e.g. to also flag objects within a few pixels of the edge (a near-edge object is often partly clipped)."""
    labels = np.asarray(labels)
    ids = np.unique(labels)
    ids = ids[ids != 0]
    b = max(0, int(border_px))
    band = np.zeros(labels.shape, dtype=bool)
    band[:b + 1, :] = True
    band[labels.shape[0] - (b + 1):, :] = True
    band[:, :b + 1] = True
    band[:, labels.shape[1] - (b + 1):] = True
    touching = set(np.unique(labels[band])) - {0}
    return pd.Series([int(i) in touching for i in ids], index=ids, name='qc_edge_touching', dtype=bool)


def flag_size_outliers(table, *, column='area', method='mad', k=3.5) -> pd.Series:
    """Robust size outliers on ``column`` (default area). Both tails: an oversegmented fragment is
    tiny, a merged pair or an aggregate is huge. ``method`` is 'mad' (robust; the only one that does not
    let the outliers corrupt their own detection)."""
    if column not in table.columns:
        return pd.Series(False, index=table.index, name='qc_size_outlier', dtype=bool)
    return pd.Series(_mad_outlier_mask(table[column].to_numpy(), k),
                     index=table.index, name='qc_size_outlier', dtype=bool)


def flag_shape_outliers(table, *, columns=('eccentricity', 'solidity'), k=3.5) -> pd.Series:
    """Robust shape outliers — flagged if a row is an outlier on ANY of the given shape columns (each
    tested independently by MAD). Worded "unusual morphology": mitotic/dead cells are real, so this is a
    review hint, not a rejection."""
    cols = [c for c in columns if c in table.columns]
    mask = np.zeros(len(table), dtype=bool)
    for c in cols:
        mask |= _mad_outlier_mask(table[c].to_numpy(), k)
    return pd.Series(mask, index=table.index, name='qc_shape_outlier', dtype=bool)


def flag_intensity_outliers(table, *, column='intensity_mean', k=3.5) -> pd.Series:
    """Robust intensity outliers — an extreme-intensity object may be an aggregate or debris. Falls back
    to a common alternate column name if the named one is absent."""
    col = column if column in table.columns else next(
        (c for c in ('intensity_mean', 'mean_intensity', 'intensity') if c in table.columns), None)
    if col is None:
        return pd.Series(False, index=table.index, name='qc_intensity_outlier', dtype=bool)
    return pd.Series(_mad_outlier_mask(table[col].to_numpy(), k),
                     index=table.index, name='qc_intensity_outlier', dtype=bool)


def flag_containment_violations(child_table, parent_labels, *,
                                centroid_cols=('centroid_row', 'centroid_col')) -> pd.Series:
    """Child objects whose centroid falls OUTSIDE any parent object — a condensate not inside a cell is
    usually a segmentation error, not biology. Needs child centroids + the parent label mask. A child
    whose centroid lands on parent background (label 0) is flagged; on a parent label, it is contained."""
    rc, cc = centroid_cols
    if parent_labels is None or rc not in child_table.columns or cc not in child_table.columns:
        return pd.Series(False, index=child_table.index, name='qc_containment_violation', dtype=bool)
    parent = np.asarray(parent_labels)
    h, w = parent.shape
    out = []
    for r, c in zip(child_table[rc].to_numpy(), child_table[cc].to_numpy()):
        if not (np.isfinite(r) and np.isfinite(c)):
            out.append(False)                     # no centroid → cannot judge, do not fabricate a violation
            continue
        ri, ci = int(round(r)), int(round(c))
        inside = (0 <= ri < h and 0 <= ci < w and parent[ri, ci] != 0)
        out.append(not inside)
    return pd.Series(out, index=child_table.index, name='qc_containment_violation', dtype=bool)


def biological_qc(table, labels, *, parent_labels=None, id_col='label',
                  size_column='area', intensity_column='intensity_mean', k=3.5) -> pd.DataFrame:
    """The aggregator: return ``table`` with the boolean flag columns present for the data it has, a
    ``qc_flags`` summary string per object (the observations, joined), and a per-flag count report on
    ``result.attrs['qc_report']``. **Never removes a row** — the returned frame has the same length as
    the input. Flags a column only when the data to compute it is present, so a table without shape
    columns simply carries no shape flag rather than a fabricated one."""
    df = table.copy()

    flags = {}
    if labels is not None and id_col in df.columns:
        edge = flag_edge_touching(labels)
        flags['qc_edge_touching'] = df[id_col].map(edge).fillna(False).astype(bool)
    if size_column in df.columns:
        flags['qc_size_outlier'] = flag_size_outliers(df, column=size_column, k=k)
    if any(c in df.columns for c in ('eccentricity', 'solidity')):
        flags['qc_shape_outlier'] = flag_shape_outliers(df, k=k)
    if any(c in df.columns for c in (intensity_column, 'mean_intensity', 'intensity')):
        flags['qc_intensity_outlier'] = flag_intensity_outliers(df, column=intensity_column, k=k)
    if parent_labels is not None:
        flags['qc_containment_violation'] = flag_containment_violations(df, parent_labels)

    for name, series in flags.items():
        df[name] = series.to_numpy()

    def _summary(row):
        return '; '.join(_FLAG_WORDS[name] for name in flags if bool(row.get(name)))
    df['qc_flags'] = df.apply(_summary, axis=1) if flags else ''

    df.attrs['qc_report'] = {name.replace('qc_', ''): int(series.sum())
                             for name, series in flags.items()}
    df.attrs['qc_k'] = float(k)
    return df
