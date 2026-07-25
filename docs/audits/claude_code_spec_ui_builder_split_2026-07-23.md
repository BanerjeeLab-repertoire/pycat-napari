# Claude Code spec — Split the `_add_*` UI builders (the five longest functions in the project)

> **✅ DONE — ALL FIVE builders decomposed (1.6.362 → 1.6.366).** `_add_advanced_analysis` (638),
> `_add_condensate_physics` (595), `_add_run_timeseries_condensate_analysis` (492), `_add_run_ts_cellpose`
> (549, 311-line `_on_run`) and `_add_lazy_preprocess_stack` (520, 358-line `_on_build`). Each split into
> widget factories (returning a SimpleNamespace) + handlers/dispatchers that unpack the namespace and run
> VERBATIM — construction order, parenting, signal wiring and worker ordering preserved. Every function ≤120;
> `_MAX_LONG_FUNCTIONS` 120 → 113. Pinned by `test_ui_builder_split` (the attribute contract, unmodified) +
> per-builder headless construction smokes (`tests/test_advanced_analysis_builder.py`).

> **◐ IN PROGRESS — 3 of 5 DONE (1.6.362, 1.6.363, 1.6.364).** `_add_advanced_analysis` (638),
> `_add_condensate_physics` (595) and `_add_run_timeseries_condensate_analysis` (492) fully decomposed into
> widget factories (returning a SimpleNamespace of handles) + handlers (unpack the namespace, run VERBATIM).
> Every function ≤120; `_MAX_LONG_FUNCTIONS` 120 → 117. Verified by `test_ui_builder_split` + construction
> smokes.
>
> **4 of 5 DONE — `_add_run_ts_cellpose` (549) shipped 1.6.365.** Its 311-line `_on_run` split into a
> dispatcher (`_ts_cp_run`) + per-method segmentation functions (otsu/rf/cellpose) + extracted callbacks
> (`_ts_cp_on_finished`, `_ts_cp_transfection_filter`) + two widget factories. Removed BOTH the builder and the
> 311-line `_on_run` from the long-function set; `_MAX_LONG_FUNCTIONS` 117 → 115. **Remaining (the last, and the
> hardest): `_add_lazy_preprocess_stack` (520, 358-line `_on_build`).**

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. **The five longest
functions in PyCAT are all UI builders**, and none has ever been touched — verified `grep -c "def _build_"`
returns **0** across all of them.

| lines | function |
|---|---|
| **638** | `advanced_analysis_ui.py::_add_advanced_analysis` |
| **595** | `condensate_physics_ui.py::_add_condensate_physics` |
| **549** | `ts_cellpose_tools.py::_add_run_ts_cellpose` |
| **520** | `timeseries/ui.py::_add_lazy_preprocess_stack` |
| **492** | `timeseries/ui.py::_add_run_timeseries_condensate_analysis` |

~2,800 lines in five functions, against a `_MAX_LONG_FUNCTIONS` ratchet of 120. Splitting these moves
the ratchet more than any other single change available.

## Why these are safe to split
They are **pure Qt construction** — widgets, layouts, signal connections. No science, no numerics, no
thresholds. A move that preserves construction order and parenting cannot change a measurement.

Coverage exists: `timeseries` 18 test files, `ts_cellpose_tools` 8, `advanced_analysis_ui` 6,
`condensate_physics_ui` 3 — plus `test_ui_structure` and the attribute-presence discipline.

## Method — attribute-presence test FIRST, then extract blocks
The realistic failure mode is **a silently missing `ui_instance` attribute** — a widget the run method
reads later. An import-only or constructs-without-error test misses that entirely.

1. **Write the attribute contract test on today's code.** For each builder, construct with a stub UI
   object and record every `ui_instance.<attr>` it sets. Assert the same set exists after the split.
   Write it **before** moving anything — afterwards it just encodes whatever the refactor produced.
2. **Extract contiguous widget blocks** into `_build_<section>` helpers, each taking the same
   `ui_instance` and appending to the same layout. Natural seams are the visual sections the builder
   already creates (a group box, a labelled row cluster, a step panel).
3. **Preserve order and parenting exactly.** Qt is order-sensitive: tab order, layout insertion index,
   and parent assignment all matter. Cut and paste; do not "tidy while here."
4. **One builder per commit**, attribute test + `test_ui_structure` + `pytest -m core` green between
   each. Mid-refactor sweeps touching many files have broken this build before.

## Suggested order (easiest evidence first)
1. `ts_cellpose_tools::_add_run_ts_cellpose` (549) — 8 test files, best-covered of the five.
2. `timeseries/ui::_add_lazy_preprocess_stack` (520) and `_add_run_timeseries_condensate_analysis` (492)
   — 18 test files covering the package.
3. `advanced_analysis_ui::_add_advanced_analysis` (638) — largest, 6 test files.
4. `condensate_physics_ui::_add_condensate_physics` (595) — thinnest coverage (3 files); do it last,
   after the pattern is well practised, and add the attribute test with extra care.

## Tests
- Attribute-presence contract holds for each builder before and after its split.
- `test_ui_structure` passes **unmodified**.
- Each UI constructs with a stub viewer after the split.
- Widget order within each section is unchanged (assert the layout's child order where practical).
- `_MAX_LONG_FUNCTIONS` **falls**; lower the ratchet to the achieved value.
- No existing test is edited to make a split pass.

## Steps
1. Attribute-presence tests for all five builders, committed and green on current code.
2. Split in the order above, one builder per commit, tests green between each.
3. Lower `_MAX_LONG_FUNCTIONS` to the new count.
4. Full `pytest -m core` green.
5. Ship each builder as its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with
   before/after line counts.

## Definition of done
- All five builders are decomposed into `_build_<section>` helpers; none exceeds the ratchet.
- Every builder's attribute contract is provably unchanged.
- `test_ui_structure` and the existing suites pass unmodified.
- The complexity ratchet is lowered, not raised.

## Cautions
- **Write the attribute test first.** This is the whole safety net; written afterwards it is worthless.
- **Qt order and parenting are behaviour** — tab order and layout index are user-visible. Preserve them.
- **Move, don't improve.** No renaming widgets, no reordering rows, no "while I'm here" fixes.
- **One builder per commit.** Five at once is un-bisectable.
- `condensate_physics_ui` has the thinnest coverage — do it last and pin it harder.
- Lower the ratchet at the end; never raise it to accommodate a partial split.
