# Claude Code spec — Lightweight catalog discovery + consume `OperationSpec` in the UI

> **✅ STATUS — DONE, shipped in 1.6.143** (stamped 2026-07-20 from a CHANGELOG cross-reference). Import-free `iter_operation_specs(live=False)`, `resolve_operation`, run-button gating, tests.

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. Addresses two
linked audit findings: catalog availability is coupled to importing heavyweight science modules, and
the `OperationSpec` fields that now exist are not yet consumed by the interfaces that should use them.
Touches `navigator/operation_spec.py`, the generated catalog, and Navigator/UI wiring.

## Finding 1 — catalog discovery requires importing every implementation module (verified)
`_discover_tag_modules()` finds tag-bearing modules by AST **without importing** — good. But
`_populate_registry()` then `importlib.import_module`s each one to execute the decorators. So the
operation vocabulary is only as available as the *implementation dependencies*.

**Reproduced in this sandbox:** `pywavelets` is declared in `pyproject.toml` but `import pywt` fails
here, and the registry test correctly fails loudly naming `image_processing_tools`,
`segmentation_tools`, `topology_tools`. The loud failure is the 1.6.124 fix working as designed — but
it also demonstrates the coupling: **a missing optional/specialist dependency makes a third of the
operation catalog undiscoverable**, which would disable Navigator entries for operations whose *specs*
are perfectly well known.

### Fix — the catalog is a build artefact; execution imports lazily
The generated catalog (`navigator/data/operation_catalog.json`, already generated-and-drift-guarded
since increment 4) is the natural lightweight manifest. Make it the **runtime** source for
*discovery*, with imports only for *execution*:
```
operation_catalog.json  →  OperationSpec catalog ALWAYS available (no science imports)
                        →  executor imported only when the operation actually runs
```
- `iter_operation_specs()` gains a `live=False` default that reads the generated catalog; `live=True`
  performs today's import-and-introspect path.
- The existing drift guard keeps the two in agreement — it already regenerates from the live
  declarations at test time, so a stale catalog fails CI. That guard is what makes reading the
  artefact safe.
- Executing an operation resolves its module/function from the spec (`module`, `function` are already
  catalog fields) and imports **at call time**. A missing dependency then produces a precise,
  actionable error for that one operation — not a silent gap in the vocabulary.
- Navigator/UI availability must therefore never depend on import success; an operation whose module
  cannot import is *listed* and *unavailable-with-a-reason* (see Finding 2), which is strictly more
  useful than absent.

## Finding 2 — `OperationSpec` fields exist but are not consumed
The audit's first recommendation: *"Do not add more fields immediately. Wire existing fields into
Navigator enable/disable state, user-visible unavailability reasons, layer input resolution, batch
audit display."* `requirements` + `runnability(spec, available) -> (can_run, reason)` shipped in
increment 5; the UI does not yet use them.

### Fix — consume what exists, add nothing
1. **Navigator enable/disable + reason.** Every operation entry queries `runnability()` against the
   current session facts (z-stack present, time axis, calibrated pixel size, two channels, GPU) and
   renders disabled with the returned reason as tooltip/subtext — *"needs a calibrated pixel size"*
   rather than a dead-end click. Add **`module_importable`** as a runnability fact so the Finding-1
   case surfaces the same way: *"needs pywavelets (optional dependency)"*.
2. **Layer input resolution.** Use `inputs` to pre-filter the layer dropdowns for an operation to
   compatible roles/targets, instead of listing every layer.
3. **Batch audit display.** Show a batch step's declared `operations` composition (increment 3) in the
   recorded-steps view, so replay is auditable in the UI, not only in tests.

**Do not add new `requirements` values in this spec.** The audit correctly warns the vocabulary will
need `constraints` (numerical/shape predicates on inputs) as a *third* concept distinct from `inputs`
(semantic artefacts) and `requirements` (session/environment facts). Overloading `requirements` now
would entrench the wrong model. Note the distinction in the module docstring; build it later.

## Tests
- `iter_operation_specs()` with no science modules importable still returns the full catalog
  (simulate by making a module unimportable; assert the count is unchanged and specs are complete).
- The drift guard still fails when the generated catalog diverges from live declarations.
- Executing an operation whose module cannot import raises a precise, named error for that operation.
- `runnability()` drives at least one Navigator entry's disabled state and reason (contract-level test,
  no Qt).
- An operation's input dropdown offers only layers matching its declared `inputs`.

## Steps
1. `iter_operation_specs(live=False)` reading the generated catalog; keep `live=True` for the guard.
2. Lazy executor import at call time; precise per-operation import error.
3. `module_importable` as a runnability fact.
4. Wire `runnability()` into Navigator enable/disable + reason text.
5. Wire `inputs` into layer-dropdown filtering.
6. Show batch-step `operations` composition in the recorded-steps view.
7. Tests above; full `pytest -m core` green.
8. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (catalog no longer requires
   importing implementations; existing OperationSpec fields now consumed by Navigator/UI/batch audit).

## Definition of done
- The operation catalog is fully available without importing any science module.
- A missing optional dependency disables ONE operation with a stated reason, not a third of the
  vocabulary.
- Navigator shows enable/disable state with user-visible reasons from `runnability()`.
- Layer dropdowns are filtered by declared `inputs`.
- Batch steps display their operation composition.
- Full `pytest -m core` green.

## Cautions
- The drift guard is what makes reading the generated artefact safe — **do not weaken it**. If it is
  ever skipped, the catalog can silently diverge from the code.
- Do NOT add new `requirements` values here; `constraints` is a separate concept and deserves its own
  design.
- An unavailable operation must be *listed with a reason*, never hidden — hiding is the dead-end the
  runnability work exists to remove.
- Keep `live=True` available; the guard and any debugging need the import path.
