"""
**Did this change DELETE something?**

Gable, after the spurious-puncta incident:

    *"how do we make sure you don't throw away good code while doing these audits — the rationale
    was even in the code and you dropped it. We need some mechanism in this workflow to track these
    drops, because for all I know every module we've validated has truncated features away."*

**The concern is exactly right, and the failure mode is real.** Every edit in this workflow is a
**whole-file rewrite** — there is no diff, no merge, no three-way. If a rewrite emits fewer lines
than it read, **the difference is simply gone**, and:

* the file still **compiles**
* every test still **passes**
* the function still **exists**, just with fewer parameters

***A capability can disappear and nothing anywhere notices.*** That is exactly what happened:
``segment_subcellular_objects`` lost ``punctate_gate``, ``image_stats``, ``punctate_gate_sigma`` and
``punctate_gate_abs_sigma`` — **four safety parameters** — and spurious puncta came back with a
green test suite.

Why a diff against the last version is NOT enough
--------------------------------------------------
A first version of this guard compared the tree against the most recent snapshot. It reported
**"nothing dropped"** while the punctate gate was **entirely missing** — because **the baseline was
itself regressed.**

***A tool that compares against a broken baseline reports ALL CLEAR while everything is gone.***
That is the same failure it exists to prevent, one level up.

So the baseline is a **HIGH-WATER MARK**: for every function ever seen in **any** snapshot, the
**largest parameter set** and the **longest body** it has ever had. A capability that disappeared
three versions ago is **still missing today**, and this still says so.

``.pycat/high_water_mark.json`` — 1,825 functions, built from nine repo snapshots spanning
1.5.304 → 1.5.517, plus the working file Meet sent.

Every hit is a QUESTION, not a verdict
---------------------------------------
**A legitimate deletion looks exactly like an accidental one.** Moving a function to another module
is fine — that is what happened to the five stack helpers in 1.5.517, and ``file_io`` re-exports
them.

**The guard's job is to make sure the question gets asked.** When a deletion is deliberate, it goes
in ``_DELIBERATE`` *with a reason* — and that list is itself the record of what was removed and why.
"""

import ast
import json
import pathlib

import pytest


_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MARK = _ROOT / ".pycat" / "high_water_mark.json"

_SHRINK_THRESHOLD = 0.70


# ── Deletions that were DELIBERATE. Each needs a reason. ──────────────────────────────────
#
# This list is not an escape hatch — it is **the record of what was removed and why.** A future
# reader should be able to check every entry.
_DELIBERATE = {
    # 1.6.248 — image_processing decomposition step 1 (highest-risk file, characterization-FIRST): the
    # automatic object-size estimators (estimate_object_size_px — the top-hat/Otsu batch estimator that feeds
    # downstream segmentation — its nested _equiv_diam helper, the brightfield variant, and the
    # auto_object_size_valid / AUTO_OBJECT_SIZE_VALID_WORKFLOWS validity gate) MOVED verbatim to toolbox/
    # image_processing/size_estimation.py. Pinned BEFORE the move by test_image_processing_size_
    # characterization (exact object_size_px / ball_radius / n_objects on a fixed scene). No threshold change.
    'image_processing_tools.py::auto_object_size_valid',
    'image_processing_tools.py::estimate_object_size_px',
    'image_processing_tools.py::_equiv_diam',
    'image_processing_tools.py::estimate_object_size_px_brightfield',

    # 1.6.249 — image_processing decomposition step 2 (foundation): the shared PRIMITIVES every algorithm
    # family reuses (apply_rescale_intensity, invert_image, upscale_image_interp — the three registered ops
    # pinned by test_image_processing_base_characterization — plus _safe_equalize_adapthist, pseudo3d_tri_
    # planar_filter, and the lazy-napari _add_image/_napari helpers) MOVED verbatim to toolbox/image_
    # processing/_base.py. Dependency-ordered so the families can import them; napari stays function-scoped.
    # Registered ops moved → catalog regenerated.
    'image_processing_tools.py::_add_image',
    'image_processing_tools.py::_napari',
    'image_processing_tools.py::_safe_equalize_adapthist',
    'image_processing_tools.py::pseudo3d_tri_planar_filter',
    'image_processing_tools.py::apply_rescale_intensity',
    'image_processing_tools.py::invert_image',
    'image_processing_tools.py::upscale_image_interp',

    # 1.6.250 — image_processing decomposition step 3: DEBLUR by pixel reassignment (deblur_by_pixel_
    # reassignment + its run_dpr viewer wrapper) MOVED verbatim to toolbox/image_processing/deblur.py, now
    # unblocked since its only in-file dep (upscale_image_interp) is in _base. Pinned by
    # test_image_processing_deblur_characterization (exact two-array output). Registered op → catalog regen.
    'image_processing_tools.py::deblur_by_pixel_reassignment',
    'image_processing_tools.py::run_dpr',

    # 1.6.251 — image_processing decomposition step 4: the FILTER/ENHANCEMENT family (2D + pseudo-3D Gaussian/
    # Gabor/DoG filters, Laplacian-of-Gaussian filter+enhancement, edge-preserving bilateral, the combined
    # peak/edge enhancer + its _convolve_k helper, and the run_ wrappers) MOVED verbatim to toolbox/image_
    # processing/filters.py. Pinned by test_image_processing_filters_characterization (exact shape/dtype/sum/
    # min/max per operator). Ten registered ops moved → catalog regenerated. Build on _base primitives.
    'image_processing_tools.py::gaussian_smooth_2d',
    'image_processing_tools.py::gaussian_smooth_3d_pseudo',
    'image_processing_tools.py::gabor_filter_3d_pseudo',
    'image_processing_tools.py::dog_blob_enhance_2d',
    'image_processing_tools.py::dog_blob_enhance_3d_pseudo',
    'image_processing_tools.py::gabor_filter_func',
    'image_processing_tools.py::peak_and_edge_enhancement_func',
    'image_processing_tools.py::_convolve_k',
    'image_processing_tools.py::run_peak_and_edge_enhancement',
    'image_processing_tools.py::apply_laplace_of_gauss_filter',
    'image_processing_tools.py::apply_laplace_of_gauss_enhancement',
    'image_processing_tools.py::run_apply_laplace_of_gauss_filter',
    'image_processing_tools.py::run_morphological_gaussian_filter',
    'image_processing_tools.py::run_clahe',
    'image_processing_tools.py::apply_bilateral_filter',
    'image_processing_tools.py::run_apply_bilateral_filter',

    # 1.6.247 — timeseries decomposition step 4 (FINAL): the preprocessing SCIENCE (upscale_stack_to_zarr,
    # _cellpose_min_diameter_px) MOVED to toolbox/timeseries/preprocessing.py and the Qt UI BUILDERS
    # (_add_ts_upscale_stack / _build_ts_upscale_check_ui / _add_lazy_preprocess_stack /
    # _add_run_timeseries_condensate_analysis / _plot_condensate_fraction, with all their nested widget
    # callbacks) MOVED to toolbox/timeseries/ui.py — both verbatim, napari/Qt kept function-scoped so ui.py
    # imports headless. With this, timeseries_condensate_tools.py is a PURE re-export shim (no defs). The
    # builders are re-exported (the menu/UI wiring calls them); test_ui_builder_split's attribute contract
    # was repointed to timeseries/ui.py unchanged.
    'timeseries_condensate_tools.py::upscale_stack_to_zarr',
    'timeseries_condensate_tools.py::_cellpose_min_diameter_px',
    'timeseries_condensate_tools.py::_add_lazy_preprocess_stack',
    'timeseries_condensate_tools.py::_add_run_timeseries_condensate_analysis',
    'timeseries_condensate_tools.py::_plot_condensate_fraction',
    # nested widget callbacks / worker-lifecycle closures inside the two big builders:
    'timeseries_condensate_tools.py::_after_bg',
    'timeseries_condensate_tools.py::_after_preproc',
    'timeseries_condensate_tools.py::_apply_dissolution',
    'timeseries_condensate_tools.py::_apply_steady_state',
    'timeseries_condensate_tools.py::_done',
    'timeseries_condensate_tools.py::_dspin',
    'timeseries_condensate_tools.py::_err',
    'timeseries_condensate_tools.py::_load_from_cache',
    'timeseries_condensate_tools.py::_on_build',
    'timeseries_condensate_tools.py::_on_cancel',
    'timeseries_condensate_tools.py::_on_check',
    'timeseries_condensate_tools.py::_on_check_correlation',
    'timeseries_condensate_tools.py::_on_discard_cache',
    'timeseries_condensate_tools.py::_on_error',
    'timeseries_condensate_tools.py::_on_finished',
    'timeseries_condensate_tools.py::_on_progress',
    'timeseries_condensate_tools.py::_on_run',
    'timeseries_condensate_tools.py::_prog',
    'timeseries_condensate_tools.py::_start_bg',
    'timeseries_condensate_tools.py::_start_worker',
    'timeseries_condensate_tools.py::run',

    # 1.6.246 — timeseries decomposition step 3: the QThread/ProcessPool WORKER PLUMBING moved verbatim
    # (behaviour-preserving, no threading semantics changed) to toolbox/timeseries/execution.py — the
    # parallel subprocess frame helpers (_worker_read_frame, _process_frame_worker) and the two lazy
    # QThread-worker factories (_make__stackprocessworker, _make_timeseriesworker) with their result caches
    # and nested closures (_prepare_source_zarr / _source_descriptor / _dispatch / _cb / cancel). Qt/napari
    # stay function-scoped so the module still imports headless. timeseries_condensate_tools re-exports the
    # two factories, which the staying UI builders call.
    'timeseries_condensate_tools.py::_worker_read_frame',
    'timeseries_condensate_tools.py::_process_frame_worker',
    'timeseries_condensate_tools.py::_make__stackprocessworker',
    'timeseries_condensate_tools.py::_make_timeseriesworker',
    'timeseries_condensate_tools.py::_prepare_source_zarr',
    'timeseries_condensate_tools.py::_source_descriptor',
    'timeseries_condensate_tools.py::_dispatch',
    'timeseries_condensate_tools.py::_cb',
    'timeseries_condensate_tools.py::cancel',

    # 1.6.245 — timeseries decomposition step 2 (scientific-core, part 2): the ANALYSIS entry point
    # (run_timeseries_condensate_analysis + the per-frame worker _ts_analyze_frame_worker + the drift/metrics
    # helpers _condensate_metrics_per_cell / _phase_shift / _apply_shift, plus the shared pool initializer
    # _init_worker_threads) MOVED verbatim to toolbox/timeseries/analysis.py. No numerics or threading
    # semantics changed — pinned BEFORE the move by test_timeseries_analysis_characterization (exact DataFrame
    # + condensate mask on a fixed synthetic scene). timeseries_condensate_tools re-exports each; the staying
    # preprocessing worker resolves _init_worker_threads via that re-export.
    'timeseries_condensate_tools.py::run_timeseries_condensate_analysis',
    'timeseries_condensate_tools.py::_ts_analyze_frame_worker',
    'timeseries_condensate_tools.py::_condensate_metrics_per_cell',
    'timeseries_condensate_tools.py::_phase_shift',
    'timeseries_condensate_tools.py::_apply_shift',
    'timeseries_condensate_tools.py::_init_worker_threads',

    # 1.6.244 — timeseries decomposition step 1 (scientific-core, part 1): the lazy zarr FRAME-ACCESS layer
    # (_session_zarr_dir / _read_source_frame / _compute_stack_global_range / _get_zarr_dir_path /
    # _materialize_stack_to_zarr + the _ZarrStack wrapper) MOVED verbatim to toolbox/timeseries/frame_access.py,
    # and estimate_temporal_correlation (numerically pinned by test_temporal_enhancement) MOVED to
    # toolbox/timeseries/correlation.py. No read/materialize semantics changed. timeseries_condensate_tools
    # re-exports each; correlation imports _read_source_frame from frame_access.
    'timeseries_condensate_tools.py::_session_zarr_dir',
    'timeseries_condensate_tools.py::_read_source_frame',
    'timeseries_condensate_tools.py::_compute_stack_global_range',
    'timeseries_condensate_tools.py::_get_zarr_dir_path',
    'timeseries_condensate_tools.py::_materialize_stack_to_zarr',
    'timeseries_condensate_tools.py::estimate_temporal_correlation',

    # 1.6.183 — `detect_beads_stack` (the 317-line VPT detection stage) was split BY PIPELINE STAGE into
    # `_choose_detection_backend`, `_pool_predetect`, `_bead_hot_mask`, `_detect_all_frames`
    # (+ `_fast_frame_rows` / `_precise_frame_rows`) and `_assemble_detections`, leaving a 116-line
    # orchestrator. Nothing was removed: every stage MOVED into a named helper. Guard-anchored — the
    # existing VPT equivalence guards pass unmodified and a serial-path characterization
    # (`test_detect_beads_stack_characterization`) pins the exact detection table across the split.
    'vpt_tools.py::detect_beads_stack',
    # 1.6.204 — `link_trajectories_bayesian` (the 245-line Bayesian/Hungarian trajectory linker) was split
    # BY COMPUTATIONAL PHASE into `_bayesian_cost_defaults`, `_start_new_tracks`, `_build_frame_cost_matrix`
    # and `_apply_frame_assignment`, leaving a ~50-line orchestrator. Nothing was removed: every phase MOVED
    # into a named helper (two provably-dead locals were dropped in the move). Pinned byte-identical by
    # `test_bayesian_linker_assignment_is_byte_identical` (exact track_id + link_cost on a fixed scenario);
    # the existing purity/gap/ambiguity property tests pass unmodified.
    'dynamic_spatial_tools.py::link_trajectories_bayesian',
    # 1.6.205 — `fit_coarsening` (the 227-line coarsening-mechanism classifier) was split BY COMPUTATIONAL
    # PHASE into `_coarsening_powerlaw_fits`, `_coarsening_is_arrested` and `_coarsening_confidence`, leaving
    # a ~35-line orchestrator. Nothing was removed: every phase MOVED into a named helper (two provably-dead
    # locals, `noise` and `r2_gap`, were dropped in the move). Pinned byte-identical by
    # `test_fit_coarsening_output_is_byte_identical`; the arrest-classification property tests pass unmodified.
    'condensate_physics_tools.py::fit_coarsening',
    # 1.6.206 — `count_molecules_single` (the 214-line single-trace N&B counter) was split BY COMPUTATIONAL
    # PHASE into `_estimate_pedestal_read_noise` (pedestal + read-noise from the tail) and `_fit_counting_nu`
    # (the free-intercept-vs-through-origin ν fit), leaving a ~55-line orchestrator. Nothing was removed:
    # every phase MOVED into a named helper with its rationale. Pinned byte-identical by
    # `test_count_molecules_single_is_byte_identical`; the accuracy/pedestal property tests pass unmodified.
    'molecular_counting_tools.py::count_molecules_single',
    # 1.6.207 — `topology_metrics` (the 192-line per-cell envelope metric) had its comment-dense basin-count
    # phase extracted to `_topo_basin_metrics`, leaving a ~55-line orchestrator. Nothing was removed: the
    # phase MOVED into a named helper with its rationale (the dead min_basin_distance/ball_radius default was
    # dropped; the params stay in the signature). Pinned byte-identical by
    # `test_topology_metrics_is_byte_identical`; the basin-count property tests pass unmodified.
    'topology_tools.py::topology_metrics',
    # 1.6.208 — `qc_focus` (the 203-line focus/sharpness QC check) was split into `_qc_focus_stack` (the 3D
    # per-frame branch) and `_qc_focus_absolute` (the single-image diffraction-limit verdict), leaving the
    # orchestrator with the na/info branches. Nothing was removed: each branch MOVED into a named helper
    # with its rationale. Pinned byte-identical by `test_qc_focus_is_byte_identical` (all five result
    # branches); the existing focus property tests pass unmodified.
    'data_qc_tools.py::qc_focus',
    # 1.6.209 — `field_summary` (the 182-line in-vitro whole-field summary) had its non-empty compute + the
    # result dict extracted to `_field_summary_metrics`, leaving the orchestrator with the docstring, setup
    # and the n == 0 empty branch. Nothing was removed: the metrics MOVED into a named helper with their
    # measured caveats. Pinned byte-identical by `test_field_summary_is_byte_identical`; the halo/contrast
    # property tests pass unmodified.
    'invitro_tools.py::field_summary',

    # 1.6.181 — `partition_measurement` (the 191-line Kp measurement-with-assumptions builder) had its
    # background-subtracted assessment extracted to `_partition_background_assumption`, leaving a 110-line
    # body. Nothing was removed: the assessment and its rationale MOVED into the helper, and
    # `test_partition_measurement_characterization` pins which branch fires and the exact checked/holds/
    # detail of the assumption unchanged across the split.
    'invitro_tools.py::partition_measurement',

    # 1.6.180 — `fit_fusion_relaxation` (the 184-line droplet-fusion relaxation fit) was split into
    # `_fusion_tau_ci`, `_fusion_window_warn` and `_fusion_model_adequacy`, leaving a 90-line fit body.
    # Nothing was removed: the fit, the CI, the window check, the two-mode test and their measured
    # rationale MOVED into the helpers; `test_fusion_relaxation_characterization` pins the fit, CI,
    # relaxations-observed, adequacy/two-mode verdicts and which warnings fire unchanged across the split.
    'fusion_tools.py::fit_fusion_relaxation',

    # 1.6.176 — `fit_frap_recovery` (the 206-line FRAP recovery fit) was split into `_frap_derive_mobile`
    # (normalisation-aware mobile fraction + over-recovery warning) and `_frap_identifiability` (the
    # per-parameter covariance CI + warning), leaving a 109-line fit body. Nothing was removed: the fit,
    # the derived quantities, the identifiability assessment and their measured rationale MOVED into the
    # helpers, and a byte-identity characterization test (`test_frap_recovery_characterization`) pins the
    # fitted params, fractions, CI widths and which warnings fire unchanged across the split.
    'frap_tools.py::fit_frap_recovery',

    # 1.6.175 — `fit_photobleaching` (the 233-line exponential-bleach fit) was split into
    # `_photobleach_tau_ci`, `_photobleach_window_metrics` and `_photobleach_window_warn`, leaving a
    # 65-line fit + orchestrate body. Nothing was removed: the fit, the tau CI, the decay-observed bounds,
    # the two-tier warning and their measured-rationale comment blocks MOVED into the helpers, and a
    # byte-identity characterization test (`test_photobleaching_characterization`) pins the fitted params,
    # R², tau CI, both bounds, correction factors and which warning tier fires unchanged across the split.
    'condensate_physics_tools.py::fit_photobleaching',

    # 1.6.174 — `fit_size_distribution_mle` (the 301-line droplet-size-distribution identifier) was split
    # BY PHASE into pure helpers — `_fit_size_models`, `_powerlaw_tail_comparison`,
    # `_size_distinguishability`, `_size_verdict` — leaving a 92-line orchestrator. Nothing was removed:
    # every fit, test and its rationale MOVED into a named helper, and a byte-identity characterization
    # test (`test_size_distribution_mle_characterization`) pins the selected model, every AIC/loglik, the
    # power-law tail test and the descriptive moments unchanged across the split.
    'invitro_tools.py::fit_size_distribution_mle',
    # 1.6.213 — the whole size-distribution domain (fit_size_distribution_mle + fit_size_distribution + the
    # phase helpers, and their nested _add / _pointwise / lognormal_pdf) MOVED verbatim from invitro_tools to
    # `toolbox/invitro/size_distribution.py`; invitro_tools re-exports the two public entry points, so every
    # caller's import still resolves. No fit or number changed (pinned by
    # test_size_distribution_mle_characterization). These keys vanished from invitro_tools.py by the move.
    'invitro_tools.py::fit_size_distribution',
    'invitro_tools.py::_add',
    'invitro_tools.py::_pointwise',
    'invitro_tools.py::lognormal_pdf',
    # 1.6.214 — the partition-coefficient domain (partition_coefficient_local + _pc_* helpers +
    # partition_measurement + partition_coefficient_field + estimate_phase_boundary, with their nested
    # _fit / _hinge / _resid) MOVED verbatim to `toolbox/invitro/partition.py`; invitro_tools re-exports the
    # four public entry points. No K_p, background, or fit changed (pinned by test_partition* +
    # calibration/ΔG tests). These keys vanished from invitro_tools.py by the move.
    'invitro_tools.py::partition_coefficient_field',
    'invitro_tools.py::estimate_phase_boundary',
    'invitro_tools.py::_fit',
    'invitro_tools.py::_hinge',
    'invitro_tools.py::_resid',
    # 1.6.216 — the remaining in-vitro analysis sections (coarsening_statistics, estimate_csat_lever_rule,
    # estimate_contact_angle, detect_and_fit_fusions, detect_sedimentation, with their nested
    # _circle_residuals / _slope_r2) MOVED verbatim to `toolbox/invitro/analysis.py`; invitro_tools re-exports
    # all five and is now a pure shim over the invitro/ package. No number changed (existing in-vitro tests
    # are the net). These keys vanished from invitro_tools.py by the move.
    'invitro_tools.py::coarsening_statistics',
    'invitro_tools.py::estimate_csat_lever_rule',
    'invitro_tools.py::estimate_contact_angle',
    'invitro_tools.py::detect_and_fit_fusions',
    'invitro_tools.py::detect_sedimentation',
    'invitro_tools.py::_circle_residuals',
    'invitro_tools.py::_slope_r2',
    # 1.6.217 — condensate_physics decomposition step 1: the coarsening domain (fit_coarsening + its
    # _coarsening_* helpers, with the nested ostwald / coalescence model fns) MOVED verbatim to
    # `toolbox/condensate_physics/coarsening.py`; the tools module re-exports fit_coarsening. No number
    # changed (test_fit_coarsening_output_is_byte_identical passes). These nested keys vanished by the move.
    'condensate_physics_tools.py::ostwald',
    'condensate_physics_tools.py::coalescence',
    # 1.6.218 — condensate_physics decomposition step 2: the photobleaching + frame-quality domains (coupled:
    # analyse_frame_quality calls fit_photobleaching) MOVED verbatim to condensate_physics/photobleaching.py
    # and frame_quality.py; the tools module re-exports fit_photobleaching / apply_bleach_correction /
    # analyse_frame_quality / detect_out_of_focus. No number changed (photobleaching + focus/debris tests
    # pass; one monkeypatch target updated to the moved module). These keys vanished by the move; the nested
    # `model` (exponential bleach model, params I0/I_inf) collides with a same-named nested fit elsewhere.
    'condensate_physics_tools.py::apply_bleach_correction',
    'condensate_physics_tools.py::analyse_frame_quality',
    'condensate_physics_tools.py::detect_out_of_focus',
    'condensate_physics_tools.py::_frame_entropy',
    'condensate_physics_tools.py::_frame_gradient_energy',
    'condensate_physics_tools.py::_fit_linear_trend',
    'condensate_physics_tools.py::_norm_slope',
    'condensate_physics_tools.py::model',
    # 1.6.219 — condensate_physics decomposition step 3: three independent leaf domains — intensity
    # (fit_bimodal_intensity + intensity_decomposition_per_cell, nested `bimodal`), survival
    # (kaplan_meier_lifetimes), and shape-relaxation (fit_aspect_ratio_relaxation) — MOVED verbatim to
    # condensate_physics/{intensity,survival,relaxation}.py; the tools module re-exports all four. No number
    # changed (the intensity/survival/fusion tests pass). These keys vanished by the move.
    'condensate_physics_tools.py::fit_bimodal_intensity',
    'condensate_physics_tools.py::intensity_decomposition_per_cell',
    'condensate_physics_tools.py::bimodal',
    'condensate_physics_tools.py::kaplan_meier_lifetimes',
    'condensate_physics_tools.py::fit_aspect_ratio_relaxation',
    # 1.6.220 — condensate_physics decomposition step 4: the MSD / anomalous-diffusion domain (compute_msd,
    # fit_anomalous_diffusion, msd_per_track, test_confinement + the _short_track_rejections / _confined_msd
    # / _aicc / _lag_window_gate / _fit_msd_powerlaw / _assess_msd_identifiability / _classify_msd_motion /
    # _package_msd_result / _insufficient_result / _report_short_track_rejections helpers) MOVED verbatim to
    # condensate_physics/msd.py; the tools module re-exports the public entry points + MIN_TRACK_LENGTH_FRAMES.
    # The golden-master MSD->D->viscosity chain passes unmodified (two monkeypatch targets updated to the
    # moved module). These keys vanished by the move.
    'condensate_physics_tools.py::compute_msd',
    'condensate_physics_tools.py::msd_per_track',
    'condensate_physics_tools.py::test_confinement',
    'condensate_physics_tools.py::_confined_msd',
    'condensate_physics_tools.py::_aicc',
    'condensate_physics_tools.py::_lag_window_gate',
    'condensate_physics_tools.py::_insufficient_result',
    'condensate_physics_tools.py::_fit_msd_powerlaw',
    'condensate_physics_tools.py::_assess_msd_identifiability',
    'condensate_physics_tools.py::_classify_msd_motion',
    'condensate_physics_tools.py::_package_msd_result',
    'condensate_physics_tools.py::_short_track_rejections',
    'condensate_physics_tools.py::_report_short_track_rejections',
    # 1.6.221 — condensate_physics decomposition COMPLETE: the microrheology-moduli domain
    # (per_track_msd_curves, compute_moduli_gser/evans/evans_bootstrap, extract_fusion_relaxation, nested
    # _iw_Jtilde) MOVED verbatim to condensate_physics/moduli.py; condensate_physics_tools.py is now a pure
    # re-export shim. No modulus number changed. These keys vanished by the move.
    'condensate_physics_tools.py::per_track_msd_curves',
    'condensate_physics_tools.py::compute_moduli_gser',
    'condensate_physics_tools.py::compute_moduli_evans',
    'condensate_physics_tools.py::compute_moduli_evans_bootstrap',
    'condensate_physics_tools.py::extract_fusion_relaxation',
    'condensate_physics_tools.py::_iw_Jtilde',
    # 1.6.235 — vpt decomposition step 1: the Stokes-Einstein viscosity domain (viscosity_measurement,
    # viscosity_from_diffusion, viscosity_interval_from_diffusion) MOVED verbatim to toolbox/vpt/viscosity.py;
    # vpt_tools re-exports all three. No number changed (golden-master viscosity chain passes). Keys vanished
    # from vpt_tools.py by the move.
    'vpt_tools.py::viscosity_measurement',
    'vpt_tools.py::viscosity_from_diffusion',
    'vpt_tools.py::viscosity_interval_from_diffusion',
    # 1.6.236 — vpt decomposition step 2: the ensemble drift-correction domain (drift_correct_com,
    # reclassify_by_temporal_stability) MOVED verbatim to toolbox/vpt/drift.py; vpt_tools re-exports both.
    # No number changed (drift tests pass). Keys vanished from vpt_tools.py by the move.
    'vpt_tools.py::drift_correct_com',
    'vpt_tools.py::reclassify_by_temporal_stability',
    # 1.6.237 — vpt decomposition step 3: the host-condensate domain (segment_host_condensate,
    # erode_host_mask, infer_host_from_beads + _fit_clipped_radius) MOVED verbatim to toolbox/vpt/host.py;
    # vpt_tools re-exports the three public entry points. No number changed. Registered ops → catalog
    # regenerated. Keys vanished from vpt_tools.py by the move.
    'vpt_tools.py::segment_host_condensate',
    'vpt_tools.py::erode_host_mask',
    'vpt_tools.py::infer_host_from_beads',
    'vpt_tools.py::_fit_clipped_radius',

    # 1.6.238 — vpt decomposition step 4: the ENTIRE bead-detection stack (LoG CPU+GPU blob detection,
    # Airy/template PSF scoring, hot-pixel masking, ring-merge dedup, the detect_beads_stack orchestrator
    # with its GPU/CPU-parallel backend chooser, and the two linking-condition probes) MOVED verbatim to
    # toolbox/vpt/detection.py — 1754 lines, byte-identical, not a single detection or its order changed.
    # vpt_tools re-exports every public entry point plus the two private helpers the parallel-equivalence
    # test imports. detect_beads_stack is a registered op → catalog regenerated. These keys (incl. the
    # nested closures _fit_sigma/_key/_time/g/local_intensity) vanished from vpt_tools.py by the move.
    'vpt_tools.py::_assemble_detections',
    'vpt_tools.py::_bead_first_frame',
    'vpt_tools.py::_bead_hot_mask',
    'vpt_tools.py::_bead_source_descriptor',
    'vpt_tools.py::_choose_detection_backend',
    'vpt_tools.py::_choose_detection_tier',
    'vpt_tools.py::_classify_fast_template',
    'vpt_tools.py::_classify_fast_template_refs',
    'vpt_tools.py::_classify_gaussian_fit',
    'vpt_tools.py::_detect_all_frames',
    'vpt_tools.py::_detect_frame_worker',
    'vpt_tools.py::_fast_frame_rows',
    'vpt_tools.py::_fit_sigma',
    'vpt_tools.py::_frame_costs_s',
    'vpt_tools.py::_gpu_build_id',
    'vpt_tools.py::_key',
    'vpt_tools.py::_pool_predetect',
    'vpt_tools.py::_pool_spawn_cost_s',
    'vpt_tools.py::_pool_speedup',
    'vpt_tools.py::_precise_frame_rows',
    'vpt_tools.py::_read_frame_from_descriptor',
    'vpt_tools.py::_run_gpu_equivalence_check',
    'vpt_tools.py::_time',
    'vpt_tools.py::assess_linking_conditions',
    'vpt_tools.py::bead_half_from_size',
    'vpt_tools.py::blob_log_gpu',
    'vpt_tools.py::build_airy_template',
    'vpt_tools.py::build_bead_template',
    'vpt_tools.py::build_hot_pixel_mask',
    'vpt_tools.py::dedup_detections',
    'vpt_tools.py::dedup_detections_ring_merge',
    'vpt_tools.py::detect_beads_frame',
    'vpt_tools.py::estimate_linking_distance_um',
    'vpt_tools.py::g',
    'vpt_tools.py::gpu_matches_cpu',
    'vpt_tools.py::local_intensity',
    'vpt_tools.py::score_beads_template',

    # 1.6.239 — vpt decomposition steps 5-6 (FINAL): the bead-population routing
    # (split_bead_populations/select_bead_population/aggregate_population_stats) MOVED to
    # toolbox/vpt/populations.py, and the run_vpt_analysis orchestrator (+ _link dispatch,
    # compare_detection_variants sweep) MOVED to toolbox/vpt/analysis.py — both byte-identical. With these,
    # vpt_tools.py is a PURE re-export shim (95 lines, no defs) over the toolbox/vpt/ package. These keys
    # vanished from vpt_tools.py by the moves; the shim re-exports each (+ _link, imported by vpt_ui).
    'vpt_tools.py::split_bead_populations',
    'vpt_tools.py::select_bead_population',
    'vpt_tools.py::aggregate_population_stats',
    'vpt_tools.py::run_vpt_analysis',
    'vpt_tools.py::_link',
    'vpt_tools.py::compare_detection_variants',

    # 1.6.240 — segmentation decomposition step 1 (leaf/foundation layer): five independent families MOVED
    # verbatim out of segmentation_tools into toolbox/segmentation/ — _to_uint16_safe -> _common.py;
    # local_thresholding_func/run_local_thresholding -> local_thresholding.py; apply_watershed_labeling/
    # opencv_watershed_func -> watershed.py; cell_mask_stretching -> morphology.py; compute_image_intensity_
    # stats/cell_has_punctate_signal (the RESTORED punctate-gate subsystem) -> intensity.py. No threshold,
    # morphology, or operation-order change. Four are registered ops → catalog regenerated. segmentation_
    # tools re-exports each. Keys vanished from segmentation_tools.py by the moves.
    'segmentation_tools.py::_to_uint16_safe',
    'segmentation_tools.py::local_thresholding_func',
    'segmentation_tools.py::run_local_thresholding',
    'segmentation_tools.py::apply_watershed_labeling',
    'segmentation_tools.py::opencv_watershed_func',
    'segmentation_tools.py::cell_mask_stretching',
    'segmentation_tools.py::compute_image_intensity_stats',
    'segmentation_tools.py::cell_has_punctate_signal',

    # 1.6.241 — segmentation decomposition step 2: the FZ and CELLPOSE families MOVED verbatim out of
    # segmentation_tools. fz.py (felzenszwalb_segmentation_and_merging + RAG merge_mean_color/
    # _weight_mean_color + fz_segmentation_and_binarization + run_ wrapper) imports local_thresholding_func
    # from the local_thresholding family; cellpose.py (the optional-dep cellpose wrapper with its version-
    # aware model build + GPU/model caches, plus the RandomForest classifier + refine_labels_with_contours)
    # imports opencv_watershed_func from the watershed family. No scale/sigma/threshold change; the optional-
    # import guard is preserved exactly. Five registered ops moved → catalog regenerated.
    'segmentation_tools.py::_weight_mean_color',
    'segmentation_tools.py::merge_mean_color',
    'segmentation_tools.py::felzenszwalb_segmentation_and_merging',
    'segmentation_tools.py::run_fz_segmentation_and_merging',
    'segmentation_tools.py::fz_segmentation_and_binarization',
    'segmentation_tools.py::_get_cellpose_gpu',
    'segmentation_tools.py::_cellpose_major_version',
    'segmentation_tools.py::available_cellpose_models',
    'segmentation_tools.py::default_cellpose_model',
    'segmentation_tools.py::_build_cellpose_model',
    'segmentation_tools.py::cellpose_segmentation',
    'segmentation_tools.py::run_cellpose_segmentation',
    'segmentation_tools.py::train_and_apply_rf_classifier',
    'segmentation_tools.py::refine_labels_with_contours',
    'segmentation_tools.py::run_train_and_apply_rf_classifier',

    # 1.6.242 — segmentation decomposition step 3: the filter-sensitivity-gated PUNCTA REFINEMENT family
    # (SNR/kurtosis/contrast gate, per-object ring-radii/background helpers, and the two bit-identical
    # fast/slow implementations behind puncta_refinement_func) MOVED verbatim to segmentation/
    # puncta_refinement.py — no threshold, morphology, or operation order changed. The module owns the
    # _PYCAT_REFINE_FAST/_DEBUG flags + _refine_debug_enabled it reads, and imports apply_watershed_labeling
    # (watershed) + _to_uint16_safe (_common). Three tests had napari-notify / _local_ring_radii patch
    # targets repointed to puncta_refinement (the module the moved filters resolve names in) — assertions
    # unchanged. puncta_refinement_filtering_func is a registered op → catalog regen.
    'segmentation_tools.py::_refine_debug_enabled',
    'segmentation_tools.py::_local_ring_radii',
    'segmentation_tools.py::_ring_masks',
    'segmentation_tools.py::_robust_bg',
    'segmentation_tools.py::_snr_conditions',
    'segmentation_tools.py::_report_refinement_drops',
    'segmentation_tools.py::puncta_refinement_filtering_func',
    'segmentation_tools.py::puncta_refinement_filtering_func_fast',
    'segmentation_tools.py::puncta_refinement_func',

    # 1.6.243 — segmentation decomposition step 4 (FINAL): the SUBCELLULAR orchestrator family
    # (segment_subcellular_objects + run_ + _segment_core + compare_segmentation_speed) MOVED verbatim to
    # segmentation/subcellular.py, importing the puncta / fz / intensity / morphology families it composes.
    # No threshold or operation-order change. With this move segmentation_tools.py is a PURE re-export shim
    # (no defs) over toolbox/segmentation/. segment_subcellular_objects is a registered op → catalog regen.
    # test_refinement_thresholds' spy patch repointed to subcellular.puncta_refinement_func (the module the
    # moved orchestrator resolves the name in) — assertions unchanged.
    'segmentation_tools.py::segment_subcellular_objects',
    'segmentation_tools.py::run_segment_subcellular_objects',
    'segmentation_tools.py::_segment_core',
    'segmentation_tools.py::compare_segmentation_speed',

    # 1.6.173 — `classify_beads` (the 306-line bead classifier) was split into its two independent
    # branches — `_classify_fast_template` (with a `_classify_fast_template_refs` reference-stats phase)
    # and `_classify_gaussian_fit` — leaving the function a 68-line empty-guard + dispatch. Nothing was
    # removed: both classifiers and their rationale MOVED into the named helpers, and a byte-identity
    # characterization test (`test_classify_beads_characterization`) pins both branches' exact labels,
    # estimates, row counts and thresholds unchanged across the split.
    'vpt_tools.py::classify_beads',

    # 1.6.172 — `partition_coefficient_local` (the 394-line local-annulus Kp measurement) was split BY
    # PHASE into pure helpers — `_pc_check_input` (intensity-provenance gate), `_pc_camera_floor`
    # (pedestal / dark-reference / extracellular), `_pc_estimate_gap` (interface-width annulus offset),
    # `_pc_measure_droplets` (the per-droplet loop + over-inclusive-mask warning) and `_pc_verdict` (the
    # six-branch reporting chain) — dropping it 393 → 108 lines. Nothing was removed: every line and its
    # rationale MOVED into a named helper, and a byte-identity characterization test
    # (`test_partition_local_characterization`) captured the exact outputs across all branches BEFORE the
    # split and asserts them unchanged after (no number moved).
    'invitro_tools.py::partition_coefficient_local',

    # 1.6.168 — science_function_split: `fit_anomalous_diffusion` (the 394-line MSD/α fit behind
    # viscosity) was split BY COMPUTATIONAL PHASE into pure helpers — `_lag_window_gate`,
    # `_fit_msd_powerlaw`, `_assess_msd_identifiability`, `_classify_msd_motion`, `_package_msd_result` —
    # dropping it 393 → 98 lines. Nothing was removed: every line MOVED into a named helper, and the
    # function's 4 numerical tests passed UNMODIFIED (no number changed). The rationale in the deleted
    # lines lives on in those helpers.
    'condensate_physics_tools.py::fit_anomalous_diffusion',

    # 1.6.100 — the MSD plot's `_on_pick` (per-line pick_event handler) was removed with its whole
    # mechanism. An audit found the pick_event-plus-debounce approach intrinsically fragile (it
    # assumed all of a click's pick_events arrive before a zero-delay timer — not a safe contract),
    # so one click still cycled through many tracks. Replaced by a single canvas `button_press_event`
    # handler (`_connect_nearest_curve_click`) that fires once per click and selects the nearest
    # curve — there is nothing to debounce. `_on_pick` has no successor by name; the capability moved
    # to `_connect_nearest_curve_click` + `_apply_pick`.
    'analysis_plots.py::_on_pick',

    # 1.6.120 — the MSD spaghetti background became ONE LineCollection and selection an OVERLAY
    # (interaction-layer Gap 4). `_render_consolidated` (the VPT panel) shrank because its bespoke
    # blit + apply-pick + connect were replaced by a call to the shared `_msd_overlay_hooks`, which the
    # standalone `plot_msd_trajectories` also uses — so the panel and the standalone brush identically
    # from ONE implementation instead of two divergent copies. Nothing was dropped; the logic moved
    # into `_msd_overlay_hooks` (+ the coords hit-tester `_connect_nearest_curve_click_coords`).
    'analysis_plots.py::_render_consolidated',

    # 1.6.106 — session load moved OFF the Qt thread. `load_session` was one 149-line function that
    # read/decoded every file AND created the napari layers in one loop — so it could not be run on a
    # worker (layer creation off the main thread is a crash). It is split: `_read_session_payload`
    # (pure decode + CSV reads, no viewer — the slow half that now runs on a QThread) and
    # `_apply_session_payload` (the `viewer.add_*` + repository writes, on the caller's thread).
    # `load_session` is now the thin orchestrator that runs the read via `qt_worker.run_with_progress`
    # and applies the result. Nothing was dropped — the whole body moved into the two helpers, and the
    # synchronous round trip is pinned by `test_session_load_threading` and the existing
    # `test_session_load_lazy_image` suite.
    'session_loader.py::load_session',

    # 1.6.106 — `_prog`, the inline progress-callback closure in `_open_session_loader._on_load`, was
    # removed. It drove an in-dialog QProgressBar from the (blocked) main thread — the bar the 1.6.81/82
    # rollout added, which advanced while the window still said "Not Responding". The worker now owns a
    # modal QProgressDialog that keeps the window painting, so a second in-dialog bar would be two bars
    # for one operation (the UX trap the roadmap flagged); the inline bar and its `_prog` driver are
    # retired together.
    'ui_modules.py::_prog',

    # 1.6.104 — the picked-bead PULSE was removed. `_pulse_layer` armed a QTimer that oscillated the
    # ring's size/opacity to draw the eye. But the ring is per-frame — present only on the bead's own
    # frame — so scrubbing away from that frame left NOTHING to pulse while the opacity slider churned
    # on for nothing (reported from the viewer). Zoom-to-bead navigation draws the eye now; the ring is
    # a static marker. No successor — the pulse mechanism is simply gone, along with `_PULSE_MS`/
    # `_PULSE_STEPS`.
    'vpt_ui.py::_pulse_layer',

    # 1.6.104 — `_follow_enabled` (VPT) went dead when a plot click became "always navigate to the
    # bead" (the user asked for it, and it is safe now the click-loop is fixed — see `_on_pick` above).
    # The reveal no longer consults a follow preference, so this wrapper had no caller. The GENERIC
    # brushing path still has its own `pycat.utils.brushing._follow_enabled` for the double-click/
    # follow_selection case — this was only VPT's now-unused copy.
    'vpt_ui.py::_follow_enabled',

    # 1.6.137 — the five pure-layout PANEL BUILDERS MOVED from vpt_ui.py into `vpt/panels.py` as the
    # `_VptPanelsMixin` (vpt_ui decomposition, step 2). Nothing was dropped: the method bodies are
    # byte-for-byte unchanged; they just live in the module vpt_ui now COMPOSES instead of implementing.
    # (`setup_ui` — the top-level construction/composition + the pixel-size gate install — deliberately
    # STAYS in vpt_ui.py; only the pieces it composes moved.) The guard keys by FILENAME, so a move
    # reads as a vanish from the old file — these record it. The functions still exist as
    # `panels.py::<name>`. vpt_ui.py: 2458 -> 1778 lines.
    'vpt_ui.py::_build_per_track_metrics',
    'vpt_ui.py::_add_host_segmentation',
    'vpt_ui.py::_add_bead_detection',
    'vpt_ui.py::_add_tracking',
    'vpt_ui.py::_add_microrheology',

    # 1.6.138 — the napari-facing layer/overlay/reveal methods MOVED from vpt_ui.py into
    # `vpt/napari_adapter.py` as `_VptNapariMixin` (vpt_ui decomposition, step 3). Behaviour-preserving
    # move; bodies unchanged. (`_on_click` is the nested pick handler defined inside
    # `_add_pickable_bead_points`, so it moved with it.) They exist now as `napari_adapter.py::<name>`.
    'vpt_ui.py::_add_pickable_bead_points',
    'vpt_ui.py::_reveal_track_in_viewer',
    'vpt_ui.py::_on_click',

    # 1.6.138 — the track-table methods MOVED from vpt_ui.py into `vpt/table_adapter.py` as
    # `_VptTableMixin` (vpt_ui decomposition, step 3). Behaviour-preserving move; bodies unchanged.
    # (`_on_row` / `_closeEvent` are nested handlers defined inside `_show_per_track_table`, so they
    # moved with it.) They exist now as `table_adapter.py::<name>`.
    'vpt_ui.py::_show_per_track_table',
    'vpt_ui.py::_highlight_track_in_table',
    'vpt_ui.py::_select_track',
    'vpt_ui.py::_on_row',
    'vpt_ui.py::_closeEvent',

    # 1.6.138 — the MSD-plot methods MOVED from vpt_ui.py into `vpt/msd_adapter.py` as `_VptMsdMixin`
    # (vpt_ui decomposition, step 3). Behaviour-preserving move; bodies unchanged. They exist now as
    # `msd_adapter.py::<name>`.
    'vpt_ui.py::_highlight_track_in_plot',
    'vpt_ui.py::_update_tracklen_hist',

    # 1.6.146 — the two Qt dialog CLASSES (`LayerDataframeSelectionDialog`, `ChannelAssignmentDialog`)
    # MOVED from file_io.py into `file_io/dialogs.py` (file_io decomposition, move 2). Their methods
    # went with them; bodies unchanged. Nothing imported these dialogs externally; `FileIOClass` now
    # imports them from `dialogs.py`. These method names are unique to the dialogs, so they read as
    # "vanished" from file_io.py — they exist now as `dialogs.py::<name>`.
    'file_io.py::get_selections',
    'file_io.py::initUI',
    'file_io.py::_est_size_mb',
    'file_io.py::_is_reconstructable',
    'file_io.py::_on_discard',

    # 1.6.146 — pure pixel-size / lazy-label helpers MOVED to `file_io/naming.py` (decomposition, move 3),
    # now headlessly testable. `file_io` re-exports them, so every call site + importer still works.
    # (`derive_layer_name`/`_clean_filename_token` deliberately stayed — two tests AST-parse them by path.)
    'file_io.py::_lazy_contrast_limits',
    'file_io.py::_tiff_pixel_size_um',
    'file_io.py::_ome_pixel_size_um',
    'file_io.py::_lazy_backing_label',

    # 1.6.146 — the three format-specific stack openers (`_open_stack_ims`, `_open_stack_generic`,
    # `_open_czi_streaming`) MOVED to `file_io/stack_openers.py` as `_StackOpenersMixin` (decomposition,
    # move 4). Kept as a mixin (not standalone functions): they write FileIOClass state and call sibling
    # methods, so a function form would be a worse seam. `FileIOClass` inherits it; bodies unchanged.
    'file_io.py::_open_stack_ims',
    'file_io.py::_open_stack_generic',
    'file_io.py::_open_czi_streaming',

    # 1.6.146 — the `_ZarrTYX` lazy IMS wrapper MOVED to `file_io/lazy_sources.py` (decomposition, move
    # 5), joining `_TiffPageStack` & friends in the Qt-free lazy-wrapper home. `file_io` re-exports it.
    'file_io.py::_ZarrTYX',

    # 1.6.147 catch-up — _ZarrTYX's dunders moved to lazy_sources.py with the class (they live as
    # lazy_sources.py::<name> now); `_file_has_imaging_metadata` was renamed to the `_safe` form.
    'file_io.py::__array__',
    'file_io.py::__len__',
    'file_io.py::_file_has_imaging_metadata',

    # 1.6.149 — MenuManager (+ its many nested helpers) MOVED from ui_modules.py to
    # ui/menu_manager.py (decomposition Phase 2). Behaviour-preserving, re-exported; the menu-
    # contract snapshot (test_menu_contract.py) proves not one action changed. Now menu_manager.py::<name>.
    'ui_modules.py::_activate',
    'ui_modules.py::_add_actions_to_menu',
    'ui_modules.py::_add_analysis_methods_to_menu',
    'ui_modules.py::_add_file_io_methods_to_menu',
    'ui_modules.py::_add_toolbox_to_menu',
    'ui_modules.py::_anchor_key',
    'ui_modules.py::_annotation_layers',
    'ui_modules.py::_apply_managed_grid',
    'ui_modules.py::_apply_override',
    'ui_modules.py::_ask_clear_or_add',
    'ui_modules.py::_autotag_user_layer',
    'ui_modules.py::_current_layer',
    'ui_modules.py::_dead',
    'ui_modules.py::_disable_in_menu',
    'ui_modules.py::_disable_napari_open_actions',
    'ui_modules.py::_enable_drops',
    'ui_modules.py::_export',
    'ui_modules.py::_fmt',
    'ui_modules.py::_fmt_interval',
    'ui_modules.py::_gather_compared_metadata',
    'ui_modules.py::_grid_tileable_visible',
    'ui_modules.py::_hide_napari_native_menus',
    'ui_modules.py::_home_fit_view',
    'ui_modules.py::_image_layer_present',
    'ui_modules.py::_is_load_action',
    'ui_modules.py::_key',
    'ui_modules.py::_maybe_warn_metadata_diff',
    'ui_modules.py::_nm',
    'ui_modules.py::_norm',
    'ui_modules.py::_obj_is_sample_loader',
    'ui_modules.py::_on_foreign_layer_inserted',
    'ui_modules.py::_on_grid_layer_vis_changed',
    'ui_modules.py::_on_grid_layers_changed',
    'ui_modules.py::_open_image_add',
    'ui_modules.py::_open_session_loader',
    'ui_modules.py::_populate',
    'ui_modules.py::_process_foreign_layers',
    'ui_modules.py::_reassert_canvas_drops',
    'ui_modules.py::_refresh',
    'ui_modules.py::_refresh_table',
    'ui_modules.py::_reorder_pycat_menu_bar',
    'ui_modules.py::_restore_grid_removed_layers',
    'ui_modules.py::_route',
    'ui_modules.py::_route_one',
    'ui_modules.py::_score',
    'ui_modules.py::_set_napari_menus_visible',
    'ui_modules.py::_setup_menu_bar',
    'ui_modules.py::_show_metadata_comparison',
    'ui_modules.py::_show_metadata_dialog',
    'ui_modules.py::_show_recorded_steps_dialog',
    'ui_modules.py::_sweep',
    'ui_modules.py::_toggle_grid_view',
    'ui_modules.py::_toggle_napari_menus',
    'ui_modules.py::make_lambda',
    'ui_modules.py::open_command_palette',
    'ui_modules.py::open_tag_inspector',

    # 1.6.150 — the batch replay handlers + their shared helpers MOVED from batch_step_registry.py into
    # the pycat.batch.steps package (decomposition, split by name-prefix family). _STEP_MAP stays in
    # batch_step_registry.py and imports them; route-equivalence + the composition guard prove replay is
    # unchanged. (_flatten_scalars/_proc are nested helpers that moved with their parent handler.)
    'batch_step_registry.py::_derive_split_companion_path',
    'batch_step_registry.py::_flatten_scalars',
    'batch_step_registry.py::_get_data',
    'batch_step_registry.py::_ivf_droplet_mask_and_image',
    'batch_step_registry.py::_load_image',
    'batch_step_registry.py::_normalize_to_float',
    'batch_step_registry.py::_proc',
    'batch_step_registry.py::_raw_counts',
    'batch_step_registry.py::_resolve_channel_for_layer',
    'batch_step_registry.py::_resolve_image_layer',
    'batch_step_registry.py::_save_array',
    'batch_step_registry.py::_source_path_for_recorded_channel',
    'batch_step_registry.py::replay_auto_crop_roi',
    'batch_step_registry.py::replay_bf_cell_segmentation',
    'batch_step_registry.py::replay_bf_condensate_segmentation',
    'batch_step_registry.py::replay_bf_preprocess',
    'batch_step_registry.py::replay_calibration_correction',
    'batch_step_registry.py::replay_cell_analysis',
    'batch_step_registry.py::replay_cellpose_segmentation',
    'batch_step_registry.py::replay_condensate_analysis',
    'batch_step_registry.py::replay_condensate_segmentation',
    'batch_step_registry.py::replay_ivbf_preprocess',
    'batch_step_registry.py::replay_ivbf_segmentation',
    'batch_step_registry.py::replay_ivf_field_summary',
    'batch_step_registry.py::replay_ivf_preprocess',
    'batch_step_registry.py::replay_ivf_segmentation',
    'batch_step_registry.py::replay_ivf_size_distribution',
    'batch_step_registry.py::replay_ivf_spatial_metrology',
    'batch_step_registry.py::replay_measure_line',
    'batch_step_registry.py::replay_open_image',
    'batch_step_registry.py::replay_open_stack',
    'batch_step_registry.py::replay_preprocessing',
    'batch_step_registry.py::replay_sacf_analysis',
    'batch_step_registry.py::replay_save_and_clear',
    'batch_step_registry.py::replay_set_frame_range',
    'batch_step_registry.py::replay_ts_cellpose_keyframe',
    'batch_step_registry.py::replay_upscaling',

    # 1.5.517 — de-duplicated. These were defined TWICE, byte-identically, in file_io.py AND
    # stack_access.py. `stack_access` now owns them and `file_io` RE-EXPORTS, so every one of the
    # 25 existing `from pycat.file_io.file_io import materialize_stack` call sites still works.
    # Verified at the time and again here.
    'file_io.py::materialize_stack',
    'file_io.py::iter_frames',
    'file_io.py::layer_is_stack',
    'file_io.py::extract_2d_plane',
    'file_io.py::warn_if_assumed_axis',

    # 1.6.5 — the status-bar flicker. `_on_mouse_move` appended a `mouse_move_callbacks` handler
    # that wrote `viewer.status`. **But napari writes `viewer.status` on the same event**, so both
    # fired and whichever ran last won — the bar alternated between two strings as the mouse moved.
    #
    # **Racing napari's writer cannot be won.** The readout now wraps the layer's `get_status()`,
    # which is where napari SOURCES the string — one writer, one string, no order to depend on.
    # `_on_mouse_move` is gone because the approach it embodied was wrong.
    'coordinate_readout.py::_on_mouse_move',

    # 1.6.9 — `_ZarrTYX_generic` was DELETED, and its `__getitem__(self, idx)` went with it.
    #
    # **It was named after the wrong thing.** It is not zarr-specific: it received zarr arrays,
    # numpy arrays AND BioIO dask arrays — and the name told every reader it could rely on zarr
    # semantics it does not have. *Worse, the TZYX branch transcoded the whole file into a
    # temporary zarr before showing anything, purely so it would have a zarr to wrap.*
    #
    # `_LazyArraySource.__getitem__(self, index)` replaces it, and was verified to behave
    # IDENTICALLY on every indexing pattern napari uses on a (T, Y, X) layer: stack[t],
    # stack[t, :, :], stack[t, y0:y1, :], stack[t0:t1].
    #
    # The parameter is not lost — it is `index` rather than `idx`. **A rename, not a removal.**
    'file_io.py::__getitem__',

    # 1.6.15 — `transpose()` DELETED from `_ZarrTYX`, `_TiffPageStack` (file_io.py) and
    # `_ZarrStack` (timeseries_condensate_tools.py). All three read::
    #
    #     def transpose(self, *axes):
    #         return self.__getitem__(0)[np.newaxis]
    #
    # **Whatever axes you asked for, you got frame 0**, shaped (1, Y, X), and nothing about the
    # result looked wrong. It is the same lie `__array__` was fixed for in 1.6.3 — and it survived
    # that fix because the guard checked `__array__` and nothing else.
    #
    # **Absence is the honest implementation, and it is proven.** The three `_ImsReader*` wrappers
    # have never defined `transpose`, and one of them carries the 600-plane IMS file that scrubs at
    # 0.5% of scene. napari duck-types for the method; not having it is a path napari already takes
    # every time it touches an IMS layer.
    #
    # A caller that genuinely needs a transposed stack must say so: `materialize_stack(...)`.
    'file_io.py::transpose',
    'timeseries_condensate_tools.py::transpose',

    # 1.6.15 — RENAMED, not removed. Both were named after a LIBRARY that is no longer used, which
    # obscures which behaviour belongs to the shared structured-reader interface and which is
    # genuinely backend-specific — the exact question the whole 1.6 migration turned on.
    #
    #     extract_aicsimage_metadata        → extract_reader_metadata
    #     extract_channel_info_from_aicsimage → extract_channel_info
    #
    # Every call site was updated in the same change (4 and 4 respectively, all internal).
    # 1.6.29 — EXTRACTED, not deleted. **The cascade, again.**
    #
    # `load_into_viewer` -> `file_io/viewer_load.py`. It is what the 2-D loader, the mask loader and
    # BOTH stack loaders call once they have an array. It is a dependency of FIVE other methods, and
    # it depended on two — `_enable_auto_scale_bar` and `_tag_loaded_layer`, both extracted in the
    # previous two releases. **Taking it now unblocks the tier above it.**
    #
    # `_auto_clear_before_load` + `clear_all_without_saving` -> `file_io/session.py`, with the
    # `_clear_everything` they both call.
    #
    # `determine_file_format_and_process_data` -> `viewer_load.py`: a ten-line legacy shim that
    # touched `self` for nothing at all.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched — including
    # `batch_processor`, which calls `clear_all_without_saving(viewer, confirm=True)` with a
    # POSITIONAL viewer.)
    'file_io.py::load_into_viewer',
    'file_io.py::_auto_clear_before_load',
    'file_io.py::clear_all_without_saving',
    'file_io.py::determine_file_format_and_process_data',

    # 1.6.28 — EXTRACTED, not deleted. **The cascade.**
    #
    # `_tag_loaded_layer` + `_prompt_pixel_size_if_needed` -> `file_io/tagging.py`
    # (`_calibration_is_from_metadata` went with them — calibration provenance is a fact about the
    # LAYER, and nothing else called it).
    #
    # `_finalise_stack_load` -> `file_io/stack_load.py`. **It could not have come out before this
    # release.** It depended on FIVE methods of its host — and all five had been extracted by the
    # previous moves:
    #
    #     _enable_auto_scale_bar / _fit_view_to_layer / _add_diameter_annotation_layers -> napari_adapter
    #     _tag_loaded_layer / _prompt_pixel_size_if_needed                              -> tagging
    #
    # *Take what depends on nothing; the next layer then depends on nothing, and comes out free.*
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_tag_loaded_layer',
    'file_io.py::_prompt_pixel_size_if_needed',
    'file_io.py::_finalise_stack_load',
    'file_io.py::_calibration_is_from_metadata',

    # 1.6.27 — EXTRACTED, not deleted.
    #
    # `_clear_everything` -> `file_io/session.py`. **It is not doing I/O, it is UNDOING it** —
    # removing layers, emptying the repository, dropping cached readers and their open handles. It
    # depends on `viewer` and `central_manager` and nothing else.
    #
    # `_add_diameter_annotation_layers` -> `file_io/napari_adapter.py`. It takes **only `viewer`**
    # and creates napari layers. It was never file I/O.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_clear_everything',
    'file_io.py::_add_diameter_annotation_layers',

    # 1.6.26 — EXTRACTED to `file_io/dialogs.py`, not deleted.
    #
    # **Asking the user is not reading the file.** Two of these kept their memory on `self` —
    # `self._multipage_axis_choice` ("remember my answer this session") and `self._local_cache_files`
    # — and **neither was ever read by another method.** They were scratch variables that happened to
    # be spelled as attributes of a 3,108-line class; they are now module-level, which is what they
    # always were.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_ask_copy_to_local',
    'file_io.py::_copy_to_local_with_progress',
    'file_io.py::_ask_multipage_axis',

    # 1.6.25 — EXTRACTED to `file_io/routing.py`, not deleted.
    #
    # **Four methods that never touched `self`.** They took `(self, file_path)` and used the `self`
    # for *nothing at all* — static functions wearing method clothes, wedged into a 3,108-line class
    # between the loaders, the dialogs and the lazy wrappers.
    #
    # They answer a question about a **path**: does this file carry real imaging metadata? did PyCAT
    # write it? does it carry an embedded tag store? is it an undeclared multipage TIFF? *No viewer,
    # no repository, no reader.*
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_file_has_imaging_metadata_safe',
    'file_io.py::_read_pycat_signifier',
    'file_io.py::_read_pycat_tags',
    'file_io.py::_tiff_multipage_undeclared',

    # 1.6.24 — EXTRACTED to `file_io/writers.py`, not deleted.
    #
    # **Writing files is not reading them, routing them, or showing them.** `_save_layer` is 243
    # lines and depended on exactly ONE thing from its 3,108-line host: `self.central_manager`.
    # `_apply_saved_tags_to_layer` depended on **nothing at all**.
    #
    # `atomic_write` moved with them — it *is* a writer concern, and leaving it behind would make
    # `writers.py` import its former host, which is a cycle. **`file_io` imports it back**, because
    # the other save paths still use it.
    #
    # (`FileIOClass` keeps a delegating stub for each method, so every caller is untouched.)
    # ...and the seven helpers NESTED INSIDE `_save_layer`, which moved with it. They are defined
    # inside the function body, so the guard tracks them as `file_io.py::<name>` — but they now
    # live in `writers.py::_save_layer`, unchanged.
    'file_io.py::_frame',
    'file_io.py::_frames',
    'file_io.py::_mask_frames',
    'file_io.py::_minimal_label_dtype',
    'file_io.py::_pycat_tag',
    'file_io.py::_to_label_array',
    'file_io.py::_to_uint16',

    'file_io.py::_save_layer',
    'file_io.py::_apply_saved_tags_to_layer',
    'file_io.py::atomic_write',

    # 1.6.60 — EXTRACTED to `file_io/readers/ims_reader.py`, not deleted (god-class decomposition
    # #3). The three lazy IMS wrapper classes (`_ImsReaderTYX`/`ZYX`/`TZYX`) and their pure helpers
    # moved out of `file_io.py`; `_open_stack_ims` is unchanged and `file_io` IMPORTS the classes +
    # `_suppress_ims_chunk_prints` + `_ims_pixel_size_um` back, so every caller is untouched.
    #
    # These six are the free functions / methods that moved with them and so no longer parse as
    # `file_io.py::<name>`:
    #   _suppress_ims_chunk_prints, _ims_indices, _ims_pixel_size_um  -> module-level in ims_reader.py
    #   _ims_frame_2d                                                 -> module-level (the classes'
    #        only caller; leaving it in file_io would have been an import cycle)
    #   _read_plane                                                   -> method of _ImsReaderZYX /
    #        _ImsReaderTZYX, now in ims_reader.py
    #   _to_float                                                     -> nested inside
    #        ims_reader.py::_ims_pixel_size_um, unchanged
    'file_io.py::_suppress_ims_chunk_prints',
    'file_io.py::_ims_indices',
    'file_io.py::_ims_pixel_size_um',
    'file_io.py::_ims_frame_2d',
    'file_io.py::_read_plane',
    'file_io.py::_to_float',

    # 1.6.24 — EXTRACTED to `file_io/napari_adapter.py`, not deleted.
    #
    # **The camera, the scale bar, and the layer-scale alignment are napari DISPLAY. They are not
    # file I/O** — they read the viewer and write the viewer, and they touch **no file, no reader,
    # no path.** They were sitting in the middle of a 3,108-line `FileIOClass` whose other 31
    # methods open, route, tag and save images.
    #
    # These four were the cleanest cut in the class: they depend on `viewer` and `central_manager`
    # and *nothing else*. They come out as plain functions with no loss, and what is left behind is
    # 237 lines smaller and one responsibility lighter.
    #
    # *The bodies did not shrink. They MOVED — and the guard's real question, "did the rationale in
    # the deleted lines survive somewhere?", is answered: `napari_adapter.py`.*
    #
    # (`FileIOClass` keeps a 3-line delegating stub for each, so every caller is untouched.)
    'file_io.py::_align_layer_scales',
    'file_io.py::_enable_auto_scale_bar',
    'file_io.py::_update_scale_bar_for_active_layer',
    'file_io.py::_fit_view_to_layer',

    # 1.6.62 — SHRUNK by extraction, not truncated (god-class decomposition #5). `_open_stack_generic`
    # went 313 → 186 lines because its metadata-read head and its per-branch lazy-wrapper construction
    # were lifted into pure modules, NOT because logic was dropped:
    #   * the head (structured reader → dims/scenes/pixel size, else the tifffile-page fallback)
    #     → `readers/stack_metadata.py::read_stack_structure`;
    #   * the four lazy branches (tifffile-fallback / time-series / z-stack / T-Z, incl. the zarr-3.2
    #     shim + multi-file OME handling) → `readers/stack_layer_builders.py`;
    #   * their shared tail (retain + contrast-pin + add_image + projection + announce) → the new
    #     `_add_lazy_stack_layer` method.
    # Every branch's behaviour — the wrappers, the retention (incl. the T-Z branch retaining nothing),
    # the contrast pinning — is preserved; the controller now orchestrates rather than inlines.
    'file_io.py::_open_stack_generic',

    'metadata_extract.py::extract_aicsimage_metadata',
    'channel_naming.py::extract_channel_info_from_aicsimage',

    # 1.6.67 — complexity-ratchet unblock (147 -> 135). These pure-Qt UI BUILDERS shrank >30%
    # because a contiguous block of widget construction / signal wiring was EXTRACTED INTO A HELPER
    # IN THE SAME FILE — not deleted. Every line survives (in `_build_*` / `_present_*` helpers);
    # same widgets, same order, same signals, zero science touched. The shrink is the move, not a
    # truncation. (The other functions split in the same pass shrank <30% and don't need an entry.)
    'frap_ui.py::_add_analysis',                                    # -> _build_fit_model
    'pipeline_snr_tools.py::_add_pipeline_snr_analysis',            # -> _build_snr_panel_widgets
    'spatial_randomness_tools.py::_add_spatial_randomness',         # -> _build_spatial_randomness_form
    'timeseries_condensate_tools.py::_add_ts_upscale_stack',        # -> _build_ts_upscale_check_ui
    'ts_cellpose_tools.py::_on_finished',                           # -> _present_transfection_filter

    # 1.6.70 — MOVED, not removed: `file_io.py` -> `lazy_sources.py`.
    #
    # `_TiffPageStack` and `_LazyArraySource` sat beside two `QDialog` subclasses in a module that
    # imports PyQt5 at module scope, so **reaching a TIFF lazy wrapper dragged in the whole GUI
    # stack** and the wrappers could not be exercised headlessly — which is precisely what a perf
    # harness or a CI perf gate needs to do. Their bodies never needed Qt; only their address did.
    #
    # The bodies moved VERBATIM. `file_io.py` re-exports both class names (plus the two OME
    # helpers), so every existing `from pycat.file_io.file_io import _TiffPageStack` caller —
    # `test_vpt_gpu_equivalence.py` does it twice — still resolves, exactly as with the five stack
    # helpers above. `tests/test_lazy_sources_headless.py` pins the new module's Qt-free contract
    # and re-checks the re-export identity.
    #
    # `_page_index` / `_get_handle` / `_read_frame` / `as_full_array` / `close` are
    # `_TiffPageStack` methods and travelled inside the class.
    'file_io.py::_page_index',
    'file_io.py::_get_handle',
    'file_io.py::_read_frame',
    'file_io.py::as_full_array',
    'file_io.py::close',
    # The OME file-set helpers are `_TiffPageStack`'s multi-file machinery and have no other
    # caller, so they moved with it — `lazy_sources` cannot import them back from `file_io`
    # (that would be a hard circular import, since `file_io` now imports `lazy_sources`).
    'file_io.py::resolve_ome_file_set',
    'file_io.py::build_ome_page_map',
}

# Qt widget plumbing. A `__init__` losing `parent`, or a callback losing an index, is a Qt idiom
# change — not a lost scientific capability. Kept separate from the list above because the risk is
# different in kind.
_QT_PLUMBING = {
    'label_and_mask_tools.py::__init__',
    'pixel_wise_corr_analysis_tools.py::__init__',
    'two_channel_coloc_tools.py::__init__',
    'two_channel_coloc_tools.py::_cb',
    'ui_utils.py::__init__',
    'file_io.py::add_image_or_mask',
    'file_io.py::open_image_auto',
    'file_io.py::_file_has_imaging_metadata',
}

_ALLOWED = _DELIBERATE | _QT_PLUMBING


def _current_signatures():
    found = {}
    for path in (_ROOT / "src" / "pycat").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            found[f"{path.name}::{node.name}"] = dict(
                lines=(node.end_lineno or node.lineno) - node.lineno,
                params=set(a.arg for a in node.args.args + node.args.kwonlyargs))
    return found


@pytest.mark.core
def test_no_SCIENTIFIC_PARAMETER_has_been_dropped():
    """**A lost parameter is a lost capability, not a refactor.**

    ``punctate_gate`` disappearing from ``segment_subcellular_objects`` is the difference between
    *"this cell is empty"* and *"this cell's noise has been stretched to look like signal."*
    """
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing — run tools/check_for_dropped_code.py to build it")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    dropped = []
    for key, best in high_water.items():
        if key in _ALLOWED or key not in current:
            continue

        lost = set(best['params']) - current[key]['params']
        if lost:
            dropped.append(f"{key}  LOST: {sorted(lost)}")

    assert not dropped, (
        "these functions have LOST PARAMETERS they once had:\n  "
        + "\n  ".join(sorted(dropped))
        + "\n\n**A lost parameter is a lost CAPABILITY.** The code still compiles and the tests "
          "still pass — that is exactly how `punctate_gate` disappeared and spurious puncta came "
          "back.\n\n"
          "If the removal was deliberate, add the key to `_DELIBERATE` **with a reason**."
    )


@pytest.mark.core
def test_no_FUNCTION_has_vanished():
    """A function that was there and is not is either a **deliberate move** or a **truncated
    rewrite**. *The guard cannot tell which, and should not try — it asks.*"""
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    vanished = sorted(k for k in set(high_water) - set(current)
                      if k not in _ALLOWED and not k.split('::')[1].startswith('__'))

    assert not vanished, (
        "these functions existed once and do not now:\n  " + "\n  ".join(vanished)
        + "\n\nIf a function was MOVED, does the old import still work? (That is what happened to "
          "the five stack helpers — `file_io` re-exports them.) Add it to `_DELIBERATE` **with a "
          "reason**."
    )


@pytest.mark.core
def test_no_FUNCTION_BODY_has_been_truncated():
    """**The signature of a truncated rewrite:** the function survives, its parameters survive, and
    its **body is a third shorter.**

    ``cell_mask_stretching`` went from **146 lines to 85** and lost its gain ceiling — *and its
    signature still had two of its four parameters, so a signature check alone would have missed
    it.*
    """
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    truncated = []
    for key, best in high_water.items():
        if key in _ALLOWED or key not in current:
            continue

        was, now = best['lines'], current[key]['lines']
        if was >= 25 and now < was * _SHRINK_THRESHOLD:
            truncated.append(f"{key}:  {was} -> {now} lines  (-{100 * (was - now) // was}%)")

    assert not truncated, (
        "these function bodies have SHRUNK by more than 30%:\n  " + "\n  ".join(sorted(truncated))
        + "\n\nThat is the signature of a rewrite that dropped code. **Did the rationale in the "
          "deleted lines survive somewhere?** If the shrink was deliberate, add the key to "
          "`_DELIBERATE` **with a reason**."
    )
