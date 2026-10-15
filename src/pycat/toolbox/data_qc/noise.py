"""Noise QC — signal-to-noise ratio against a MAD-based noise floor, measured PER FRAME.

Split out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np

from pycat.toolbox.data_qc._base import _robust_noise_std, _to_float


def _frame_snr(frame):
    """(snr, signal, noise) of one 2-D frame; snr NaN when there is no measurable noise or no structure."""
    noise = _robust_noise_std(frame)
    p5, p95 = np.percentile(frame, [5, 95])
    signal = float(p95 - p5)
    if not (noise > 0 and signal > 0):
        return float('nan'), signal, noise
    return signal / noise, signal, noise


def qc_snr(img, axis=None):
    """Signal-to-noise: signal (5–95th-percentile spread) over noise (two-axis MAD of adjacent differences).

    **Per frame, never on an averaged stack** (Completion Spec G3). The stack used to be collapsed to its mean
    frame first, so the noise fell as sqrt(N) before the estimator ran: identical per-frame data (sigma 25) scored
    SNR 4.7 ("warn") as one frame, 12.2 ("good") as 16 frames and 33 as 200 — the verdict flipped on frame COUNT,
    and the reported noise (2.3 for the true per-frame 25) was not the acquisition's. A stack now reports the
    MEDIAN of its per-frame SNRs, and says so.

    A z-stack (``axis='z'``) reports its BEST plane instead: its outer planes are deliberately out of focus, so the
    median plane understates the SNR the acquisition achieved at focus.

    A field with no measurable noise or no structure (flat, or synthetic) is declined (``na``): an infinite SNR
    used to be reported as "good"."""
    from pycat.toolbox.data_qc import thresholds as th
    a = _to_float(img)
    frames = list(a) if a.ndim == 3 else [a]
    per = [_frame_snr(f) for f in frames]
    snrs = np.array([p[0] for p in per], dtype=float)
    finite = snrs[np.isfinite(snrs)]
    signal = float(np.median([p[1] for p in per]))
    noise = float(np.median([p[2] for p in per]))
    basis = ("single frame" if len(frames) == 1 else
             f"best of {len(frames)} focal planes" if axis == 'z' else f"median of {len(frames)} per-frame SNRs")
    how = ("Signal = 5–95th-percentile intensity spread; noise = robust MAD estimate from adjacent-pixel "
           "differences along BOTH image axes (the larger, so row/column-structured noise is seen). SNR = "
           f"signal / noise, measured per frame — here the {basis}; never on an averaged stack, which would "
           "divide the noise by sqrt(frames).")
    if finite.size == 0:
        return dict(
            name='SNR / noise', tier='core', status='na', value=None, unit='×',
            headline='no measurable noise or structure — SNR is not defined here',
            how=how + " This field has no measurable noise (or no structure above it): a flat or synthetic "
                      "image has no SNR to judge.",
            good='', diag=dict(noise=noise, signal=signal, per_frame_snr=snrs))
    snr = float(np.max(finite) if axis == 'z' else np.median(finite))
    status = 'good' if snr >= th.SNR_GOOD else ('warn' if snr >= th.SNR_WARN else 'bad')
    return dict(
        name='SNR / noise', tier='core', status=status,
        value=snr, unit='×',
        headline=f"SNR ≈ {snr:.1f}  (noise σ ≈ {noise:.1f}; {basis})",
        how=how,
        good="SNR ≳ 10 is comfortable; below ~4 the structure is buried in "
             "noise — increase exposure/illumination or average frames.",
        diag=dict(noise=noise, signal=signal, per_frame_snr=snrs))
