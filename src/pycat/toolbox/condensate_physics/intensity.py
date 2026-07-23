"""Condensate **intensity-histogram decomposition** — split out of condensate_physics_tools (1.6.219).

fit_bimodal_intensity + intensity_decomposition_per_cell: a two-Gaussian decomposition of a condensate
intensity histogram into dilute + dense phases. Moved VERBATIM - no number changed. The tools module
re-exports both.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import skimage as sk
from scipy import optimize

from pycat.utils.object_ref import bbox_columns_from_regionprops as _bbox_cols


# ---------------------------------------------------------------------------
# 2. Intensity histogram decomposition
# ---------------------------------------------------------------------------

def fit_bimodal_intensity(
    image: np.ndarray,
    cell_mask: np.ndarray,
    n_bins: int = 256,
    min_dense_fraction: float = 0.01,
) -> dict:
    """
    Fit a bimodal Gaussian mixture to the pixel intensity distribution
    within a cell, extracting dilute and dense phase intensities.

    The two Gaussians represent:
      G1: dilute phase (background cytoplasm / nucleoplasm)
      G2: dense phase (condensate interior)

    Parameters
    ----------
    image : (H, W) float32 in [0, 1]
    cell_mask : (H, W) binary mask for the cell
    n_bins : histogram bins
    min_dense_fraction : minimum expected dense-phase pixel fraction.
        If the fit places fewer pixels in G2, G2 is not returned.

    Returns
    -------
    dict with keys:
        dilute_mean    : mean intensity of dilute phase (proxy for C_sat)
        dilute_std     : std of dilute phase
        dense_mean     : mean intensity of dense phase (proxy for C_dense)
        dense_std      : std of dense phase
        dense_fraction : fraction of cell pixels classified as dense phase
        partition_coeff: dense_mean / dilute_mean (intensity partition coeff)
        fit_success    : bool
        histogram_x    : bin centres (for plotting)
        histogram_y    : normalised counts (for plotting)
        fit_y          : fitted bimodal curve
        fit_y1         : dilute-phase Gaussian component
        fit_y2         : dense-phase Gaussian component
    """
    pixels = image[cell_mask > 0].ravel()
    if len(pixels) < 100:
        return dict(fit_success=False)

    counts, edges = np.histogram(pixels, bins=n_bins, density=True)
    centres = 0.5 * (edges[:-1] + edges[1:])

    # Initial guesses: dilute at 10th percentile, dense at 90th percentile
    p10 = float(np.percentile(pixels, 10))
    p90 = float(np.percentile(pixels, 90))

    def bimodal(x, a1, m1, s1, a2, m2, s2):
        g1 = a1 * np.exp(-0.5 * ((x - m1) / (s1 + 1e-9))**2)
        g2 = a2 * np.exp(-0.5 * ((x - m2) / (s2 + 1e-9))**2)
        return g1 + g2

    p0 = [counts.max() * 0.8, p10, 0.05,
          counts.max() * 0.2, p90, 0.05]
    bounds = ([0, 0, 1e-4, 0, 0, 1e-4],
              [np.inf, 1, 1,  np.inf, 1, 1])

    try:
        popt, _ = optimize.curve_fit(bimodal, centres, counts,
                                      p0=p0, bounds=bounds, maxfev=5000)
        a1, m1, s1, a2, m2, s2 = popt

        # Ensure G1 is dilute (lower mean) and G2 is dense (higher mean)
        if m1 > m2:
            a1, m1, s1, a2, m2, s2 = a2, m2, s2, a1, m1, s1

        # Classify pixels
        g1_resp = a1 * np.exp(-0.5*((pixels - m1)/max(s1, 1e-9))**2)
        g2_resp = a2 * np.exp(-0.5*((pixels - m2)/max(s2, 1e-9))**2)
        dense_px = (g2_resp > g1_resp).sum()
        dense_frac = dense_px / len(pixels)

        y_fit = bimodal(centres, *popt)
        y1    = a1 * np.exp(-0.5*((centres - m1)/max(s1, 1e-9))**2)
        y2    = a2 * np.exp(-0.5*((centres - m2)/max(s2, 1e-9))**2)

        return dict(
            dilute_mean=float(m1),
            dilute_std=float(s1),
            dense_mean=float(m2),
            dense_std=float(s2),
            dense_fraction=float(dense_frac),
            partition_coeff=float(m2 / max(m1, 1e-9)),
            fit_success=dense_frac >= min_dense_fraction,
            histogram_x=centres,
            histogram_y=counts,
            fit_y=y_fit,
            fit_y1=y1,
            fit_y2=y2,
        )
    except Exception:  # broad-ok: reports fit_success=False — an honest failure flag with no fabricated fit values
        return dict(fit_success=False)


def intensity_decomposition_per_cell(
    image: np.ndarray,
    labeled_cells: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """Run bimodal intensity decomposition for each labeled cell."""
    rows = []
    for prop in sk.measure.regionprops(labeled_cells):
        cmask = (labeled_cells == prop.label)
        result = fit_bimodal_intensity(image, cmask)
        row = {'cell_label': prop.label,
               'cell_area_um2': prop.area * microns_per_pixel**2,
               # Keep the bbox: a row without it cannot be brushed back to an image.
               **_bbox_cols(prop)}
        if result.get('fit_success'):
            row.update({k: result[k] for k in
                        ('dilute_mean','dilute_std','dense_mean',
                         'dense_std','dense_fraction','partition_coeff')})
        rows.append(row)
    return pd.DataFrame(rows)
