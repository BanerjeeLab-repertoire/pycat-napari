"""
PyCAT In Vitro Condensate Toolbox
====================================
Analysis functions specific to in vitro droplet / LLPS assays imaged on
a coverslip without cells.

Core differences from cellular analysis
-----------------------------------------
- No cell mask: the whole imaging field is the sample
- Background = buffer (very clean, uniform intensity baseline)
- Volume fraction (Φ) replaces condensate fraction (% of cell area)
- Partition coefficient = droplet intensity / bulk buffer intensity
- C_sat directly measurable from bulk fluorescence outside droplets
- Droplet size distributions follow polymer-physics scaling laws
- Coarsening / coalescence kinetics are much cleaner than in cells
- Contact angle (BF) characterises surface wetting behaviour
- Dilution-series experiments yield phase diagram tie-lines

All spatial, dynamic, morphological, and biophysical analyses from the
cellular toolkit apply identically after segmentation — they operate on
masks and centroids without caring about modality or biological context.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""

from __future__ import annotations

import warnings
import numpy as np

from pycat.utils.object_ref import bbox_columns_from_regionprops
import pandas as pd

# Notifications via the shim: keeps this module importable with no GUI stack (1.5.378).
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info

# These report an INTENSITY RATIO, so they need the detector's zero point. Measured with a
# TRUE Kp of 30: on min-max normalised data the reported value swung from 323 to 22 with the
# noise level alone; on a top-hat + LoG image it came out NEGATIVE (-11.96).
from pycat.utils.intensity_semantics import IntensitySemantics, require_intensity
from pycat.utils.general_utils import debug_log
import skimage as sk
from scipy import ndimage, optimize, stats
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Per-field summary  ->  moved to invitro/field_summary.py (1.6.215)
# ---------------------------------------------------------------------------
# The whole-field droplet summary moved to its own domain module; re-exported so every caller
# (invitro UIs, batch steps, timeseries) keeps importing it from invitro_tools.
from pycat.toolbox.invitro.field_summary import field_summary  # noqa: E402,F401


# ---------------------------------------------------------------------------
# 2. Size distribution analysis  ->  moved to invitro/size_distribution.py (1.6.213)
# ---------------------------------------------------------------------------
# The MLE size-distribution fitting moved to its own domain module; re-exported so every caller
# (invitro UIs, batch steps, the op-catalog api string) keeps importing it from invitro_tools.
from pycat.toolbox.invitro.size_distribution import (  # noqa: E402,F401
    fit_size_distribution_mle, fit_size_distribution)
# ---------------------------------------------------------------------------
# Coarsening + C_sat  ->  moved to invitro/analysis.py (1.6.216)
# ---------------------------------------------------------------------------
from pycat.toolbox.invitro.analysis import (  # noqa: E402,F401
    coarsening_statistics, estimate_csat_lever_rule)


# ---------------------------------------------------------------------------
# Partition-coefficient measurement  ->  moved to invitro/partition.py (1.6.214)
# ---------------------------------------------------------------------------
# The calibration-sensitive partition/K_p family moved to its own domain module; re-exported so every
# caller (invitro UIs, batch steps, timeseries) keeps importing it from invitro_tools.
from pycat.toolbox.invitro.partition import (  # noqa: E402,F401
    partition_coefficient_local, partition_measurement, partition_coefficient_field,
    estimate_phase_boundary)



# ---------------------------------------------------------------------------
# Contact angle + fusion + sedimentation  ->  moved to invitro/analysis.py (1.6.216)
# ---------------------------------------------------------------------------
from pycat.toolbox.invitro.analysis import (  # noqa: E402,F401
    estimate_contact_angle, detect_and_fit_fusions, detect_sedimentation)
