"""
PyCAT Video Particle Tracking (VPT) Tools
==========================================
Microrheology by tracking fluorescent probe beads (20 nm - 2 µm) diffusing
inside an in-vitro biomolecular condensate (host phase).

Pipeline
--------
1. Segment the host condensate system (one fluorescence channel).
2. Erode the condensate mask inward to exclude beads near the condensate
   interface — interface dynamics (fusion, flow, surface tension gradients)
   corrupt the assumption of pure thermal diffusion in the bulk.
3. Detect beads (a second fluorescence channel, typically green but any color)
   frame-by-frame via Laplacian-of-Gaussian blob detection, keeping only
   beads inside the eroded host mask.
4. Link bead detections into trajectories (TrackMate LAP by default, or one
   of PyCAT's native linkers).
5. Drift-correct via ensemble center-of-mass subtraction (removes stage drift
   and bulk condensate translation/flow).
6. Compute per-track and ensemble MSD, fit MSD(τ) = 4Dτ^α, and derive
   viscosity via the Stokes-Einstein relation η = kT / (6πRD).

This mirrors the established manual workflow (load TrackMate XML → COM drift
correction → per-track MSD → ensemble fit → Stokes-Einstein) but runs
end-to-end from raw multichannel image data within PyCAT.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

import warnings
from typing import Optional

import numpy as np

from pycat.utils.tag_registry import tags_layer
import pandas as pd

import skimage as sk
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
from pycat.utils.general_utils import debug_log
# The one place the minimum-track-length number lives, with the lag-window
# reasoning it is derived from. Imported rather than repeated: a second copy of a
# scientific default is a second thing to forget to change.
from pycat.toolbox.condensate_physics_tools import MIN_TRACK_LENGTH_FRAMES
import scipy.ndimage as ndi

# Notifications go through the shim so this module's PHYSICS (detection, MSD,
# diffusion fitting, viscosity) stays importable and testable without a GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# Boltzmann constant (J/K)


# ---------------------------------------------------------------------------
# 1-2. Host condensate segmentation + interface erosion  ->  moved to host.py (1.6.237)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.host import (  # noqa: E402,F401
    segment_host_condensate, erode_host_mask, infer_host_from_beads)

# ---------------------------------------------------------------------------
# 3. Bead detection + linking-condition probes + GPU/parallel backends  ->  moved to detection.py (1.6.238)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.detection import (  # noqa: E402,F401
    detect_beads_frame, blob_log_gpu, bead_half_from_size, build_airy_template, build_hot_pixel_mask, dedup_detections_ring_merge, dedup_detections, build_bead_template, score_beads_template, classify_beads, _read_frame_from_descriptor, _detect_frame_worker, assess_linking_conditions, estimate_linking_distance_um, gpu_matches_cpu, detect_beads_stack)

# ---------------------------------------------------------------------------
# 4b. Bead population routing (primary probes vs. aggregate secondary set)  ->  moved to populations.py (1.6.239)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.populations import (  # noqa: E402,F401
    split_bead_populations, select_bead_population, aggregate_population_stats)

# ---------------------------------------------------------------------------
# 5. Ensemble center-of-mass drift correction  ->  moved to drift.py (1.6.236)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.drift import (  # noqa: E402,F401
    reclassify_by_temporal_stability, drift_correct_com)

# ---------------------------------------------------------------------------
# 6. Stokes-Einstein viscosity  ->  moved to viscosity.py (1.6.235)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.viscosity import (  # noqa: E402,F401
    viscosity_measurement, viscosity_interval_from_diffusion, viscosity_from_diffusion)

# ---------------------------------------------------------------------------
# Full pipeline orchestration (headless / batch-friendly)  ->  moved to analysis.py (1.6.239)
# ---------------------------------------------------------------------------
from pycat.toolbox.vpt.analysis import (  # noqa: E402,F401
    run_vpt_analysis, _link, compare_detection_variants)
