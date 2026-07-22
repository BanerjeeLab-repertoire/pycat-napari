"""Temporal correlation estimation - split out of timeseries_condensate_tools (1.6.244).

estimate_temporal_correlation samples frame pairs and estimates the frame-to-frame intensity correlation,
returning a regime classification + a recommendation for temporal enhancement. Moved VERBATIM - pinned by
test_temporal_enhancement (recovers the seeded correlation exactly). Reads frames via the frame_access
source reader.
"""
from __future__ import annotations

import numpy as np
from pycat.toolbox.timeseries.frame_access import _read_source_frame


def estimate_temporal_correlation(
    stack_data, n_sample_pairs: int = 20,
) -> dict:
    """
    Estimate frame-to-frame correlation in a time-series stack to check
    whether the acquisition is in an "oversampling regime" where
    pseudo-3D tri-planar temporal filtering (treating T like Z — see
    pseudo3d_tri_planar_filter) is justified.

    The same physical argument that makes tri-planar filtering valid for
    a Z-stack — genuine correlation between adjacent slices, typically
    from Nyquist-or-better axial sampling — only transfers to the time
    axis when frames are acquired fast enough relative to the sample's
    dynamics that adjacent frames are still highly similar. A slow
    time-lapse (minutes between frames, substantial condensate movement/
    fusion/fission between frames) does NOT have this property, and
    applying tri-planar-across-T filtering there would blur together
    frames that are only coincidentally adjacent in the file, not
    genuinely correlated — the opposite of what the technique is for.

    Parameters
    ----------
    stack_data : array-like, shape (T, H, W)
        Raw (unprocessed) time-series stack. Sampled, not read in full,
        for speed on large stacks.
    n_sample_pairs : int
        Number of consecutive-frame pairs to sample (evenly spaced across
        the stack) for the correlation estimate.

    Returns
    -------
    dict with keys:
        mean_correlation   : mean Pearson correlation between consecutive
                             sampled frame pairs (0-1, higher = more
                             oversampled / more redundant between frames)
        min_correlation, max_correlation
        regime             : 'oversampled' (mean_correlation > 0.9),
                             'moderate' (0.7-0.9),
                             'undersampled' (< 0.7)
        recommendation     : human-readable guidance string
    """
    n_t = stack_data.shape[0]
    if n_t < 2:
        return dict(mean_correlation=np.nan, regime='insufficient_data',
                    recommendation='Need at least 2 frames to estimate temporal correlation.')

    n_pairs = min(n_sample_pairs, n_t - 1)
    sample_indices = np.linspace(0, n_t - 2, n_pairs, dtype=int)

    correlations = []
    for t in sample_indices:
        f0 = np.asarray(_read_source_frame(stack_data, int(t)))
        f1 = np.asarray(_read_source_frame(stack_data, int(t) + 1))
        f0_flat = f0.ravel().astype(np.float64)
        f1_flat = f1.ravel().astype(np.float64)
        if f0_flat.std() < 1e-9 or f1_flat.std() < 1e-9:
            continue
        corr = float(np.corrcoef(f0_flat, f1_flat)[0, 1])
        correlations.append(corr)

    if not correlations:
        return dict(mean_correlation=np.nan, regime='insufficient_data',
                    recommendation='Could not compute correlation (flat/uniform frames sampled).')

    mean_corr = float(np.mean(correlations))
    min_corr  = float(np.min(correlations))
    max_corr  = float(np.max(correlations))

    if mean_corr > 0.9:
        regime = 'oversampled'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — this acquisition "
               f"is temporally oversampled. Pseudo-3D tri-planar temporal filtering "
               f"is well-justified and should improve consistency without blurring "
               f"real dynamics.")
    elif mean_corr > 0.7:
        regime = 'moderate'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — moderate temporal "
               f"correlation. Tri-planar temporal filtering may help but could "
               f"slightly soften fast dynamics; inspect results before relying on it "
               f"for quantitative analysis.")
    else:
        regime = 'undersampled'
        rec = (f"Mean frame-to-frame correlation {mean_corr:.2f} — frames change "
               f"substantially between timepoints. Tri-planar temporal filtering is "
               f"NOT recommended here: it would blend together frames that are only "
               f"coincidentally adjacent, not genuinely correlated, and could blur "
               f"or misrepresent real condensate dynamics.")

    return dict(mean_correlation=mean_corr, min_correlation=min_corr,
               max_correlation=max_corr, regime=regime, recommendation=rec)
