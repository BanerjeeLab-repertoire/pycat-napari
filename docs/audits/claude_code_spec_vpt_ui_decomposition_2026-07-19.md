# Claude Code spec — Decompose `vpt_ui.py`: make the new contracts ABSORB the old code

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. This spec exists
to answer the central charge of the external audit: *"New clean abstractions may become additional
layers wrapped around the same large procedural centers rather than replacing responsibilities inside
them."* The audit's stated success metric for the next revision is not more metadata machinery — it is
that one of the concentration points becomes **materially smaller**. Touches `vpt_ui.py` + new adapter
modules. Behaviour-preserving.

## The verified charge
Module sizes across the last two audits:

| module | previous | current | change |
|---|---:|---:|---|
| `ui/ui_modules.py` | 5555 | **5573** | +18 |
| `file_io/file_io.py` | 2787 | **2805** | +18 |
| `batch_step_registry.py` | 1613 | **1663** | +50 |
| `vpt_ui.py` | 2458 | **2458** | 0 |

Every one grew or held while `SelectionService`, `SelectionView`, `EntityRef`, `OperationSpec`, the
plot-backend abstraction, and the scene stack were added *beside* them. The architecture is real, but
so far it is additive.

## Why `vpt_ui.py` is the right first target
The audit names it, and it is the safest choice because **the safety net now exists**: the
`SelectionView` contract (`tests/selection_view_contract.py` + `test_selection_view_contract.py`), plus
`test_brushing.py`, `test_brushing_interaction.py`, `test_linked_selection_dock.py`,
`test_selection_service.py`, and the VPT equivalence tests. A decomposition that preserves behaviour
will keep those green; one that does not will fail loudly. That is exactly the condition under which
refactoring is cheap.

Verified structure — the file is dominated by a few very large methods:
`(275 lines @ 951)`, `(234 @ 1977)`, `(229 @ 2229)`, `(184 @ 1396)`, `(165 @ 1226)`, `(165 @ 779)`.
Six methods account for ~1250 lines — over half the file.

## Target
**Reduce `vpt_ui.py` by at least 25% (2458 → ≤ 1840 lines) by MOVING responsibilities out, not by
deleting features or reflowing lines.** The file should end up responsible for *construction and
wiring* only.

## The decomposition — extract along the contracts that already exist
Create a `src/pycat/toolbox/vpt/` package (or `vpt_adapters/`) and move, not copy:

1. **`vpt/msd_adapter.py`** — the MSD plot's model/renderer/hit-testing/selection wiring. It already
   implements the `SelectionView` protocol conceptually; make that explicit (`view_id`,
   `apply_selection`, `close`) so `test_selection_view_contract.py` covers it directly.
2. **`vpt/table_adapter.py`** — track-table row↔entity mapping, programmatic-update suppression,
   selection emission.
3. **`vpt/napari_adapter.py`** — Tracks/Points layer creation, overlay highlight, reveal/camera logic
   (including the `_revealing` re-entrancy guard).
4. **`vpt/panels.py`** — the large `_build_*` UI-construction methods (pure Qt layout, no logic).

`vpt_ui.py` retains: widget construction, signal wiring, and composition of the adapters. **The audit's
phrasing is the acceptance criterion: it should compose these pieces, not implement their internals.**

## Rules that make this safe
- **Move, don't rewrite.** Each extraction is cut-and-paste plus import fixes. Any behaviour change is
  out of scope and must be a separate commit — otherwise a regression is indistinguishable from an
  intended edit.
- **One extraction per commit**, running `pytest -m core` between each. The session's history shows
  multi-file sweeps breaking builds; incremental is the rule.
- **No new features.** Resist "while I'm here" improvements.
- If an extraction is blocked by a genuine circular dependency, stop and report it rather than
  inventing a workaround — that dependency is itself the finding.

## Tests
- All existing VPT/selection/brushing tests must stay green **unmodified**. If a test needs editing to
  pass, the refactor changed behaviour — revert and reconsider.
- Add the new adapters to the `SelectionView` contract suite so they are covered by the shared
  contract rather than ad-hoc tests.
- **Add a size ratchet:** extend `tests/test_complexity_budget.py` with a per-file line ceiling for
  `vpt_ui.py` set at the post-refactor value, so it cannot grow back. Mirror the existing
  `does not GROW` idiom. Do the same for `ui_modules.py` and `file_io.py` at their *current* values —
  that alone stops the +18/+50 drift the audit measured, at zero refactoring cost.

## Steps
1. Add the per-file line ratchet for `vpt_ui.py`, `ui_modules.py`, `file_io.py`,
   `batch_step_registry.py` at today's values (this is the cheap, immediate win — do it first).
2. Extract `vpt/panels.py` (pure layout — the lowest-risk move).
3. Extract `vpt/napari_adapter.py`.
4. Extract `vpt/table_adapter.py`.
5. Extract `vpt/msd_adapter.py`; register it in the `SelectionView` contract suite.
6. Lower the `vpt_ui.py` ratchet to the achieved value.
7. Full `pytest -m core` green after EACH step.
8. Ship: own version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG stating the before/after
   line count — the audit asked for a measurable reduction, so report the number.

## Definition of done
- `vpt_ui.py` ≤ 1840 lines (≥25% reduction), containing construction and wiring only.
- Four adapter modules own the extracted responsibilities; the MSD and table adapters are covered by
  the shared `SelectionView` contract suite.
- Every pre-existing test passes **unmodified**.
- Per-file line ratchets prevent regrowth of all four concentration points.
- CHANGELOG reports the measured reduction.

## Cautions
- **Move, don't rewrite** — the tests can only protect you if behaviour is unchanged.
- Do not modify a test to make the refactor pass. That inverts the safety net.
- The ratchet on the *other three* files is the highest-value/lowest-cost part of this spec: it stops
  the measured growth immediately, even before they are decomposed. Do it in step 1.
- Do not start `ui_modules.py` or `file_io.py` decomposition in the same version — one concentration
  point at a time, with the tests proving each.
