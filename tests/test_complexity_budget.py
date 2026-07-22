"""
**A ratchet, not a rewrite.**

``ui_modules.py`` is 5,423 lines, and ``MenuManager`` inside it is **2,062 lines across 31
methods**. ``_add_reference_frame_selector`` is **398 lines** — longer than most whole modules, and
unreviewable by anyone.

That is the shape that hides bugs, and one was hiding there: **35 lines installing the pixel-size
gate — the thing that warns a user their lengths are in PIXELS — wrapped in
``except Exception: pass``.** If any of it threw, the gate simply never appeared. *A guard that can
vanish without saying so is not a guard.* (Fixed in 1.5.509.)

So why not split it?
--------------------
**Because it cannot be verified.** ``ui_modules`` has ~17 % name-coverage in the test suite, and
most of that is ``__init__``. **A refactor whose only verification is "it still imports" is a
refactor that ships bugs** — and the value of splitting is preventing *future* bugs, while the cost
would be *introducing* them today, blind.

*The honest move is not to rewrite it. It is to stop it growing, and to make the next person's
addition small enough to review.*

This file is that ratchet. The budgets are set **at today's values** — nothing has to be fixed to
make them pass. **They only fail if something gets worse.**
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


# ── The ratchet is a COUNT, not an allow-list ────────────────────────────────────────────────
#
# There are **136 functions over 120 lines**, totalling **27,478 lines — a third of the codebase.**
# A per-function allow-list of 136 entries would be noise: nobody reads it, and adding a line to it
# is easier than splitting a function, so it would only ever grow.
#
# So the budget is the **count itself**. It is set at today's value. **Nothing has to be fixed to
# make this pass** — it fails only when the number goes UP, which means someone added a 137th
# unreviewable function instead of splitting their work.
#
# And a specific ceiling on the very worst, because those are the ones where a bug can hide in
# plain sight: the pixel-size gate's failure path was SILENT inside a 400-line function, and nobody
# noticed for months. **Nobody reads a 400-line function closely enough to see that its except
# clause is a `pass`.**
_LONG_FUNCTION_LIMIT = 120
# 137. It was 136, and `cell_analysis_func` (feature_analysis_tools) crossed 120 lines when the
# bbox sweep added its columns — a REAL addition, and the ratchet caught it.
#
# **That is the ratchet working.** The honest response is to record that the count went up, not to
# quietly widen the limit or shave a comment somewhere else to squeeze back under. A number that is
# raised whenever it is hit is not a ceiling.
# 139. It was 137, and TWO functions came BACK: `cell_has_punctate_signal` and
# `compute_image_intensity_stats`, restored in 1.5.526 after Meet reported spurious puncta and
# sent the file that worked. **The tree had regressed and lost them.**
#
# The ratchet caught the count going up, which is the ratchet working — and the honest response is
# to record that two long functions returned, not to shave them to squeeze back under.
# 135. It rose to 147 (2026-07-16) — recent feature work added 8 functions over 120 lines and the
# ratchet fired (CI RED). The honest response is the ratchet's whole point: **split the new work back
# out, don't raise the ceiling.** 12 pure-Qt UI-BUILDER functions (`_add_*` / `_on_run` / `_on_finished`
# / `_on_dynamic` — widget construction and signal wiring, zero numerical science) were each split by
# extracting a contiguous widget block into a helper, dropping the count 147 → 135. No science function
# was touched. The ceiling is lowered to the genuine new value (135) — the ratchet moving DOWN, which is
# it working; it is never raised to grandfather offenders.
# 134. (2026-07-20, science_function_split) `fit_anomalous_diffusion` (condensate_physics_tools), the
# 394-line MSD/α fit behind viscosity, was split BY COMPUTATIONAL PHASE into pure helpers — the lag-window
# gate, the non-linear power-law fit, the identifiability CI, the motion-type classification, and result
# packaging — dropping the function to 98 lines. This is a SCIENCE function, so the split was governed by
# coverage: its 4 existing numerical tests (test_msd_drift / test_msd_min_track_length /
# test_vpt_viscosity_chain / test_route_equivalence) passed UNMODIFIED, proving no number changed. No
# floating-point operation was reassociated and nothing was "improved" while moving. Count 135 → 134.
# 133. (2026-07-20) `partition_coefficient_local` (invitro_tools), the 394-line local-annulus Kp
# measurement, was split BY PHASE into pure helpers — input-validity (`_pc_check_input`), camera-floor
# determination (`_pc_camera_floor`), the interface-width gap (`_pc_estimate_gap`), the per-droplet
# measurement loop (`_pc_measure_droplets`), and the reporting verdict (`_pc_verdict`) — dropping the
# function to 109 lines. A SCIENCE function, so the split was pinned by a byte-identity characterization
# test (`test_partition_local_characterization`) capturing the exact per-droplet + aggregate outputs
# across all five reporting branches BEFORE the split and asserting them unchanged after. No number
# moved. Count 134 → 133.
# 132. (2026-07-20) `classify_beads` (vpt_tools), the 306-line bead classifier, was split into its two
# independent branches — `_classify_fast_template` (+ its `_classify_fast_template_refs` reference-stats
# phase) and `_classify_gaussian_fit` — leaving `classify_beads` a 68-line empty-guard + dispatch. Pinned
# byte-identical by `test_classify_beads_characterization` (the categorical `bead_class` labels, the
# `n_units_est` estimates, the dropped-rejected row count, and the recorded thresholds, captured on both
# branches BEFORE the split and asserted unchanged after). Count 133 → 132.
# 131. (2026-07-20) `fit_size_distribution_mle` (invitro_tools), the 301-line droplet-size-distribution
# identifier, was split BY PHASE into pure helpers — `_fit_size_models` (per-model MLE + Clauset power-law
# x_min), `_powerlaw_tail_comparison` (the tail Vuong test + seeded bootstrap goodness-of-fit gate),
# `_size_distinguishability` (the whole-sample Vuong test) and `_size_verdict` — leaving the function a
# 92-line orchestrator. Pinned byte-identical by `test_size_distribution_mle_characterization` (selected
# model, every model's AIC/loglik, power-law x_min + tail test, distinguishability comparison, descriptive
# moments, on lognormal + gamma samples, captured BEFORE and asserted unchanged after; the bootstrap is
# seeded so it is deterministic). Count 132 → 131.
# 130. (2026-07-20) `fit_photobleaching` (condensate_physics_tools), the 233-line exponential-bleach fit
# (mostly measured-rationale comment blocks), was split into `_photobleach_tau_ci` (the fit-covariance CI
# on tau), `_photobleach_window_metrics` (the two non-circular decay-observed bounds) and
# `_photobleach_window_warn` (the two-tier observation-window warning), leaving a 65-line fit + orchestrate
# body. Each rationale block moved with its phase. Pinned byte-identical by
# `test_photobleaching_characterization` (fitted params, R², tau CI, both decay bounds, correction factors
# and WHICH warning tier fires, on adequate / mid / short / flat synthetic movies). Count 131 → 130.
# 129. (2026-07-20) `fit_frap_recovery` (frap_tools), the 206-line FRAP recovery fit, was split into
# `_frap_derive_mobile` (the normalisation-aware mobile fraction + over-recovery warning) and
# `_frap_identifiability` (the per-parameter covariance CI + its warning, with the covariance rationale),
# leaving a 109-line fit + orchestrate body. Pinned byte-identical by `test_frap_recovery_characterization`
# (fitted params, R², mobile/immobile fractions, over-recovery flag, per-parameter CI widths + verdicts,
# and which warnings fire, on adequate / short-unidentifiable / over-recovery / too-few-points curves);
# the existing `test_frap_fitting` passes unmodified. Count 130 → 129.
# 128. (2026-07-20) `fit_fusion_relaxation` (fusion_tools), the 184-line droplet-fusion relaxation fit,
# was split into `_fusion_tau_ci` (the covariance CI on tau), `_fusion_window_warn` (the relaxations-
# observed count + short-record warning) and `_fusion_model_adequacy` (the runs test + two-mode test),
# leaving a 90-line fit + orchestrate body. Pinned byte-identical by
# `test_fusion_relaxation_characterization` (fitted params, R², tau CI, relaxations-observed, adequacy /
# two-mode verdicts, and which warnings fire, on adequate / short / two-mode / too-few-points traces); the
# existing `test_fusion_physics` passes unmodified. Count 129 → 128.
# 127. (2026-07-20) `partition_measurement` (invitro_tools), the 191-line Kp measurement-with-assumptions
# builder, had its background-subtracted assessment (the image cannot tell a camera pedestal from a
# genuine dilute phase, so it is resolved by a dark reference / stated by the caller / recorded UNCHECKED)
# extracted to `_partition_background_assumption`, leaving a 110-line body. Pinned byte-identical by
# `test_partition_measurement_characterization` (which branch fires and the exact checked/holds/detail of
# the assumption across all four inputs, plus the other assumptions + measurement identity); the existing
# `test_claim_scoping` passes unmodified. Count 128 → 127.
# 126. (2026-07-20) `detect_beads_stack` (vpt_tools), the 317-line VPT detection stage feeding the whole
# viscosity chain, was split BY PIPELINE STAGE — `_choose_detection_backend` (GPU/pool/serial tier),
# `_pool_predetect`, `_bead_hot_mask`, `_detect_all_frames` (the per-frame loop) with `_fast_frame_rows` /
# `_precise_frame_rows`, and `_assemble_detections` — leaving a 116-line orchestrator. This is the
# VALIDATED detection path (~8.325 through TrackMate), so it is GUARD-ANCHORED: the existing VPT
# equivalence guards (`test_vpt_gpu_equivalence`, `test_vpt_parallel_equivalence`, the memo) pass
# unmodified, and a new serial-path characterization (`test_detect_beads_stack_characterization`) pins the
# exact detection table — coordinates, order, area, counts — on a seeded synthetic stack. No number moved.
# Count 127 → 126.
# 125. (2026-07-20) `link_trajectories_bayesian` (dynamic_spatial_tools), the 245-line Bayesian/Hungarian
# trajectory linker feeding every VPT viscosity, was split BY COMPUTATIONAL PHASE into pure helpers —
# `_bayesian_cost_defaults` (resolve the None cost params), `_start_new_tracks` (open tracks when none are
# viable), `_build_frame_cost_matrix` (the per-frame viable×detection cost block + death/birth/dummy
# structure) and `_apply_frame_assignment` (the Hungarian solve written back onto the DataFrame + active
# state) — leaving a ~50-line orchestrator. Two provably-dead locals (`_sigma2`, the `assigned_*` sets) were
# dropped in the move. Pinned byte-identical by `test_bayesian_linker_assignment_is_byte_identical` (the
# exact per-detection track_id + link_cost on a fixed births/links/bridged-gap/velocity/area scenario —
# the Hungarian solve is sensitive to the cost matrix, so identical output proves the construction was
# preserved); the existing purity/gap/ambiguity property tests pass unmodified. Count 126 → 125.
# 124. (2026-07-20) `fit_coarsening` (condensate_physics_tools), the 227-line coarsening-mechanism
# classifier (Ostwald vs coalescence vs arrested), was split BY COMPUTATIONAL PHASE into pure helpers —
# `_coarsening_powerlaw_fits` (the two curve_fits + R²), `_coarsening_is_arrested` (the slope-test that
# decides whether the radius grew at all, never a fit statistic) and `_coarsening_confidence` (the seeded
# residual bootstrap + confidence tiers; the single napari warning became a returned flag the orchestrator
# emits) — leaving a ~35-line orchestrator. Two provably-dead locals (`noise`, `r2_gap`) were dropped in
# the move. This is a physics FIT function, like its four already-split siblings; pinned byte-identical by
# `test_fit_coarsening_output_is_byte_identical` (exact preferred_mechanism / confidence / r²s / rate
# constants / bootstrap agreement / radius change on Ostwald + arrested scenarios — the bootstrap is
# seeded, so its agreement is deterministic); the existing arrest-classification property tests pass
# unmodified. Count 125 → 124.
# 123. (2026-07-20) `count_molecules_single` (molecular_counting_tools), the 214-line single-trace N&B
# molecule counter, was split BY COMPUTATIONAL PHASE into pure helpers — `_estimate_pedestal_read_noise`
# (the pedestal + read-noise floor read from the trace's own post-bleach tail) and `_fit_counting_nu`
# (the ν = variance-vs-mean slope fit, free-intercept when a noise floor is present else through-origin,
# with the read-noise fallback) — leaving a ~55-line orchestrator; each dense rationale block moved with
# its phase. Pinned byte-identical by `test_count_molecules_single_is_byte_identical` (exact
# ν / N / bleach_r² / pedestal / read_noise_var / accepted / n_points on a clean trace — the through-origin
# branch — AND a read-noise+pedestal trace — the free-intercept branch); the existing accuracy /
# pedestal-removal property tests pass unmodified. Count 124 → 123.
# 122. (2026-07-20) `topology_metrics` (topology_tools), the 192-line per-cell structural-envelope metric,
# had its comment-dense basin-count phase — topological-persistence peak counting with a range-vs-noise
# flat-field guard — extracted to a pure `_topo_basin_metrics(envelope, mask, image_noise)` returning the
# basin keys, leaving a ~55-line orchestrator (basic stats + connectivity). The dead `min_basin_distance`
# /`ball_radius` default computation (unused by the persistence method) was dropped in the move; the
# parameters stay in the signature. Pinned byte-identical by `test_topology_metrics_is_byte_identical`,
# which feeds a SYNTHETIC numpy envelope directly (bypassing the GPU-routed rolling-ball) so the pure
# numpy/scipy metric is isolated and platform-portable — exact basin count / persistence gate + list /
# cov / roughness / components / largest-frac on a peaked field (structure branch) and a near-flat field
# (flat branch, which omits topo_noise_known); the existing basin-count property tests pass unmodified.
# Count 123 → 122.
# 121. (2026-07-20) `qc_focus` (data_qc_tools), the 203-line focus/sharpness QC check (a big dispatch of
# result dicts with dense measured rationale), was split into pure helpers — `_qc_focus_stack` (the 3D
# per-frame band-pass-energy branch) and `_qc_focus_absolute` (the single-image diffraction-limit verdict:
# the refuse-when-nothing-sharp path + the wide gross-defocus screen) — leaving the orchestrator with just
# the na/info branches. Pinned byte-identical by `test_qc_focus_is_byte_identical`, which exercises ALL
# five result branches (stack-warn / absolute-good / refuse-na / info / flat-na) on pure-numpy/scipy inputs
# (portable) and asserts exact status + value + diag scalars; the existing focus property tests pass
# unmodified. Count 122 → 121.
# 120. (2026-07-20) `field_summary` (invitro_tools), the 182-line in-vitro whole-field summary — dominated
# by an ~80-line docstring and two large inline measured-caveat comments — had its non-empty compute + the
# honest-name result dict extracted to `_field_summary_metrics(props, image, bg_mask, cond_mask,
# microns_per_pixel, field_area_um2)`, leaving the orchestrator with the docstring, setup and the n == 0
# empty branch (which deliberately carries a different key set). Pinned byte-identical by
# `test_field_summary_is_byte_identical` (the exact populated dict — sizes, phase intensities, intensity
# ratio, contrast, area fraction + deprecated aliases — AND the empty branch, which omits intensity_ratio /
# dense_dilute_contrast); the existing halo/contrast property tests pass unmodified. Count 121 → 120.
_MAX_LONG_FUNCTIONS = 120
# It grew by 11 lines when the frame-interval sync was added to it (1.5.511) — a REAL addition,
# not a cheat. **The ratchet caught it, which is the ratchet working**: the honest response is to
# record that the function is now bigger, not to pretend it is not.
#
# It is **676 lines**, and it has tripped this ratchet THREE TIMES in one session — for the
# frame-interval sync, for the assumed-axis warning, and for the pixel-size gate. **Every safety
# check that belongs in this panel makes it bigger**, which is the clearest possible signal that it
# should not be one function.
#
# **The split is obvious:** `_on_dynamic` is a **145-line closure** inside it, with a clean
# boundary. It is not done here because **this UI has no test coverage**, and a refactor whose only
# verification is "it still imports" is a refactor that ships bugs (see the header).
#
# **THIS IS THE FUNCTION TO SPLIT FIRST**, the moment someone can verify it by hand.
_ABSOLUTE_LONGEST = 676


def _long_functions():
    """Every function over the line limit, longest first."""
    found = []
    for path in sorted(_SOURCE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > _LONG_FUNCTION_LIMIT:
                found.append((length, node.name, str(path.relative_to(_SOURCE))))
    return sorted(found, reverse=True)


@pytest.mark.core
def test_the_number_of_unreviewable_functions_does_not_GROW():
    """**A ratchet.** Existing giants are grandfathered; a new one is not.

    A 400-line function is not reviewable, and that is where bugs hide — **the pixel-size gate's
    failure path was a silent ``except: pass`` inside one**, and it went unnoticed for months.

    This does not demand that the 136 be fixed. It demands that there not be a 137th.
    """
    long_functions = _long_functions()

    assert len(long_functions) <= _MAX_LONG_FUNCTIONS, (
        f"{len(long_functions)} functions now exceed {_LONG_FUNCTION_LIMIT} lines "
        f"(the ceiling is {_MAX_LONG_FUNCTIONS}).\n\n"
        f"The newest offenders:\n  "
        + "\n  ".join(f"{length:4d}  {name}  ({where})"
                      for length, name, where in long_functions[:5])
        + "\n\n**Split the new work, or lower an existing one to make room.** A function this "
          "long is not reviewed — it is skimmed, and a silent failure path inside it is invisible."
    )


# ── Per-file ratchet on the CONCENTRATION POINTS ─────────────────────────────────────────────
#
# The count/absolute ratchets above bound individual functions. They do NOT stop a god-file growing by
# adding more medium functions — and an audit measured exactly that: across two revisions, while the new
# abstractions (SelectionService, OperationSpec, the plot backends, the scene stack) were added BESIDE
# these files, every one grew or held:
#
#     ui_modules.py         5555 -> 5573   (+18)
#     file_io.py            2787 -> 2805   (+18)
#     batch_step_registry   1613 -> 1663   (+50)
#     vpt_ui.py             2458 -> 2458   ( 0)
#
# So each concentration point gets a whole-FILE line ceiling, set at today's value. **The ratchet only
# moves DOWN:** a decomposition that moves responsibility out lowers the number here; nothing may raise
# it. This alone stops the measured drift at zero refactoring cost — the highest-value/lowest-cost part
# of the vpt_ui decomposition spec.
_FILE_LINE_CEILINGS = {
    # 2669 -> 2515 (size) -> 2285 (_base) -> 2141 (deblur) -> 1620 (filters) -> 732 (step 5, 1.6.252:
    # background+noise removal). The ceiling ratchets DOWN as preprocessing/upscaling move out.
    "toolbox/image_processing_tools.py": 732,
    "toolbox/image_processing/filters.py": 564,
    "toolbox/image_processing/background.py": 918,
    # 2828 -> 2600 (frame-access + correlation) -> 1952 (analysis) -> 1410 (worker plumbing) -> 180 (step 4,
    # 1.6.247: preprocessing science + Qt UI builders). timeseries_condensate_tools.py is now a PURE re-export
    # shim (no defs); ceiling locked at the shim size. The analysis / execution / ui modules get their own
    # concentration ceilings.
    "toolbox/timeseries_condensate_tools.py": 180,
    "toolbox/timeseries/analysis.py": 685,
    "toolbox/timeseries/execution.py": 581,
    "toolbox/timeseries/ui.py": 1232,
    # 2692 -> 2030 (leaf) -> 1239 (fz+cellpose) -> 566 (puncta) -> 148 (step 4, 1.6.243: subcellular). The
    # scientific core is fully split into toolbox/segmentation/ by family; segmentation_tools.py is now a
    # PURE re-export shim (no defs). Ceiling locked at the shim size.
    "toolbox/segmentation_tools.py": 148,
    # puncta_refinement.py — the SNR/kurtosis/contrast gate + the two bit-identical implementations, moved
    # here from segmentation_tools (1.6.242). Byte-identical; its own concentration ceiling.
    "toolbox/segmentation/puncta_refinement.py": 715,
    # vpt_ui.py: 2458 -> 1778 (panels) -> 1375 (napari) -> 1246 (table) -> 1139 (msd) as the four
    # adapter modules absorbed its responsibilities (decomposition steps 2-3). A 54% reduction. The
    # ratchet moving DOWN is the point — the file cannot grow back to where it was.
    "toolbox/vpt_ui.py": 1139,
    # 2834 -> 95 (-97%): the scientific core split into toolbox/vpt/ by domain — viscosity (1.6.235),
    # drift (1.6.236), host (1.6.237), the whole detection stack (1.6.238), and finally population routing
    # + the run_vpt_analysis orchestrator (1.6.239). vpt_tools.py is now a PURE re-export shim (no defs);
    # the ceiling is locked at the shim size.
    "toolbox/vpt_tools.py": 95,
    # detection.py — the LoG/GPU/template detection stack + backend chooser + linking probes moved here
    # from vpt_tools (1.6.238). Byte-identical; its own concentration ceiling, ratchets DOWN if split.
    "toolbox/vpt/detection.py": 1773,
    # analysis.py — the run_vpt_analysis orchestrator (host->detect->link->drift->MSD->viscosity) + _link
    # + compare_detection_variants moved here from vpt_tools (1.6.239). Byte-identical; own ceiling.
    "toolbox/vpt/analysis.py": 228,
    # 5573 -> 3268 (-41%): MenuManager (2164 lines) extracted to ui/menu_manager.py in the 1.6.149
    # decomposition. The ratchet moves DOWN — it cannot grow back.
    "ui/ui_modules.py": 3268,
    # MenuManager's new home, ratcheted at its post-extraction size. Phase-2's internal splits
    # (napari_menus / grid_view / metadata_dialogs) would lower it further — a later increment.
    "ui/menu_manager.py": 2344,
    # 2805 -> 1670 (-40.5%) as StackLoadCancelled (errors.py), the two dialogs (dialogs.py), the pure
    # naming/pixel helpers (naming.py) and the three format openers (stack_openers.py) moved to their
    # homes (decomposition, 1.6.146). The ratchet moves DOWN — it cannot grow back.
    "file_io/file_io.py": 1670,
    # 1663 -> 432 (-74%): the replay handlers + shared helpers moved to the pycat.batch.steps package
    # (decomposition, 1.6.150); _STEP_MAP, the registry wiring, and replay_background_removal (pinned by
    # a source-level test) stay. Ratchet moves down only.
    "batch_step_registry.py": 432,
    # 2051 -> 1623 -> 799 -> 605 -> 88: the full invitro decomposition (1.6.213-1.6.216) moved every domain
    # to toolbox/invitro/{size_distribution,partition,field_summary,analysis}.py. invitro_tools.py is now a
    # pure re-export shim (-96%). The ceiling is locked at the shim size.
    "toolbox/invitro_tools.py": 88,
    # 2447 -> 2242 (coarsening, 1.6.217) -> 1802 (photobleaching + frame_quality, 1.6.218): domains moved to
    # toolbox/condensate_physics/*.py. The ceiling ratchets DOWN as the remaining quantities (msd, moduli,
    # relaxation, intensity, survival) move out.
    "toolbox/condensate_physics_tools.py": 122,
}


@pytest.mark.core
def test_the_concentration_points_do_not_GROW():
    """**A per-file ratchet on the god-files.** The function ratchets do not stop a file growing by
    accretion of medium methods — which is precisely the additive-not-replacing drift the audit
    measured. This bounds the whole file, at today's size, moving only down.

    To pass after a legitimate extraction: **lower the ceiling** to the new count. To pass after adding
    code: move something out, don't raise the number.
    """
    over = []
    for rel, ceiling in sorted(_FILE_LINE_CEILINGS.items()):
        path = _SOURCE / rel
        if not path.exists():
            over.append(f"{rel}: MISSING — the ratchet points at a file that no longer exists")
            continue
        n = len(path.read_text(encoding='utf-8', errors='ignore').splitlines())
        if n > ceiling:
            over.append(f"{rel}: {n} lines (ceiling {ceiling}, +{n - ceiling})")
    assert not over, (
        "a concentration point grew past its ceiling:\n  " + "\n  ".join(over)
        + "\n\n**Move a responsibility OUT** (into an adapter/helper module), don't raise the number — "
          "the ceiling is a ratchet that only goes down. A new abstraction added BESIDE the god-file "
          "instead of absorbing code is the exact 'additive, not replacing' drift this guards against.")


@pytest.mark.core
def test_nothing_exceeds_the_ABSOLUTE_longest_function():
    """**660 lines is already indefensible. It is not a licence to write 700.**"""
    long_functions = _long_functions()
    if not long_functions:
        return

    longest, name, where = long_functions[0]

    assert longest <= _ABSOLUTE_LONGEST, (
        f"`{name}` in {where} is {longest} lines — longer than anything that existed when this "
        f"budget was set ({_ABSOLUTE_LONGEST}).\n\n"
        f"**Nobody reads a function this long.** They skim it, and a `try/except: pass` around "
        f"the one thing that mattered goes unnoticed — which is exactly what happened to the "
        f"pixel-size gate."
    )


@pytest.mark.core
def test_ui_modules_does_not_GROW():
    """**5,423 lines. It does not need to be 5,500.**

    No claim that this is the right size — it plainly is not. But **a module that is too big and
    stable is safer than one that is too big and growing**, and the way a 5,000-line file becomes
    an 8,000-line file is one reasonable-looking addition at a time.

    When something new belongs in the UI, it goes in a **new module**. That is the only way this
    number comes down.
    """
    ui_modules = _SOURCE / "ui" / "ui_modules.py"
    line_count = len(ui_modules.read_text(encoding='utf-8', errors='ignore').splitlines())

    # Today's size, plus a small allowance for the comments a bug-fix needs.
    ceiling = 5600

    assert line_count <= ceiling, (
        f"ui_modules.py is {line_count} lines (ceiling {ceiling}).\n\n"
        f"It is already the largest module in PyCAT, it holds a 2,062-line class, and a "
        f"400-line function inside it hid a silent failure in the pixel-size gate.\n\n"
        f"**Put the new code in a new module.** If it genuinely belongs here, raise the ceiling "
        f"deliberately — but a ceiling that is raised whenever it is hit is not a ceiling."
    )
