"""**Which of these feature columns are near-duplicates? — a minimal non-redundant set, reported not enforced.**

PyCAT emits wide feature tables: `regionprops` geometry plus custom condensate / partition / intensity
columns. Many are near-duplicates — `area`, `convex_area`, `equivalent_diameter` and `major_axis_length`
all track object size; several intensity columns co-vary almost perfectly. Feeding 40 such columns into a
downstream PCA or classifier silently gives "size" four times the weight of a unique feature, and nothing
reports it. This module does.

**Four decisions make it honest:**

1. **Spearman by default, not Pearson.** Morphometric relationships are often monotonic but not linear
   (area vs diameter is a square law); Pearson understates that redundancy. Both are offered; the default
   tells the truth about this kind of data.
2. **Cluster, don't threshold pairwise.** Redundancy is transitive-ish (A~B, B~C ⇒ group all three even
   if A~C is just under threshold). Correlation-distance hierarchical clustering with average linkage,
   not an order-dependent pairwise drop.
3. **The representative is CHOSEN, not arbitrary.** Within a group keep the column that is (a) defined in
   the measurement ontology (an interpretable quantity beats a derived one), else (b) the most complete
   (fewest NaNs), else (c) alphabetical — and record *why*, so the minimal set is reproducible.
4. **Report, never auto-drop.** `analyze_redundancy` says what *could* be dropped; `minimal_feature_set`
   is opt-in. Nothing here ever removes a column from the caller's table — that is the silent-mutation
   failure the filter-sensitivity programme exists to catch.

**Dataset-specific by construction.** Two independent measurements can correlate by coincidence on one
table. The report describes redundancy on THIS table; it is not a universal fact and must not be baked
into a global default.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass(frozen=True)
class RedundancyReport:
    """The outcome of a redundancy analysis on one table (see module docstring for the guarantees).

    ``correlation`` is the |r| matrix over the analysed columns; ``groups`` are the clusters of
    mutually-redundant columns (each with >1 member); ``representatives`` maps each group's kept column to
    the REASON it was chosen; ``dropped`` are the columns a minimal set could omit; ``excluded`` maps a
    column that could not be analysed (constant / too few values) to the stated reason.
    """
    correlation: pd.DataFrame
    groups: list
    representatives: dict          # representative column -> reason it was chosen
    dropped: tuple
    method: str
    threshold: float
    excluded: dict                 # column -> reason it was left out of the analysis


def _numeric_columns(table, columns):
    if columns is not None:
        return [c for c in columns if c in table.columns]
    return [c for c in table.columns if pd.api.types.is_numeric_dtype(table[c])]


def _partition_analysable(table, cols):
    """Split candidate columns into the ones with real spread to correlate, and the ones excluded (with a
    stated reason). A constant column has UNDEFINED correlation — trivially redundant, but for a different
    reason than co-variation, so it is flagged separately rather than silently clustered."""
    kept, excluded = [], {}
    for c in cols:
        s = pd.to_numeric(table[c], errors='coerce')
        finite = s[np.isfinite(s)]
        if len(finite) < 3:
            excluded[c] = 'insufficient data (fewer than 3 finite values)'
        elif float(np.std(finite.to_numpy(), ddof=0)) <= 1e-12:
            excluded[c] = 'constant (correlation undefined)'
        else:
            kept.append(c)
    return kept, excluded


def _correlation_distance(sub, method):
    """|r| matrix and its 1-|r| distance matrix. A pair with too little non-NaN OVERLAP is marked
    ``insufficient`` (NaN in |r|) rather than ``uncorrelated``; in the distance matrix it becomes the
    maximum distance (1.0), so an unmeasurable pair never clusters."""
    min_overlap = max(2, int(0.5 * len(sub)))
    corr = sub.corr(method=method, min_periods=min_overlap)
    absr = corr.abs()
    dist = 1.0 - absr.to_numpy()
    dist[~np.isfinite(dist)] = 1.0            # insufficient-overlap / NaN pairs → maximally distant
    np.fill_diagonal(dist, 0.0)
    dist = (dist + dist.T) / 2.0              # symmetrise against tiny float asymmetry
    return absr, dist


def _cluster(cols, dist, threshold):
    """Average-linkage hierarchical clustering, cut at distance ``1 - threshold``. Returns the groups with
    more than one member (a lone column is not redundant). Transitive by construction: A~B, B~C pulls C
    into {A, B} even when A~C sits just under the pairwise threshold."""
    from collections import defaultdict
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import squareform

    if len(cols) < 2:
        return []
    condensed = squareform(dist, checks=False)
    z = linkage(condensed, method='average')
    labels = fcluster(z, t=1.0 - float(threshold), criterion='distance')
    buckets = defaultdict(set)
    for col, lab in zip(cols, labels):
        buckets[int(lab)].add(col)
    return [frozenset(v) for v in buckets.values() if len(v) > 1]


def _choose_representative(group, sub):
    """The one column to keep from a redundant group, by a STATED rule (so the minimal set is
    reproducible): an ontology-defined column wins; else the most complete (fewest NaNs); else
    alphabetical. Returns ``(column, reason)``."""
    from pycat.utils.measurement_ontology import describe
    members = sorted(group)
    onto = [c for c in members if describe(c) is not None]
    if onto:
        return onto[0], 'ontology-defined (a named, interpretable quantity)'
    nan_counts = {c: int(pd.to_numeric(sub[c], errors='coerce').isna().sum()) for c in members}
    fewest = min(nan_counts.values())
    tied = sorted(c for c in members if nan_counts[c] == fewest)
    if len(tied) == 1:
        return tied[0], 'most complete (fewest NaNs)'
    return tied[0], 'alphabetical (deterministic tie-break)'


def analyze_redundancy(table, *, columns=None, method='spearman',
                       threshold=0.95) -> RedundancyReport:
    """Find groups of mutually-redundant feature columns in ``table`` — **reporting, never dropping**.

    ``columns`` restricts the analysis (default: every numeric column). ``method`` is 'spearman'
    (default — catches monotonic square-law redundancy Pearson misses) or 'pearson'. ``threshold`` is the
    |r| above which columns are treated as redundant (clustered transitively at distance ``1 - threshold``).
    The result is **specific to this table** — see the module docstring. The input table is never modified.
    """
    table = pd.DataFrame(table)
    cols = _numeric_columns(table, columns)
    kept, excluded = _partition_analysable(table, cols)
    if len(kept) < 2:
        return RedundancyReport(correlation=pd.DataFrame(), groups=[], representatives={},
                                dropped=(), method=method, threshold=float(threshold),
                                excluded=excluded)

    sub = table[kept].apply(pd.to_numeric, errors='coerce')
    absr, dist = _correlation_distance(sub, method)
    groups = _cluster(kept, dist, threshold)

    representatives, dropped = {}, []
    for g in groups:
        rep, reason = _choose_representative(g, sub)
        representatives[rep] = reason
        dropped.extend(sorted(m for m in g if m != rep))

    return RedundancyReport(correlation=absr, groups=groups, representatives=representatives,
                            dropped=tuple(dropped), method=method, threshold=float(threshold),
                            excluded=excluded)


def minimal_feature_set(report: RedundancyReport) -> list:
    """The opt-in minimal non-redundant set: every analysed column that a redundant group did not make
    droppable — i.e. one representative per group plus every ungrouped column, in the table's column
    order. Never drops an ungrouped column. Constant/insufficient columns (``report.excluded``) are a
    separate category and are not included here."""
    dropped = set(report.dropped)
    return [c for c in report.correlation.columns if c not in dropped]
