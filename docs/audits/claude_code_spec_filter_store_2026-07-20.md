# Claude Code spec — FilterStore: separate the active population from selection

> **✅ STATUS — core DONE, shipped in 1.6.179.** `utils/filter_store.py` — `Filter` (predicate + members +
> source + active) and `FilterStore` (set_filter / clear / population / is_active + its OWN change channel,
> reading/writing NO selection state), plus `resolve_render_tier` (the four-tier emphasis resolver
> adapters call), `filtered_result_note` (a filtered result records its predicate + counts) and
> `filter_table` (restrict to `population()`, never silently). **The isolation invariant is pinned in both
> directions** (`tests/test_filter_store.py`): a filter change leaves `SelectionState` untouched and a
> selection change leaves the filter untouched, plus the grep-level no-implicit-filtering contract (no
> selection handler calls `set_filter`), the population API, the four tiers, and the no-mutation of inputs.
> Follow-on (the thin UI integration, a separate broad surface): wiring `resolve_render_tier` into each
> live entity adapter's styling and an explicit filter-control widget. The mechanism they consume is
> delivered and tested.

**Date:** 2026-07-20 · **Target tree:** 1.6.171 · Verified against the 1.6.171 tree. The
interaction-layer roadmap's item #1, whose stated prerequisite has now landed: `SelectionState` exists
(and cohort selection shipped in 1.6.170), so filtering can finally be defined *against* selection
rather than tangled with it.

## The distinction (from the deferred roadmap, verbatim intent)
```
Selection: which entities am I examining?     (transient attention)
Filter:    which entities are in the active analytical population?  (the analysed set)
```
A plot should be able to show, simultaneously: the **filtered population** (low emphasis), the
**selected** (strong), the **pinned** (persistent distinct style), and the **excluded** (ghosted or
hidden). Verified: `SelectionState` handles selected/hovered/pinned/cohort — but there is **no
`FilterStore`**, and filtering currently happens *inside individual analyses* rather than as shared
state. The roadmap's suspicion is that this entanglement is a source of lag and of the "clicking a
plot changed my results" surprise.

## Why it is now buildable
The prerequisite was "the selection state model must exist to contrast against." It does. And cohort
selection (1.6.170) already introduced the idea of a *defined subset carrying its definition* — a
filter is the durable, analysis-affecting sibling of a cohort. The concepts are adjacent; this spec
makes the boundary explicit and enforced.

## Design — `utils/filter_store.py`
```python
@dataclass(frozen=True)
class Filter:
    predicate: str              # human-readable: 'area > 12 µm²', 'genotype == WT'
    members: frozenset[str]     # resolved entity ids in the active population
    source: str                 # which control/action created it
    active: bool

class FilterStore:
    def set_filter(self, f: Filter) -> None
    def clear(self) -> None
    def population(self) -> frozenset[str] | None   # None = no filter = everything
    def is_active(self) -> bool
    # emits a change signal analogous to SelectionState's, on its OWN channel
```

### The load-bearing invariants
1. **Selection never mutates the filter, and filtering never mutates the selection.** They are
   separate stores on separate channels. Clicking a plot point changes `SelectionState`; it must
   **not** touch `FilterStore`. This is the entire point — encode it as a test, not just a convention.
2. **A filter is explicit and named.** It carries its `predicate` string so the UI can always say
   *"showing 214 of 1,032 objects: area > 12 µm²"*. An anonymous filtered set is the black box this
   codebase rejects.
3. **Filtering is opt-in and reversible.** Applying a filter changes the analysed population; clearing
   it restores everything. No plot interaction may apply a filter implicitly — only an explicit filter
   action.
4. **The filter is the population the analysis sees.** When active, downstream aggregation (means,
   comparative figures, exports) operates on `population()`, and the result **records that a filter was
   applied** (predicate + count) so a filtered result is never mistaken for an unfiltered one.

### Relationship to comparative phenotyping
A "condition" in comparative phenotyping is arguably a filter. **Do not merge them in this spec** —
keep `FilterStore` as the general mechanism and let comparative phenotyping remain its own grouping
for now. Note the relationship in the docstring so a future increment can unify them deliberately
rather than by accident.

## Rendering — four visual tiers
Adapters that render entities gain a four-tier style, in emphasis order:
- **excluded** (not in the active population): ghosted or hidden per a display toggle;
- **filtered-in** (in population, not selected): low emphasis, the baseline;
- **selected**: strong;
- **pinned**: distinct persistent style.
Adapters that do not understand filtering must **degrade gracefully** — render everything as before,
ignoring the filter. The `SelectionView` contract suite already tests graceful degradation; extend it
with a filter-unaware case.

## Tests (`core`)
- Setting a filter changes `population()`; clearing restores `None` (everything).
- **The isolation invariant:** a selection change leaves `FilterStore` untouched, and a filter change
  leaves `SelectionState` untouched. Two tests, both directions. This is the spec's reason to exist.
- A filtered aggregation operates on the population and **records the predicate + count** in the
  result.
- An adapter that ignores filtering still renders (graceful-degradation contract case).
- The four tiers resolve correctly for an entity that is variously excluded / filtered-in / selected /
  pinned.
- No plot interaction path calls `set_filter` (grep-level assertion in the test that selection
  handlers don't import/telegraph to the FilterStore).

## Steps
1. `utils/filter_store.py` — `Filter` + `FilterStore` with its own change channel.
2. Wire downstream aggregation to honour `population()` when active, and record the predicate on
   results.
3. Four-tier rendering in the entity adapters; extend the contract suite with a filter-unaware case.
4. An explicit filter control (build a predicate → set the filter); no implicit application.
5. Tests above, especially the two isolation invariants.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- `FilterStore` holds the active analytical population as explicit, named, reversible state, separate
  from `SelectionState`.
- Selection and filtering provably do not mutate each other.
- Filtered results record the predicate and count; an unfiltered result is never confused with a
  filtered one.
- Four-tier rendering works; filter-unaware adapters degrade gracefully.
- No plot interaction applies a filter implicitly.
- Full `pytest -m core` green.

## Cautions
- **The isolation invariant is the whole spec.** If selection and filtering can mutate each other, the
  separation has failed regardless of what else works — test both directions explicitly.
- **No implicit filtering.** A brush/click is attention, not a population change; only an explicit
  filter action changes the analysed set.
- A filter must carry its predicate — an anonymous active subset is a black box.
- Do not merge "condition" (comparative phenotyping) into `FilterStore` here; note the relationship,
  unify later on purpose.
- A filtered result must announce itself; a mean over a filtered population that looks like a mean
  over everything is a reproducibility hazard.
