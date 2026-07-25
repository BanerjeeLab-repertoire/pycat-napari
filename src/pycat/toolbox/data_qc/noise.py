"""Noise QC — signal-to-noise ratio against a MAD-based noise floor.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc._base import _robust_noise_std, _mean_frame

def qc_snr(img):
    """Signal-to-noise: signal (robust dynamic range) over noise (robust
    high-frequency estimate)."""
    a = _mean_frame(img)
    noise = _robust_noise_std(a)
    # signal = spread of the real structure, robustly (5–95 percentile range)
    p5, p95 = np.percentile(a, [5, 95])
    signal = float(p95 - p5)
    snr = signal / noise if noise > 0 else np.inf
    status = 'good' if snr >= 10 else ('warn' if snr >= 4 else 'bad')
    return dict(
        name='SNR / noise', tier='core', status=status,
        value=float(snr), unit='×',
        headline=f"SNR ≈ {snr:.1f}  (noise σ ≈ {noise:.1f})",
        how="Signal = 5–95th-percentile intensity spread; noise = robust "
            "estimate from adjacent-pixel differences. SNR = signal / noise.",
        good="SNR ≳ 10 is comfortable; below ~4 the structure is buried in "
             "noise — increase exposure/illumination or average frames.",
        diag=dict(noise=noise, signal=signal))
