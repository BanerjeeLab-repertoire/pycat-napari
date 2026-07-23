"""
pycat/batch_step_registry.py
============================
Headless replay functions for PyCAT batch processing.

Each replay function calls the underlying pure-numpy computation functions
directly, completely bypassing the napari viewer and all Qt dialogs.
Results (masks and DataFrames) are saved straight to disk.

The pipeline order for Condensate Analysis is:
  open_image → preprocessing → background_removal → cellpose_segmentation
  → cell_analysis → condensate_segmentation → condensate_analysis → save_and_clear
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from pycat.file_io.image_reader import open_image

if TYPE_CHECKING:
    from pycat.batch_processor import BatchProcessor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# Replay functions
# Each signature: fn(state, image_path, params, output_dir) -> None
#
# `state` is a plain dict shared across all steps for one file, holding:
#   state['image']           – raw image array
#   state['preprocessed']    – preprocessed image array
#   state['data_instance']   – BaseDataClass instance for this file
#   state['labeled_cells']   – labeled cell mask array (from cell_analysis)
#   state['puncta_mask']     – refined puncta mask array (from condensate_seg)
# ---------------------------------------------------------------------------















# ---------------------------------------------------------------------------
# Step map
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# Brightfield / In-vitro replay functions
# ---------------------------------------------------------------------------























# The replay handlers moved to pycat.batch.steps (decomposition 1.6.150); _STEP_MAP imports them.
from pycat.batch.steps.io_steps import (replay_open_image, replay_open_stack, replay_save_and_clear, replay_set_frame_range, replay_auto_crop_roi)
from pycat.batch.steps.preprocessing_steps import (replay_preprocessing, replay_upscaling, replay_calibration_correction)
from pycat.batch.steps.segmentation_steps import (replay_cellpose_segmentation, replay_ts_cellpose_keyframe)
from pycat.batch.steps.brightfield_steps import (replay_bf_preprocess, replay_bf_condensate_segmentation, replay_bf_cell_segmentation, replay_ivbf_preprocess, replay_ivbf_segmentation)
from pycat.batch.steps.invitro_steps import (replay_ivf_preprocess, replay_ivf_field_summary, replay_ivf_size_distribution, replay_ivf_spatial_metrology, replay_ivf_segmentation)
from pycat.batch.steps.analysis_steps import (replay_condensate_analysis, replay_measure_line, replay_cell_analysis, replay_sacf_analysis, replay_condensate_segmentation)

from pycat.batch.steps._common import _get_data, _save_array, _raw_counts, _normalize_to_float  # for replay_background_removal (kept here: a source-level test pins it)


# replay_background_removal stays in this file: test_batch_matches_the_recording reads its SOURCE here
# (a white-box scale-logic check), so moving it would break a test the spec forbids editing.
def replay_background_removal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay enhanced RB-Gaussian background removal on the preprocessed image.

    Matches the interactive `run_enhanced_rb_gaussian_bg_removal`: if the input is
    already preprocessed (sparse, peaked distribution), it applies the
    non-destructive `soft_foreground_suppression` using the session's suppression
    params rather than the destructive rolling-ball subtraction, so batch and GUI
    produce the same 'Enhanced Background Removed' result.
    """
    from pycat.toolbox.image_processing_tools import (
        rb_gaussian_bg_removal_with_edge_enhancement, soft_foreground_suppression)
    import math

    preprocessed = state.get('preprocessed')
    if preprocessed is None:
        print("[PyCAT Batch] background_removal: no preprocessed image in state — skipping.")
        return

    data_instance = state['data_instance']
    ball_radius = math.ceil(int(params.get('ball_radius',
                                _get_data(data_instance, 'ball_radius', 50))))
    sp = params.get('foreground_suppression_params', None) or {}

    # Which layer was active when background removal was clicked?
    active_name = str(params.get('active_layer')
                      or params.get('active_image_layer') or '').lower()
    on_fluor = 'fluorescence' in active_name  # default (incl. "segmentation") -> seg

    def _enhance(img):
        # ── The "already enhanced" HEURISTIC IS SCALE-DEPENDENT, and batch changed the scale ──
        #
        # ``_already_enhanced = median(nonzero) < 0.05`` — and ``_normalize_to_float`` maps the
        # image minimum to **zero**, which **moves the median.**
        #
        # Measured, on a **high-contrast image — a bright spot on a dim background, i.e. exactly a
        # condensate image**:
        #
        #     path            median          verdict              processing applied
        #     INTERACTIVE     **403 counts**  not enhanced         **full rolling-ball removal**
        #     BATCH           **0.030**       **"already enhanced"**  **soft suppression only**
        #
        # ***The two paths take DIFFERENT BRANCHES and apply COMPLETELY DIFFERENT PROCESSING.***
        # This is not a scale shift in one number — it is a different algorithm.
        #
        # The GUI hands ``active_layer.data`` — **raw counts** — to the same heuristic. Batch must
        # do the same, or the branch it takes is decided by a normalisation the GUI never applied.
        img = _raw_counts(img)
        # Detect already-preprocessed input (same heuristic as the GUI runner).
        n = img.astype(np.float32)
        m = float(n.max())
        if m > 0:
            n = n / m
        nz = n[n > 0.001]
        already = (nz.size > 10 and float(np.median(nz)) < 0.05)
        if already:
            return soft_foreground_suppression(
                img, ball_radius,
                strength=sp.get('strength'), log_p=sp.get('log_p'),
                con_p=sp.get('con_p'), min_area=sp.get('min_area'),
                border_grow=sp.get('border_grow')).astype(np.float32)
        return rb_gaussian_bg_removal_with_edge_enhancement(img, ball_radius).astype(np.float32)

    if on_fluor:
        fluor_proc = state.get('preprocessed_fluorescence',
                               state.get('fluorescence_image', state['image']))
        state['preprocessed_fluorescence'] = _enhance(fluor_proc)
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print(f"[PyCAT Batch]   Background removal done (active layer: fluorescence).")
    else:
        state['preprocessed'] = _enhance(preprocessed)
        _save_array(state['preprocessed'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print(f"[PyCAT Batch]   Background removal done (active layer: segmentation).")


_STEP_MAP = {
    'open_image':               replay_open_image,
    'open_stack':               replay_open_stack,    # unified IMS + TIFF stack
    'set_frame_range':          replay_set_frame_range,
    'auto_crop_roi':            replay_auto_crop_roi,
    'lazy_preprocess_stack':    lambda s,p,pa,o: print('[PyCAT Batch]   TS preprocessing skipped in headless mode (zarr cache required).'),
    'open_ims_file':            replay_open_stack,    # legacy key — keep for old configs
    'open_image_stack':         replay_open_stack,    # legacy key — keep for old configs
    'measure_line':              replay_measure_line,
    'upscaling':                replay_upscaling,
    'preprocessing':            replay_preprocessing,
    'background_removal':       replay_background_removal,
    'calibration_correction':   replay_calibration_correction,
    'cellpose_segmentation':    replay_cellpose_segmentation,
    'cell_analysis':            replay_cell_analysis,
    'condensate_segmentation':  replay_condensate_segmentation,
    'condensate_analysis':      replay_condensate_analysis,
    'sacf_analysis':            replay_sacf_analysis,
    'ts_cellpose_keyframe':      replay_ts_cellpose_keyframe,
    'spatial_metrology':        lambda s,p,pa,o: print('[PyCAT Batch]   Spatial metrology skipped in headless mode.'),
    'dynamic_spatial':        lambda s,p,pa,o: print('[PyCAT Batch]   dynamic_spatial skipped.'),
    'organizational_metrics':        lambda s,p,pa,o: print('[PyCAT Batch]   organizational_metrics skipped.'),
    'export_timeseries_video':            lambda s,p,pa,o: print('[PyCAT Batch]   Video export skipped in headless mode.'),
    'timeseries_condensate_analysis':     lambda s,p,pa,o: print('[PyCAT Batch]   TS condensate analysis skipped in headless mode.'),
    'two_channel_condensate_coloc':       lambda s,p,pa,o: print('[PyCAT Batch]   Two-channel colocalization skipped in headless mode.'),
    'bf_preprocess':              replay_bf_preprocess,
    'bf_cell_segmentation':       replay_bf_cell_segmentation,
    'bf_condensate_segmentation': replay_bf_condensate_segmentation,
    'ivf_preprocess':             replay_ivf_preprocess,
    'ivf_segmentation':           replay_ivf_segmentation,
    'ivf_field_summary':          replay_ivf_field_summary,
    'ivf_size_distribution':      replay_ivf_size_distribution,
    'ivf_spatial_metrology':      replay_ivf_spatial_metrology,
    'ivf_dynamics':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF dynamics skipped (time-series; not a per-image batch step).'),
    'ivf_phase_diagram':          lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF phase diagram skipped (dilution series; manual multi-condition input).'),
    'ivf_frame_qc':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   IVF frame QC skipped (time-series; not a per-image batch step).'),
    'msd_analysis':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   MSD / condensate biophysics skipped (time-series; not a per-image batch step).'),
    'ivbf_preprocess':            replay_ivbf_preprocess,
    'ivbf_segmentation':          replay_ivbf_segmentation,
    'zstack_bg_removal':              lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack background removal skipped in headless mode '
        '(batch file loading only extracts single 2D planes — 3D volume batch '
        'processing is not yet supported; run this step interactively in the '
        'Z-Stack Condensate Analysis dock).'),
    'zstack_cell_segmentation':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack cell segmentation skipped in headless mode '
        '(requires a 3D volume — not available via batch file loading).'),
    'zstack_condensate_segmentation': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Z-stack condensate segmentation skipped in headless mode '
        '(requires a 3D volume — not available via batch file loading).'),
    # Interactive general-analysis steps — recorded for provenance but not
    # replayed headlessly (they are exploratory tools that require interactive
    # layer/ROI selection rather than a fixed automated sequence).
    'local_thresholding':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   local_thresholding skipped in headless mode '
        '(interactive general-analysis step).'),
    'label_binary_mask':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   label_binary_mask skipped in headless mode '
        '(interactive general-analysis step).'),
    'measure_region_props':     lambda s,p,pa,o: print(
        '[PyCAT Batch]   measure_region_props skipped in headless mode '
        '(interactive general-analysis step).'),
    # Video Particle Tracking — interactive microrheology; recorded for
    # provenance but not headlessly replayed (requires interactive channel
    # selection and produces terminal microrheology output, not stored masks).
    'vpt_segment_host':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT host segmentation skipped in headless mode '
        '(interactive multichannel step).'),
    'vpt_infer_host':           lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT infer-host-from-beads skipped in headless mode '
        '(interactive step).'),
    'vpt_detect_beads':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT bead detection skipped in headless mode.'),
    'vpt_link_trajectories':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT trajectory linking skipped in headless mode.'),
    'vpt_microrheology':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   VPT microrheology skipped in headless mode '
        '(terminal reporting step).'),
    # FRAP — interactive ROI selection; recorded for provenance, not replayed.
    'frap_define_roi':          lambda s,p,pa,o: print(
        '[PyCAT Batch]   FRAP ROI definition skipped in headless mode.'),
    'frap_analysis':            lambda s,p,pa,o: print(
        '[PyCAT Batch]   FRAP analysis skipped in headless mode '
        '(interactive ROI + terminal reporting step).'),
    # Droplet Fusion — interactive fit-window selection; recorded, not replayed.
    'fusion_build_signal':      lambda s,p,pa,o: print(
        '[PyCAT Batch]   Fusion signal build skipped in headless mode.'),
    'fusion_fit':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   Fusion fit skipped in headless mode '
        '(interactive fit-window + terminal reporting step).'),
    'spatial_randomness':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   spatial_randomness skipped in headless mode '
        '(interactive image/ROI selection step).'),
    # Temperature-dependent condensate — interactive sync/annotation; recorded.
    'temperature_sync':         lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature sync skipped in headless mode '
        '(interactive TIFF/CSV selection).'),
    'temperature_turbidity':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature turbidity skipped in headless mode.'),
    'temperature_export_video': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Temperature annotated export skipped in headless mode.'),
    # Force-Distance curve — interactive .h5 load + channel/plot selection.
    'fd_load':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD load skipped in headless mode (interactive .h5 selection).'),
    'fd_segment':               lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD segment skipped in headless mode.'),
    'fd_plot':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD plot skipped in headless mode.'),
    'fd_export':                lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD export skipped in headless mode.'),
    'fd_rips':                  lambda s,p,pa,o: print(
        '[PyCAT Batch]   FD rip detection skipped in headless mode.'),
    'molecular_counting':       lambda s,p,pa,o: print(
        '[PyCAT Batch]   Molecular counting skipped in headless mode '
        '(interactive stack/mask selection).'),
    'gaussian_localization':    lambda s,p,pa,o: print(
        '[PyCAT Batch]   Gaussian localization skipped in headless mode '
        '(interactive image/points selection).'),
    'client_enrichment':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   Client enrichment skipped in headless mode '
        '(interactive layer selection).'),
    'intensity_profile':        lambda s,p,pa,o: print(
        '[PyCAT Batch]   Intensity profile skipped in headless mode '
        '(interactive shapes/points selection).'),
    'morphological_complexity': lambda s,p,pa,o: print(
        '[PyCAT Batch]   Morphological complexity skipped in headless mode '
        '(interactive mask selection).'),
    'save_and_clear':           replay_save_and_clear,
}


# ---------------------------------------------------------------------------
# Batch step → operation COMPOSITION  (OperationSpec increment 3)
# ---------------------------------------------------------------------------
# A batch step and a catalog operation are DIFFERENT abstraction levels: a step
# is a workflow stage (`condensate_segmentation`), an operation is a
# layer-producing transform (`subcellular_segment`). Measured: `_STEP_MAP` and
# the op catalog have ZERO name overlap — that is correct design, not drift, so
# the two vocabularies are NOT merged. The honest relationship is *composition*:
# a step INVOKES one or more catalog/measure operations. That mapping cannot be
# inferred (a step's replay function calls whatever it calls), so it is DECLARED
# here, next to the steps, and drift-guarded against the live operation vocabulary
# (tests/navigator/test_batch_step_composition.py): rename an op and the build
# breaks instead of replay silently breaking.
#
# Each value is the tuple of operation ids the step invokes, verified against the
# step's replay function (its toolbox imports → the op that function declares).
# Only steps whose invoked ops are UNAMBIGUOUS are declared — staged population,
# the same discipline as OperationSpec increment 2. A step that also calls an
# UNtagged helper (a composite with no op id) declares only its registered ops;
# a step invoking no catalog/measure op (file I/O, save/clear, skip-stubs) is left
# out rather than declared empty. Later work raises the coverage floor.
_STEP_OPERATIONS: dict[str, tuple[str, ...]] = {
    'preprocessing':            ('preprocess',),
    'upscaling':                ('upscale',),
    'calibration_correction':   ('bg_subtract_clear', 'flatfield'),
    'auto_crop_roi':            ('multi_otsu',),
    'cellpose_segmentation':    ('cellpose', 'stardist'),
    'condensate_segmentation':  ('mask_stretch', 'subcellular_segment'),
    # 'ivf_segmentation' deliberately NOT declared: a fixed op tuple misleads for 4 of its 5 recorded methods -- see test_batch_step_composition._MIN_STEPS_DECLARED for the rationale.
    'bf_condensate_segmentation': ('bf_segment',),
    'ivbf_segmentation':        ('bf_segment',),
    'ivf_size_distribution':    ('invitro.size_distribution',),   # a MEASURE op
}


def step_operations(step_name: str) -> tuple[str, ...]:
    """The catalog/measure operation ids a batch step invokes, or () if undeclared.

    The declared composition (see ``_STEP_OPERATIONS``). ``()`` means either the step invokes no
    registered operation (file I/O, save/clear, a skip-stub) or its mapping is not yet declared —
    the coverage guard, not this accessor, distinguishes the two.
    """
    return _STEP_OPERATIONS.get(step_name, ())


def all_step_operations() -> dict:
    """The whole declared step → operations composition (a copy)."""
    return {k: tuple(v) for k, v in _STEP_OPERATIONS.items()}


def register_all_steps(bp: "BatchProcessor"):
    # A duplicate key in _STEP_MAP is silently swallowed by Python (the later
    # entry wins), which makes it a latent trap: someone implements a real
    # replay handler, a stale skip-stub further down the dict overrides it, and
    # they debug a handler that never runs. `morphological_complexity` was
    # registered twice for exactly this reason. The dict literal cannot report
    # it, so check the SOURCE for repeated keys at import time and say so.
    _warn_on_duplicate_step_keys()
    for name, fn in _STEP_MAP.items():
        bp.register_step(name, fn)
    print(f"[PyCAT Batch] Registered {len(_STEP_MAP)} headless replay steps.")


def _warn_on_duplicate_step_keys():
    """Report any step name written more than once in the _STEP_MAP literal."""
    try:
        import ast, inspect, collections
        tree = ast.parse(inspect.getsource(inspect.getmodule(register_all_steps)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "_STEP_MAP"
                       for t in node.targets):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            keys = [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            dups = [k for k, n in collections.Counter(keys).items() if n > 1]
            if dups:
                print("[PyCAT Batch] WARNING: duplicate replay step name(s) in "
                      f"_STEP_MAP: {dups}. The LAST definition wins and the "
                      "earlier one is silently discarded.")
    except Exception:
        pass
