> **✅ STATUS — DONE (chokepoint shipped in 1.6.196; Part C1 per-row frames shipped 1.6.188).**
> `EntitySpec` + a declaration registry (`register_entity_spec` / `entity_spec_for`) and the
> `finalize_entity_table` chokepoint live in `utils/entity_ref.py`; `operation_runner.execute` calls it
> automatically on a DataFrame result, driven by the operation captured from `operation_context` — so
> `operation_id` comes from the declaration, not a hard-coded string. The 3 manual sites (cell / puncta /
> region props) migrated to the declaration with byte-identical ids, and the top previously-unstamped
> producers (condensate, tracks, colocalized objects) now gain identity by declaration. `finalize` is
> idempotent (no double-stamp) and co-generates identity + location in one pass; per-row frames work.
> `tests/test_auto_identity_stamping.py` pins all of it. **Follow-on:** wiring the `entity_registry` at this
> same chokepoint (populate a record → id + registry entry) is the remaining `entity_registry`-spec item.

# Claude Code spec — Automatic entity-identity stamping via result finalization

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The audit's stated
next identity milestone: *"make stamping automatic through the operation/result infrastructure rather
than manually adding it to individual analysis functions."* Confirmed the problem — stamping reaches
only 3 of many object-producing paths. This closes the coverage gap by stamping at a chokepoint.

## Verified state
`stamp_entity_ids(` is called at exactly **3 sites**: `feature_analysis_tools.py` (cell + puncta) and
`label_and_mask_tools.py` (generic region props). Every other object-producing route — condensates,
droplets, beads, tracks, clusters, colocalized objects, topology objects, QC objects, spatial tables —
is **linked by row position** unless it happens to pass through one of those three. The code now labels
that degraded state honestly (good), but the goal is coverage, and manual stamping will never reach it:
every new analysis is one more forgotten `stamp_entity_ids` call away from silent row-position linking.

Also verified: `operation_id` is a **hard-coded string** (`operation_id='cell_analysis'` at
`feature_analysis_tools.py:511`), which the audit flags as a divergence risk between interactive and
batch routes.

## The fix — stamp at result finalization, not per analysis
`utils/operation_runner.py::execute` already marshals results through `on_result` on the main thread —
that is the single point every operation's output passes through. Make identity stamping happen there,
driven by the operation's declared metadata, so an analysis gets correct identity **by default** rather
than by remembering to ask.

### Part A — declare identity intent on the operation
An operation that produces an object table declares how its rows are identified — ideally on its
`OperationSpec` (the canonical operation registry the audit wants `operation_id` to come from):
```python
# on the OperationSpec / operation metadata
produces_entities: EntitySpec | None = EntitySpec(
    entity_type='condensate',
    label_column='label',
    parent_column='cell_label',   # None if top-level
    frame_column='frame',         # None if single-frame
)
```
`operation_id` comes from the spec itself — **not a hard-coded string** — which fixes the
interactive/batch divergence the audit names.

### Part B — finalize stamps automatically
In the result-finalization path, when a result is a DataFrame and the operation declares
`produces_entities`, call the stamping logic with the operation's real `operation_id`, the declared
columns, and the dataset id. An operation that declares nothing is left untouched (and, as today,
honestly labelled row-position-linked).

### Part C — fix the three under-specified API points the audit named
1. **Per-row frames.** The current API takes one scalar `frame`; a multi-frame table stamps every row
   with `reference_frame`, which is wrong. Add `frame_column='frame'` so identity derives frame
   per-row; reserve scalar `frame` for genuinely single-frame tables. (Directly the audit's §2 point.)
2. **operation_id from the spec** — as above, no hard-coded strings.
3. **Identity + location generated together.** Stamp the opaque `_pycat_entity_id` and the resolution
   columns (bbox, layer id, frame, source) in ONE pass from one entity record, so a row can never carry
   correct identity with stale location. (The audit's "identity and resolution can diverge" point.)

## Scope discipline
- **Increment 1 = the chokepoint + migrating the existing 3 sites to it + declaring specs for the
  highest-value unstamped producers** (condensates, tracks, colocalized objects). Not all of them at
  once — but the mechanism plus the top few, proving coverage grows by *declaration* not by *new
  stamping calls*.
- Leave the manual `stamp_entity_ids` available for the rare path that needs it, but the default
  becomes automatic.
- **Do not** change what an entity id *is* (the `EntityKey` scheme is validated) — only *where and how
  reliably* it gets applied.

## Tests (`core`)
- An operation declaring `produces_entities` gets its result stamped automatically at finalization,
  with the operation's real `operation_id` (not a literal string).
- **Per-row frames:** a multi-frame table stamps each row with its own frame, not a single reference
  frame.
- Identity + location are produced together — every stamped row has both a valid `_pycat_entity_id`
  and consistent resolution columns (no id-without-location rows).
- An operation declaring nothing is left unstamped and honestly labelled (unchanged behaviour).
- The three migrated sites (cell, puncta, region props) produce identical identities to before
  (equivalence — the migration changes the route, not the result).
- A previously-unstamped producer (e.g. condensates) now receives identity once its spec is declared.

## Steps
1. `EntitySpec` + `produces_entities` on the operation metadata; `operation_id` sourced from the spec.
2. Stamp in the result-finalization path (`operation_runner` on_result) when declared.
3. Add `frame_column` support; generate identity + location in one pass.
4. Migrate the 3 existing sites to declaration; assert identical output.
5. Declare specs for the top unstamped producers (condensates, tracks, colocalized objects).
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (identity stamping now automatic
   via finalization; operation_id from spec; per-row frames; identity+location co-generated).

## Definition of done
- Object-table results are stamped automatically at finalization when the operation declares entities.
- `operation_id` comes from the operation spec, not hard-coded strings.
- Per-row frame identity works; identity and location are generated together.
- The 3 existing sites migrate with identical output; the top unstamped producers now gain identity.
- Full `pytest -m core` green.

## Cautions
- **Don't change the entity-id scheme** — it is validated. Change only coverage and reliability.
- **operation_id from the spec** — hard-coded strings are exactly the interactive/batch divergence the
  audit warns about.
- Identity and location must be co-generated — an id with stale location is a subtle correctness bug.
- Declaring entities is opt-in per operation; an undeclared operation stays honestly row-linked, not
  silently wrong.
- This is coverage increment 1 — the mechanism plus the top producers, not every producer at once.
