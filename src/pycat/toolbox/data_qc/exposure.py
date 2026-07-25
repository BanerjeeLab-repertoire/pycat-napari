"""Exposure QC — saturation / clipping / dynamic-range checks.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc._base import _to_float, _dtype_max

def qc_saturation(img):
    """Fraction of pixels clipped at the sensor ceiling or floor."""
    a = _to_float(img)
    full = _dtype_max(img)
    hi = float(np.mean(a >= full * (1 - 1e-6)))

    # ── A dark background is not a clipped floor ────────────────────────────────
    #
    # ``lo = mean(a <= 0)`` counts every pixel at zero as "clipped at the sensor floor". On a
    # background-subtracted image — which PyCAT produces everywhere — **half the background is
    # at zero by construction**, and the check reported **"9.17 % at floor -> POOR"** on an image
    # whose only real defect was elsewhere.
    #
    # A floor clip that MATTERS is one that has truncated real signal, and the signature of that
    # is a **spike** at zero: many pixels sharing exactly the minimum value, where a
    # noise-limited background would spread them over several counts. That is the same
    # pile-up logic the ceiling check uses (1.5.465), applied at the other end.
    #
    # A camera also has a pedestal, so a raw acquisition essentially never has pixels at exactly
    # zero. **Zeros mean the image has already been processed** — and a processed image's floor
    # is not a sensor property.
    _at_zero = float(np.mean(a <= 0.0))
    if _at_zero > 0:
        # Is the zero a SPIKE, or just the low tail of a noisy background? Compare the count at
        # zero against the count in the next few levels up.
        _near = float(np.mean((a > 0) & (a <= max(full * 0.01, 3.0))))
        _is_spike = _at_zero > 3.0 * max(_near, 1e-9)
    else:
        _is_spike = False

    lo = _at_zero if _is_spike else 0.0
    worst = max(hi, lo)
    status = 'good' if worst < 0.001 else ('warn' if worst < 0.01 else 'bad')
    return dict(
        name='Saturation / clipping', tier='core', status=status,
        value=worst * 100.0, unit='%',
        headline=f"{hi*100:.2f}% at ceiling, {lo*100:.2f}% at floor",
        how="Fraction of pixels sitting exactly at the sensor's maximum (or at "
            "zero). Clipped pixels have lost their true intensity.",
        good="Well under 0.1% clipped. Any bright saturated region means "
             "intensity/quantitative measurements there are unreliable.",
        diag=dict(hist_counts=np.histogram(a, bins=64)[0],
                  hist_edges=np.histogram(a, bins=64)[1], ceiling=full))
