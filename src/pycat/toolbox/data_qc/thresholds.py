"""Every verdict threshold the acquisition-QC checks use, in one place, each with its provenance.

A bare ``0.005`` buried in a check cannot be defended in a methods paper; an honest "chosen by inspection" can
(Completion Spec G12). Each constant carries one line saying where it came from:

* ``# PROVENANCE: measured``  -- a table measured on synthetic data with a known answer (cited where it lives);
* ``# PROVENANCE: argued``    -- a physical / statistical argument, given in the comment;
* ``# PROVENANCE: none — chosen by inspection`` -- no derivation exists. These are screens, not measurements,
  and re-deriving them against real data is explicitly out of the MVP's scope.

A check names the constant rather than repeating the number, so changing a threshold is one edit here.
"""
from __future__ import annotations

# ── Saturation / clipping (exposure.py) ────────────────────────────────────────────────────────────────────
SATURATION_GOOD_FRACTION = 0.001        # PROVENANCE: none — chosen by inspection
SATURATION_WARN_FRACTION = 0.01         # PROVENANCE: none — chosen by inspection
# A clip dumps everything above the ceiling into ONE level: count AT the max vs the levels just below it.
CEILING_SPIKE_LEVELS = 5                # PROVENANCE: measured — _base._dtype_max table (ratio 1.0 unclipped vs 30-156 clipped)
CEILING_SPIKE_RATIO = 2.0               # PROVENANCE: measured — same table; unclipped ratio 1.0, clipped >= 30
CEILING_SPIKE_MIN_PIXELS = 2            # PROVENANCE: argued — one or two pixels sharing the max is chance, not a pile-up
# A floor with this fraction of pixels at EXACTLY zero has been processed (a camera has a pedestal); its floor is
# not a sensor property and is declined rather than judged.
PROCESSED_FLOOR_FRACTION = 0.001        # PROVENANCE: argued — a raw sensor with a pedestal essentially never reads 0

# ── SNR (noise.py) ─────────────────────────────────────────────────────────────────────────────────────────
SNR_GOOD = 10.0                         # PROVENANCE: none — chosen by inspection
SNR_WARN = 4.0                          # PROVENANCE: none — chosen by inspection

# ── Sampling (sampling.py) ─────────────────────────────────────────────────────────────────────────────────
NYQUIST_GOOD_RATIO = 1.05               # PROVENANCE: argued — Nyquist is ratio 1 by definition; 5 % slack, chosen by inspection
NYQUIST_WARN_RATIO = 2.0                # PROVENANCE: none — chosen by inspection
NYQUIST_OVERSAMPLED_RATIO = 0.4         # PROVENANCE: none — chosen by inspection
TIME_SAMPLING_GOOD_RATIO = 1.0          # PROVENANCE: argued — temporal Nyquist: sample faster than tau/2
TIME_SAMPLING_WARN_RATIO = 2.0          # PROVENANCE: none — chosen by inspection

# ── Illumination (illumination.py) ─────────────────────────────────────────────────────────────────────────
VIGNETTING_GOOD_RATIO = 0.9             # PROVENANCE: none — chosen by inspection
VIGNETTING_WARN_RATIO = 0.7             # PROVENANCE: none — chosen by inspection
GHOSTING_GOOD_PEAK = 0.005              # PROVENANCE: none — chosen by inspection
GHOSTING_WARN_PEAK = 0.012              # PROVENANCE: none — chosen by inspection

# ── Focus (focus.py) ───────────────────────────────────────────────────────────────────────────────────────
FOCUS_ABS_GOOD_RATIO = 1.5              # PROVENANCE: argued — a wide screen: the step-vs-blob constant makes the ratio uncertain by ~1.5x
FOCUS_ABS_WARN_RATIO = 2.5              # PROVENANCE: argued — same; above it object sizes are measurably overestimated
FOCUS_ABS_REFUSE_ABOVE = 3.0            # PROVENANCE: argued — nothing small in the field: soft objects and soft focus are indistinguishable
# A RATIO floor (refuse below ~0.8x) was specified and measured out: a genuinely diffraction-limited blob field
# reads 0.42-0.45x (blob constant 1.65 vs step 2.51, plus pixelation), the same range as a blank noise field
# (0.6x). The ratio cannot separate them; the gradient-over-noise test below can.
# The sharpest gradient must stand clear of what white noise alone produces (99.9th pct of |grad| of white noise
# with sigma s is ~2.6 s for np.gradient's central differences).
FOCUS_EDGE_OVER_NOISE = 6.0             # PROVENANCE: argued — ~2.3x the white-noise 99.9th-percentile gradient
FOCUS_STACK_DIP_FRACTION = 0.5          # PROVENANCE: measured — focus.qc_focus docstring (defocused frame 0.22x vs 0.98x median)
FOCUS_STACK_WARN_FRACTION = 0.15        # PROVENANCE: none — chosen by inspection

# ── Stability (stability.py) ───────────────────────────────────────────────────────────────────────────────
BLEACH_GOOD_REMAINING = 0.85            # PROVENANCE: argued — losing up to ~a third is correctable by an exp(-t/tau) division
BLEACH_WARN_REMAINING = 0.50            # PROVENANCE: argued — past half, late frames are mostly noise
# The foreground is real only if it stands this many noise sigmas above the background in the time average.
BLEACH_FOREGROUND_SIGMAS = 5.0          # PROVENANCE: none — chosen by inspection
DRIFT_GOOD_FEATURES = 0.5               # PROVENANCE: argued — sub-object drift leaves each object overlapping itself
DRIFT_WARN_FEATURES = 1.0               # PROVENANCE: argued — a full feature size breaks per-object time series
DRIFT_GOOD_FOV = 0.01                   # PROVENANCE: none — chosen by inspection (fallback when no feature size)
DRIFT_WARN_FOV = 0.05                   # PROVENANCE: none — chosen by inspection
VIBRATION_BAD_P = 0.01                  # PROVENANCE: argued — conventional significance against a 400-permutation null
VIBRATION_WARN_P = 0.05                 # PROVENANCE: argued — conventional significance
VIBRATION_MIN_FRAMES = 20               # PROVENANCE: measured — stability.qc_vibration docstring (no power below ~20 frames)
VIBRATION_PERMUTATIONS = 400            # PROVENANCE: argued — p resolution 1/401, enough to separate 0.01 from 0.05

# ── Aberration (aberration.py) ─────────────────────────────────────────────────────────────────────────────
SPHERICAL_GOOD_ASYMMETRY = 0.20         # PROVENANCE: none — chosen by inspection
SPHERICAL_WARN_ASYMMETRY = 0.40         # PROVENANCE: none — chosen by inspection
CHROMATIC_GOOD_PX = 2.0                 # PROVENANCE: measured — aberration.qc_chromatic table (registered pair reads up to ~2 px)
CHROMATIC_WARN_PX = 4.0                 # PROVENANCE: measured — same table (4 px shift recovered to 0.16 px)
CHROMATIC_MAX_FOV_FRACTION = 0.05       # PROVENANCE: argued — lateral chromatic aberration is a few pixels at most

# ── Object-level biological QC (biological.py) ─────────────────────────────────────────────────────────────
BIOLOGICAL_MAD_K = 3.5                  # PROVENANCE: none — chosen by inspection (the common robust-outlier convention)
BIOLOGICAL_WARN_FRACTION = 0.25         # PROVENANCE: none — chosen by inspection
