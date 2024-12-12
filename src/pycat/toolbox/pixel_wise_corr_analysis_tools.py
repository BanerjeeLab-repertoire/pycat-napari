"""
Pixel-Wise Correlation Coefficient Analysis (PWCA) Module for PyCAT

This module contains functions for pixel-wise correlation analysis, including Pearson's correlation, Spearman's correlation, Kendall's Tau, and 
Costes' thresholding method for colocalization analysis. It also provides tools for generating cross-correlation matrices, calculating intensity 
correlation coefficients, and scrambling pixels for testing statistical significance purposes. The module includes functions for visualizing
correlation results, such as scatter plots, cytofluorograms, and histograms for intensity correlation analysis. 

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np
import scipy
import skimage as sk
import matplotlib.pyplot as plt
import pandas as pd
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QLabel
from napari.utils.notifications import show_error as napari_show_error

# Local application imports
from pycat.ui.ui_utils import show_dataframes_dialog



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

def li_ica_histogram(image1, image2, roi_mask):
    """
    Generates data for a 2D histogram to visualize Li's Intensity Correlation Analysis (ICA) between two images,
    optionally within a region of interest (ROI). This visualization assists in interpreting the intensity correlation results.

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
    ica_product : numpy.ndarray
        The product of differences from the mean for each pixel, suitable for histogram plotting.
    image1 : numpy.ndarray
        The masked first image, suitable for histogram plotting.
    image2 : numpy.ndarray
        The masked second image, suitable for histogram plotting.

    Notes
    -----
    This histogram data can be useful for detailed analysis and presentation of co-localization results, helping to identify
    patterns or anomalies in the correlation of intensities between the two images.
    """
    if roi_mask is not None:
        # Apply the ROI mask to both images if provided.
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]
    
    # Calculate the ICA product as done in the li_intensity_correlation function.
    ica_product = (image1 - np.mean(image1)) * (image2 - np.mean(image2))

    return ica_product, image1, image2


def li_ica_plot(ica_product, image1, image2):
    """
    Generates scatter plots to visualize the relationship between the Li's ICA product and the intensities of two images,
    helping to understand the correlation and intensity distribution between the two.

    Parameters
    ----------
    ica_product : numpy.ndarray
        The product of differences from the mean for each pixel, obtained from Li's ICA.
    image1 : numpy.ndarray
        Intensity values of the first image.
    image2 : numpy.ndarray
        Intensity values of the second image.

    Notes
    -----
    Two scatter plots are created side by side: one plotting the ICA product versus image1's intensities,
    and the other against image2's intensities. This visual representation can be pivotal for assessing the degree
    of co-localization or interaction between the two images.

    Please see figure 2 in the reference for an example of how this histogram can be used to interpret the results [ica_plot_1]_:
    https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6729428/

    References
    ----------
    .. [ica_plot_1] Li, Q., Lau, A., Morris, T. J., Guo, L., Fordyce, C. B., & Stanley, E. F. (2004). 
        A syntaxin 1, Galpha(o), and N-type calcium channel complex at a presynaptic nerve terminal: analysis by quantitative immunocolocalization. 
        Journal of Neuroscience, 24(16), 4070-4081. doi: 10.1523/JNEUROSCI.0346-04.2004
    """

    # Initialize a figure with two subplots (side by side) for comparative visualization.
    fig, axs = plt.subplots(1, 2, figsize=(10, 5))

    # Plotting the ICA product against the intensity values from image1.
    axs[0].scatter(ica_product, image1, alpha=0.4, color='blue', s=5)
    axs[0].set_title("ICA Product vs Image 1", fontsize=14)
    axs[0].set_xlabel("Prod of the Diff from Mean (A-a)*(B-b)", fontsize=12)
    axs[0].set_ylabel("Image 1 Intensity (a.u.)", fontsize=12)

    # Plotting the ICA product against the intensity values from image2.
    axs[1].scatter(ica_product, image2, alpha=0.4, color='green', s=5)
    axs[1].set_title("ICA Product vs Image 2", fontsize=14)
    axs[1].set_xlabel("Prod of the Diff from Mean (A-a)*(B-b)", fontsize=12)
    axs[1].set_ylabel("Image 2 Intensity (a.u.)", fontsize=12)

    # Adjust the layout to make it tight and show the plot.
    plt.tight_layout()
    plt.show()
    

def cytofluorogram_plot(image1, image2):
    """
    Creates a cytofluorogram by plotting the intensity values of one image against those of another,
    typically used in cellular studies to compare two different fluorescence markers.

    Parameters
    ----------
    image1 : numpy.ndarray
        Intensity values of the first image.
    image2 : numpy.ndarray
        Intensity values of the second image.

    Notes
    -----
    A cytofluorogram is a scatter plot that is useful for visualizing the correlation between the fluorescence
    intensities of two distinct images, facilitating the analysis of how these intensities vary in relation to each other.
    """
    # Initialize a figure for the cytofluorogram.
    fig, ax = plt.subplots(1, 1, figsize=(5, 5))

    # Scatter plot of the intensity values from image1 against those from image2.
    ax.scatter(image1, image2, alpha=0.4, s=5)
    ax.set_title("Cytofluorogram", fontsize=16)
    ax.set_xlabel("Image 1 Intensity (a.u.)", fontsize=14)
    ax.set_ylabel("Image 2 Intensity (a.u.)", fontsize=14)

    # Adjust the layout and show the plot.
    plt.tight_layout()
    plt.show()


def scramble_blocks(image, block_size):
    """
    Scrambles the pixels of an image in blocks of a specified size. This function is intended for demonstration purposes and
    requires additional handling for images that are not perfectly divisible by the block size.

    Parameters
    ----------
    image : numpy.ndarray
        The image array to be scrambled.
    block_size : tuple
        The size of each block to scramble, specified as a tuple matching the image dimensions.

    Returns
    -------
    scrambled_image : numpy.ndarray
        The image with scrambled blocks.

    Notes
    -----
    This function currently serves as a placeholder and lacks full implementation details. It assumes that the
    image dimensions are perfectly divisible by the block size. Further development is required for robust functionality.
    """
    # This function does not work, and needs to be rewritten, it is merely a placeholder
    # It requires extensive logic for dealing with images/arrays that are not perfectly divisible by the block size
    # where the block size is determined by the psf resolution 
    scrambled_image = np.copy(image)
    for dim in range(image.ndim):
        shape = list(image.shape)
        num_blocks = shape[dim] // block_size[dim]
        shape[dim] = block_size[dim]
        for idx in np.ndindex(*shape):
            block_indices = [slice(idx[i], idx[i] + block_size[i]) if i == dim else idx[i] for i in range(image.ndim)]
            block = scrambled_image[tuple(block_indices)]
            block_shape = block.shape
            block_flat = block.flatten()
            np.random.shuffle(block_flat)
            scrambled_image[tuple(block_indices)] = block_flat.reshape(block_shape)
    return scrambled_image

def scramble_pixels(image, roi_mask, block_size=1):
    """
    Scrambles the pixels of an image either globally or within a specified region of interest (ROI), with an option to
    scramble in blocks of specified sizes.

    Parameters
    ----------
    image : numpy.ndarray
        The image array to be scrambled.
    roi_mask : numpy.ndarray, optional
        A boolean mask indicating the region of interest. If None, the entire image is scrambled.
    block_size : int or tuple of int, optional
        The size of blocks to be scrambled. If an integer is provided, it's considered uniform across all dimensions.
        If a tuple, it should match the image dimensions.

    Returns
    -------
    numpy.ndarray
        The scrambled image.

    Raises
    ------
    ValueError
        If `block_size` is not an integer or a tuple matching the image dimensions, or if any dimension of `block_size` 
        is less than 1.

    Notes
    -----
    This function provides flexibility in scrambling, allowing for selective scrambling within a region or across the entire image.
    It's particularly useful for testing or simulating disturbances in image data.
    """
    if isinstance(block_size, int):
        block_size = (block_size,) * image.ndim  # Ensure block_size is a tuple matching the image dimensions.
    elif not isinstance(block_size, tuple) or len(block_size) != image.ndim:
        raise ValueError("block_size must be an integer or a tuple of the same length as the array dimensions")

    if any(size < 1 for size in block_size):
        raise ValueError("Block size must be at least 1 in all dimensions")
    
    if roi_mask is not None:
        # If a ROI mask is specified, scramble only within the mask.
        return scramble_pixels_within_mask(image, roi_mask)
    
    else:
        # If block size is 1 in all dimensions, perform a simple pixel-wise scramble.
        if all(size == 1 for size in block_size):
            return np.random.permutation(image.flatten()).reshape(image.shape)

    # For block sizes other than 1, use a specialized scrambling function (not shown here).
    return scramble_blocks(image, block_size)


def scramble_pixels_within_mask(image, mask):
    """
    Scrambles the pixels within a specified mask of an image to disrupt any inherent spatial relationships. This method
    is often used in image analysis to assess the impact of pixel arrangement on analytical outcomes.

    Parameters
    ----------
    image : numpy.ndarray
        The image array in which pixels are to be scrambled.
    mask : numpy.ndarray
        A boolean array of the same shape as `image`, indicating the pixels to be scrambled.

    Returns
    -------
    scrambled_image : numpy.ndarray
        The image with pixels scrambled only within the specified mask regions.

    Notes
    -----
    Only the pixels within the mask are scrambled, preserving the pixel values outside of the mask.
    """
    masked_indices = np.where(mask)  # Find the indices of the pixels within the mask.
    scrambled_image = np.copy(image)  # Create a copy of the image to scramble pixels within.
    
    # Extract the masked pixels.
    masked_pixels = image[masked_indices]
    
    # Scramble the masked pixels.
    np.random.shuffle(masked_pixels)
    
    # Reassign the scrambled pixels back to their original positions within the mask.
    scrambled_image[masked_indices] = masked_pixels
    
    return scrambled_image

def perform_costes_test(image1, image2, cc_method, roi_mask, num_randomizations=100):
    """
    Performs Costes' statistical significance test to validate the non-randomness of colocalization between two images,
    using pixel randomization. This method compares an observed colocalization coefficient to a distribution generated
    by randomizing one image's pixels.

    Parameters
    ----------
    image1 : numpy.ndarray
        The first image array.
    image2 : numpy.ndarray
        The second image array.
    cc_method : function
        The correlation coefficient method to be used for calculation (e.g., Pearson's correlation).
    roi_mask : numpy.ndarray
        A boolean mask indicating the region of interest for colocalization analysis.
    num_randomizations : int, optional
        The number of randomizations to perform for generating the null distribution, default is 100.

    Returns
    -------
    p_value : float
        The p-value for the observed colocalization coefficient against the null distribution, rounded to four decimal places.
    cc_distribution : numpy.ndarray
        The null distribution of colocalization coefficients generated by randomizing one image's pixels.

    Notes
    -----
    Costes' test involves scrambling one of the images multiple times to generate a distribution of colocalization
    coefficients under the null hypothesis of random colocalization. The significance of the observed colocalization
    is assessed based on how extreme it is in this null distribution.
    """
    observed_cc = cc_method(image1, image2, roi_mask)[0]  # Calculate the observed colocalization coefficient.
    cc_distribution = []  # Initialize list to hold the randomized colocalization coefficients.
    extreme_cc_count = 0  # Counter for the number of times randomized coefficient is more extreme than observed.
    
    for _ in range(num_randomizations):
        scrambled_image = scramble_pixels(image1, roi_mask)  # Randomly scramble the pixels of the first image.
        scrambled_cc = cc_method(scrambled_image, image2, roi_mask)[0]  # Calculate the colocalization coefficient with the scrambled image.
        
        cc_distribution.append(scrambled_cc)
        # Count if the randomized coefficient is more extreme than the observed, for both positive and negative observed coefficients.
        if (observed_cc >= 0 and scrambled_cc > observed_cc) or (observed_cc < 0 and scrambled_cc < observed_cc):
            extreme_cc_count += 1

    p_value = extreme_cc_count / num_randomizations  # Calculate the p-value as the proportion of more extreme cases.

    return np.round(p_value, 4), np.round(cc_distribution, 4)

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


def cross_correlation_matrix(image1, image2, roi_mask=None):
    """
    Computes the cross-correlation matrix for two images to assess the degree to which they are correlated with each other
    at different shifts and displacements. Optionally, the computation can be confined to a specified region of interest (ROI).
    Additionally, this function calculates the peak value of the cross-correlation matrix, identifies its location,
    and determines the mean squared error (MSE) between the two images, providing a comprehensive assessment of their similarity.

    Parameters
    ----------
    image1 : numpy.ndarray
        The first input image, expected to be a 2D array.
    image2 : numpy.ndarray
        The second input image, expected to be of the same dimensions as `image1`.
    roi_mask : numpy.ndarray, optional
        An optional boolean array of the same shape as `image1` and `image2`, indicating the region of interest.
        If provided, calculations are restricted to the masked area.

    Returns
    -------
    cc_scipy : numpy.ndarray
        The cross-correlation matrix, rounded to four decimal places.
    peak_value : float
        The peak value of the cross-correlation matrix, rounded to four decimal places.
    peak_location : tuple
        The (y, x) location of the peak value within the matrix.
    mse : float
        The mean squared error (MSE) between the two images, rounded to four decimal places.

    Notes
    -----
    Cross-correlation is a measure of similarity between two signal sources as a function of the displacement of one relative
    to the other. This function is particularly useful in applications where alignment or similarity between two images needs
    to be evaluated, such as in image registration or tracking. The calculation of MSE provides an additional metric for assessing
    the direct pixel-wise differences between the images.
    """
    # Apply the ROI mask if provided.
    if roi_mask is not None:
        image1 = image1[roi_mask]
        image2 = image2[roi_mask]

    # Calculate the cross-correlation matrix using `scipy.signal.correlate` with 'full' mode to consider all overlaps.
    cc_scipy = scipy.signal.correlate(image1, image2, mode='full')

    # Find the peak value in the cross-correlation matrix and its location.
    peak_value = np.max(cc_scipy)
    peak_location = np.unravel_index(np.argmax(cc_scipy), cc_scipy.shape)

    # Calculate the mean squared error (MSE) between the two images.
    # This requires the original, unmasked images to have the same size, so either reshape or apply the same ROI.
    mse = np.sum((image1 - image2) ** 2) / image1.size

    # Return the cross-correlation matrix, peak value, location of the peak, and MSE, all rounded to 4 decimal places.
    return np.round(cc_scipy, 4), np.round(peak_value, 4), peak_location, np.round(mse, 4)


class pwcaDialog(QDialog):
    """
    A PyQt dialog window designed for the selection of correlation analysis methods within the Napari pyQT application.
    This dialog provides checkboxes grouped by different types of analysis such as Correlation Coefficient Analysis (CCA),
    Modified Costes Analysis (MCA), Correlation Matrix Analysis (CMA), and Intensity Correlation Plots. Users can select,
    deselect, accept, or cancel their choices, making it versatile for varied analytical needs.

    Attributes
    ----------
    checkboxes : list
        A list of QCheckBox widgets used to enable selection of different analysis methods.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget of this dialog, typically the main window of the application. Defaults to None.
    
    Methods
    -------
    _create_section_layout(title, options):
        Helper method to create a section with checkboxes based on the provided title and list of options.
    _create_selection_buttons():
        Helper method to create layout with 'Select All' and 'Deselect All' buttons.
    _create_action_buttons():
        Helper method to create layout with 'OK' and 'Cancel' buttons.
    select_all():
        Selects all checkboxes within the dialog.
    deselect_all():
        Deselects all checkboxes within the dialog.
    get_selected_methods():
        Returns a list of the selected analysis methods based on the checkboxes that are checked.
    """
    def __init__(self, parent=None):
        """
        Initializes the pwcaDialog with various sections for selecting correlation analysis methods, including buttons
        for selecting all options, deselecting all, accepting the selection, or cancelling.
        """
        super().__init__(parent)
        self.setWindowTitle('Select Correlation Analysis Methods')
        self.checkboxes = []

        # Main layout for the dialog
        self.layout = QVBoxLayout(self)

        # Define different sections and their corresponding analysis methods
        cca_methods = ["Pearson's R value", "Spearman's R value", "Kendall's Tau value",
                       "Weighted Tau value", "Li's ICQ value", "Mander's Overlap Coefficient", 
                       "Mander's k1 value", "Mander's k2 value"]
        costes_methods = ["Costes Automatic Thresholded M1 & M2", "Calculate Costes Significance", 
                          "Perform Modified Costes Thresholding"]
        cma_methods = ["Calculate Correlation Matrix and metrics"]
        intensity_correlation_plots = ["Li's ICA Histogram", "Cytofluorogram"]

        # Create sections for each analysis type
        self.layout.addLayout(self._create_section_layout("Correlation Coefficient Analysis", cca_methods)) #Correlation Coefficient Analysis (CCA)
        self.layout.addLayout(self._create_section_layout("Modified Costes Analysis", costes_methods)) #Modified Costes Analysis (MCA)
        self.layout.addLayout(self._create_section_layout("Correlation Matrix Analysis", cma_methods)) #Correlation Matrix Analysis (CMA)
        self.layout.addLayout(self._create_section_layout("Intensity Correlation Plots", intensity_correlation_plots)) #Intensity Correlation Plots

        # Add selection and action buttons
        self.layout.addLayout(self._create_selection_buttons()) # Select All and Deselect All buttons
        self.layout.addLayout(self._create_action_buttons()) # OK and Cancel buttons

    def _create_section_layout(self, title, options):
        """
        Creates a layout for a specific section within the dialog, populating it with checkboxes for each provided
        analysis option. Each section is titled and separated for clear distinction.

        Parameters
        ----------
        title : str
            The title of the section, which categorizes the options.
        options : list of str
            A list of descriptions for each checkbox representing an analysis method.

        Returns
        -------
        QVBoxLayout
            A layout containing a label for the section title and checkboxes for each option.
        """
        section_layout = QVBoxLayout()
        section_label = QLabel(title)
        section_layout.addWidget(section_label)

        for option in options:
            checkbox = QCheckBox(option)
            self.checkboxes.append(checkbox)
            section_layout.addWidget(checkbox)

        return section_layout

    def _create_selection_buttons(self):
        """
        Creates a layout with 'Select All' and 'Deselect All' buttons to provide quick selection management within
        the dialog. This facilitates easier user interaction when multiple options are available.

        Returns
        -------
        QHBoxLayout
            A horizontal layout containing the 'Select All' and 'Deselect All' buttons.
        """
        selection_layout = QHBoxLayout()
        select_all_button = QPushButton('Select All')
        select_all_button.clicked.connect(self.select_all)
        deselect_all_button = QPushButton('Deselect All')
        deselect_all_button.clicked.connect(self.deselect_all)

        selection_layout.addWidget(select_all_button)
        selection_layout.addWidget(deselect_all_button)

        return selection_layout

    def _create_action_buttons(self):
        """
        Creates a layout with 'OK' and 'Cancel' buttons. The 'OK' button confirms the selection, while the 'Cancel'
        button closes the dialog without saving changes.

        Returns
        -------
        QHBoxLayout
            A horizontal layout containing the 'OK' and 'Cancel' buttons.
        """
        action_layout = QHBoxLayout()
        ok_button = QPushButton('OK')
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton('Cancel')
        cancel_button.clicked.connect(self.reject)

        action_layout.addWidget(ok_button)
        action_layout.addWidget(cancel_button)

        return action_layout

    def select_all(self):
        """Selects all checkboxes within the dialog."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(True)

    def deselect_all(self):
        """Deselects all checkboxes within the dialog."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(False)

    def get_selected_methods(self):
        """
        Retrieves the list of analysis methods that have been selected by the user. This method checks the state of
        each checkbox to compile a list of chosen methods.

        Returns
        -------
        list of str
            A list containing the labels of the selected checkboxes, representing the user's chosen analysis methods.
        """
        return [checkbox.text() for checkbox in self.checkboxes if checkbox.isChecked()]


def pixel_wise_correlation_analysis(image1, image2, roi_mask, method_selections, label_flag, viewer):
    """
    Performs selected pixel-wise correlation analyses on two images within an optional region of interest (ROI).
    This function can modify the Napari viewer by adding new layers or display various histograms or plots based
    on the methods selected. It leverages a method-function mapping to accommodate a wide range of correlation
    analysis techniques.

    Parameters
    ----------
    image1 : numpy.ndarray
        The first input image array; should have the same dimensions as image2.
    image2 : numpy.ndarray
        The second input image array; should have the same dimensions as image1.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same shape as the input images that specifies the region of interest.
        If None, the analysis is performed on the entire image.
    method_selections : list of str
        A list containing the names of correlation analysis methods to be performed.
    label_flag : bool
        Indicates whether to return the results as pandas DataFrames (True) or to modify the viewer with new
        layers or display histograms/plots based on the selected methods (False).
    viewer : napari.Viewer
        The Napari Viewer instance where analysis results may be displayed as new layers.

    Returns
    -------
    table1_data : pandas.DataFrame or None
        The results of the pixel-wise correlation analysis, including the correlation coefficients and p-values.    
    table2_data : pandas.DataFrame or None
        The results of the Costes thresholding analysis, including the Costes coefficients and p-values.
    correlation_matrix_table : pandas.DataFrame or None
        The correlation matrix analysis results, including the peak value, location, and mean squared error.

    Notes
    -----
    The function employs a dictionary to map selected methods to their respective analysis functions, facilitating
    flexible and dynamic analysis based on user selection. Special processing cases like Costes thresholding and
    correlation matrix analysis are handled separately within the function. The results of the analysis can be
    visualized directly in the Napari viewer or returned as structured data for further processing.

    Depending on the value of `label_flag`, this function returns:
        - If `label_flag` is True: One or more pandas DataFrames containing the analysis results.
        - If `label_flag` is False: None, but the function modifies the Napari viewer or displays histograms/plots.
    """
    
    selected_methods = method_selections.copy()

    # Dictionary to map methods to their respective functions
    method_functions = {
        "Pearson's R value": pearsons_correlation,
        "Spearman's R value": spearman_r_calculation,
        "Kendall's Tau value": kendall_tau_calculation,
        "Weighted Tau value": weighted_tau_calculation,
        "Li's ICQ value": li_intensity_correlation,
        "Mander's Overlap Coefficient": manders_overlap,
        "Mander's k1 value": manders_k1_calculation,
        "Mander's k2 value": manders_k2_calculation,
        "Calculate Costes Significance": perform_costes_test,
        "Costes Automatic Thresholded M1 & M2": costes_thresholding,
        "Perform Modified Costes Thresholding": costes_thresholding,
        "Calculate Correlation Matrix and metrics": cross_correlation_matrix, 
        "Li's ICA Histogram": li_ica_histogram,
        "Cytofluorogram": None
    }

    # Initialize placeholders for results tables
    table1_data, table2_data, correlation_matrix_table = None, None, None

    # Handle special cases and method selections.
    # This includes checking for and removing special analysis options from the selected methods list.
    # For each special case, perform the necessary analysis and update the result tables accordingly.

    # Check for Modified Costes Methods 
    perform_costes_thresh = 'Perform Modified Costes Thresholding' in selected_methods
    if perform_costes_thresh:
        selected_methods.remove('Perform Modified Costes Thresholding')

    # Check for Costes Automatic Thresholded M1 & M2 option
    costes_m1_m2_selected = "Costes Automatic Thresholded M1 & M2" in selected_methods
    if costes_m1_m2_selected:
        selected_methods.remove('Costes Automatic Thresholded M1 & M2')

    # Check for Correlation Matrix Analysis option
    correlation_matrix_selected = "Calculate Correlation Matrix and metrics" in selected_methods
    if correlation_matrix_selected:
        selected_methods.remove('Calculate Correlation Matrix and metrics')

    # Check for Li's ICA Histogram option
    ica_histogram_selected = "Li's ICA Histogram" in selected_methods
    if ica_histogram_selected:
        selected_methods.remove("Li's ICA Histogram")
    
    # Check for Cytofluorogram option
    cytofluorogram_selected = "Cytofluorogram" in selected_methods
    if cytofluorogram_selected:
        selected_methods.remove("Cytofluorogram")

    # Process standard correlation analysis methods.
    table1_data = process_pwcca_methods(selected_methods, method_functions, image1, image2, roi_mask)

    # Additional processing for selected special cases (e.g., Costes thresholding, correlation matrix analysis).
    if costes_m1_m2_selected:
        # Perform Costes Thresholding
        thresh1, thresh2 = method_functions['Costes Automatic Thresholded M1 & M2'](image1, image2, roi_mask)
        costes_m1 = np.round(np.sum(image1[image1 > thresh1])/np.sum(image1), 4)
        costes_m2 = np.round(np.sum(image2[image2 > thresh2])/np.sum(image2), 4)
        # Append results to the table
        row_m1 = {'Method': 'Costes Automatic Thresholded M1', 'Coefficient': costes_m1, 'P-Value': np.nan}
        row_m2 = {'Method': 'Costes Automatic Thresholded M2', 'Coefficient': costes_m2, 'P-Value': np.nan}
        if 'Calculate Costes Significance' in selected_methods:
                row_m1['Costes P-Value'] = np.nan
                row_m2['Costes P-Value'] = np.nan
        row_df1 = pd.DataFrame(row_m1, index=[0])
        table1_data = pd.concat([table1_data, row_df1], ignore_index=True)
        row_df2 = pd.DataFrame(row_m2, index=[0])
        table1_data = pd.concat([table1_data, row_df2], ignore_index=True)


    # If Costes Thresholding is selected, re-run with thresholded images
    if perform_costes_thresh:
        thresh1, thresh2 = method_functions['Perform Modified Costes Thresholding'](image1, image2, roi_mask)
        if roi_mask is not None:
            thresh_mask = (image1 > thresh1) & (roi_mask > 0)
        else:
            thresh_mask = (image1 > thresh1)
        # Apply the Costes threshold mask to the images
        thresholded_image1 = image1 * thresh_mask
        thresholded_image2 = image2 * thresh_mask
        if np.sum(thresh_mask) == 0:
            napari_show_error("No pixels above the threshold. Modified Costes thresholding unavailable. There is likely no positive correlation.")
        else:
            # Add these thresholded images to the viewer as new layers
            viewer.add_image(thresholded_image1, name='Thresholded Image 1', colormap='red', blending='additive')
            viewer.add_image(thresholded_image2, name='Thresholded Image 2', colormap='green', blending='additive')
            table2_data = process_pwcca_methods(selected_methods, method_functions, thresholded_image1, thresholded_image2, thresh_mask)
            if costes_m1_m2_selected:
                table2_data = pd.concat([table2_data, row_df1], ignore_index=True)
                table2_data = pd.concat([table2_data, row_df2], ignore_index=True)


    # If Correlation Matrix Analysis is selected, calculate the correlation matrix and metrics
    if correlation_matrix_selected:
        correlation_matrix, max_val, max_val_loc, mean_sq_er = method_functions['Calculate Correlation Matrix and metrics'](image1, image2, roi_mask)
        # Create a dictionary with 'Metric' and 'Value' for each metric
        correlation_matrix_dict = {
            'Metric': [
                'Maximum CM Value',
                'Max CM Location',
                'Mean Squared Error Between Images'
            ],
            'Value': [
                [max_val],
                [max_val_loc],
                [mean_sq_er]
            ]
        }
        # Create the DataFrame with consistent format
        correlation_matrix_table = pd.DataFrame(correlation_matrix_dict)

    # Display or return results based on `label_flag` and selected options for histograms and cytofluorograms.
    if label_flag:
        return table1_data, table2_data, correlation_matrix_table
    # Plots Li's ICA histogram
    if ica_histogram_selected:
        ica_product, li_img1, li_img2 = li_ica_histogram(image1, image2, roi_mask)
        li_ica_plot(ica_product, li_img1, li_img2)
    # Plots Cytofluorogram (intensity of image1 vs intensity of image2)
    if cytofluorogram_selected:
        if roi_mask is not None:
            cyto_img1 = image1[roi_mask > 0]
            cyto_img2 = image2[roi_mask > 0]
        else:
            cyto_img1 = image1.flatten()
            cyto_img2 = image2.flatten()
        cytofluorogram_plot(cyto_img1, cyto_img2)


    return table1_data, table2_data, correlation_matrix_table


def process_pwcca_methods(selected_methods, method_functions, image1, image2, roi_mask):
    """
    Processes selected correlation analysis methods for two images within an optional region of interest (ROI),
    compiling the results into a pandas DataFrame. This function iterates over a list of selected methods, 
    applies the corresponding analysis function for each, and aggregates the results.

    This setup allows for flexible addition or modification of analysis methods without altering the core 
    function structure. Each method's function is expected to return a correlation coefficient and a p-value,
    which are then recorded in a structured format.

    Parameters
    ----------
    selected_methods : list of str
        The names of the correlation analysis methods to be processed. This list may be modified to handle special
        cases such as Costes significance testing.
    method_functions : dict
        A dictionary mapping method names to their respective function implementations. Each function should
        return a tuple (coefficient, p-value).
    image1 : numpy.ndarray
        The first input image array. Must have the same dimensions as `image2`.
    image2 : numpy.ndarray
        The second input image array. Must have the same dimensions as `image1`.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same shape as the input images specifying the region of interest.
        If None, the analysis is performed on the entire image.

    Returns
    -------
    data_table1 : pandas.DataFrame
        A DataFrame containing the results of the correlation analysis. Each row represents a method and includes
        the method name, the calculated coefficient, and the corresponding p-value. Additional columns, such as
        'Costes P-Value', are included as necessary based on selected special processing cases.

    Notes
    -----
    Special handling for 'Calculate Costes Significance' is implemented by checking its presence in the method list,
    performing the significance testing if applicable, and then adding the results to the output DataFrame. This method
    adapts to dynamic changes in the analysis methods list, facilitating easy updates or modifications to the analytical
    techniques employed.
    """
    
    selected_methods_copy = selected_methods.copy()

    # Define the structure of the results DataFrame.
    data_table1 = pd.DataFrame(columns=['Method', 'Coefficient', 'P-Value'])

    # Check for Costes Significance option
    perform_costes_sig = "Calculate Costes Significance" in selected_methods_copy
    if perform_costes_sig:
        data_table1['Costes P-Value'] = np.nan
        selected_methods_copy.remove('Calculate Costes Significance')

    # Process each selected method
    for method in selected_methods_copy:
        if method in method_functions:

            coeff, p_val = method_functions[method](image1, image2, roi_mask)

            # Append results to the table
            row = {'Method': method, 'Coefficient': coeff, 'P-Value': p_val}
            if perform_costes_sig:
                # Costes Significance Test is not applicable for Mander's Overlap Coefficient
                if method == "Manders Overlap Coefficient":
                    row['Costes P-Value'] = np.nan
                else:
                    row['Costes P-Value'], _ = method_functions['Calculate Costes Significance'](image1, image2, method_functions[method], roi_mask)

            # Create a DataFrame row for the current method and add it to the data table
            row_df = pd.DataFrame(row, index=[0])
            data_table1 = pd.concat([data_table1, row_df], ignore_index=True)
    
    
    return data_table1


def run_pwcca(image_layer1, image_layer2, roi_mask_layer, data_instance, viewer):
    """
    Executes Pixel-Wise Correlation Coefficient Analysis (PWCCA) on two image layers with an optional
    region of interest (ROI) mask. The function facilitates user interaction to select specific analysis methods,
    performs the analysis, and displays the results. It also stores these results for future use in a provided data
    instance object.

    Parameters
    ----------
    image_layer1 : napari.layers.Image
        The first image layer for analysis, providing the primary dataset.
    image_layer2 : napari.layers.Image
        The second image layer for analysis, which must be of the same dimensions as the first.
    roi_mask_layer : napari.layers.Labels, optional
        An optional layer providing a mask to define the ROI for the analysis. The analysis will be restricted
        to this region if provided.
    viewer : napari.Viewer
        The Napari viewer instance used for visualizing any results directly in the GUI.
    data_instance : object
        An object designed to store the results of the analysis. This object must have a 'data_repository'
        attribute (a dictionary) where results are stored.

    Raises
    ------
    ValueError
        If the input images do not have the same dimensions, or if the ROI mask is provided and does not have
        the same dimensions as the images.

    Notes
    -----
    This function initiates a dialog for user input to select desired correlation methods, then proceeds to
    perform the analysis based on the user selections. Results can include various statistical measures and
    visual data representations, which are displayed in the viewer and stored in the data_instance object.
    The analysis can handle both simple binary masks and more complex labeled masks, facilitating detailed
    region-specific analyses.
    """
    # Extract the image data from the layers.
    image1 = image_layer1.data
    image2 = image_layer2.data
    roi_mask = roi_mask_layer.data if roi_mask_layer is not None else None

    # Ensure input images and the ROI mask have the same shape.
    if image1.shape != image2.shape:
        raise ValueError("Input images must have the same shape.")

    if roi_mask is not None and roi_mask.shape != image1.shape:
        raise ValueError("ROI mask must have the same shape as the input images.")

    # Create and execute the PWCCA dialog to get user method selections.
    dialog = pwcaDialog()
    result = dialog.exec_()

    if result == QDialog.Accepted:
        method_selections = dialog.get_selected_methods()
    elif result == QDialog.Rejected:
        return  # Exit if the dialog is rejected.

    # Initialize DataFrames to store concatenated results for different metrics.
    concatenated_table1_data = pd.DataFrame()
    concatenated_table2_data = pd.DataFrame()
    concatenated_correlation_matrix_table = pd.DataFrame()

    label_flag = False  # Flag to track if working with labeled regions.

    # Determine if the roi_mask is binary or labeled and proceed accordingly.
    if roi_mask is not None and np.unique(roi_mask).size > 2:  # Labeled mask case.
        unique_labels = np.unique(roi_mask)[1:]  # Exclude 0 (background) to get unique labels.

        # Analyze each labeled region separately.
        for label in unique_labels:
            specific_roi_mask = (roi_mask == label).astype(bool)
            if label > 1:
                label_flag = True

            # Perform the pixel-wise correlation analysis for the current label.
            table1_data, table2_data, correlation_matrix_table = pixel_wise_correlation_analysis(image1, image2, specific_roi_mask, method_selections, label_flag, viewer)

            # Handle concatenation of results depending on whether label_flag is set.
            if label_flag:
                # Merge results by method, adding label suffixes to distinguish different labels.
                concatenated_table1_data = pd.merge(concatenated_table1_data, table1_data, on='Method', how='outer', suffixes=('', f'_{label}')) if table1_data is not None else concatenated_table1_data
                concatenated_table2_data = pd.merge(concatenated_table2_data, table2_data, on='Method', how='outer', suffixes=('', f'_{label}')) if table2_data is not None else concatenated_table2_data
                concatenated_correlation_matrix_table = pd.merge(concatenated_correlation_matrix_table, correlation_matrix_table, on='Metric', how='outer', suffixes=('', f'_{label}')) if correlation_matrix_table is not None else concatenated_correlation_matrix_table
            else:
                # For the first label or binary masks, concatenate results directly.
                concatenated_table1_data = pd.concat([concatenated_table1_data, table1_data], ignore_index=True)
                concatenated_table2_data = pd.concat([concatenated_table2_data, table2_data], ignore_index=True)
                concatenated_correlation_matrix_table = pd.concat([concatenated_correlation_matrix_table, correlation_matrix_table], ignore_index=True)
    else:
        # For binary masks or no mask, convert the mask to boolean if it exists, and run analysis as before.
        roi_mask = roi_mask.astype(bool) if roi_mask is not None else None
        table1_data, table2_data, correlation_matrix_table = pixel_wise_correlation_analysis(image1, image2, roi_mask, method_selections, label_flag, viewer)
        # Concatenate the results.
        concatenated_table1_data = pd.concat([concatenated_table1_data, table1_data], ignore_index=True)
        concatenated_table2_data = pd.concat([concatenated_table2_data, table2_data], ignore_index=True)
        concatenated_correlation_matrix_table = pd.concat([concatenated_correlation_matrix_table, correlation_matrix_table], ignore_index=True)

    # Finalize the data tables by setting indices and rounding.
    if not concatenated_table1_data.empty:
        concatenated_table1_data.set_index('Method', inplace=True)
        concatenated_table1_data = concatenated_table1_data.round(4)
    else:
        concatenated_table1_data = None
    if not concatenated_table2_data.empty:
        concatenated_table2_data.set_index('Method', inplace=True)
        concatenated_table2_data = concatenated_table2_data.round(4)
    else:
        concatenated_table2_data = None
    if not concatenated_correlation_matrix_table.empty:
        concatenated_correlation_matrix_table.set_index('Metric', inplace=True)
        concatenated_correlation_matrix_table = concatenated_correlation_matrix_table.round(4)
    else:
        concatenated_correlation_matrix_table = None

    # Package the results for display.
    tables_info = [
        ("Correlation Coefficient Table", concatenated_table1_data),
        ("Costes-Thresholded Correlation Coefficient Table", concatenated_table2_data),
        ("Correlation Matrix Metrics", concatenated_correlation_matrix_table)
    ]

    # Display the analysis results to the user.
    window_title = "Pixel-Wise Correlation Coefficient Analysis"
    show_dataframes_dialog(window_title, tables_info)

    # Store the analysis results in the data instance for future access.
    data_instance.data_repository["PWCCA_coefficient_df"] = concatenated_table1_data
    data_instance.data_repository["PWCCA_costes_thresh_coefficient_df"] = concatenated_table2_data
    data_instance.data_repository["PWCCA_correlation_matrix_df"] = concatenated_correlation_matrix_table
