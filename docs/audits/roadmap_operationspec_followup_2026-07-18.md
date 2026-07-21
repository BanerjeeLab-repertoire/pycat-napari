# Roadmap — OperationSpec increments 2–5 (the follow-up)

> **STATUS (2026-07-19): COMPLETE.** All five increments shipped — inc 1 (1.6.68), inc 2 `inputs`/graph
> (1.6.126), inc 3 batch-step composition (1.6.127), inc 4 generate the Navigator catalog (1.6.128),
> inc 5 `requirements`/runnability gating (1.6.129). Remaining follow-on *consumers* (not part of this
> roadmap): wiring the runnability gate into UI widgets, and the separate `tag_resolver` binding table.

**Date:** 2026-07-18 · **Tree:** 1.6.121 · Companion to `operation_spec.py` (increment 1, shipped
1.6.68). Increment 1 deliberately stopped at *validate-first, generate-nothing*; this maps the path it
promised. Grounded in a fresh survey of the actual registries.

## Where increment 1 left off
`operation_spec.py` (132 lines) defines a frozen `OperationSpec(id, role, summary, target, produces,
aliases, registered_by)` as a **typed read-only view** over `tag_registry._OPERATIONS` — the dict the
`@tags_layer` decorator already populates. Its drift guard
(`tests/navigator/test_operation_spec_matches_catalog.py`, 4 tests) makes any divergence between the
live decorators and the committed snapshot (`navigator/data/operation_catalog.json`) a test failure.

Its own docstring states the plan: *"Once the snapshot is provably faithful to the live decorators
(zero drift, enforced), a LATER increment can flip one subsystem at a time from 'validate against the
spec' to 'generate from the spec' as a proven-safe change."* And it explicitly defers
`inputs / parameters / contexts / batchable / requirements` — *"an unpopulated field that nothing
checks is exactly the drift this effort exists to prevent."*

That precondition is now met: 85 `@tags_layer` decorators, 79 catalog entries, drift guarded and green.

## The verified registry landscape
| # | encoding | size | relationship to the spec |
|---|---|---|---|
| 1 | `tag_registry._OPERATIONS` (`@tags_layer`) | 85 decorators | **the source of truth** — inc 1 reads it |
| 2 | `navigator/data/operation_catalog.json` | 79 ops | snapshot, drift-guarded ✅ |
| 3 | `batch_step_registry._STEP_MAP` | 68 steps | **different granularity — see below** |
| 4 | UI menu wiring in `ui_modules.py` | 5555 lines | not yet related to the spec |
| 5 | `tag_resolver` binding table | exists (`tag_resolver.py:237`) | step→tag-query, separate concern |

**The critical finding — measured, not assumed: the batch `_STEP_MAP` and the op catalog have ZERO
name overlap.** Batch names *workflow steps* (`bf_cell_segmentation`, `condensate_analysis`,
`client_enrichment`); the catalog names *layer-producing operations* (`cellpose`, `bg_subtract`,
`bandpass`). Increment 1 was right to exclude `_STEP_MAP`: **this is not drift, it is a different
abstraction level.** Any increment that tries to unify them by name will fail. The honest relationship
is *composition* — a batch step invokes one or more catalog operations — and that mapping has to be
declared, not inferred.

---

## Increment 2 — populate `inputs` + `produces` WITH validation (the enabling step)
Add the first deferred fields, honouring inc 1's rule (a field arrives only with the validation that
makes it real):
- `inputs: tuple[str, ...]` — the layer roles/targets an operation consumes.
- Tighten `produces` (already present) against what the operation actually emits.
- **The validation that makes them real:** a test that, for every op, the declared `inputs` are
  satisfiable by some other op's `produces` (or are a root input like a loaded image) — i.e. the
  operation graph has no dangling edges. A declared input nothing can produce is a bug the test names.
- Source the values from the `@tags_layer` declaration (extend the decorator to accept `inputs=`),
  NOT from a side table — one source of truth stays the rule.

**Why first:** `inputs`+`produces` turn the flat vocabulary into a **graph**, which is what every later
increment needs (batch composition, UI gating, autopopulation).

## Increment 3 — declare the batch-step → operation composition
Given the zero-overlap finding, do NOT rename either vocabulary. Instead:
- give each `_STEP_MAP` entry an explicit `operations: tuple[str, ...]` declaring which catalog ops it
  invokes;
- a drift guard asserting every named op exists in the catalog (so renaming an op breaks the build
  instead of silently breaking replay);
- this makes batch replay *auditable against the operation vocabulary* for the first time, and is the
  prerequisite for ever generating batch steps.

**Deliberately NOT:** merging `_STEP_MAP` into the catalog. Different granularity is correct design.

## Increment 4 — flip ONE subsystem from validate to generate
The payoff inc 1 was built for. Pick the lowest-risk consumer — the **Navigator catalog** — and
generate `operation_catalog.json` from `iter_operation_specs()` at build/test time instead of
committing a hand-maintained snapshot. The drift guard becomes a regeneration check. One subsystem
only; prove the pattern before touching UI or batch.

## Increment 5 — `requirements` / gating, and (separately) the binding table
- `requirements`: what an op needs to be *runnable* (pixel size, a time axis, a mask, GPU). The
  machinery already exists in `navigator/capabilities.py` (`Capability.satisfied_by`) and
  `contracts.py` (`ModuleContract.requires_inputs`) — increment 5 connects the spec to it so the UI
  can grey out an operation with a stated reason rather than failing at click time.
- The **binding table** (`tag_resolver.py:237`, step→tag-query autopopulation) is a *related but
  separate* effort with its own curation spreadsheets. Keep it out of the OperationSpec increments;
  note the seam so they compose later.

---

## Sequencing
```
inc 1 (shipped) ──► inc 2 inputs/produces ──► inc 3 batch composition ──► inc 4 generate one ──► inc 5 requirements
                    [the graph]                [auditable replay]         [the payoff]           [UI gating]
```
Strictly sequential: 3 needs the graph from 2; 4 should not generate a vocabulary whose edges aren't
validated; 5 is gating on top of a proven graph.

## Standing cautions
- **One source of truth.** Every field arrives on the `@tags_layer` declaration, never a parallel side
  table — a second table is the exact tax this effort removes.
- **No field without its validation** (inc 1's own rule).
- **Do not unify batch steps with catalog ops by name** — measured zero overlap; they are different
  abstraction levels. Compose, don't merge.
- Generate one subsystem at a time, each behind its own guard.
