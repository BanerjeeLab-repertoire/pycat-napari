"""**The Feature Explorer's card assembler — one legible pane over the whole measurement platform.**

For any column in a results table, `build_feature_card` answers the questions a scientist actually has —
*what is this? can I trust it here? how sensitive is it? what else moves with it? where did it come from?
what does it look like across my objects?* — by **aggregating the modules that already computed those
answers**, not by recomputing:

| card field | source |
|---|---|
| definition / equation / units / caveats | `measurement_ontology` |
| reliability grade + worst-first reasons  | `reliability` (the MRI) |
| stability verdict                        | `measurement_stability` |
| correlated-with                          | `feature_redundancy` |
| provenance summary                       | `feature_provenance` |
| distribution (mini-histogram)            | the column's own values |

**Aggregate, never recompute.** Each field is pulled from `context` — the results the caller already has
— and **degrades to `None` when its source did not run** for this measurement. A card with only a
definition, or only a distribution, is correct: it says what it knows and no more. Fabricating a field
whose source did not run is the failure this platform exists to avoid. The only thing computed here is the
distribution binning, which is a read of the data itself, not an analysis.

The assembler is Qt-free and `core`-testable; the dock (`ui/feature_explorer_dock.py`) is a thin shell
over it whose mini-histogram reuses the cohort-emitting histogram (1.6.170) so a bin click selects those
objects.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass(frozen=True)
class FeatureCard:
    """Everything the Explorer knows about one measurement, each field `None`/empty when its source did
    not run. Never fabricated — a sparse card is an honest card."""
    key: str
    definition: str | None = None
    equation: str | None = None
    units: str | None = None
    caveats: tuple = ()
    reliability: str | None = None           # MRI grade: 'high'|'moderate'|'low'|'unreliable'
    reliability_reasons: tuple = ()
    stability: str | None = None             # 'stable'|'sensitive'|'unstable'|'population-change'|...
    correlated_with: tuple = ()
    provenance_summary: str | None = None
    distribution: object | None = None       # dict(counts=..., edges=...) — the mini-histogram


def _lookup(source, key):
    """A context source may be a per-measurement mapping (``{key: result}``) or a single result object
    that already refers to this measurement. Return the entry for ``key`` from a mapping, else the object
    itself, else ``None``."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return source


def _correlated_with(redundancy, key):
    """The other columns in ``key``'s redundancy group (empty when it is not in a group, or no report was
    given). This is exactly what the report says is redundant WITH this column ON THIS TABLE."""
    if redundancy is None:
        return ()
    groups = getattr(redundancy, 'groups', None) or []
    for g in groups:
        if key in g:
            return tuple(sorted(c for c in g if c != key))
    return ()


def _distribution(table, key, bins):
    """Binned counts of the column's finite values — the mini-histogram, computed independently of any
    analysis (it is a read of the data). ``None`` when the column is absent, non-numeric, or empty."""
    if key not in getattr(table, 'columns', ()):
        return None
    s = pd.to_numeric(table[key], errors='coerce').to_numpy()
    s = s[np.isfinite(s)]
    if s.size == 0:
        return None
    counts, edges = np.histogram(s, bins=bins)
    return dict(counts=counts, edges=edges, n=int(s.size))


def build_feature_card(table, key, *, context=None, bins=20) -> FeatureCard:
    """Assemble the `FeatureCard` for measurement ``key`` from the ontology and whatever ran, in
    ``context`` — a dict with optional keys ``reliability`` / ``stability`` / ``redundancy`` /
    ``provenance``, each either a per-measurement mapping or a single result for this measurement.

    **Recomputes nothing** and **mutates nothing**. Every field degrades to ``None``/empty when its source
    is absent; only the distribution is derived here, from the column itself.
    """
    from pycat.utils.measurement_ontology import describe

    context = context or {}
    mdef = describe(key)

    rel = _lookup(context.get('reliability'), key)
    stab = _lookup(context.get('stability'), key)
    prov = _lookup(context.get('provenance'), key)

    provenance_summary = None
    if isinstance(prov, str):
        provenance_summary = prov                  # already a formatted summary — carry it through
    elif prov is not None:
        try:
            from pycat.utils.feature_provenance import describe_provenance
            provenance_summary = describe_provenance(prov)
        except Exception:      # broad-ok: provenance is an optional card field — never break the card
            provenance_summary = None

    return FeatureCard(
        key=key,
        definition=(mdef.definition if mdef else None),
        equation=(mdef.equation if mdef else None),
        units=(mdef.units if mdef else None),
        caveats=(tuple(mdef.caveats) if mdef else ()),
        reliability=getattr(rel, 'grade', None),
        reliability_reasons=tuple(getattr(rel, 'reasons', ()) or ()),
        stability=getattr(stab, 'verdict', None),
        correlated_with=_correlated_with(context.get('redundancy'), key),
        provenance_summary=provenance_summary,
        distribution=_distribution(table, key, bins),
    )
