"""Shared low-level primitives for the acquisition-QC checks (data_qc_decomposition).

`_to_float` / `_dtype_max` (dtype-aware normalisation), `_robust_noise_std` (MAD-based noise estimate),
`_mean_frame` (collapse a stack to one representative frame), and `_not_applicable` (the standard skipped-check
result). Used across the check families, so they live here rather than in any one of them. Moved VERBATIM out of
`data_qc_tools`, which re-exports them.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log


def _to_float(img):
    # float32, not float64: QC is a diagnostic, and float32 is ample precision for every metric here
    # (clipping fractions, SNR ratios, focus variance, phase-correlation drift). float64 DOUBLED the
    # memory of an already-large stack — an 18.8 GiB allocation that OOM'd QC on a 600-frame 2048²
    # movie — and for a stack already decoded as float32 this is now a no-op view rather than a copy.
    return np.asarray(img, dtype=np.float32)


def _robust_noise_std(img):
    """Noise std from median absolute adjacent-pixel differences (robust to
    real structure and sparse edges)."""
    d = np.abs(np.diff(_to_float(img), axis=-1))
    if d.size == 0:
        return 0.0
    return 1.4826 * float(np.median(d)) / np.sqrt(2.0)


def _dtype_max(img):
    """Best guess at the sensor's full-scale value for clipping detection.

    **The container's maximum is not the sensor's ceiling**, and using it makes the saturation
    check blind to the most common case there is.

    A 12-bit camera writing into a ``uint16`` array clips at **4095**, not 65535. A camera run
    at reduced gain clips lower still. ``np.iinfo(uint16).max`` is 65535, so a check against it
    finds **nothing**. Measured, on a ``uint16`` image whose two brightest objects are
    genuinely flat-topped:

    ==================================  ==============  ==========
    image                               truly clipped   reported
    ==================================  ==============  ==========
    clipped at 65535 (the dtype max)    0.0 %          0.00 % good
    clipped at 4095 (a 12-bit sensor)   **1.2 %**      **0.00 % good**
    clipped at 1000 (gain-limited)      **9.1 %**      **0.00 % good**
    ==================================  ==============  ==========

    **Nine percent of the pixels destroyed, reported as "good".**

    So the ceiling is detected from the DATA: if a large number of pixels sit *exactly* at the
    image maximum, that maximum **is** the ceiling — a real, unclipped scene has a smooth
    intensity distribution and essentially never repeats its brightest value. The dtype max is
    kept as the fallback when no such pile-up exists.
    """
    a = np.asarray(img)
    if np.issubdtype(a.dtype, np.integer):
        # A pile-up AT the image maximum is the signature of clipping, wherever the ceiling
        # sits. One pixel happening to be brightest is not a pile-up; hundreds is.
        try:
            if a.size:
                obs_max = float(a.max())
                n_at_max = int((a == a.max()).sum())

                # ── A pile-up is a SPIKE, not a pixel count ──────────────────────
                #
                # A fixed threshold (``> max(10, 0.0001 * size)``) is not scale-free, and it
                # missed a real clip by ONE pixel: a 256x256 image with 9 pixels flat-topped at
                # the ceiling was reported as **"0.00 % at ceiling, GOOD"** while its histogram
                # showed the spike plainly. **Nine clipped pixels are still clipped** — they are
                # the peaks of the brightest objects, and every intensity measured on them is
                # destroyed.
                #
                # The physical signature is scale-free: **a clip dumps everything above the
                # ceiling into ONE bin**, while an unclipped distribution tapers smoothly to its
                # maximum. Compare the count AT the max against the count in the few levels just
                # below it:
                #
                #     image             n@max    n just below    ratio
                #     unclipped         1        0               **1.0**
                #     clipped at 900    151      5               **30.2**
                #     clipped at 700    313      2               **156.5**
                #
                # No magic count, and it works on a 256x256 crop and a 2048x2048 frame alike.
                _below = int(((a >= a.max() - 5) & (a < a.max())).sum())
                _is_spike = n_at_max > 2 and n_at_max > 2.0 * max(_below, 1)

                if _is_spike and obs_max > 0:
                    return obs_max
        except Exception as _exc:
            debug_log('saturation: could not detect the ceiling from the data', _exc)
        return float(np.iinfo(a.dtype).max)
    # floats: assume the data max is the ceiling unless it looks normalised
    m = float(np.nanmax(a)) if a.size else 1.0
    if m <= 1.0 + 1e-6:
        return 1.0
    # common camera bit depths
    for full in (255, 4095, 65535):
        if m <= full:
            return float(full)
    return m


def _mean_frame(data):
    """Collapse a (T/Z, H, W) stack to a representative 2-D frame (the mean)."""
    a = _to_float(data)
    return a.mean(axis=0) if a.ndim == 3 else a


def _not_applicable(name, why):
    """A check that cannot apply is reported as N/A **with the reason** — never as 'good'.

    Reporting 'good' for a question the data cannot answer is a quiet lie: the user reads a
    clean report and concludes their data passed a test that was never run. Reporting a
    confident 'bad' is worse — they go and fix something that is not broken, and they learn to
    distrust the whole report.

    So the check appears, greyed out, saying **why** it does not apply. That is the
    anti-black-box answer: the user can see that PyCAT considered it and declined, rather than
    wondering whether it was silently skipped.
    """
    return dict(name=name, tier='core', status='na', value=None, unit='',
                headline='not applicable to this data', how=why, good='', diag=None)


# ── shared registration-shift normaliser (used by drift / vibration / chromatic checks) ──
def _shift_normalise(frame):
    """Strip the intensity scale before a phase correlation, so BRIGHTNESS cannot look like MOTION.

    ``phase_cross_correlation`` is supposed to be intensity-robust — it works on the normalised
    cross-power spectrum. **It is not robust enough when the frame is globally scaled**, because
    the DC term and the noise floor move together and the sub-pixel peak fit is biased.

    Measured: a **photobleaching** stack (which gets dimmer every frame and **does not move at
    all**) drove ``qc_vibration`` to **p = 0.010, status "bad"** — a confident report of a
    periodic vibration source. The shift trace was tracking the exponential intensity decay,
    which is smooth and monotonic, and therefore highly concentrated in the low-frequency bins:
    exactly the signature the permutation test looks for.

    **The user is sent to check their pumps and fans, and the stage is fine.**

    Z-scoring each frame removes the global scale and offset, leaving only the structure that a
    registration should key on.
    """
    f = np.asarray(frame, dtype=float)
    sd = float(f.std())
    if not np.isfinite(sd) or sd <= 0:
        return f - float(f.mean())
    return (f - float(f.mean())) / sd
