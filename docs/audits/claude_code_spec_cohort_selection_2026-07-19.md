# Claude Code spec — Cohort & typed selection targets

> **✅ STATUS — DONE.** Parts A/B (the `Cohort` target + `select_cohort` + `SelectionState.cohort`) and the
> comparative box/violin group emitter shipped in **1.6.151**. The two deferred emitters — histogram bin
> and aggregate row — shipped in **1.6.170** as `utils/cohort_targets.py` (`bin_cohort`,
> `aggregate_cohort`, `attach_histogram_brushing`, `select_aggregate_row`, `cohort_dock_label`): pure,
> GUI-free membership logic (bin membership matches the drawn range exactly, last bin closed;
> aggregate row = the contributing set with "summarizes N objects") that rides the real `SelectionService`
> — `select_cohort` fills `selected`, so the overlay highlights every member for free, and selection≠filter
> is pinned. `tests/test_cohort_targets.py`. These are the reusable emitters the shipped comparative case
> established; attaching them to a live brushable histogram / aggregate-table dock is follow-on when those
> surfaces are built (none exists in the tree today — the emitters are the increment the spec asked to land
> first: *"land bins/groups/aggregates first"*).

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. The top item on
the deferred-interaction roadmap (`roadmap_interaction_layer_deferred_2026-07-17.md`, recommended
pickup order #1: *"unlocks histogram/box/heat-map brushing and makes aggregates honest — the most
scientific value"*). Its prerequisites have all landed. Additive; extends the selection model rather
than replacing it.

## The gap (verified)
`SelectionState` holds `selected` / `hovered` / `pinned` as **sets of entity-id strings**. There is no
`cohort`, no `EntitySelection(kind='query')`, and no typed target — grep for `cohort` and
`EntitySelection` in `selection_service.py` returns nothing.

Consequences, all real today:
- **A histogram bin cannot be selected honestly.** A bar represents a *set* of entities whose metric
  falls in a range. With only entity-id sets, clicking a bar either selects nothing or would have to
  enumerate members with no record that they form a group.
- **Box/violin groups have the same problem** — and comparative-phenotyping increment 3 has now
  shipped exactly these plots, so the need is concrete rather than hypothetical.
- **Aggregate rows resolve to one arbitrary member.** A per-cell mean or population fit summarizes
  many objects; selecting it should highlight *all* contributors and say so, not silently pick one.
  Brushing increment 5 specced a basic `kind="query"` for this; the general version is this spec.
- **Heat-map cells** may refer to an entity, a row, a column variable, or a rectangle — none
  expressible today.

## The design
### Part A — a typed selection target
Extend the model so a selection can be a **cohort** with a stated definition, not just an id set:
```python
@dataclass(frozen=True)
class Cohort:
    members: frozenset[str]          # entity ids
    definition: str                  # human-readable: "area ∈ [12.0, 18.0) µm²", "genotype=WT"
    kind: str                        # 'bin' | 'group' | 'aggregate' | 'filter'
    source_view: str
```
`SelectionState` gains `cohort: Cohort | None` alongside the existing fields. **Additive** — every
existing consumer that reads `selected`/`hovered`/`pinned` keeps working unchanged.

Crucially the cohort carries its **definition**, so a view can say *"42 objects, area ∈ [12, 18) µm²"*
rather than showing an anonymous highlight. That is the honesty requirement: a user must be able to
see *why* those objects are grouped.

### Part B — cohort commands
`select_cohort(cohort, source)`, and `clear` clears it alongside `selected`/`hovered` while preserving
`pinned` (matching existing semantics). Echo-suppression, generation counting, and the deferred lane
work unchanged — cohorts ride the existing service.

### Part C — adapters emit and render cohorts
- **Histogram**: clicking a bin emits a cohort of the entities in that range, with the range as the
  definition. Do **not** treat the rectangle as an entity.
- **Box/violin (comparative figures)**: clicking a group emits a cohort for that condition.
- **Aggregate table rows**: emit the cohort of contributing objects; the dock states
  *"summarizes N objects"* and offers navigate-to-parent — never resolves to one arbitrary member.
- **Image/labels overlay**: highlight *all* cohort members (the increment-4 overlay artist already
  supports k selected points — reuse it, do not re-colour N objects).

Adapters that do not understand cohorts must **degrade gracefully** — ignore the cohort field, keep
rendering `selected`. The `SelectionView` contract tests must confirm this so a cohort selection can
never break an un-updated view.

### Part D — what this is NOT
**A cohort is not a filter.** Selection = transient attention; filtering = the active analytical
population. A cohort selection must never mutate the DataFrame or change which objects are analysed.
The FilterStore remains deferred and separate; note the boundary in the module docstring so the two
do not blur later.

## Tests
- Cohort round-trip: emit → state carries members + definition + kind → subscriber receives it.
- **Degradation**: an adapter that ignores `cohort` still works (contract-suite case).
- Histogram bin → cohort membership matches the bin's true range (compute independently, assert
  equality) — the test that the grouping is *correct*, not just non-empty.
- Aggregate row → cohort equals the contributing set; the reported count matches.
- `clear` drops cohort + selected + hovered, keeps pinned.
- Echo suppression: the emitting view does not receive its own cohort.
- A cohort selection leaves the underlying DataFrame untouched (the selection≠filter guarantee).

## Steps
1. `Cohort` + `SelectionState.cohort` (additive) + `select_cohort` command.
2. Extend the `SelectionView` contract with the graceful-degradation case; verify existing adapters.
3. Histogram bin → cohort.
4. Comparative-figure group → cohort (box/violin from increment 3).
5. Aggregate row → cohort, with "summarizes N objects" in the dock.
6. Overlay renders all cohort members via the existing overlay artist.
7. Full `pytest -m core` green (complexity budget).
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (typed cohort selection;
   histogram bins, comparative groups and aggregate rows now select honestly).

## Definition of done
- A selection can be a cohort carrying members, a human-readable definition, and a kind.
- Histogram bins, comparative-figure groups, and aggregate rows emit cohorts; the overlay highlights
  all members; the dock states how many objects are summarized.
- Adapters that ignore cohorts still work (proven by the contract suite).
- Cohort selection never mutates data or the analytical population.
- Full `pytest -m core` green.

## Cautions
- **Additive only.** Existing consumers of `selected`/`hovered`/`pinned` must not need changes.
- **A cohort must carry its definition** — an anonymous group highlight is the black box this codebase
  avoids. The user must be able to see why those objects are grouped.
- **Selection ≠ filtering.** Do not let a cohort change the analysed population; that is the deferred
  FilterStore's job and conflating them is explicitly warned about in the deferred roadmap.
- Reuse the existing overlay artist for multi-member highlight — do not re-colour N objects (that is
  the O(N) pattern increment 4 removed).
- Do not build heat-map typed targets in this increment; land bins/groups/aggregates first.
