# Claude Code spec — OperationSpec, increment 1: **validate-first** (no generation yet)

> **✅ STATUS — DONE, shipped in 1.6.68** (git commit dd3dd47; predates the current CHANGELOG, which starts
> at 1.6.103). `src/pycat/navigator/operation_spec.py` provides the frozen `OperationSpec` +
> `iter_operation_specs()` — a read-only typed view over `tag_registry._OPERATIONS` (no new source of
> truth). The validate-first mechanism is the drift-guard test
> `tests/navigator/test_operation_spec_matches_catalog.py` (coverage / no-stale / field-fidelity), with a
> `regenerate_operation_catalog()` path (`op_catalog.py`). Increments 2–5 (1.6.126–1.6.129) built on it and
> added `inputs`/`requirements`/runnability. Every increment-1 Definition-of-done item met.

**Date:** 2026-07-15 · **Target tree:** 1.6.63+ (assumes file-I/O cleanup + `lazy_sources.py` have
landed first). Verified against the 1.6.63 tree. Does NOT touch `file_io.py` — no collision with the
loader work.

## The strategy (why validate-first)
An external architecture audit's headline: *one operation's identity is separately encoded in the UI,
batch system, Navigator, tag system, and the science function* — so adding a feature means editing ~7
parallel systems that can drift. The proposed cure is a canonical `OperationSpec` that the subsystems
are generated from. **We are NOT doing the big-bang rewrite** (high risk, pre-manuscript). Increment 1
is the safe foundation: **define the spec, and make DRIFT a test failure — generate nothing yet.**
Once the spec provably describes the live operations with zero drift, flipping any one subsystem from
"validate against spec" to "generate from spec" later becomes a proven-safe change.

**Scope of increment 1 (pinned decision):** the tag-registry ↔ Navigator-op-catalog overlap ONLY.
Leave the batch `_STEP_MAP` out of this increment — its vocabulary is a DIFFERENT granularity
(coarse workflow-stage names like `cellpose_segmentation`) than the fine-grained `@tags_layer` op ids
(`clahe`, `multi_otsu`), and reconciling the two is a later increment. Do not touch batch here.

## What already exists (verified in 1.6.63 — build ON this, don't replace it)
- `@tags_layer(op, role, summary, target=, produces=, aliases=)`
  (`utils/tag_registry.py:135`) decorates **69** operations; it calls `register_operation(...)` which
  stores a per-op dict in `_OPERATIONS`: `{op, role, summary, target, produces, aliases,
  registered_by}` (`tag_registry.py:126`). It also stamps `fn.__pycat_op__`. **This is the seed
  OperationSpec — the operation's identity, tag transform, and implementing function already linked in
  one place.**
- The Navigator op-catalog `src/pycat/navigator/data/operation_catalog.json` is a COMMITTED SNAPSHOT
  described as *"extracted from the real `@tags_layer` decorators"* (`op_catalog.py:20`). It currently
  holds **79** op entries (69 from `@tags_layer` + ~10 from `_measure_ops()`, which are measure/interpret
  ops NOT declared via `@tags_layer` — `op_catalog.py:159`). `build_operation_registry` reads this JSON
  plus `_measure_ops()` (`op_catalog.py:365–387`).
- **The gap the audit warns about is LIVE here:** the catalog is a snapshot with **no regeneration
  script and no test that it still matches the live decorators.** The existing test
  (`tests/navigator/test_operations.py::test_catalog_has_real_layer_operations`) only checks a
  hardcoded handful (`cellpose`, `clahe`) are present — it CANNOT catch a decorator that was added,
  removed, or had its role/target changed without the JSON being regenerated.

## Task
### Part A — the typed `OperationSpec` (a view, not a new registry)
Add `src/pycat/navigator/operation_spec.py`:
```python
@dataclass(frozen=True)
class OperationSpec:
    id: str                      # the @tags_layer op id (== __pycat_op__)
    role: str                    # what it produces (ROLES)
    summary: str
    target: str | None           # what it operates on (TARGETS)
    produces: str                # output role (defaults to role)
    aliases: tuple[str, ...]
    registered_by: str | None    # module.qualname of the implementing fn
    # increment-1 stops here. Later increments ADD (all Optional, default None):
    #   inputs, parameters, contexts, batchable, requirements — do NOT add them now.
```
Plus a builder `iter_operation_specs() -> list[OperationSpec]` that reads the LIVE `_OPERATIONS`
registry (import the tag modules so decorators have run — mirror how `op_catalog` triggers
registration) and yields one `OperationSpec` per registered `@tags_layer` op. This is a typed view
over `_OPERATIONS`; it introduces NO new source of truth. (Do NOT include `_measure_ops` here —
those aren't `@tags_layer` ops; increment 1 is the decorator set only.)

### Part B — the drift guard (the actual value of increment 1)
Add `tests/navigator/test_operation_spec_matches_catalog.py` (mark `core`). Assert the committed
`operation_catalog.json` is FAITHFUL to the live `@tags_layer` decorators:
1. **Coverage:** every live `@tags_layer` op (from `iter_operation_specs()`) appears in the catalog.
   A decorator added without regenerating the JSON → test fails, naming the missing op.
2. **No stale layer-ops:** every catalog entry that is a *layer op* (i.e. not one of the
   `_measure_ops()` set — compute that set and exclude it) corresponds to a live decorator. A decorator
   removed but left in the JSON → test fails, naming the stale op. (Measure-ops are legitimately
   JSON-only; exclude them explicitly so the test doesn't false-positive on the ~10 of them.)
3. **Field fidelity:** for every live op, the catalog's `role`/`produces`/`target` match the
   decorator's. A decorator whose `target` changed without regeneration → test fails, naming the field.

This replaces the weak hardcoded-handful check with a complete, self-maintaining drift guard. Keep the
old test or fold its two assertions in — don't lose the `cellpose`/`clahe` explicit checks.

### Part C — the regeneration path (so a failing guard is FIXABLE, not just loud)
A drift guard that can't be satisfied is a trap. Provide the one command that regenerates the snapshot
from the live decorators: add/confirm `op_catalog.py` has (or add to it) a
`regenerate_operation_catalog(path=None)` that walks `iter_operation_specs()` + `_measure_ops()` and
writes `operation_catalog.json` deterministically (sorted keys, stable order). Expose it as
`python -m pycat.navigator.op_catalog --regenerate` (or a tiny `scripts/regen_op_catalog.py`). The test
failure message must name this command, so the fix for legitimate drift is "run the regen, commit the
JSON." Regenerate ONCE as part of this task so the guard starts GREEN (this also resolves whatever the
current 79-vs-69 discrepancy is — confirm the 10 extras are all `_measure_ops`, not stale layer-ops;
if any ARE stale, the regen removes them and that's a real cleanup).

## Definition of done
- `operation_spec.py` with `OperationSpec` (increment-1 fields only) + `iter_operation_specs()`.
- `test_operation_spec_matches_catalog.py` (core) proving coverage + no-stale + field-fidelity; GREEN
  after a one-time regenerate.
- `regenerate_operation_catalog(...)` + a `--regenerate` entry point; the test names it on failure.
- The 79-vs-69 discrepancy explained (measure-ops) or resolved (stale ops removed).
- Nothing generated INTO the UI/batch/navigator yet — this increment only makes drift catchable.
- Shipped: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG; the CHANGELOG entry
  states this is the validate-first foundation of the OperationSpec consolidation, batch deliberately
  excluded.

## Cautions
- **Additive only.** Do NOT change `@tags_layer`, `register_operation`, `_OPERATIONS`, or the
  catalog's consumers. `OperationSpec` is a READ view + a test; it must not alter any runtime behaviour.
- Increment-1 `OperationSpec` carries ONLY the fields `@tags_layer` already declares. Do NOT add
  `inputs`/`parameters`/`batchable` speculatively — unpopulated fields that nothing validates are
  exactly the drift this effort exists to prevent. They arrive in a later increment WITH their
  validation.
- The `core` test must run headlessly — importing the tag modules to populate `_OPERATIONS` must not
  drag in Qt/napari. If a `@tags_layer`-bearing module imports Qt at module scope, import just the
  registry-populating modules, or xfail-guard that module, and note it (it's a `test_headless_science`
  cousin).
- Do NOT reconcile batch `_STEP_MAP` here. When someone later wants batch in the spec, that's its own
  spec — the fine-vs-coarse vocabulary mapping needs its own design pass.

## What this sets up (next increments — do NOT do here)
Once the guard is green: (1) extend `OperationSpec` with `inputs`/`parameters` + a validate-test that
the UI form and the spec agree for a pilot op; (2) then flip ONE subsystem (op-catalog is the natural
first) from committed-snapshot to generated-at-build from `iter_operation_specs()`, deleting the JSON;
(3) batch `_STEP_MAP` reconciliation, its own spec. Each is a proven-safe step because the guard
already establishes the spec is faithful.
