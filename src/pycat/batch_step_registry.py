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
from pycat.batch.steps.brightfield_steps import (replay_bf_preprocess, replay_bf_condensate_segmentation, replay_bf_condensate_analysis, replay_bf_cell_segmentation, replay_ivbf_preprocess, replay_ivbf_segmentation, replay_bf_od_metrics, replay_bf_per_cell_summary, replay_ivbf_field_summary, replay_ivbf_spatial_metrology, replay_ivbf_size_distribution)
from pycat.batch.steps.invitro_steps import (replay_ivf_preprocess, replay_ivf_field_summary, replay_ivf_size_distribution, replay_ivf_spatial_metrology, replay_ivf_segmentation, replay_ivf_droplet_segment, replay_ivf_droplet_analysis, replay_client_enrichment)
from pycat.batch.steps.vpt_steps import replay_vpt_microrheology
from pycat.batch.steps.not_replayed import NOT_REPLAYED, _advice_only, _not_replayed  # noqa: F401  (re-exported)
from pycat.batch.steps.fibril_steps import (replay_bilateral_filter, replay_peak_edge_enhancement, replay_morphological_gaussian, replay_local_thresholding, replay_label_binary_mask, replay_measure_binary_mask)
from pycat.batch.steps.timeseries_cell_steps import replay_ts_cells_time_average, replay_cell_timeseries
from pycat.batch.steps.timeseries_invitro_steps import (replay_tsivf_segment_stack, replay_tsivf_link_condensates, replay_tsivf_object_trajectories, replay_tsivf_field_trajectories, replay_tsivf_kinetics)
from pycat.batch.steps.analysis_steps import (replay_condensate_analysis, replay_measure_line, replay_cell_analysis, replay_sacf_analysis, replay_condensate_segmentation, replay_pixel_coloc, replay_spatial_metrology, replay_dynamic_spatial, replay_msd_analysis)

from pycat.batch.steps._common import _get_data, _save_array, _raw_counts, _normalize_to_float, _active_layer_channel_role  # for replay_background_removal (kept here: a source-level test pins it)

# replay_background_removal stays in this file: test_batch_matches_the_recording reads its SOURCE here
# (a white-box scale-logic check), so moving it would break a test the spec forbids editing.
def replay_background_removal(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay enhanced RB-Gaussian background removal on the preprocessed image.

    Matches the interactive `run_enhanced_rb_gaussian_bg_removal`: always the
    destructive rolling-ball + Gaussian + Gabor chain, on whatever image is in
    state -- no "does this already look preprocessed" branch. See
    `run_enhanced_rb_gaussian_bg_removal`'s docstring (background.py) for why
    the old auto-detect-and-route-to-soft_foreground_suppression behaviour was
    removed. Kept in lock-step with the GUI runner deliberately: an earlier
    version of this heuristic was found to pick a DIFFERENT branch than the GUI
    on the same image because batch's `_raw_counts`/`_normalize_to_float`
    handling moved the intensity scale the heuristic keyed on -- removing the
    branch entirely also removes that whole class of GUI/batch mismatch.
    """
    from pycat.kernel import OperationService
    import math

    preprocessed = state.get('preprocessed')
    if preprocessed is None:
        print("[PyCAT Batch] background_removal: no preprocessed image in state — skipping.")
        return

    data_instance = state['data_instance']
    # ball_radius: same override as replay_preprocessing — when this batch has
    # per-image auto-estimation active, the recorded params snapshot (captured
    # once, on whichever image was active during the original GUI recording)
    # must not override the LIVE per-image estimate already resolved onto
    # data_instance. See replay_preprocessing for the full rationale.
    if state.get('_auto_ball_radius'):
        ball_radius = math.ceil(int(_get_data(data_instance, 'ball_radius', 50)))
    else:
        ball_radius = math.ceil(int(params.get('ball_radius',
                                    _get_data(data_instance, 'ball_radius', 50))))

    active_name = str(params.get('active_layer')  # see _active_layer_channel_role
                      or params.get('active_image_layer') or '').lower()
    on_fluor, _fluor_key = _active_layer_channel_role(state, active_name)

    def _enhance(img):
        # Raw counts, matching what the GUI hands active_layer.data as -- see
        # replay_preprocessing for why batch must not pre-normalise here.
        img = _raw_counts(img)
        return OperationService.produce('bg_removal_enhanced', {'image': img},
                                        {'ball_radius': ball_radius}).astype(np.float32)

    if on_fluor:
        fluor_proc = state.get('preprocessed_fluorescence',
                               state.get('fluorescence_image', state['image']))
        state['preprocessed_fluorescence'] = _enhance(fluor_proc)
        if _fluor_key is not None:   # PROCESSED store, not raw — see replay_preprocessing
            state.setdefault('channels_processed_by_name', {})[_fluor_key] = state['preprocessed_fluorescence']
        _save_array(state['preprocessed_fluorescence'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print("[PyCAT Batch]   Background removal done (active layer: fluorescence).")
    else:
        state['preprocessed'] = _enhance(preprocessed)
        _save_array(state['preprocessed'],
                    output_dir / f"{image_path.stem}_bg_removed.tiff")
        print("[PyCAT Batch]   Background removal done (active layer: segmentation).")


_STEP_MAP = {
    'open_image':               replay_open_image,
    'open_stack':               replay_open_stack,    # unified IMS + TIFF stack
    'set_frame_range':          replay_set_frame_range,
    'auto_crop_roi':            replay_auto_crop_roi,
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
    'ts_cells_time_average':     replay_ts_cells_time_average,   # 1.7.004 cellular time series
    'cell_timeseries':           replay_cell_timeseries,
    'tsivf_segment_stack':       replay_tsivf_segment_stack,       # in-vitro time series (1.7.004)
    'tsivf_link_condensates':    replay_tsivf_link_condensates,
    'tsivf_object_trajectories': replay_tsivf_object_trajectories,
    'tsivf_field_trajectories':  replay_tsivf_field_trajectories,
    'tsivf_kinetics':            replay_tsivf_kinetics,
    'spatial_metrology':        replay_spatial_metrology,
    'dynamic_spatial':        replay_dynamic_spatial,
    'pixel_colocalization':       replay_pixel_coloc,
    'bf_preprocess':              replay_bf_preprocess,
    'bf_cell_segmentation':       replay_bf_cell_segmentation,
    'bf_condensate_segmentation': replay_bf_condensate_segmentation,
    'bf_condensate_analysis':     replay_bf_condensate_analysis,
    'ivf_preprocess':             replay_ivf_preprocess,
    'ivf_segmentation':           replay_ivf_segmentation,
    'ivf_droplet_segment':        replay_ivf_droplet_segment,
    'ivf_droplet_analysis':       replay_ivf_droplet_analysis,
    'ivf_field_summary':          replay_ivf_field_summary,
    'ivf_size_distribution':      replay_ivf_size_distribution,
    'ivf_spatial_metrology':      replay_ivf_spatial_metrology,
    'msd_analysis':               replay_msd_analysis,
    'ivbf_preprocess':            replay_ivbf_preprocess,
    'ivbf_segmentation':          replay_ivbf_segmentation,
    # The fibril chain (1.7.005): each step records the layer it ran on and replays on the same input.
    'bilateral_filter':         replay_bilateral_filter,
    'peak_edge_enhancement':    replay_peak_edge_enhancement,
    'morphological_gaussian':   replay_morphological_gaussian,
    'local_thresholding':       replay_local_thresholding,
    'label_binary_mask':        replay_label_binary_mask,
    'measure_binary_mask':      replay_measure_binary_mask,
    'vpt_microrheology':        replay_vpt_microrheology,
    'client_enrichment':        replay_client_enrichment,   # headless calibrated concentration (reliability_index roadmap)
    # Recorded by the GUI but never registered until 1.7.005 (Completion Spec E2).
    'bf_od_metrics':            replay_bf_od_metrics,
    'bf_per_cell_summary':      replay_bf_per_cell_summary,
    'ivbf_field_summary':       replay_ivbf_field_summary,
    'ivbf_spatial_metrology':   replay_ivbf_spatial_metrology,
    'upscale_advice':           _advice_only,
    'ivbf_size_distribution':   replay_ivbf_size_distribution,
    'save_and_clear':           replay_save_and_clear,
    **NOT_REPLAYED,             # recorded steps batch cannot replay (pycat.batch.steps.not_replayed)
}


# The step -> operation composition (``_STEP_OPERATIONS``) lives in pycat.batch.step_operations.
from pycat.batch.step_operations import _STEP_OPERATIONS, all_step_operations, step_operations  # noqa: E402,F401


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
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)] + list(NOT_REPLAYED)
            dups = [k for k, n in collections.Counter(keys).items() if n > 1]
            if dups:
                print("[PyCAT Batch] WARNING: duplicate replay step name(s) in "
                      f"_STEP_MAP: {dups}. The LAST definition wins and the "
                      "earlier one is silently discarded.")
    except Exception:
        pass
