# Roadmap — Interaction layer: deferred ideas

**Date:** 2026-07-17 · Companion to `claude_code_spec_interaction_layer_2026-07-17.md`. That spec took
the immediately-buildable, verified gaps from an architecture review of PyCAT's brushing layer. This
document captures the ideas from that review that were **deliberately deferred** — good, but either
premature, larger than one increment, or dependent on the interaction-layer spec landing first. A
roadmap, not a spec; each item becomes its own spec when its turn comes.

## Sequencing context
```
brushing arc (inc 1–5)  ──►  interaction-layer spec  ──►  pyqtgraph backend
        [LANDED]              [next: state model,          [after: first
                               hit-testing, adapter         consumer of the
                               protocol]                    adapter contract]
                                      │
                                      └──►  the deferred items below
```
Most items here depend on the adapter contract + `SelectionState` from the interaction-layer spec.
Do not start them before it lands.

---

## 1. Selection vs Filtering — separate stores (review §9)
**The idea:** brushing and filtering answer different questions and should not share machinery.
```
Selection: which entities am I examining?
Filter:    which entities are in the active analytical population?
```
So a plot can simultaneously show: filtered population (low emphasis), selected (strong), pinned
(distinct persistent style), excluded (ghosted/hidden). Clicking a plot must NOT mutate the DataFrame
or rebuild the active subset unless the user explicitly invokes a filter action — the review suspects
selection/filter entanglement is a source of lag.

**Why deferred:** PyCAT has no `FilterStore` today, and filtering currently happens inside individual
analyses rather than as shared state. Introducing a store is a real design increment (what IS the
active population? per-plot or per-session? does it persist to the consolidated table?) and it
interacts with the comparative-phenotyping work (a "condition" is arguably a filter). Worth doing —
but as its own spec, after the selection state model exists to contrast with.

**Prerequisite:** interaction-layer spec (needs `SelectionState` to define the boundary against).
**Also relates to:** comparative-phenotyping increment 2 (the consolidated table's grouping/filtering).

## 2. Full `ViewCoordinator` lifecycle (review §11)
**The idea:** a coordinator owning registration/lifecycle — unique view IDs, guaranteed callback
disconnection on close, matplotlib cid removal, Qt signal disconnection, no retained stale adapters,
and new views immediately receiving current state.

**Why deferred:** the interaction-layer spec already takes the two highest-value pieces (`close()` in
the `SelectionView` protocol + "on registration, push current state"). The remaining coordinator
machinery is only worth formalizing once there are several adapters registering and unregistering
dynamically — otherwise it's ceremony. Revisit when the adapter count grows (pyqtgraph, histogram,
heat map, metadata panel all landing).

**Prerequisite:** interaction-layer spec + ≥4 real adapters.

## 3. Per-plot-type interaction policies (review §8)
**The idea:** different visualizations have different semantic objects, so hit-testing should differ:
- **scatter** → nearest point; lasso; rectangle; shift/ctrl additive
- **MSD/curve** → nearest line segment (the interaction-layer spec builds this one)
- **histogram** → a bar is a COHORT: clicking a bin selects all entities whose metric falls in that
  range — not the rectangle as an entity
- **box/violin** → clicking a distribution selects a group/filter context; displayed points resolve to
  entities
- **heat map** → typed targets: one cell, a row entity, a column variable, or a rectangle of
  entities×variables

**Why deferred:** the MSD/curve case is the one that's broken today and is in the spec. The others are
new capability, and the histogram/heat-map cases require the selection model to support **typed,
set-valued targets** ("this selection is a cohort defined by a bin range", "this is a column
variable") — a real extension of `SelectionState` beyond entity id sets. Sequence: land the state
model, then extend it for cohort/typed selections, then add per-plot policies.

**Prerequisite:** interaction-layer spec; likely a `SelectionState` extension for cohort/typed targets.

## 4. Aggregate/cohort selections as first-class
**The idea:** related to §8 — a per-cell mean row or a population-fit summarizes many objects.
Selecting it should highlight ALL contributors and state "summarizes N objects", not resolve to one
arbitrary member.

**Why deferred:** brushing increment 5 specced a basic version (`EntitySelection(kind="query")`).
Making it general — cohorts from histogram bins, box-plot groups, filter predicates — is the same
typed-target extension as §3. Fold these together into one "cohort selection" spec.

## 5. Spatial index for hit-testing at scale (review §7)
**The idea:** for thousands of selectable lines, flatten segments, store bounding boxes, query an
R-tree/grid near the cursor, exact-distance only local candidates.

**Why deferred:** the review itself says PyCAT doesn't need this initially — at ~100 displayed curves a
direct scan is instantaneous, once per click. Build only if profiling shows hit-testing is slow with a
much larger displayed set. **Do not pre-optimize this.**

## 6. Generalize the interaction layer beyond VPT (review §19 phase 5)
**The idea:** use the same selection system for cells, nuclei, puncta, trajectories, droplets, ROIs,
colocalization pairs, FRAP regions, topology features, cluster assignments — at which point it stops
being "the VPT selection system" and becomes PyCAT's interaction layer.

**Why deferred:** this is the natural CONSEQUENCE of the adapter contract, not a separate build — each
new adapter is a small increment once the protocol and contract tests exist. Track it as an ongoing
direction: when a new analysis gets a plot, it gets an adapter. Worth a short spec only if a batch of
adapters is built at once.

## 7. Move widget-specific logic out of `vpt_ui.py` (review §19 phase 2)
**The idea:** `vpt_ui.py` should COMPOSE `TrackTableAdapter` / `NapariTrackAdapter` / `MSDPlotAdapter`,
not implement their internals.

**Why deferred:** it's a decomposition of a 2174-line god-file — real value (the engineering audit
flagged `vpt_ui.py` among the large files), but it's refactoring rather than capability, and it's much
safer once the adapter contract tests exist to prove behaviour is preserved. Natural follow-on to the
interaction-layer spec; also serves the complexity ratchet.

**Prerequisite:** interaction-layer spec (the contract tests are the safety net for the refactor).

---

## Recommended order when picking these up
1. **Cohort/typed selection targets** (§3 + §4 merged) — unlocks histogram/box/heat-map brushing and
   makes aggregates honest. The most scientific value.
2. **`vpt_ui.py` adapter decomposition** (§7) — safe once contract tests exist; serves the ratchet.
3. **FilterStore / selection-vs-filter separation** (§1) — real design work; coordinate with
   comparative-phenotyping.
4. **ViewCoordinator lifecycle** (§2) — when the adapter count justifies it.
5. **Generalize to all object types** (§6) — ongoing, incremental, no big-bang.
6. **Spatial index** (§5) — only if profiling demands it.

## Standing caution
The review proposes a fresh `pycat/interaction/` package tree. PyCAT already has `entity_ref.py` and
`selection_service.py` carrying that responsibility. **Extend in place.** A parallel implementation is
the duplicate-registry tax the engineering audit called out — the single most important thing to avoid
while working through this list.
