# Claude Code spec — OperationSpec increment 2: `inputs`, and the operation GRAPH

**Date:** 2026-07-18 · **Target tree:** 1.6.121 · Verified against the 1.6.121 tree. Adds the first of
the fields increment 1 deferred — **with the validation that makes it real**, per that increment's own
rule. Turns the flat operation vocabulary into a graph, which every later increment needs. Additive;
no behaviour change. Touches `utils/tag_registry.py`, `navigator/operation_spec.py`, the
`@tags_layer`-decorated functions, and tests. Not `file_io.py`.

**PREREQUISITE (met):** increment 1 shipped (1.6.68) and its drift guard is green — 85 `@tags_layer`
decorators, 79 catalog entries, `tests/navigator/test_operation_spec_matches_catalog.py` passing.

## Why now, and why `inputs` first
`operation_spec.py` states the plan explicitly: *"`inputs` / `parameters` / `batchable` /
`requirements` arrive in a LATER increment **with the validation that makes them real** — an
unpopulated field that nothing checks is exactly the drift this effort exists to prevent."*

`inputs` is the right first field because it is the one that changes the data structure's *kind*:
today the spec is a flat LIST of 85 operations; with `inputs` + the existing `produces` it becomes a
**directed graph** (`op_a.produces → op_b.inputs`). Increment 3 (batch composition), increment 4
(generating a subsystem) and increment 5 (runnability gating) all need that graph. Adding
`parameters`/`batchable` first would add fields without changing what the spec can *answer*.

## Part A — declare `inputs` on the decorator (one source of truth)
Extend `@tags_layer` (`utils/tag_registry.py`) to accept an optional `inputs=` argument: the layer
role(s)/target(s) the operation consumes, drawn from the SAME vocabularies `role`/`target` already use
(`tag_registry.ROLES` / `TARGETS`) — do **not** invent a third vocabulary.

```python
@tags_layer(op='bg_subtract', role='image', target='image', inputs=('image',))
def subtract_background(...): ...
```
Rules:
- values must be registered role/target names — an unregistered value is a hard error at import, the
  same way an unregistered tag already is (`test_tag_registry.py::test_an_UNREGISTERED_tag_is_REFUSED`);
- `inputs` is **optional**; an operation that declares nothing is a *root* (loads/creates a layer from
  a file, not from another layer). Absent ≠ empty-and-wrong — see Part C's staged enforcement;
- store it in `_OPERATIONS` alongside the existing fields; `_register_ui_operations()` gets the same
  ability.

## Part B — surface it on `OperationSpec`
Add `inputs: tuple[str, ...] = ()` to the frozen dataclass and populate it in
`iter_operation_specs()`. Keep the field ordering/back-compat so existing consumers and the catalog
comparison keep working. Extend `operation_catalog.json` with the field, and extend the existing drift
guard (`test_catalog_fields_match_the_live_declaration`) to cover it — a declared-vs-snapshot
divergence on `inputs` must fail like any other field.

## Part C — THE VALIDATION (this is what makes the field real)
New `tests/navigator/test_operation_graph.py` (mark `core`):
1. **No dangling edges.** For every op with declared `inputs`, each input role is either (a) produced
   by at least one other operation (`some_op.produces == that role`), or (b) a declared ROOT role — a
   small explicit set (e.g. `image` as loaded from disk) named in the test, not inferred. An input
   nothing can produce is a bug the test names with the op id.
2. **Vocabulary agreement.** Every `inputs` value is in `ROLES`/`TARGETS` — no free strings.
3. **The graph is traversable.** From the root role(s), the set of reachable operations is computable;
   report (don't fail on) any operation unreachable from a root — an unreachable op is a real smell
   worth surfacing as a warning-level test output, but may be legitimate (a UI-only op).
4. **Coverage ratchet.** Count operations that declare `inputs`. Assert the count does not DECREASE
   (the same downward-only ratchet idiom as `test_complexity_budget.py::_MAX_LONG_FUNCTIONS`, inverted
   — a floor rather than a ceiling). This is how the declarations get populated incrementally without
   requiring all 85 at once, and without silently regressing.

**Staged population is expected and correct.** Do NOT attempt to annotate all 85 operations in this
increment. Annotate a meaningful first tranche — the layer-producing image/segmentation ops where the
input role is unambiguous — set the ratchet floor at whatever lands, and let later work raise it.
Declaring an input you had to guess is worse than declaring none.

## Steps
1. `@tags_layer(inputs=…)` + `_OPERATIONS` storage + unregistered-value rejection.
2. `OperationSpec.inputs` + `iter_operation_specs()` population + catalog field + drift-guard coverage.
3. Annotate the unambiguous tranche of operations.
4. `tests/navigator/test_operation_graph.py` (4 checks above), including the coverage floor.
5. Full `pytest -m core` green — especially `test_tag_registry.py`,
   `test_operation_spec_matches_catalog.py`, and the complexity budget.
6. Ship: own version + PyPI push + commit (EXPLICIT filenames: tag_registry.py, operation_spec.py, the
   annotated modules, operation_catalog.json, the tests, pyproject, CHANGELOG) + CHANGELOG entry
   (OperationSpec inc 2: `inputs` declared on the decorator with graph validation; the vocabulary is
   now a graph).

## Definition of done
- `@tags_layer` accepts `inputs=`; unregistered values are refused at import.
- `OperationSpec.inputs` is populated, snapshotted, and drift-guarded.
- The graph test proves no dangling edges, vocabulary agreement, traversability, and a
  non-decreasing declaration count.
- A meaningful tranche is annotated; the rest is explicitly deferred behind the ratchet floor.
- Full `pytest -m core` green; no behaviour change.

## Cautions
- **One source of truth** — `inputs` lives on the decorator, never in a side table. A parallel table is
  the exact tax this effort exists to remove.
- **No field without validation** (increment 1's rule) — if the graph test can't be made to pass,
  do not ship the field.
- Reuse `ROLES`/`TARGETS`; do not introduce a third vocabulary for input kinds.
- Do NOT annotate an operation whose input role is ambiguous — leave it undeclared and let the ratchet
  floor capture progress. A guessed declaration is drift with extra steps.
- Do NOT touch `batch_step_registry._STEP_MAP` here. Measured: it has **zero name overlap** with the
  catalog because it names workflow steps, not layer operations. Composition is increment 3.
- Do not add `parameters` / `batchable` / `requirements` in this increment.
