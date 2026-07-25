"""Colocalization **thresholding** — the Costes threshold determination and the Manders sensitivity sweep
(coloc_decomposition).

`costes_thresholding` (+ its `costes_linear_model` regression) find the intensity thresholds below which two
channels are treated as background, per Costes; `manders_threshold_sensitivity` sweeps the Manders M1/M2 across
thresholds so a coefficient is reported with its stability, not as a single fragile number. Moved VERBATIM out
of `pixel_wise_corr_analysis_tools`, which re-exports them — no number changed; pinned by the coloc test net.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy


def manders_threshold_sensitivity(image1, image2, roi_mask,
                                  base_threshold1=None, base_threshold2=None,
                                  perturbations=(-0.30, -0.20, -0.10, 0.0,
                                                 0.10, 0.20, 0.30)):
    """How much does Manders' M1/M2 move when the threshold is PERTURBED?

    Manders' coefficients are *defined by* a threshold, so the number is only as
    defensible as that choice. Costes' method (also in this module) picks it in a
    principled way — but a single reported M1 still hides how much the answer hinges on
    where the cut landed.

    **The perturbation must be anchored to a threshold a real analysis would use.**
    A naive sweep over fixed percentiles of the whole image is the wrong grid and gives a
    misleading answer: with sparse objects, most percentiles land *inside the background*,
    where Manders is meaningless anyway. Two perfectly colocalised channels then appear
    "unstable" (M1 ranging 0.79–1.00) purely because the low thresholds admitted
    background noise. Verified while building this — the naive version raised a false
    alarm on identical channels.

    So: start from the base threshold (Costes, Otsu, or one supplied by the caller) and
    perturb it by ±10/20/30 % of the signal range. That answers the question that actually
    matters: *if my threshold were somewhat different — as another analyst's would be —
    would I report a different number?*

    Returns
    -------
    dict with:
      grid_df           : DataFrame of perturbation, thresholds, M1, M2, n_pixels
      m1_range/m2_range : (min, max) across the perturbations
      m1_spread/m2_spread
      stable            : True when BOTH spreads are < 0.10
      verdict           : plain-English statement of what may be concluded
    """
    a = np.asarray(image1, dtype=float)
    b = np.asarray(image2, dtype=float)
    m = np.ones(a.shape, dtype=bool) if roi_mask is None else np.asarray(roi_mask) != 0
    av, bv = a[m], b[m]
    if av.size == 0:
        return dict(grid_df=pd.DataFrame(), stable=False,
                    verdict="Empty ROI: no threshold sensitivity to report.")

    # Base threshold: Otsu is a reasonable, dependency-free stand-in when the caller has
    # not supplied one (the Costes threshold, when available, is the better anchor).
    def _base(v):
        try:
            from skimage.filters import threshold_otsu
            return float(threshold_otsu(v))
        except Exception:
            return float(np.percentile(v, 95))

    t1 = float(base_threshold1) if base_threshold1 is not None else _base(av)
    t2 = float(base_threshold2) if base_threshold2 is not None else _base(bv)

    # Perturb by a fraction of the SIGNAL range above the threshold, not of the raw
    # intensity: a ±30% shift of an absolute value means nothing without a scale.
    r1 = max(float(av.max()) - t1, 1e-9)
    r2 = max(float(bv.max()) - t2, 1e-9)

    rows = []
    for p in perturbations:
        ta = t1 + p * r1
        tb = t2 + p * r2
        A = av > ta
        B = bv > tb
        both = A & B
        m1 = float(av[both].sum() / av[A].sum()) if A.any() and av[A].sum() > 0 else np.nan
        m2 = float(bv[both].sum() / bv[B].sum()) if B.any() and bv[B].sum() > 0 else np.nan
        rows.append(dict(perturbation=p, threshold_ch1=ta, threshold_ch2=tb,
                         M1=m1, M2=m2, n_pixels_above=int(both.sum())))
    grid = pd.DataFrame(rows)

    m1v = grid['M1'].to_numpy(dtype=float); m1v = m1v[np.isfinite(m1v)]
    m2v = grid['M2'].to_numpy(dtype=float); m2v = m2v[np.isfinite(m2v)]
    if m1v.size == 0 or m2v.size == 0:
        return dict(grid_df=grid, stable=False, base_threshold1=t1, base_threshold2=t2,
                    verdict="Manders could not be computed across the perturbation grid.")

    m1_spread = float(m1v.max() - m1v.min())
    m2_spread = float(m2v.max() - m2v.min())
    stable = bool(m1_spread < 0.10 and m2_spread < 0.10)

    if stable:
        verdict = (f"M1 varies by {m1_spread:.2f} and M2 by {m2_spread:.2f} under a "
                   f"\u00b130% threshold perturbation. The coefficients are stable: the "
                   f"reported value does not hinge on the exact cut.")
    else:
        verdict = (f"M1 varies by {m1_spread:.2f} (range "
                   f"{m1v.min():.2f}\u2013{m1v.max():.2f}) and M2 by {m2_spread:.2f} "
                   f"under a \u00b130% threshold perturbation. The coefficient depends "
                   f"materially on the threshold, so it is an artefact of the cut as much "
                   f"as of the biology. Report the RANGE, state the thresholding method, "
                   f"or use a threshold-free measure (Pearson, Spearman, Li's ICQ) for the "
                   f"headline number.")

    return dict(
        grid_df=grid,
        base_threshold1=t1, base_threshold2=t2,
        m1_range=(float(m1v.min()), float(m1v.max())),
        m2_range=(float(m2v.min()), float(m2v.max())),
        m1_spread=m1_spread, m2_spread=m2_spread,
        stable=stable, verdict=verdict,
    )


def costes_linear_model(x, a, b):
    """
    Defines a linear model y = ax + b for fitting in Costes' thresholding algorithm, which is used
    to analyze the relationship between the intensity values of two fluorescence channels.

    Parameters
    ----------
    x : numpy.ndarray
        An array of x-values, which are the intensity values of one channel (e.g., red channel).
    a : float
        The slope of the linear model, representing the rate of change in y relative to x.
    b : float
        The intercept of the linear model, representing the y-value when x is zero.

    Returns
    -------
    numpy.ndarray
        The computed y-values for each x-value, based on the linear model.
    
    Notes
    -----
    This function is used within a curve fitting procedure to determine the optimal linear relationship
    between two fluorescence channels, which is a critical step in Costes' automatic thresholding for
    colocalization analysis.
    """
    return a * x + b


def costes_thresholding(red_channel, green_channel, roi_mask):
    """
    Applies Costes' thresholding method to determine intensity thresholds above which there is significant
    colocalization between two fluorescence channels, typically used in microscopy images.

    Parameters
    ----------
    red_channel : numpy.ndarray
        A 2D array representing the red channel of an image, where the colocalization is to be analyzed.
    green_channel : numpy.ndarray
        A 2D array representing the green channel of an image, where the colocalization is to be analyzed.
    roi_mask : numpy.ndarray or None
        A boolean mask indicating the region of interest. If None, the entire image is considered for analysis.

    Returns
    -------
    threshold_red : float
        The calculated threshold for the red channel above which significant colocalization is observed.
    threshold_green : float
        The calculated threshold for the green channel above which significant colocalization is observed.

    Notes
    -----
    The method iteratively finds the optimal thresholds by fitting a linear model to the intensities of the two channels and then
    calculating Pearson correlation coefficients. The process aims to maximize the correlation by excluding pixels below the
    thresholds that contribute insignificantly to the overall correlation, ensuring only significant colocalized signals are considered.
    This method assumes a primarily positive correlation between the channels and will not be effective for negatively correlated images.
    """
    # Apply ROI mask if provided, else flatten the entire channel arrays.
    if roi_mask is not None:
        red_flat = red_channel[roi_mask == 1]
        green_flat = green_channel[roi_mask == 1]
    else:
        red_flat = red_channel.flatten()
        green_flat = green_channel.flatten()

    # Fit the linear model to the intensity values of the red and green channels.
    params, _ = scipy.optimize.curve_fit(costes_linear_model, red_flat, green_flat)
    a, b = params  # Extract linear model parameters.

    # Calculate Pearson correlation coefficient for initial assessment.
    r, _ = scipy.stats.pearsonr(red_flat, green_flat)

    # Initialize the threshold starting from the maximum intensity observed.
    max_intensity = max(np.max(red_flat), np.max(green_flat))
    threshold = max_intensity

    # Define the stopping condition for the iterative thresholding.
    min_nonzero_intensity = max(np.min(red_flat[np.nonzero(red_flat)]), np.min(green_flat[np.nonzero(green_flat)])) + 0.01

    iterations = 0
    # Iteratively adjust the threshold to find the optimal values.
    while threshold > min_nonzero_intensity and np.abs(r) > 0.1 and iterations < 50:
        # Apply current thresholds to identify pixels to be excluded.
        mask = (red_flat > threshold) & (green_flat > a * threshold + b)
        if np.any(mask):
            # Recalculate Pearson correlation coefficient for pixels below the current threshold.
            r, _ = scipy.stats.pearsonr(red_flat[~mask], green_flat[~mask])

        # Decrement the threshold slightly for the next iteration.
        threshold -= 0.01
        iterations += 1

    # Calculate the final thresholds for both channels.
    threshold_red = threshold
    threshold_green = a * threshold + b

    return threshold_red, threshold_green
