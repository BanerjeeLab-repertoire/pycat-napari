"""
PyCAT Time-Series Condensate Analysis
======================================
Tracks total condensate area per cell across all frames of a (T, H, W)
image stack, using a fixed cell mask from a reference frame with optional
phase-correlation drift correction between frames.

Algorithm per frame
-------------------
1. Apply drift correction to the frame relative to the reference frame
   (optional, uses phase cross-correlation via skimage).
2. For each labeled cell in the fixed mask, run condensate segmentation
   (segment_subcellular_objects) using the bounding-box crop optimisation.
3. Compute per-cell metrics:
      total_condensate_area_px  — sum of refined puncta mask pixels in cell
      total_condensate_area_um2 — converted to µm²
      condensate_fraction       — condensate area / cell area
      n_condensates             — number of individual condensate objects
      mean_condensate_area_um2  — mean area per individual condensate
4. Aggregate all frames into a tidy DataFrame indexed by (frame, cell_label).

Integration
-----------
Added to CondensateAnalysisUI.setup_ui() as:
    self.central_manager.toolbox_functions_ui._add_run_timeseries_condensate_analysis(
        layout=self.condensate_layout)

And to MenuManager._add_analysis_methods_to_menu():
    'Time-Series Condensate Analysis': (
        self.central_manager.analysis_methods_ui._switch_to_condensate_analysis,
        {'base_data_repository': ...}
    )

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import numpy as np
from pycat.utils.general_utils import debug_log
import pandas as pd
import skimage as sk
# ── napari and Qt are imported LAZILY ─────────────────────────────────────────
#
# Of the 23 top-level objects in this module, five use a GUI symbol: two QThread workers and
# three widget builders. The analysis — `estimate_temporal_correlation`,
# `upscale_stack_to_zarr` and the rest — uses none. A module-scope import blocked the
# headless import of all of it for the sake of the widgets.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning

from pycat.toolbox.segmentation_tools import segment_subcellular_objects
from pycat.toolbox.ts_cache_manager import (
    get_cache_paths, cache_exists, write_meta, discard_cache, cache_size_mb
)


# ---------------------------------------------------------------------------
# Lazy stack preprocessing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Zarr-backed frame-by-frame processing
# ---------------------------------------------------------------------------
# Architecture: process every frame once in a background worker, writing
# each frame immediately to a zarr store on disk.  The zarr store is then
# handed to napari as a _ZarrStack wrapper — lazy reads, zero dask, no
# recomputation on slider scrub, no SSL crash.

import tempfile as _tempfile


# ---------------------------------------------------------------------------
# Lazy zarr stack access: session dir, source-frame read, global range, materialize  ->  moved to frame_access.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.frame_access import (  # noqa: E402,F401
    _session_zarr_dir, _read_source_frame, _compute_stack_global_range, _get_zarr_dir_path, _materialize_stack_to_zarr)



# ---------------------------------------------------------------------------
# Temporal correlation estimate + regime/recommendation  ->  moved to correlation.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.correlation import (  # noqa: E402,F401
    estimate_temporal_correlation)



# ---------------------------------------------------------------------------
# _ZarrStack lazy napari-compatible wrapper  ->  moved to frame_access.py (1.6.244)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.frame_access import (  # noqa: E402,F401
    _ZarrStack)



# ---------------------------------------------------------------------------
# Parallel frame processing helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Parallel subprocess frame reader  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# _init_worker_threads (ProcessPoolExecutor thread-pinning initializer, shared)  ->  moved to analysis.py (1.6.245)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.analysis import (  # noqa: E402,F401
    _init_worker_threads)



# ---------------------------------------------------------------------------
# Parallel frame worker + the stack-process QThread-worker factory  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.execution import (  # noqa: E402,F401
    _make__stackprocessworker)




# ---------------------------------------------------------------------------
# Stack upscaling to zarr + cellpose min-diameter target  ->  moved to preprocessing.py (1.6.247)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.preprocessing import (  # noqa: E402,F401
    _cellpose_min_diameter_px, upscale_stack_to_zarr)



# ---------------------------------------------------------------------------
# Upscale-stack + lazy-preprocess UI builders  ->  moved to ui.py (1.6.247)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.ui import (  # noqa: E402,F401
    _build_ts_upscale_check_ui, _add_ts_upscale_stack, _add_lazy_preprocess_stack)



# ---------------------------------------------------------------------------
# Time-series condensate analysis: run_timeseries_condensate_analysis + per-frame worker + drift/metrics helpers  ->  moved to analysis.py (1.6.245)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.analysis import (  # noqa: E402,F401
    _phase_shift, _apply_shift, _condensate_metrics_per_cell, _ts_analyze_frame_worker, run_timeseries_condensate_analysis)



# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Time-series analysis QThread-worker factory  ->  moved to execution.py (1.6.246)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.execution import (  # noqa: E402,F401
    _make_timeseriesworker)




# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Run-analysis UI builder + condensate-fraction plot  ->  moved to ui.py (1.6.247)
# ---------------------------------------------------------------------------
from pycat.toolbox.timeseries.ui import (  # noqa: E402,F401
    _add_run_timeseries_condensate_analysis, _plot_condensate_fraction)

