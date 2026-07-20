# Claude Code spec — Feature redundancy analysis

> **✅ STATUS — DONE, shipped in 1.6.177.** `toolbox/feature_redundancy.py` — `RedundancyReport`,
> `analyze_redundancy` (Spearman default, transitive average-linkage clustering, ontology-preferred
> representative with recorded reason, constant/NaN exclusion, dataset-specific), and opt-in
> `minimal_feature_set`; report-never-drop and no-mutation pinned. `tests/test_feature_redundancy.py`
> (known-duplicate grouping, cry-wolf, transitive clustering, Spearman-vs-Pearson square law, ontology
> representative, constant exclusion, minimal set, no-mutation contract). The prerequisite for the Feature
> Explorer's correlated-with field.

**Date:** 2026-07-20 · **Target tree:** 1.6.171 · Verified against the 1.6.171 tree. The smallest of
the "measurement platform" cluster and a prerequisite for the Feature Explorer. Pure analysis on the
output table; no new measurements, no UI-critical path. Additive.

## The gap (verified)
PyCAT emits wide feature tables — `regionprops` geometry plus custom condensate/partition/intensity
columns. Many of these are **near-duplicates**: `area`, `convex_area`, `equivalent_diameter`, and
`major_axis_length` all track object size; several intensity columns co-vary almost perfectly. Nothing
reports this. Verified: no collinearity/VIF/correlation-pruning of the emitted descriptors exists (grep
for `redundan`/`collinear`/`VIF` finds only incidental mentions, no module).

Why it matters:
- A user feeding 40 columns into a downstream classifier or PCA is silently giving four copies of
  "size" four times the weight of a unique feature.
- The Feature Explorer (next spec) wants to show, per measurement, *"correlated with: area,
  convex_area (r>0.98)"* — that needs this computation to exist first.
- It is a rigor point for the manuscript: reporting a **minimal non-redundant feature set** is
  something reviewers respect and most tools do not offer.

## Design — `toolbox/feature_redundancy.py`
```python
@dataclass(frozen=True)
class RedundancyReport:
    correlation: pd.DataFrame          # the full |r| matrix (or a chosen method)
    groups: list[frozenset[str]]       # clusters of mutually-redundant columns
    representatives: dict[str, str]    # group -> the one column to keep
    dropped: tuple[str, ...]           # columns a minimal set could omit
    method: str                        # 'pearson' | 'spearman'
    threshold: float

def analyze_redundancy(table, *, columns=None, method='spearman',
                       threshold=0.95) -> RedundancyReport
def minimal_feature_set(report) -> list[str]
```

### The design decisions that make it honest
1. **Spearman by default, not Pearson.** Feature relationships here are often monotonic but not linear
   (area vs diameter is a square-law); Pearson would understate the redundancy. Offer both; default to
   the one that tells the truth about this data.
2. **Cluster, don't just threshold pairwise.** Redundancy is transitive-ish: if A~B and B~C, all three
   belong in one group even if A~C is just under threshold. Use correlation-distance hierarchical
   clustering with a stated linkage, not a naive pairwise drop that depends on column order.
3. **The representative is chosen, not arbitrary.** Within a redundant group, keep the column that is
   (a) in the measurement ontology (a defined, interpretable quantity beats a derived one), else (b)
   the most complete (fewest NaNs), else (c) alphabetical for determinism. Record *why* each
   representative was chosen — an arbitrary pick would make the "minimal set" non-reproducible.
4. **Report, never auto-drop.** `analyze_redundancy` returns what *could* be dropped;
   `minimal_feature_set` is opt-in. The analysis must never silently remove columns from a user's
   table — that is the same silent-mutation failure mode the filter-sensitivity programme exists to
   catch.

## Handling the traps
- **A constant or near-constant column** has undefined correlation — exclude it with a stated reason
  (it is trivially redundant but for a different reason; flag separately).
- **NaN-heavy columns** distort correlation; require a minimum non-NaN overlap (say 50%) to compute a
  pair, and mark pairs that fell below it as `insufficient overlap` rather than `uncorrelated`.
- **Correlation is not causation or identity** — two independent measurements can correlate on a
  particular dataset by coincidence. So the report is dataset-specific and must say so: *"redundant on
  THIS table"*, not *"always redundant"*. Do not bake a dataset's grouping into a global default.

## Tests (`core`, synthetic)
- A table with a known duplicate (a column equal to `2*area`) groups the two together and keeps one.
- A table of independent random columns produces no groups (the cry-wolf test).
- Transitive clustering: A=B, B=C, A~C just under threshold → all three in one group.
- Representative selection prefers an ontology-defined column over a derived one.
- `minimal_feature_set` returns exactly one column per group and never drops an ungrouped column.
- The analysis leaves the input table unmodified (contract test).
- Spearman catches a square-law relationship that Pearson at the same threshold misses.

## Steps
1. `toolbox/feature_redundancy.py` — `RedundancyReport`, `analyze_redundancy`, `minimal_feature_set`.
2. Clustering + representative selection with recorded reasons.
3. Constant/NaN handling with stated exclusions.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Redundant feature groups are detected by clustering (not order-dependent pairwise dropping).
- Representatives are chosen by a stated rule preferring ontology-defined columns.
- A minimal non-redundant set is available opt-in; the analysis never mutates the table.
- Constant/NaN-heavy columns are handled with stated reasons.
- Results are labelled dataset-specific.
- Full `pytest -m core` green.

## Cautions
- **Report, never auto-drop.** Silent column removal is a data-mutation failure; keep it opt-in.
- **Spearman default** — Pearson understates the square-law redundancies common in morphometrics.
- Cluster transitively; a pairwise drop is order-dependent and non-reproducible.
- Label the report dataset-specific — redundancy on one table is not a universal fact.
- Do not build the Feature Explorer UI here; this is the computation it will consume.
