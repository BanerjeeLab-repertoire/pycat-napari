"""
PyCAT Condensate Physics Toolbox
==================================
Quantitative biophysical analysis tools for liquid-liquid phase separation.

Functions
---------
1.  Mean squared displacement (MSD) + anomalous diffusion fitting
    α < 1: subdiffusion / caged / gel-like
    α = 1: Brownian / liquid-like
    α > 1: directed / active transport
    Gives apparent diffusion coefficient D and anomalous exponent α.

2.  Intensity histogram decomposition (bimodal Gaussian)
    Fits the pixel intensity distribution within a cell as a mixture of
    two Gaussians (dilute phase + dense phase), extracting:
      - C_sat  : saturation concentration proxy (dilute-phase peak)
      - C_dense: dense-phase concentration proxy
      - Dense-phase fraction by pixel count

3.  Saturation concentration (C_sat) estimation — lever rule fitting
    Plots condensate fraction vs time (or condition) and fits the lever
    rule φ_condensate = (C_total - C_sat) / (C_dense - C_sat) to extract
    C_sat when total concentration is varied.

4.  Fusion kinetics — aspect ratio relaxation fitting
    After a merge event, fits the time series of post-merge aspect ratio
    to an exponential decay: AR(t) = 1 + (AR_0 - 1)·exp(-t/τ)
    giving the capillary relaxation time τ = η·R/γ.

5.  Coarsening kinetics
    Fits mean condensate radius vs time to distinguish:
      R(t) ~ t^(1/3) : Ostwald ripening (diffusion-limited dissolution/growth)
      R(t) ~ t^(1/2) : Lifshitz-Slyozov coalescence
      R(t) ~ const   : arrested / kinetically trapped

6.  Photobleaching correction
    Fits exponential decay to mean whole-cell fluorescence and divides
    each frame by the fitted curve to remove bleaching contribution.

7.  Out-of-focus frame detection
    Laplacian variance metric: low variance → blurry / out-of-focus frame.

8.  Surface tension proxy from shape fluctuations
    Variance of condensate boundary over time ∝ k_BT / γ·R.
    Requires tracked condensate boundary time series.

9.  Kaplan-Meier survival curve for condensate lifetimes
    Handles censoring (condensates present at movie start/end).

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""
from __future__ import annotations

# This module is now a PURE RE-EXPORT SHIM. The condensate material-properties physics was decomposed by
# quantity into the ``pycat.toolbox.condensate_physics`` package (1.6.217-1.6.221); every previously-public
# name is re-exported below so existing callers keep importing it from ``condensate_physics_tools``.

# ---------------------------------------------------------------------------
# 1. Mean Squared Displacement  ->  moved to condensate_physics/msd.py (1.6.220)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.msd import (  # noqa: E402,F401
    compute_msd, fit_anomalous_diffusion, msd_per_track, test_confinement,
    MIN_TRACK_LENGTH_FRAMES, _MAX_LAG_FRACTION, _HONEST_LAG_COUNT,
    _short_track_rejections, _report_short_track_rejections)

# ---------------------------------------------------------------------------
# 2. Intensity histogram decomposition  ->  moved to intensity.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.intensity import (  # noqa: E402,F401
    fit_bimodal_intensity, intensity_decomposition_per_cell)

# ---------------------------------------------------------------------------
# 3. Fusion kinetics — aspect ratio relaxation  ->  moved to relaxation.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.relaxation import (  # noqa: E402,F401
    fit_aspect_ratio_relaxation)

# ---------------------------------------------------------------------------
# 4. Coarsening kinetics  ->  moved to condensate_physics/coarsening.py (1.6.217)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.coarsening import fit_coarsening  # noqa: E402,F401

# ---------------------------------------------------------------------------
# 5. Photobleaching correction  ->  moved to photobleaching.py (1.6.218)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.photobleaching import (  # noqa: E402,F401
    fit_photobleaching, apply_bleach_correction)

# ---------------------------------------------------------------------------
# 6. Frame quality analysis  ->  moved to frame_quality.py (1.6.218)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.frame_quality import (  # noqa: E402,F401
    analyse_frame_quality, detect_out_of_focus)

# ---------------------------------------------------------------------------
# 7. Survival analysis (Kaplan-Meier)  ->  moved to survival.py (1.6.219)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.survival import (  # noqa: E402,F401
    kaplan_meier_lifetimes)

# ---------------------------------------------------------------------------
# Per-track MSD curves + microrheology moduli  ->  moved to moduli.py (1.6.221)
# ---------------------------------------------------------------------------
from pycat.toolbox.condensate_physics.moduli import (  # noqa: E402,F401
    per_track_msd_curves, compute_moduli_gser, compute_moduli_evans, compute_moduli_evans_bootstrap, extract_fusion_relaxation)
