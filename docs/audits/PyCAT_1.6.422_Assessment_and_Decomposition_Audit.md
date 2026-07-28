# PyCAT (v1.6.422) — Landed / Left, and a Decomposition Audit

*Assessed the fresh upload (**1.6.422**, 7 versions past 1.6.415). Landed/left verdicts were checked
against the code and registries; decomposition findings come from file-, package-, and function-level
structural analysis of all 348 source modules.*

---

# Part 1 — What landed, what's left (vs. the Next-Steps specs)

## Landed ✅

**N2 navigator adapters grew 5 → 8**, and — importantly — all three new ones route to **real handlers**,
correctly following the "handler-then-adapter, never point at a skip-stub" rule from the N2 spec:
- `ivf_droplet_segment` → `replay_ivf_droplet_segment` (in-vitro fluorescence segmentation, 1.6.416)
- `invitro.size_distribution` → `replay_ivf_size_distribution` (the measure→interpret chain, 1.6.417)
- `pixel_wise_corr.pearson_manders` → `replay_pixel_coloc` (two-channel coloc within a ROI, 1.6.418)

Each is verified present in `batch_step_registry.py` with a declared op-composition. The in-vitro
fluorescence pipeline now runs end-to-end from the dock, as does within-ROI two-channel coloc.

**N1 marker guard landed** — `test_heavy_import_tests_are_marked` is now in
`tests/test_ci_dependencies.py:932`, exactly the AST guard the N1 spec described (module-scope base-stack
import + no `core`/`base`/`integration`/`gui` marker ⇒ fail), and it correctly exempts files that carry a
module-scope `importorskip` or a GUI-bound pycat import. Two of the six files got explicit `base` markers
(`test_data_management`, `test_file_io`).

**Two solid new capabilities (beyond the specs):**
- **Biological object graph** (1.6.419–420) — the cell→puncta join + aggregation, placed in a focused
  `utils/object_graph.py`. This is the "linked object model" roadmap item starting to materialize.
- **Typed result models** (1.6.421–422) — `utils/result_models.py` + `results_store.py`; batch analysis
  steps now carry a typed `AnalysisResult` with `to_dict` serialization. Good structural groundwork.

## Left / deferred ⚠

**N2b-1 (VPT microrheology) — not built.** `'vpt_microrheology'` is still a skip-stub
(`batch_step_registry.py:294`, `print('…skipped in headless mode')`). The team went with the in-vitro
fluorescence + coloc adapters instead — a reasonable call (they had Phase-3 momentum on that chain), but
it means the VPT pipeline the N2 spec ranked #1 for scientific value is still panel-only. The MSD/
spatial-metrology/dynamic-spatial handlers are likewise still stubs. So the "grow adapters" thrust is
progressing, just on a different pipeline order than suggested.

**N4 (manuscript Fig 2 wiring) — not done.** Fig 2 is still greyed; nothing populates
`benchmark_results` into the panel context. (Recall the suite itself already exists — this remains a thin
wiring task.)

**N3 (skeleton-geodesic cross-check test / helper) — not done.** No shared helper, no
`test_tortuosity_impls_agree`. The two implementations still agree numerically but are unguarded against
drift.

**N1 residual — 4 of 6 files still unmarked** (`test_kaplan_meier`, `test_loaders_agree_on_scale`,
`test_explore_refine_export_ui`, `test_feature_analysis`). The guard *passes* because each is exempt via
`importorskip` or a GUI-bound import — so CI is green — but the N1 spec's intent was explicit
classification, and these remain implicit. Minor; add the markers when next touching those files.

**N5/N6 — not started** (Costes/Manders labelling suffixes; the four medium-item verification passes).
Expected — they were lowest in the sequencing.

**Net:** the forward thrust (adapters) and two roadmap features advanced well; the specific cheap wins
(Fig 2 wiring, the geodesic test, the last 4 markers) are still open. Nothing regressed.

---

# Part 2 — Decomposition audit

**Overall: the decomposition is mature and healthy, with a specific, bounded set of remaining
grab-bags.** The prior god-file decomposition (1.6.256) produced 11 well-structured sub-packages, and
the discipline is genuinely good — but ~19 files remain over 1000 lines, and a handful of those are
grab-bags rather than cohesive-large. The distinction matters: not every big file should be split.

## What's healthy (don't touch)

- **11 clean sub-packages** from the god-file split: `vpt` (15 modules), `data_qc` (12),
  `condensate_physics` (10), `segmentation` (10), `image_processing` (8), `coloc` (7), `timeseries` (7),
  `invitro` (6), `masks` (5), `manuscript` (3). Focused, single-domain.
- **Zero circular imports.** I checked whether any sub-module imports back up through its compatibility
  shim (`vpt_tools`, `condensate_physics_tools`, `invitro_tools`) — none do. The shims are one-directional
  re-export surfaces, which is the correct pattern.
- **6 clean re-export shims** (`vpt_tools.py`, `condensate_physics_tools.py`, `data_qc_tools.py`,
  `pixel_wise_corr_analysis_tools.py`, `label_and_mask_tools.py`, `invitro_tools.py`) — small, zero
  `def`s, pure backward-compat. All import successfully. This is exactly how to preserve the public API
  while splitting the implementation.
- **New code is well-placed** — `object_graph.py`, `result_models.py`, `results_store.py` are focused
  single-responsibility modules in `utils/`, not bolted onto an existing monolith.

## Cohesive-large (acceptable as-is, low priority)

These are big but single-domain — splitting would fragment a coherent concept for line-count's sake:
- **`correlation_func_analysis_tools.py`** (1548) — all CCF+ACF: computation, Gaussian fitting, plotting,
  UI runners. One domain. *Optional:* the ~6 `plot_*` functions could move to a sibling
  `correlation_plots.py`, but the analysis core is cohesive.
- **`metadata_extract.py`** (1390) — all metadata parsing (OME-XML, description blobs, frame-interval
  reconciliation). Cohesive; the 33 functions are all "turn raw file metadata into the common schema."
- **`vpt/detection.py`** (1261), **`condensate_physics/msd.py`** (906), **`spatial_metrology_tools.py`**
  (899) — each a single scientific domain. Fine.

## Grab-bags (real decomposition targets, in priority order)

**1. `toolbox/analysis_plots.py` (1625 lines, 43 functions, 0 classes) — the clearest target.** This is a
flat pile of unrelated plotters sharing only "imports matplotlib": VPT panels, FRAP recovery, coarsening,
KM survival, molecular counting, fusion relaxation, line/radial profiles, enrichment distributions,
spatial metrology, phase diagrams, focus diagnostics, plus the brushing/interaction helpers. No internal
section structure. **Split by analysis domain**, mirroring the toolbox sub-packages that already exist:
```
toolbox/plots/
  __init__.py              # re-export shim: `from pycat.toolbox.analysis_plots import *` stays valid
  _brushing.py             # _connect_nearest_curve_click*, add_brushing, _segment_distance* (the interaction core)
  vpt_plots.py             # plot_vpt_panel, _draw_msd_into, _draw_moduli_into, _draw_van_hove, plot_moduli, plot_msd_trajectories
  kinetics_plots.py        # plot_frap_recovery, plot_coarsening, plot_fusion_relaxation, plot_km_survival
  counting_plots.py        # plot_molecular_counting
  profile_plots.py         # plot_line_profiles, plot_radial_profiles, plot_enrichment_distribution
  spatial_plots.py         # plot_spatial_metrology, plot_phase_diagram
  qc_plots.py              # plot_focus_diagnostic, plot_distributions
```
Keep `analysis_plots.py` as the re-export shim (the pattern already used 6× elsewhere), so no caller
breaks. This is the single highest-value decomposition: 1625 lines → ~7 focused modules of 150–350 each,
and it aligns the plotting layer with the analysis sub-packages.

**2. `ui/analysis_methods_ui.py` (1626 lines, 9 classes) — split by panel.** It holds 7 distinct analysis
UI panels (`CondensateAnalysisUI`, `TimeSeriesCondensateUI`, `ObjectColocAnalysisUI`,
`PixelColocAnalysisUI`, `ColocalizationAnalysisUI`, `GeneralAnalysisUI`, `FibrilAnalysisUI`) plus the
`AnalysisMethodsUI` base and a `CollapsibleSection` widget. These are independent panels — a change to the
fibril panel has no reason to sit in the same file as the coloc panels. **Split into
`ui/analysis_panels/` with one module per panel**, `base.py` for `AnalysisMethodsUI`, and
`widgets.py` for `CollapsibleSection`; shim `analysis_methods_ui.py` re-exports all. Note the toolbox
already does exactly this for other UIs (`vpt_ui`, `frap_ui`, `brightfield_ui` are separate files) — this
file is the one place several panels still cohabit.

**3. `ui/ui_diagnostics_mixin.py` (1146 lines, 10 methods on one mixin)** and **`ui/menu_manager.py`
(1105)** — larger mixins/managers. Lower priority: a mixin is *meant* to aggregate `_add_*` methods, and a
menu manager is one responsibility. Split only if the `_add_*` methods cluster into clear sub-domains
(they may: SNR/diagnostics vs pipeline vs coordinate readout). Assess before splitting; these are
borderline, not obvious grab-bags.

## Function-level targets (independent of file splits)

Several functions are 250–415 lines — long enough to hide bugs and hard to test in isolation. The worst
offenders are UI builders and orchestrators:
- `run_pycat.py:run_pycat_func` (415) — the app bootstrap; extract the dock-construction and
  menu-wiring phases into named helpers.
- `timeseries/execution.py:_make__stackprocessworker` (402) — a closure factory; the worker body should
  be a module-level class/function, not a 400-line nested closure.
- `ui/analysis_methods_ui.py:_add_reference_frame_selector` (398) — a single widget builder at 400 lines;
  decompose the sub-sections into helpers.
- `two_channel_coloc_tools.py:_add_run_two_channel_coloc` (353), `vpt_ui.py:_on_detect_beads` (272),
  `menu_manager.py:_setup_menu_bar` (324) — same pattern: extract cohesive blocks into named helpers so
  each does one thing and can be unit-tested.

These are refactors-in-place (extract-method), not file splits — do them opportunistically when touching
the function, not as a campaign.

## Decomposition hygiene recommendation (one guard)

The decomposition discipline is good but **unguarded** — nothing stops a future god-file from re-growing.
Add a lightweight structural test (mark it `core`, pure `ast`/`pathlib`, no heavy imports), matching the
existing test-suite philosophy:
```python
def test_no_module_exceeds_the_decomposition_ceiling():
    """After the 1.6.256 god-file split, no NEW module should re-grow past the ceiling. Existing
    known-large cohesive modules are grandfathered by an explicit allowlist; anything else over the
    ceiling is a decomposition regression to split (see the plots/ and analysis_panels/ patterns)."""
    CEILING = 1000
    ALLOW = {  # cohesive-large, reviewed and accepted
        "correlation_func_analysis_tools.py", "metadata_extract.py", "vpt/detection.py",
        "temperature_tools.py", "brightfield_tools.py", "coloc/object_based.py",
        "general_image_tools.py", "ts_cellpose_tools.py", "file_io.py",
        "feature_analysis_tools.py", "condensate_physics/msd.py", ...
    }
    offenders = [f for f in src_modules if lines(f) > CEILING and rel(f) not in ALLOW]
    assert not offenders, "Modules over the decomposition ceiling (split them):\n" + ...
```
The allowlist makes the cohesive-large files an explicit, reviewed decision rather than an accident, and
forces any *new* 1000-line module to be a conscious addition to the list — which is where someone will
ask "should this be split?" As `analysis_plots.py` and `analysis_methods_ui.py` get split, they come off
the implicit-large set and the ceiling tightens naturally.

---

## Bottom line

**Landed/left:** the adapter thrust advanced (5→8, all to real handlers) and two roadmap features
(object graph, typed results) landed cleanly; the cheap specced wins (VPT handler, Fig 2 wiring, geodesic
test, last 4 markers) are still open but nothing regressed.

**Decomposition:** genuinely healthy — 11 clean sub-packages, zero circular imports, correct shim
pattern, well-placed new code. The remaining work is bounded and clear: **two real grab-bags**
(`analysis_plots.py` and `analysis_methods_ui.py`, both splittable by the same domain-package + shim
pattern already proven 6× in this codebase), a handful of **cohesive-large files to leave alone**, some
**long functions** to extract-method opportunistically, and **one structural guard** to keep the
discipline from eroding. The `plots/` split is the highest-value single move — it's mechanical, it
follows an established pattern, and it aligns the plotting layer with the analysis sub-packages.
