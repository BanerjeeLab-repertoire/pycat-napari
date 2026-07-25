"""Colocalization **raw pairwise measures** — the correlation/overlap coefficients (coloc_decomposition).

Pearson (`pearsons_correlation`), the Manders overlap + M1/M2 coefficients (`manders_overlap`,
`manders_k1_calculation`, `manders_k2_calculation`), and the rank correlations (`spearman_r_calculation`,
`kendall_tau_calculation`, `weighted_tau_calculation`). Each takes two images + an ROI mask and returns a
scalar measure; none depends on another coloc function. Moved VERBATIM out of `pixel_wise_corr_analysis_tools`,
which re-exports them — no number changed; pinned by the colocalization test net.
"""
from __future__ import annotations

import numpy as np
import scipy
import skimage as sk

from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_warning as napari_show_error
from pycat.utils.general_utils import debug_log


def pearsons_correlation(image1, image2, roi_mask):
    """
    Calculates Pearson's correlation coefficient between two images, optionally within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is considered.

    Returns
    -------
    pcc : float
        Pearson's correlation coefficient. 
    p_val : float
        The corresponding p-value, both rounded to four decimal places.

    Notes
    -----
    Pearson's correlation measures the linear correlation between two datasets. Results are only valid if both
    images are of the same size and the mask, if applied, matches their dimensions.
    """
    # ── A WHOLE-FRAME ROI makes Pearson measure the CELL SHAPE, not colocalisation ──
    #
    # Pearson over the whole frame asks "are both channels bright in the same places" — and
    # the biggest structure both channels share is **the cell itself**, bright inside and dark
    # outside. That alone saturates the metric.
    #
    # Measured on channels that are COMPLETELY INDEPENDENT (zero real colocalisation), with
    # both carrying the same cell shape:
    #
    #     scene                          whole frame    cell ROI
    #     independent (r should be 0)    **0.987**      **0.011**
    #     50 % co-localised              0.997          0.712
    #     fully co-localised             1.000          1.000
    #
    # **On the whole frame all three read ~0.99 — the metric cannot distinguish them at all.**
    # Inside the cell ROI the shared shape is gone (every pixel is in the cell) and Pearson
    # measures the real correlation.
    #
    # (The camera pedestal, by contrast, does NOT matter here: Pearson is invariant to an
    # additive offset. Verified — a pedestal of 500 leaves r unchanged at 0.010.)
    try:
        _mask = np.asarray(roi_mask)
        if _mask.dtype != bool:
            _mask = _mask > 0
        _frac = float(_mask.mean()) if _mask.size else 0.0
        if _frac > 0.95:
            napari_show_warning(
                f"Pearson: the ROI covers {_frac:.0%} of the frame — that is effectively the "
                f"WHOLE IMAGE, and Pearson then measures the CELL SHAPE, not colocalisation."
                f"\n\nBoth channels are bright inside the cell and dark outside, and that "
                f"shared structure alone saturates the metric. Measured on channels that are "
                f"COMPLETELY INDEPENDENT (zero real colocalisation) but share a cell shape: "
                f"**r = 0.987 over the whole frame, and r = 0.011 inside the cell ROI.** A "
                f"50 %-colocalised scene reads 0.997 over the frame and 0.712 in the ROI — so "
                f"over the whole frame, no colocalisation and half colocalisation are "
                f"indistinguishable.\n\nRestrict the ROI to the cell (or the compartment you "
                f"mean to test). The number you get from a whole-frame ROI is not a "
                f"colocalisation coefficient.")
    except Exception as _exc:
        debug_log('Pearson: could not assess the ROI coverage', _exc)

    if image1.ndim == 2:
        # If the image is 2D, use a specific function (e.g., from skimage) to calculate the Pearson's Correlation Coefficient.
        pcc, p_val = sk.measure.pearson_corr_coeff(image1, image2, roi_mask)
    else: 
        # For non-2D arrays (e.g., 1D, 3D, or ND), flatten the arrays and use scipy's pearsonr function.
        # This block also considers the ROI mask if provided.
        if roi_mask is not None:
            image1 = image1[roi_mask]
            image2 = image2[roi_mask]
        pcc, p_val = scipy.stats.pearsonr(image1.flatten(), image2.flatten())

    return np.round(pcc, 4), np.round(p_val, 4)


def manders_overlap(image1, image2, roi_mask):
    """
    Calculates Mander's overlap coefficient for two images using a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray
        A boolean mask indicating the region of interest.

    Returns
    -------
    moc : float
        The Mander's overlap coefficient rounded to four decimal places.
    p_val : float
        NaN as a placeholder for a p-value, which is not available for this method.

    Notes
    -----
    Mander's overlap coefficient is used to quantify the degree of overlap between two images.
    """
    if image1.ndim != 2:
        # Manders' Overlap requires 2D images; if not 2D, warn the user and exit the function.
        napari_show_error('Image must be 2D for Manders Overlap Coefficient')
        return np.nan, np.nan
    
    # Calculate the Mander's Overlap Coefficient using the skimage function.
    moc = sk.measure.manders_overlap_coeff(image1, image2, roi_mask)
    moc_p = np.nan  # Placeholder for formatting consistency with other functions

    return np.round(moc, 4), moc_p


def manders_k1_calculation(image1, image2, roi_mask):
    """
    Calculates Mander's k1 coefficient, reflecting the contribution of the first image's intensity to the overlap 
    within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. 

    Returns
    -------
    k1 : float
        Mander's k1 coefficient rounded to four decimal places.
    p_val : float
        NaN as a placeholder for a p-value which is not available for this method.

    Notes
    -----
    Mander's k1 coefficient measures how much of the first image's intensity overlaps with the second image.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images, if specified.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]
    
    # Calculate the Mander's k1 Coefficient
    k1 = np.sum(image1 * image2) / np.sum(np.square(image1))

    return np.round(k1, 4), np.nan


def manders_k2_calculation(image1, image2, roi_mask):
    """
    Calculates Mander's k2 coefficient, reflecting the contribution of the second image's intensity to the overlap 
    within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. 

    Returns
    -------
    k2 : float
        Mander's k2 coefficient rounded to four decimal places.
    p_val : float
        NaN as a placeholder for a p-value which is not available for this method.

    Notes
    -----
    Mander's k2 coefficient measures how much of the second image's intensity overlaps with the first image.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images, if specified.
        image1 = image1[roi_mask] 
        image2 = image2[roi_mask]

    # Calculate the Mander's k2 Coefficient
    k2 = np.sum(image1 * image2) / np.sum(np.square(image2))

    return np.round(k2, 4), np.nan


def spearman_r_calculation(image1, image2, roi_mask):
    """
    Calculates Spearman's rank correlation coefficient between two images, optionally within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is considered.

    Returns
    -------
    spearman_coeff : float
        The Spearman's rank correlation coefficient rounded to four decimal places.
    spearman_p : float
        The p-value associated with the Spearman's correlation, rounded to four decimal places.

    Notes
    -----
    Spearman's correlation assesses the monotonic relationship between two datasets, ranking the data before 
    calculating the Pearson correlation on these ranks. This measure is non-parametric and does not assume a normal 
    distribution of the data.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images if provided.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]

    # Flatten the images and use scipy.stats.spearmanr to calculate the Spearman's correlation.
    spearman_coeff, spearman_p = scipy.stats.spearmanr(image1.flatten(), image2.flatten())

    return np.round(spearman_coeff, 4), np.round(spearman_p, 4)


def kendall_tau_calculation(image1, image2, roi_mask):
    """
    Calculates Kendall's Tau, a non-parametric statistic used to measure the ordinal association between two 
    measured quantities, optionally within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is considered.

    Returns
    -------
    kendall_coeff : float
        Kendall's Tau coefficient rounded to four decimal places.
    kendall_p : float   
        The p-value associated with the Kendall's Tau coefficient, rounded to four decimal places.

    Notes
    -----
    Kendall's Tau is especially useful for data without a normal distribution and can handle ties in the data.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images if provided.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]

    # Flatten the images and use scipy.stats.kendalltau to calculate Kendall's Tau.
    kendall_coeff, kendall_p = scipy.stats.kendalltau(image1.flatten(), image2.flatten())

    return np.round(kendall_coeff, 4), np.round(kendall_p, 4)


def weighted_tau_calculation(image1, image2, roi_mask):
    """
    Calculates the weighted Tau coefficient between two images, providing a refined version of Kendall's Tau that considers
    the strength of association between pairs, optionally within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is considered.

    Returns
    -------
    weighted_tau : float
        The weighted Tau coefficient rounded to four decimal places.
    weighted_p : float
        The p-value associated with the weighted Tau coefficient, rounded to four decimal places.

    Notes
    -----
    Weighted Tau is advantageous in the presence of ties or varying degrees of association between rankings, making it suitable
    for complex data sets.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images if provided.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]

    # Flatten the images and use scipy.stats.weightedtau to calculate the Weighted Tau.
    weighted_tau, weighted_p = scipy.stats.weightedtau(image1.flatten(), image2.flatten())

    return np.round(weighted_tau, 4), np.round(weighted_p, 4)


def li_intensity_correlation(image1, image2, roi_mask):
    """
    Calculates Li's Intensity Correlation Analysis (ICA) coefficient [li_ica_1]_, which quantifies the pixel intensity relationship between two images,
    particularly useful for co-localization studies in bioimaging, optionally within a region of interest (ROI).

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is considered.

    Returns
    -------
    icq : float
        The Intensity Correlation Quotient (ICQ) rounded to four decimal places.
    p_val : float
        The p-value associated with the ICQ, calculated using the z-score for a two-tailed test rounded to four decimal places.
    tuple
        The Intensity Correlation Quotient (ICQ) and its associated p-value, both rounded to four decimal places.

    Notes
    -----
    Li's ICA is particularly effective in bioimaging for assessing the degree of co-localization between different fluorescent markers.
    The ICQ provides a normalized measure of correlation, with values above zero indicating positive correlation.

    References
    ----------
    .. [li_ica_1] Li, Q., Lau, A., Morris, T. J., Guo, L., Fordyce, C. B., & Stanley, E. F. (2004). 
        A syntaxin 1, Galpha(o), and N-type calcium channel complex at a presynaptic nerve terminal: analysis by quantitative immunocolocalization. 
        Journal of Neuroscience, 24(16), 4070-4081. doi: 10.1523/JNEUROSCI.0346-04.2004
        https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6729428/
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images if provided.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]
    
    # Calculate the product of differences from mean, which is a key step in Li's ICA.
    ica_product = (image1 - np.mean(image1)) * (image2 - np.mean(image2))
    
    # Perform a non-parametric sign test on the product to determine positive and negative correlations.
    ica_nps_test = np.sign(ica_product)
    
    # Count the number of positive and negative products for the ICQ calculation.
    N_pos_px = np.sum(ica_nps_test > 0)
    N_neg_px = np.sum(ica_nps_test < 0)
    
    # Calculate the Intensity Correlation Quotient (ICQ) which is the ratio of postive ica product pixels on the total, 
    #centered on the range (-0.5, 0.5)
    icq = (N_pos_px / np.size(ica_product)) - 0.5

    # Calculate the total number of pixels considered and compute the z-score for the p-value calculation.
    N_px = N_pos_px + N_neg_px
    z = (N_pos_px - N_px / 2) / np.sqrt(N_px / 4)

    # Calculate the p-value using the z-score for a two-tailed test.
    p_value = 2 * scipy.stats.norm.sf(abs(z)) # sf is the survival function (1 - cdf)

    return np.round(icq, 4), np.round(p_value, 4)
