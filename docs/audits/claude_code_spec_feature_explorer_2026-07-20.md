# Claude Code spec — Feature Explorer: the unifying interface over the measurement layer

> **✅ STATUS — DONE, shipped in 1.6.178.** `utils/feature_explorer.py` — `FeatureCard` +
> `build_feature_card`, aggregating definition/units/caveats (ontology), reliability grade + reasons,
> stability verdict, correlated-with (redundancy report), provenance summary, and the value distribution,
> each degrading to `None` per missing source; recomputes nothing, mutates nothing (all pinned in
> `tests/test_feature_explorer.py`). The thin dock (`ui/feature_explorer_dock.py`) lays out the searchable
> column list + card panel and wires the mini-histogram through the 1.6.170 cohort emitter
> (`attach_histogram_brushing`) so a bin click selects those objects — AST-verified; needs an in-app
> glance for the live rendering (viewer-coupled). The assembler is the deliverable; the dock is the shell.

**Date:** 2026-07-20 · **Target tree:** 1.6.171 · Verified against the 1.6.171 tree. The roadmap's
unifying interface — *"an interactive measurement browser instead of a flat spreadsheet."* Deferred
until its data sources existed. **They now all do**, which is what makes this an assembly job rather
than new science.

## Why now (verified prerequisites)
The Feature Explorer is a *view* over four things PyCAT already computes:

| what it shows per measurement | source | present |
|---|---|---|
| definition, equation, units, caveats, reference | `measurement_ontology.py` | ✅ |
| parameter sensitivity / stability | `measurement_stability.py` | ✅ |
| reliability grade + why | `reliability.py` (MRI) | ✅ |
| correlated measurements | `feature_redundancy.py` | ← the companion spec |
| the value itself + provenance | `feature_provenance.py`, consolidated table | ✅ |

So the Explorer invents nothing — it is the single pane that makes the measurement platform legible.
Verified: no `feature_explorer`/`FeatureExplorer` exists today.

## What it is
For any measurement in a results table, a panel answering the questions a scientist actually has:
- **What is this?** — ontology definition, equation, units.
- **Can I trust it here?** — the MRI reliability grade with its decomposition (worst-first reasons).
- **How sensitive is it?** — the stability verdict (does it move when I nudge the threshold?).
- **What else moves with it?** — the redundancy group (correlated columns).
- **Where did it come from?** — the provenance chain.
- **What does it look like across my objects?** — a small distribution (histogram) with the
  cohort-selection hook already built (1.6.170), so clicking a bin selects those objects.

## Design — a read-mostly aggregator, Qt-thin
```python
@dataclass(frozen=True)
class FeatureCard:
    key: str
    definition: str | None       # ontology
    equation: str | None
    units: str | None
    caveats: tuple[str, ...]
    reliability: str | None      # MRI grade
    reliability_reasons: tuple[str, ...]
    stability: str | None        # 'stable'|'sensitive'|'unstable'|None
    correlated_with: tuple[str, ...]
    provenance_summary: str | None
    distribution: object | None  # binned counts for the mini-histogram

def build_feature_card(table, key, *, context) -> FeatureCard
```
`build_feature_card` **pulls from the existing modules** — it does not recompute. Each field degrades
to `None` when its source did not run for this measurement (an ungated field is honest; a fabricated
one is not — the same rule the whole platform follows). A card with only a definition is fine; it
says what it knows.

### The UI
A dock: left = searchable list of the table's columns (grouped by the ontology's feature families if
present, flat otherwise); right = the `FeatureCard` for the selected one. The mini-histogram reuses
the cohort-emitting histogram from 1.6.170 so selection flows to the viewer. Keep the Qt layer thin —
all content comes from `build_feature_card`, which is `core`-testable without a display.

### Scope discipline
- **One card assembler, tested headless.** The value is the aggregation logic; the dock is a shell.
- **Do not re-run analyses to fill a card.** If stability wasn't computed, the card shows "not
  assessed" with an offer to run it — it does not silently trigger a sweep.
- Start with the measurements that have the richest sources (partition/concentration/ΔG, size,
  intensity); every other column still gets a card, just a sparser one.

## Tests (`core`)
- `build_feature_card` for a measurement with all sources present returns every field populated.
- A measurement with no ontology entry returns `definition=None` but still fills value/distribution —
  partial cards work.
- Fields degrade to `None` per missing source; nothing is fabricated.
- The distribution binning matches an independent computation (the histogram is correct, not
  decorative).
- The correlated-with list matches the redundancy report for that column.
- Card assembly does not recompute or mutate anything (contract test).

## Steps
1. `utils/feature_explorer.py` — `FeatureCard` + `build_feature_card`, pulling from ontology,
   reliability, stability, redundancy, provenance.
2. Graceful degradation per missing source.
3. The dock: searchable column list + card panel + cohort-emitting mini-histogram.
4. Family grouping from the ontology when available.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Any table column yields a `FeatureCard` aggregating definition, reliability, stability, correlations,
  provenance, and distribution — from existing sources, recomputing nothing.
- Missing sources degrade to "not assessed", never fabricated.
- The dock lets a user browse columns and see the card; the mini-histogram emits cohort selections.
- Full `pytest -m core` green.

## Cautions
- **Aggregate, never recompute.** The Explorer is a lens; if a source didn't run, say so and offer to
  run it — do not silently trigger heavy analysis from a browse action.
- **Partial cards are correct.** A measurement with only a definition still gets a card; do not hide
  columns just because their metadata is sparse.
- Keep the Qt layer thin and the assembler `core`-testable — the logic is the deliverable.
- Depends on the feature-redundancy spec for the correlated-with field; land that first or gate the
  field on its presence.
- Do not fold Methods-section generation in here; that is a later synthesis over the same sources.
