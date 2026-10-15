"""Exposure QC — saturation / clipping / dynamic-range checks.

Split out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np

from pycat.toolbox.data_qc._base import _to_float, _dtype_max

def qc_saturation(img):
    """Fraction of pixels clipped at the sensor ceiling; the floor is judged only when it is a sensor floor.

    The ceiling comes from ``_dtype_max``, which reads a clip from the data's own pile-up for integer AND float
    images alike (G5) -- so the same pixel values get the same verdict whatever container holds them."""
    from pycat.toolbox.data_qc import thresholds as th
    a = _to_float(img)
    full = _dtype_max(img)
    finite = a[np.isfinite(a)]
    hi = float(np.mean(finite >= full * (1 - 1e-6))) if finite.size else 0.0

    # ── A floor of exact zeros is a PROCESSED image's floor. Decline it; do not decide it. ──────────
    #
    # A camera has a pedestal, so a raw acquisition essentially never has pixels at exactly zero. **Zeros mean the
    # image has already been processed** (background-subtracted, clipped at 0) — and a processed image's floor is
    # not a sensor property. The old spike test compared the count at zero against a window 1 % of the dtype
    # range wide (655 counts on uint16), which swallowed most of a subtracted background: an image with **46.6 %
    # of its pixels at exactly 0 reported "0.00 % at floor, good"** (Completion Spec G9). Before that, a plain
    # ``mean(a <= 0)`` called half a subtracted background "clipped" and the image POOR. Neither is supportable:
    # the floor is reported as not assessable, with the fraction, and the verdict rests on the ceiling alone.
    at_zero = float(np.mean(finite <= 0.0)) if finite.size else 0.0
    floor_assessable = at_zero < th.PROCESSED_FLOOR_FRACTION
    status = ('good' if hi < th.SATURATION_GOOD_FRACTION else
              ('warn' if hi < th.SATURATION_WARN_FRACTION else 'bad'))
    floor_text = ("0 at floor" if floor_assessable else
                  f"floor not assessable ({at_zero * 100:.1f}% of pixels at exactly 0 — a processed image)")
    counts, edges = np.histogram(finite, bins=64) if finite.size else (np.zeros(64), np.linspace(0, 1, 65))
    return dict(
        name='Saturation / clipping', tier='core', status=status,
        value=hi * 100.0, unit='%',
        headline=f"{hi*100:.2f}% at ceiling, {floor_text}",
        how="Fraction of pixels sitting exactly at the sensor's ceiling, which is read from the data's own "
            "pile-up at its maximum (the same test for integer and float images). Clipped pixels have lost their "
            "true intensity. A floor of exact zeros means the image was processed (a camera has a pedestal), so "
            "the floor is reported as not assessable rather than judged.",
        good="Well under 0.1% clipped. Any bright saturated region means "
             "intensity/quantitative measurements there are unreliable.",
        diag=dict(hist_counts=counts, hist_edges=edges, ceiling=full,
                  floor_fraction_at_zero=at_zero, floor_assessable=floor_assessable))
