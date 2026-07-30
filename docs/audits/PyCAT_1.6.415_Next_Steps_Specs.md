# PyCAT — Specs for the Outstanding Items & Next Steps (from the 1.6.415 audit)

*Written against the live tree at **1.6.415**. Every file:line, function signature, and registry key
below was read out of the source. Ordered by the audit's leverage ranking: CI hygiene first (cheap,
unblocks nothing but closes a real gap), then the main forward thrust (navigator adapters — with a
critical prerequisite the audit surfaced), then the consistency/manuscript items.*

**Ground rules (unchanged):** each **code** change is its own version bump + PyPI push + commit; docs
fold forward; ship changed-files-only as `pycat_<VERSION>_changed.zip` with the 4-line handoff; every
new behaviour ships with the test that proves it. Where a spec adds a batch handler, it also ships the
route-equivalence assertion the suite already uses.

---

## N1 — Close the pytest-marker gap (CI hygiene) — *~20 min, do first*

> **STATUS — DONE (2026-07-28, test-only / git-only).** The recurrence guard `test_heavy_import_tests_are_marked`
> is in `test_ci_dependencies.py` (`core`, non-vacuous — proven to flag a throwaway unmarked heavy-import file).
> Correction to the finding on inspection: 4 of the 6 named files were ALREADY classified — `test_kaplan_meier`,
> `test_loaders_agree_on_scale`, `test_feature_analysis` carry per-test `@pytest.mark.base`, and
> `test_explore_refine_export_ui` carries `@pytest.mark.integration` (it uses `qtbot`, so a blanket `base` marker
> would mis-classify it — the guard was written to accept core/base/integration/gui and to exempt GUI-bound and
> importorskip files, so it does not false-positive on them). Only `test_data_management` and `test_file_io` were
> truly unmarked; they got a module `base` marker so they run in the base lane, and were grandfathered into
> `_SILENTLY_SKIPPABLE_AT_IMPORT` because they import a GUI-bound pycat module at module scope (lazy-import
> follow-up left). Full gate green.

**Finding.** `tests/test_kaplan_meier.py` hard-imports `pandas` at module scope (line 15) but carries no
`pytestmark`. It's *safe today* — the conftest minimal-lane guard (`pytest_ignore_collect`,
`tests/conftest.py:113`) ignores it because it has no `core` marker, and the `core or base` lane installs
pandas — but it's classified only implicitly, and it's the exact file that produced your original
`ModuleNotFoundError` under a partial `pytest -m core`. Five other files share the gap
(`test_loaders_agree_on_scale`, `test_data_management`, `test_explore_refine_export_ui`,
`test_feature_analysis`, `test_file_io`).

**Spec.**

1. Add the tier marker to each of the six files, directly under the imports:
   ```python
   pytestmark = pytest.mark.base      # imports pandas/scipy/skimage at module scope
   ```
   Tier by the workflow's rule (`core.yml`): a test is `core` only if it runs on **numpy + pytest
   alone**; anything importing pandas/scipy/skimage/etc. is `base`. All six are `base`.
2. **Add the recurrence guard** to `tests/test_ci_dependencies.py` (a pure-AST check, imports nothing
   heavy, mark it `core` so it runs in every lane):
   ```python
   import ast, pathlib

   _BASE_STACK = {"pandas", "scipy", "skimage", "matplotlib", "cv2",
                  "sklearn", "SimpleITK", "seaborn", "networkx", "openpyxl"}

   def _module_scope_imports(tree):
       names = set()
       for node in tree.body:                        # module scope only
           if isinstance(node, ast.Import):
               names |= {a.name.split(".")[0] for a in node.names}
           elif isinstance(node, ast.ImportFrom) and node.module:
               names.add(node.module.split(".")[0])
       return names

   def _has_marker(tree):
       # matches `pytestmark = pytest.mark.core|base` and @pytest.mark.core|base decorators
       for node in ast.walk(tree):
           if isinstance(node, ast.Attribute) and node.attr in ("core", "base"):
               v = node.value
               if isinstance(v, ast.Attribute) and v.attr == "mark":
                   return True
       return False

   def test_heavy_import_tests_are_marked():
       """A test that imports a base-stack package at module scope MUST carry a core/base marker.
       An unmarked heavy-import file aborts collection in the minimal-lane seam (the pandas failure
       of 2026-07-25). Classification is what conftest keys off — make it explicit, not implicit."""
       offenders = []
       for f in pathlib.Path("tests").glob("test_*.py"):
           tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
           heavy = _module_scope_imports(tree) & _BASE_STACK
           # a module-scope importorskip guard is an acceptable alternative to a marker
           has_guard = "importorskip" in f.read_text(encoding="utf-8", errors="ignore")
           if heavy and not _has_marker(tree) and not has_guard:
               offenders.append(f"{f.name}: module-scope {sorted(heavy)} but no core/base marker")
       assert not offenders, "Unmarked heavy-import test files:\n" + "\n".join(offenders)
   ```
   *(Note: `test_kaplan_meier.py` uses `importorskip` for the science module but still hard-imports
   pandas. The guard above treats an `importorskip` anywhere in the file as an acceptable alternative —
   but the cleanest state is the marker. Prefer adding the marker even where a guard exists, so the
   intent is declared, not inferred.)*
3. **Verify:**
   ```
   pip install numpy pytest && pip install --no-deps -e .
   pytest -m core -o addopts= -v        # every base file IGNORED at collection, no ModuleNotFoundError
   ```
   Then temporarily strip a marker and confirm `test_heavy_import_tests_are_marked` fails naming that
   file; restore it.

---

## N2 — Grow navigator execution adapters (the main forward thrust)

**Critical prerequisite the audit surfaced.** "Add a VPT/MSD adapter" is a **two-layer** job, not one.
The batch registry (`batch_step_registry.py`) has **35 of ~63 steps as skip-stubs** that only
`print('[PyCAT Batch] … skipped in headless mode')` — including the *entire* VPT/MSD/spatial-stats
family: `msd_analysis` (:253), `spatial_metrology` (:232), `dynamic_spatial` (:233),
`organizational_metrics` (:234), `vpt_detect_beads` (:289), `vpt_link_trajectories` (:291),
`vpt_microrheology` (:293). An `ExecAdapter` pointing at a skip-stub would run the navigator plan and
compute **nothing** — worse than "run from the panel," because it would *look* like it ran. So each new
adapter requires: **(a)** a real headless handler (like `replay_client_enrichment` was built in
1.6.387), then **(b)** the adapter that routes to it, then **(c)** the route-equivalence test.

**Do not add an adapter whose `batch_step` is still a skip-stub.** The adapter set may only cover steps
with a real handler. This is why the current 5 adapters are exactly the 5 with real handlers.

### N2a — The adapter pattern (reference — every new adapter follows this)

`ExecAdapter(plan_step, batch_step, params_from)` (`navigator/executor.py`):
- `plan_step` — the navigator op-id or module name the planner emits.
- `batch_step` — a `_STEP_MAP` key **that has a real handler**, or a callable `(intent[, state]) -> key`
  for target/modality-dependent dispatch.
- `params_from(intent, ctx, state, reviewed)` — derives the handler's `params` dict, applying reviewed
  values where the handler actually reads them. Follow `_bf_segment_params` / `_cellpose_params`: pull
  from `reviewed` first, then `ctx`, then the handler's grounded default; write to `data_instance` if the
  handler reads a knob from there rather than from `params`.

Register in `_ADAPTERS`, and — if the plan_step is an **op-id** rather than a module name — add it to
`_OP_TO_ADAPTER_MODULE` **or** key the adapter on the op-id directly (both work via `_adapter_for`; see
N5 for the comment that documents this). Ship the route-equivalence assertion in
`tests/test_batch_step_composition.py` (the `_MIN_STEPS_DECLARED` / `step_operations` machinery already
there) and a `CanonicalCase` in `tests/navigator/`.

### N2b — Priority order (each = build handler, then adapter, then test — one version each)

The order maximizes scientific value and reuses the most existing machinery:

> **STATUS N2b-1(a) — DONE (1.6.423).** `replay_vpt_microrheology` built in `batch/steps/vpt_steps.py`, registered
> (skip-stub replaced), op composition declared, registry held at its 432 ceiling. The whole chain runs headless;
> the scale gate refuses a pixel-unit viscosity (verdict, not a number). Gate `test_vpt_microrheology_handler.py`
> (`base`, 3): the full chain recovers the seeded viscosity within ±15% on a synthetic bead stack; both scale-gate
> refusals proven.
> **STATUS N2b-1(b,c) — DONE (1.6.426).** Adapter `ExecAdapter("vpt.microrheology", "vpt_microrheology",
> _vpt_microrheology_params)` in `_ADAPTERS`; the bead/viscosity plan chains the microrheology terminal, which runs
> the whole chain. The "Video Particle Tracking" CanonicalCase (already reproducible at plan level) now runs end to
> end. Gate `test_navigator_vpt_adapter.py` (`base`, 3): adapter resolves, guided viscosity == manual bit for bit,
> scale gate still refuses through the adapter path. **N2b-1 COMPLETE — the VPT flagship computes in the guided flow.**

**1. VPT microrheology → viscosity (highest value; Gable's flagship).** The science chain
(`detect_beads_stack → link → compute_msd → fit_anomalous_diffusion → viscosity_from_diffusion`) is all
pure-Python and proven headless (the golden-master harness runs it). What's missing is the batch
*handler* wrapping it.
   - **(a) Build `replay_vpt_microrheology`** in `batch/steps/` (new `vpt_steps.py` or into an existing
     module): signature `(state, image_path, params, output_dir)`, load the bead stack via `iter_frames`,
     run the chain with the physical-scale gate (pixel size from `state`/metadata — refuse to emit
     viscosity in pixel units, exactly as the op_catalog's `state:calibrated` requirement demands),
     write `<stem>_vpt_microrheology.csv` (per-track D, α, ensemble viscosity + the localization-offset
     term), and stash the scale-validity verdict for the reliability index (mirror
     `replay_client_enrichment`'s verdict-stash pattern). **Register** `'vpt_microrheology'` →
     `replay_vpt_microrheology` in `batch_step_registry.py` (replacing the skip-stub at :293) and declare
     its op composition in `_STEP_OPERATIONS`.
   - **(b) Adapter:** `ExecAdapter("vpt.microrheology", "vpt_microrheology", _vpt_microrheology_params)`.
     `_vpt_microrheology_params` threads the reviewed bead radius, frame interval, temperature, pixel
     size, and linking distance into the handler's params. The planner already selects the bead terminal
     target-aware, so no `_pick` change is needed.
   - **(c) Tests:** a `base` route test that the handler recovers the golden-master viscosity within
     tolerance on the seeded bead fixture; a route-equivalence assertion; a `CanonicalCase` for the VPT
     pipeline.
   - **Scale gate is non-negotiable:** the handler must refuse (verdict + no number) when pixel size is
     absent/1.0, never emit a pixel-unit viscosity. This is the same discipline as
     `check_calibration_validity`.

**2. Spatial metrology (Ripley's L / nearest-neighbour) — reuses an existing tool. — DONE (1.6.427).**
   - STATUS: `replay_spatial_metrology` built in `analysis_steps.py` (the cellular analogue of the existing
     `replay_ivf_spatial_metrology`) — it wraps the shared `run_all_spatial_metrics` (Ripley's L + NN + radial
     density, which subsumes `ripleys_l`) run PER CELL on the segmented objects' centroids via
     `get_puncta_centroids`, writes `<stem>_spatial_metrology.csv` + `state['spatial_metrology_df']`, and skips
     any cell with < 2 objects. Registered net-zero against the 432 ceiling (extended the `analysis_steps`
     import + swapped the skip-stub). Adapter `ExecAdapter("spatial_metrology.ripley", "spatial_metrology",
     _spatial_metrology_params)` added; coverage guard updated. Route test in
     `tests/navigator/test_navigator_spatial_metrology_adapter.py` (guided == manual, keyed per cell). Full gate
     green. NOTE: no separate `CanonicalCase` yet — deferred with N2b-3's, since both share the segmentation
     upstream and are better added together.
   - **(a) Build `replay_spatial_metrology`** wrapping `spatial_metrology_tools.ripleys_l` (+ the
     nearest-neighbour / radial-profile family — note S1's `radial_localization_profile` is now correct,
     so it's safe to expose headlessly). Input: a points/labels layer + the cell mask. Output:
     `<stem>_spatial_metrology.csv`. Register `'spatial_metrology'` → the handler (replacing the skip-stub
     at :232).
   - **(b) Adapter:** `ExecAdapter("spatial_metrology.ripley", "spatial_metrology", _spatial_metrology_params)`.
   - **(c)** route test + `CanonicalCase`.

**3. Dynamic spatial (trajectory linking → merge/fission) — 2D+t. — DONE (1.6.428).**
   - STATUS: `replay_dynamic_spatial` built in `analysis_steps.py` — self-contained from a segmented (T,H,W)
     label stack: `extract_frame_properties` → `link_trajectories` (motion) and `detect_merge_fission` (fusion),
     writing `*_dynamic_spatial_tracks.csv` + `*_dynamic_spatial_events.csv`. BOTH ops
     (`dynamic_spatial.link_trajectories` CREATE + `dynamic_spatial.detect_merge_fission` INTERPRET) key to the
     one handler via `_dynamic_spatial_params`; a `_dynamic_spatial_done` guard stops a both-ops plan tracking
     twice. Refuses cleanly with no 3-D stack (never fabricates a per-frame segmentation). Registered net-zero
     against the 432 ceiling. Route test in `tests/navigator/test_navigator_dynamic_spatial_adapter.py` (guided ==
     manual, guard, clean refusal). Full gate green. NOTE: no `CanonicalCase` yet — deferred (see N2b-2's note);
     the batch lane has no upstream producer of a labelled time-series stack today, so the end-to-end plan-level
     case waits on a time-series segmentation handler.
   - **(a) Build `replay_dynamic_spatial`** wrapping `dynamic_spatial_tools.link_trajectories` (+
     `detect_merge_fission`). Register `'dynamic_spatial'` (replacing the skip-stub at :233). Dispatch on
     dimensionality — this is a time-series op, so the `_context_score` machinery already ranks it
     correctly once a real handler exists.
   - **(b) Adapter** keyed on the tracking op-id; **(c)** tests.

**4. MSD / condensate biophysics — DONE (1.6.430, the "build it" branch).**
   - STATUS: The stub's "not a per-image batch step" premise was refuted by VPT (a whole-stack handler already
     runs in the batch loop) and by `dynamic_spatial` now writing a trajectory table that is exactly
     `compute_msd`'s contract (track_id / frame / y_um / x_um). So the decision is resolved by BUILDING, not
     documenting: `replay_msd_analysis` (`analysis_steps.py`) reads `state['dynamic_spatial_tracks_df']` → shared
     `compute_msd` → `fit_anomalous_diffusion`, writing `*_msd.csv` + `*_msd_fit.csv` and storing `D_um2_per_s`.
     Both diffusion ops (`condensate_physics.compute_msd` + `.fit_anomalous_diffusion`) key to it via `_msd_params`
     (guard `_msd_done`); the scale gate refuses a pixel²/frame D (needs calibrated pixel size + frame interval),
     exactly like VPT. Registered net-negative against the 432 ceiling. Route test
     `tests/navigator/test_navigator_msd_adapter.py` (D≈0.05 recovered, guided == manual, guard, scale refusal).
     Full gate green.
   - ORIGINAL SPEC (for reference): `msd_analysis` (:253) is stubbed as *"time-series; not a per-image
batch step."* This one is genuinely different: MSD needs the *whole* stack, not per-frame replay. Either
build a stack-level handler (the batch loop would need a stack-aware entry) **or** leave it panel-only
and document why. Decide explicitly; don't leave it as a silent stub that an adapter might later point
at. If left panel-only, add a comment at the stub and in the adapter-coverage test asserting it's
*intentionally* unadapted (like the `ivf_segmentation` precedent at `_STEP_OPERATIONS`).

**5+. The remaining families** (`organizational_metrics`, coloc, timeseries-condensate, z-stack) follow
the same three-step recipe as bandwidth allows, each flipping one pipeline to end-to-end.

### N2c — Close the 13-pipeline oracle incrementally

As each pipeline gains a full adapter chain, add its passing `CanonicalCase` to `tests/navigator/` and
assert the planner's generated step set equals the corresponding `ui/workflow_checklist.py` literal
(spine-level first, then exact keys). The oracle is "closed" when every one of the 13 hardcoded
checklists has a green `CanonicalCase`. Track remaining ones in a single `xfail`-marked list so the gap
is visible, not implicit.

---

## N3 — Extract a shared skeleton-geodesic helper (consistency, cosmetic) — DONE (test-only, git-only).
STATUS: Delivered the anti-drift **cross-check test** (the spec's preferred deliverable), skipped the optional
helper extraction (its "nice-to-have" internal tidiness isn't worth touching working geodesic code). Added to
`tests/test_tortuosity_consistency.py` (`base`, +4): a parametrized guard that `tortuosity_per_object` (MC,
scipy-sparse) and `fibril_morphometry`'s main segment (FB, NetworkX per-edge) agree to `< 1e-9` on UNBRANCHED
skeletons (rod, L-bend, and a new curved arc — they share the end-to-end geodesic, so they agree to ~1e-15), plus
a companion test pinning that on a BRANCHED Y they **legitimately diverge** (MC = whole-object geodesic diameter
across two arms; FB = longest single segment) so the difference is never mistaken for a bug and "fixed" into
false agreement. IMPORTANT CORRECTION to the spec's snippet: its illustrative `test_tortuosity_impls_agree` put a
Y-shape in the agreement set — empirically the Y diverges by ~0.09 (they measure different quantities on a branch),
so the agreement fixtures MUST be unbranched; the Y belongs in the divergence test instead. Full gate green.



**Finding.** The S3 fix put a correct geodesic-diameter computation in
`morphological_complexity_tools.py:291` (scipy sparse + KD-tree `query_pairs`, degree-1 endpoints,
`shortest_path`), while `fibril_tools.py:335` computes tortuosity its own way (a **NetworkX** graph from
`build_skeleton_graph`, per-edge traced `path`, end-to-end on `path[-1]-path[0]`). They now **agree
numerically** but are two implementations on two different graph representations, free to drift.

**Important nuance — this is NOT a trivial extraction.** They operate on different substrates
(scipy-sparse adjacency vs NetworkX edge-paths) and at different granularities (whole-object
geodesic-diameter vs per-segment). A naïve "share one function" would force one module onto the other's
representation and risk regressing a working path.

**Spec (low-risk version).**

1. Factor **only the pure geometry** — "given skeleton pixel coords and their unit-distance adjacency,
   return (path_len_px, end_to_end_px) between the two farthest degree-1 endpoints" — into a small helper
   in a shared module (e.g. `toolbox/_skeleton_geometry.py`):
   ```python
   def geodesic_diameter(skel_pts, adj):
       """Longest shortest-path between two degree-1 skeleton endpoints (falls back to the all-node
       geodesic diameter for a closed loop). Returns (path_len_px, end_to_end_px)."""
       ...  # the exact body currently in morphological_complexity_tools.py:300-318
   ```
2. `morphological_complexity_tools.tortuosity_per_object` calls it directly (it already builds `adj`).
3. `fibril_tools` is left as-is **unless** you want strict unification — its NetworkX per-edge path is a
   richer representation (it also yields curvature/persistence length), so forcing it through the helper
   would lose information. Instead, add a **cross-check test** that both produce the same tortuosity on a
   shared fixture (an L-bend, a Y-shape), so drift is caught even without shared code:
   ```python
   def test_tortuosity_impls_agree():
       for mask in (l_bend_mask(), y_shape_mask(), straight_rod_mask()):
           t_mc = tortuosity_per_object(mask)['tortuosity'].iloc[0]
           t_fb = fibril_morphometry(mask)['segment_rows'][...]['tortuosity']  # main segment
           assert abs(t_mc - t_fb) < 0.05
   ```
   The test is the real anti-drift guarantee; the helper extraction is the nice-to-have. Prefer the test;
   do the helper only for `morphological_complexity`'s own internal tidiness.

---

## N4 — Wire manuscript Fig 2 (benchmark/validation) — *it's a wiring task, not a build*

**Finding, corrected on inspection.** The validation harness **already exists and is complete**:
`benchmarks/run_suite.py` `run_case(case)` returns `{dice, iou, f1, n_pred, n_gt, count_error,
runtime_s}` against **constructed** ground truth, over canonical cases in `benchmarks/cases.py`
(puncta/partition/cells), with cross-release drift tracking. `benchmark_tools.py` has `pixel_overlap`
(Dice/IoU), `matched_detection` (F1), `basic_metrics`. So Fig 2 isn't greyed for want of a suite — it's
greyed because the panel's `available(context)` looks for a `benchmark_results` key that nothing puts in
the context (`manuscript/panels.py:177`, requirement: *"Run the validation suite … needs ground-truth
masks"*).

**Spec.**

1. **Add a thin adapter** from the suite to the panel context. In the manuscript context builder (wherever
   `qc_image`, `consolidated_long`, etc. are assembled), add an optional `benchmark_results` entry
   populated by running the suite:
   ```python
   from benchmarks.run_suite import run_all_cases   # or run_case over cases.ALL_CASES
   context["benchmark_results"] = run_all_cases()    # list of per-case metric dicts
   ```
   If `run_all_cases` doesn't exist as a one-call entry, add it to `run_suite.py` (it already loops cases
   internally for the CLI): `def run_all_cases(): return [run_case(c) for c in ALL_CASES]`.
2. **Implement the Fig 2 `generate(context)`** in `manuscript/panels.py` (currently the panel greys
   because it has no composer): render the per-case Dice/F1 as a table (family, method, dice, iou, f1,
   count_error) and, when ≥2 releases are recorded in the suite's history file, a small
   metric-stability strip. `available` gates on `context.get("benchmark_results")` being non-empty.
3. **Non-gating**, like the other panels: a suite failure records the panel `error`, never breaks the
   report.
4. **Test** (`base`): a context with `benchmark_results` from a one-case run produces a Fig 2 table whose
   columns are the known metric vocabulary; an empty context greys it with the requirement string.

*(This also feeds the reliability-index "the methods agree" claim — the same Dice/F1 numbers are the
benchmark-agreement input the MRI cluster wants.)*

---

## N5 — Small consistency fixes (fold into the next change touching each file) — DONE (1.6.429).
STATUS: Both parts shipped. N5a — Costes output rows now read `Costes Automatic Thresholded M1/M2 (intensity,
auto-threshold)` and object-based rows read `Mander's M1/M2 (object overlap)` (provenance suffixes, stem
preserved; the object-based relabel is at the output row only via `_OBCA_OUTPUT_LABELS`, selection key untouched);
added `_M1_FAMILY_LABEL_GLOSSARY` in `coloc/analysis.py` mapping every M1-family label to its definition + ref.
`tests/test_costes_manders.py` updated to the suffixed labels. N5b — replaced the (now-inaccurate) "keyed by the
REAL module name" comment above `_ADAPTERS` with the dual-convention note (module-name vs op-id keys) + author
guidance. Full gate green. NOTE: the Manders k1/k2 and Overlap Coefficient labels were left as-is (they are
already distinct from "M1" by name) — the glossary documents them; only the two literally-"M1"-named collisions
got suffixes.



**N5a — Costes/Manders "M1" labelling.** Confirmed multiple distinct labels for related-but-different
quantities across the coloc panels: `coloc/analysis.py` emits `'Costes Automatic Thresholded M1'`
(:610), the object-based path (`coloc/object_based.py:162,847`) emits `"Mander's M1 value"`, and there
are `k1`/`k2` (`"Mander's k2 value"`, :480) and significance entries. The *arithmetic* is now correct
(A3 fixed the cross-referencing), but a reader gets three different "M1"-family labels with no statement
of how they differ. **Spec:** don't rename (that breaks saved tables); instead add a one-line
provenance suffix to each so they're self-distinguishing in the output table —
e.g. `'Costes Automatic Thresholded M1 (intensity, auto-threshold)'` vs
`"Mander's M1 (object overlap)"` — and add a short docstring block in `coloc/analysis.py` mapping each
label to its definition + reference (this also seeds the measurement-ontology roadmap item). No math
change; a labelling/clarity change.

**N5b — Document the two adapter-keying conventions.** `_ADAPTERS` (`navigator/executor.py`) mixes
module-name keys (translated via `_OP_TO_ADAPTER_MODULE`) and op-id keys (matched directly by
`_adapter_for`). It works, but it's a trap for the next adapter author. **Spec:** add one comment above
`_ADAPTERS`:
```python
# Keys may be EITHER a module name (resolved for an op-id step via _OP_TO_ADAPTER_MODULE) OR an op-id
# matched directly. _adapter_for tries the step name directly first, then the op→module translation.
# When you add an adapter: if plan_step is an op-id with no module indirection, key it directly here;
# if it's a coarse module fronting several ops, key it by module and add the op→module rows.
```
Zero behaviour change.

---

## N6 — Re-verify the remaining prior-audit medium science items

These were flagged in the original scientific-method audit, are **not** in any S-spec, and were not
re-checked in the 1.6.415 pass. Each needs a *verification* pass (run it on a controlled input) before
deciding whether it needs a fix — don't fix blind. In priority order:

1. **SpIDA noise-tail truncation** — confirm whether the histogram fit still truncates the low-intensity
   tail in a way that biases the monomer fraction. **VERIFIED — bias CONFIRMED at low density (1.6.x, test-only).**
   `build_intensity_histogram` drops every pixel at/below the noise floor (`p = p[p > 0]`). At low density a large
   fraction of pixels see zero molecules (P(k=0)=e^-N), so the cut removes real information and inflates N: +56%
   at true N=2, catastrophic (>20x) at N=1, tracking the dropped-zero fraction; the valid regime (N>=5, e^-N
   negligible) is unaffected — which is why the pre-existing N=8 baseline test passes. Encoded in
   `tests/test_group_a_moments.py`: a CHARACTERISATION test pinning the mechanism (bias scales with dropped
   fraction; N=5 unaffected) + a strict-`xfail` FAILING GOLDEN-MASTER asserting N≈2 recovery, the acceptance test
   for the fix. FIX DIRECTION (evidence-backed, deferred to its own real-data-validated change — changing a core
   fit's histogram for all users must not be done blind): keep the low tail instead of `p[p>0]` — on noisy
   synthetics that recovers low-N D≈truth and leaves the high-N regime bit-identical.
2. **Partition-coefficient clipping** — check whether `partition_coefficient_field` clips or floors in a
   way that distorts K_p at low dilute-phase intensity. **VERIFIED — recovers truth; CLOSED with a guard
   (test-only).** For a well-posed (uniform) dilute phase, Kp = dense/bulk is recovered EXACTLY across the whole
   low range (bulk 0.3 → 0.001, Kp 2 → 600, 0% error) — the percentile == mean == bulk, so neither floor engages.
   The two floors (mean-fallback when the percentile is degenerate; the `bulk_div > 1e-6` guard) only engage on a
   degenerate right-skewed dark background, where they trade a ~1e8 divide-by-≈0 explosion for a bounded bias — a
   documented, deliberate choice. Encoded in `tests/test_partition.py`: a parametrized golden-master pinning exact
   Kp recovery across low dilute intensities (the regression guard) + a characterization pinning that the
   mean-fallback stays bounded (not an explosion) on the degenerate background. No fix needed.
3. **GLCM / LBP over the bbox** — confirm texture features are computed over the object mask, not the
   full bounding box (bbox includes background → contaminated texture). **VERIFIED — GLCM IS contaminated
   (test-only golden-master; fix deferred).** `calculate_glcm_features` crops the ROI *bounding box*
   (`crop_bounding_box(...)[0]`) and runs `graycomatrix` over it with NO mask restriction — a uniform disc (true
   contrast 0) reports contrast ≈ 11919, entirely the object/background edge (even a zero-noise background does
   it). `calculate_image_entropy` restricts via `cropped_mask`; LBP restricts its histogram but computes codes
   over the bbox, so its boundary ring is contaminated too — GLCM is the clear case. The pre-existing
   `test_glcm_features_with_mask` missed it (rectangular mask → bbox == mask). Encoded in
   `tests/test_feature_analysis.py`: a characterization pinning the contamination + a strict-`xfail` FAILING
   GOLDEN-MASTER asserting ≈0 contrast for a uniform object. **FIX SHIPPED (1.6.435).** `_masked_graycomatrix`
   restricts the co-occurrence to object–object pixel pairs (vectorised bincount per distance/angle); it is
   byte-identical to skimage's `graycomatrix(symmetric, normed)` when the mask is the whole bbox (guarded), so
   every existing GLCM number (no ROI / rectangular ROI) is preserved and only non-rectangular objects change —
   correctly. A uniform object now gives contrast 0.0 (was ~11919). The golden-master flipped from strict-`xfail`
   to passing; a skimage-equivalence regression guard was added. (LBP's milder boundary contamination is
   documented but left as-is — no failing golden-master was written for it.)
4. **Size-distribution detection-limit truncation** — check the MLE size distribution handles the
   left-truncation at the detection limit. **VERIFIED — it does NOT (test-only golden-master; fix deferred).**
   `fit_size_distribution_mle`'s whole-sample fits (lognormal via closed-form log-moments; gamma/weibull/
   exponential via scipy `.fit(floc=0)`) are UNTRUNCATED MLEs; only the power law uses a lower cut-off (Clauset
   x_min). The `xmin` parameter in the signature is DEAD — it never reaches the whole-sample fits. Left-truncating
   a true lognormal (median 5 µm) at a detection limit inflates the fitted median (+14% at r_min=3, +49% at
   r_min=5) and deflates the spread; passing `xmin` corrects nothing. Encoded in
   `tests/test_size_distribution_mle_characterization.py`: a baseline (untruncated recovers truth) + a
   characterization pinning the truncation bias of the default fit + a strict-`xfail` FAILING GOLDEN-MASTER that
   `xmin` should recover the true lognormal from detection-limited data. FIX (deferred, and additive since `xmin`
   defaults to None so existing untruncated behaviour is unchanged): honour `xmin` with a truncated likelihood
   `f(r)/(1−F(xmin))` per candidate. **FIX SHIPPED (1.6.431).** `xmin` now drives a left-truncated MLE for all four
   whole-sample candidates (numerical maximisation of `Σ log f(rᵢ) − n·log(1−F(xmin))`, seeded by the untruncated
   estimate); the AIC and the Vuong test carry the same truncation correction. The golden-master flipped from
   strict-`xfail` to passing (recovers mu≈log 5, σ≈0.5 from data truncated at 4 µm). `xmin=None` stays
   byte-identical (existing callers unaffected); the result dict gains `detection_limit_xmin`.

**N6 SUMMARY:** all four verified. #2 (partition) recovers truth → CLOSED with a regression guard. #1 (SpIDA
low-density), #3 (GLCM bbox), #4 (size-dist truncation) each CONFIRMED biased → each pinned with a
characterization + a strict-`xfail` failing golden-master that becomes its fix's acceptance test. All test-only;
fixes to the three core science functions deferred to focused, individually-validated changes (each alters a
shipping function's output broadly and must not be done blind).

**Spec per item:** write a golden-master test on a synthetic input with a known answer (a known monomer
fraction, a known K_p, a known texture, a known size distribution), run the current function, and record
whether it recovers truth. If it does → close the item with the test as the regression guard. If it
doesn't → that test becomes the failing golden-master for a fix spec. This is the same
"verify-then-fix" discipline the S-series used; C5 came off the list this way (the S2 fix resolved it).

---

## Delivery sequencing (each line = one code change = one version + ritual)

1. **N1** — markers + guard (CI hygiene; do first, ~20 min).
2. **N2b-1** — `replay_vpt_microrheology` handler, then its adapter, then tests (two versions: handler,
   then adapter — the handler is independently useful in batch even before the adapter).
3. **N4** — Fig 2 wiring (small, un-greys a manuscript panel, feeds the reliability index).
4. **N2b-2/3** — spatial-metrology and dynamic-spatial handlers + adapters (one pipeline per version).
5. **N3** — the tortuosity cross-check test (+ optional helper extraction).
6. **N5a/N5b** — labelling suffixes + the keying comment (fold into the next change touching those files).
7. **N6** — the four medium-item verification passes (each a test; a fix only where the test fails).
8. Continue **N2** adapter growth pipeline-by-pipeline, closing `CanonicalCase`s toward the 13-pipeline
   oracle.

The through-line: N1 is hygiene, N2 is the real forward leverage (now correctly framed as
handler-then-adapter, because 35 of the batch steps are still skip-stubs), N4 is a cheap manuscript win,
and N3/N5/N6 are consistency and diligence. The execution layer being proven means N2 is additive and
low-risk — each handler+adapter flips exactly one pipeline from panel-only to end-to-end without
touching the ones that already work.
