"""
Correlation Function Analysis Module for PyCAT

This module provides functions for computing and analyzing cross-correlation functions (CCF) 
between two arrays or images. The CCF is a measure of similarity between two signals or images
as a function of a shift applied to one of them. The module includes functions for computing 1D and 2D
CCFs, fitting Gaussian models to the CCFs, and visualizing the results. It also provides utilities
for preparing the results in a structured format for further analysis and reporting.

This module alos provides functions for computing the auto-correlation function (ACF) of a single image
and fitting Gaussian models to the ACF. The ACF is a measure of the similarity of an image with itself
as a function of a shift applied to the image. The ACF can be used to estimate the size of features in
the image. 

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np
import scipy.ndimage as ndi
import scipy.stats as stats
from scipy.optimize import curve_fit
import matplotlib.pyplot as plt
import pandas as pd
import skimage as sk
from napari.utils.notifications import show_warning as napari_show_warning

# Local application imports
from pycat.ui.ui_utils import show_dataframes_dialog
from pycat.utils.general_utils import crop_bounding_box



# Cross-correlation function (CCF) analysis tools

def smooth_array(array, window_size=3):
    """
    Applies a moving average filter to smooth the input array. This is commonly used to reduce noise in a signal.

    Parameters
    ----------
    array : numpy.ndarray
        The input array to be smoothed. Can be any one-dimensional array of numerical values.
    window_size : int, optional
        The size of the moving average window, specifying how many elements are averaged
        to compute the smoothed value. Defaults to 3.

    Returns
    -------
    numpy.ndarray
        The smoothed array, which will have the same shape as the input array. Edges of the
        array will be less affected by the averaging due to boundary effects in the convolution.

    Example
    -------
    >>> smooth_array(np.array([1, 2, 3, 4, 5]), window_size=3)
    array([1.33333333, 2.        , 3.        , 4.        , 3.66666667])
    """
    window = np.ones(window_size) / window_size # Create a uniform window for averaging
    return np.convolve(array, window, mode='same')  # Apply convolution to smooth the array


def gaussian_1d(x, amplitude, mean, stddev):
    """
    Computes a one-dimensional Gaussian function, often used to model the distribution of data.

    Parameters
    ----------
    x : numpy.ndarray
        The input values over which the Gaussian function is computed.
    amplitude : float
        The peak amplitude of the Gaussian function.
    mean : float
        The mean (center) of the Gaussian function, where the peak occurs.
    stddev : float
        The standard deviation of the Gaussian, determining the width of the bell curve.

    Returns
    -------
    numpy.ndarray
        The values of the Gaussian function evaluated at 'x'.

    Example
    -------
    >>> gaussian_1d(np.array([1, 2, 3]), amplitude=1, mean=2, stddev=1)
    array([0.60653066, 1.        , 0.60653066])
    """
    # Precompute constant part of the expression
    exp_component = -1 / (2 * stddev ** 2)
    return amplitude * np.exp(exp_component * (x - mean) ** 2)


def gaussian_2d(xy, amplitude, x0, y0, sigma_x, sigma_y):
    """
    Computes a two-dimensional Gaussian function, often used in image processing to generate Gaussian blurs.

    Parameters
    ----------
    xy : tuple of numpy.ndarray
        A pair of arrays (x, y) representing the meshgrid coordinates over which the Gaussian function is computed.
    amplitude : float
        The peak amplitude of the Gaussian function.
    x0, y0 : float
        The center of the Gaussian function in x and y dimensions.
    sigma_x, sigma_y : float
        The standard deviations (widths) of the Gaussian function in x and y dimensions.

    Returns
    -------
    numpy.ndarray
        The values of the Gaussian function evaluated over the 2D space defined by 'xy'.

    Example
    -------
    >>> x = np.linspace(-1, 1, 3)
    >>> y = np.linspace(-1, 1, 3)
    >>> xx, yy = np.meshgrid(x, y)
    >>> gaussian_2d((xx, yy), amplitude=1, x0=0, y0=0, sigma_x=1, sigma_y=1)
    array([[0.36787944, 0.60653066, 0.36787944],
           [0.60653066, 1.        , 0.60653066],
           [0.36787944, 0.60653066, 0.36787944]])
    """
    x, y = xy
    exp_component_x = -1 / (2 * sigma_x ** 2)
    exp_component_y = -1 / (2 * sigma_y ** 2)
    return amplitude * np.exp(exp_component_x * (x - x0) ** 2 + exp_component_y * (y - y0) ** 2)


def _compute_1d_correlation(array1, array2, max_shift, roi_mask):
    """
    Computes the 1D cross-correlation function (CCF) between two arrays, with an option to focus the computation
    within a specified region of interest (ROI). This function shifts one array over the other within a defined range
    and calculates the Pearson correlation coefficient for each shift.

    Parameters
    ----------
    array1 : numpy.ndarray
        The reference array for which the correlation is computed.
    array2 : numpy.ndarray
        The array that is shifted and correlated against array1.
    max_shift : int
        The maximum number of elements by which array2 is shifted relative to array1 for correlation computation.
    roi_mask : numpy.ndarray, optional
        A binary mask specifying the Region Of Interest (ROI) within which the correlation is computed.
        Must be of the same size as array1 and array2.

    Returns
    -------
    ccf_values : numpy.ndarray
        An array containing the correlation coefficients corresponding to each shift.
    shifts : numpy.ndarray
        An array of integer shift values ranging from -max_shift to max_shift.

    Notes
    -----
    The correlation at each shift is calculated using the Pearson correlation coefficient formula. If an ROI mask is
    provided, only the data points where the mask is True are considered in the computation.
    """
    ccf_values = np.zeros(2 * max_shift + 1)  # Initialize array to store CCF values
    shifts = np.arange(-max_shift, max_shift + 1)  # Array of shift values
    for i, shift_val in enumerate(shifts):
        shifted_array = ndi.shift(array2, shift_val, mode='nearest')  # Shift array2
        # Apply ROI mask if provided
        if roi_mask is not None:
            masked_array1 = array1[roi_mask]
            masked_shifted_array = shifted_array[roi_mask]
        else:
            masked_array1 = array1.flatten()  # Use entire array if no mask
            masked_shifted_array = shifted_array.flatten()

        # Calculate correlation for the current shift, considering the mask
        if masked_array1.size > 0 and masked_shifted_array.size > 0:
            ccf = stats.pearsonr(masked_array1, masked_shifted_array)[0]
            ccf_values[i] = ccf
        else:
            ccf_values[i] = 0 # or np.nan  # Handle cases with no overlap

    return ccf_values, shifts


def fit_gaussian_1d(ccf_values, shifts):
    """
    Fits a Gaussian function to 1D cross-correlation function values to determine the peak and width of the correlation.

    Parameters
    ----------
    ccf_values : numpy.ndarray
        The cross-correlation function values obtained from shifting one array over another.
    shifts : numpy.ndarray
        The array of shift values corresponding to each CCF value.

    Returns
    -------
    results : dict or None
        A dictionary containing the results of the Gaussian fitting (parameters and diagnostics) or None if the fitting fails.

    Notes
    -----
    The Gaussian fitting process assumes a bell-shaped curve typical for cross-correlation functions in signal processing.
    This function uses the `curve_fit` method from `scipy.optimize` to perform the fitting. The initial guesses for the
    Gaussian parameters are determined based on the properties of the CCF values.
    """
    shifts_bottom_quarter = len(shifts)//4
    shifts_top_quarter = 3*len(shifts)//4
    smoothed_ccf = smooth_array(ccf_values)  # Smooth CCF values for better fitting
    max_ccf = max(smoothed_ccf[shifts_bottom_quarter:shifts_top_quarter], key=abs)  # Find maximum CCF value for initial guess

    # Determine initial guess parameters for Gaussian fitting
    if max_ccf > 0:
        p0 = [1, 0, 1]  # Assume positive peak
    else:
        p0 = [-1, 0, 1]  # Assume negative peak for inverted Gaussian

    try:
        # Perform Gaussian fitting
        popt, pcov = curve_fit(gaussian_1d, shifts, ccf_values, p0=p0)
        results = _extract_fit_results_1d(ccf_values, shifts, popt, pcov)
    except RuntimeError:
        results = None  # Fitting failed

    return results


def _extract_fit_results_1d(ccf_values, shifts, popt, pcov):
    """
    Extracts and organizes the results from the 1D Gaussian fitting process into a structured dictionary. 
    This function provides a comprehensive summary of the Gaussian fit to the cross-correlation function (CCF) values,
    including fit parameters and statistical measures of the fit quality.

    Parameters
    ----------
    ccf_values : numpy.ndarray
        The cross-correlation function values, which represent the correlation between two arrays at varying shifts.
    shifts : numpy.ndarray
        The array of shift values corresponding to each CCF value. These are the values at which the correlation was calculated.
    popt : numpy.ndarray
        The optimal values for the parameters of the Gaussian function, typically obtained from a curve fitting procedure.
        Expected to contain [amplitude, mean (peak position), standard deviation (width of the peak)].
    pcov : numpy.ndarray
        The covariance matrix associated with the parameter estimates from the curve fit. This matrix provides a measure of
        the parameter uncertainty, which can be used to calculate the standard error of the fit parameters.

    Returns
    -------
     results : dict
        A dictionary containing detailed results of the Gaussian fitting, which includes:
        - 'ccf_values': The original cross-correlation function values used for the fitting.
        - 'shifts': The shift values associated with the CCF values.
        - 'gaussian_params': The optimized parameters of the Gaussian fit.
        - 'gaussian_peak': The peak position of the Gaussian fit on the shift axis.
        - 'amplitude': The maximum amplitude observed in the CCF values.
        - 'peak_location': The shift position at which the maximum CCF value occurs.
        - 'ccf_sigma': The standard deviation of the CCF values, providing a measure of variability.
        - 'goodness_of_fit': The standard errors of the estimated Gaussian parameters, derived from the diagonal of the covariance matrix.
        - 'max_smoothed_ccf': The maximum value of the CCF after applying a smoothing filter.

    Notes
    -----
    The function assumes that the input CCF values have already been smoothed if necessary and that the Gaussian fitting
    has been successfully completed with parameters conducive to generating meaningful results. The returned dictionary
    is useful for analyzing the quality and characteristics of the fit, as well as for reporting and visualization purposes.
    """
    ccf_sigma = np.std(ccf_values)  # Standard deviation of CCF values

    # Compile results into a dictionary
    results = {
        'ccf_values': ccf_values,
        'shifts': shifts,
        'gaussian_params': popt,
        'gaussian_peak': popt[1],  # Peak position of the Gaussian
        'amplitude': max(ccf_values.ravel(), key=abs),  # Max amplitude of the CCF values
        'peak_location': shifts[np.argmax(np.abs(ccf_values))],  # Location of peak CCF value
        'ccf_sigma': ccf_sigma,  # Standard deviation of the CCF
        'goodness_of_fit': np.sqrt(np.diag(pcov)),  # Standard error of the estimated parameters
        'max_smoothed_ccf': max(smooth_array(ccf_values), key=abs)  # Max value after smoothing
    }
    return results


def _compute_2d_correlation(array1, array2, max_shift, roi_mask):
    """
    Computes the 2D cross-correlation function between two arrays, potentially restricted to a specified region
    of interest (ROI). This function measures how similar array2 is to array1 when array2 is shifted by various amounts
    in the x and y directions.

    Parameters
    ----------
    array1 : numpy.ndarray
        The first input array for which the cross-correlation is computed.
    array2 : numpy.ndarray
        The second input array that is shifted and correlated against array1.
    max_shift : int
        The maximum number of elements by which array2 is shifted in both x and y directions.
    roi_mask : numpy.ndarray, optional
        A binary mask specifying the ROI within the arrays. Only the areas where roi_mask is True will be considered.

    Returns
    -------
    ccf_values : numpy.ndarray
        A 2D array containing the cross-correlation function values.
    x, y : numpy.ndarray, numpy.ndarray
        Meshgrid arrays representing the shifts in the x and y directions.

    Notes
    -----
    The cross-correlation function is computed by shifting array2 across array1 within the range defined
    by [-max_shift, max_shift] in both dimensions. If an ROI mask is provided, the correlation is only computed
    within the specified region.
    """
    ccf_values = np.zeros((2 * max_shift + 1, 2 * max_shift + 1))
    x, y = np.meshgrid(np.arange(-max_shift, max_shift + 1), np.arange(-max_shift, max_shift + 1))
    
    for i in range(-max_shift, max_shift + 1):
        for j in range(-max_shift, max_shift + 1):
            shifted_array = ndi.shift(array2, [i, j], mode='nearest')
            # Conditionally apply the ROI mask
            if roi_mask is not None:
                # Function crop_bounding_box should crop the array to the bounding box defined by the mask
                masked_array1, cropped_mask, _ = crop_bounding_box(array1, roi_mask)
                masked_shifted_array, _, _ = crop_bounding_box(shifted_array, roi_mask)
            else:
                masked_array1 = array1.copy()
                masked_shifted_array = shifted_array.copy()
                cropped_mask = None

            # Calculate correlation for masked regions
            if masked_array1.size > 0 and masked_shifted_array.size > 0:
                ccf, _ = sk.measure.pearson_corr_coeff(masked_array1, masked_shifted_array, cropped_mask)
                ccf_values[i + max_shift, j + max_shift] = ccf
            else:
                ccf_values[i + max_shift, j + max_shift] = 0 # or np.nan  # Handle cases with no overlap

    return ccf_values, x, y


def fit_gaussian_2d(ccf_values, x, y):
    """
    Fits a 2D Gaussian function to the computed cross-correlation function values to determine the peak correlation
    and its distribution in two dimensions.

    Parameters
    ----------
    ccf_values : numpy.ndarray
        The 2D cross-correlation function values obtained from comparing two arrays.
    x : numpy.ndarray
        The x-coordinates of the shifts applied during the cross-correlation calculation.
    y : numpy.ndarray
        The y-coordinates of the shifts applied during the cross-correlation calculation.

    Returns
    -------
    results : dict or None
        A dictionary containing the results of the Gaussian fitting, including parameters and diagnostics,
        or None if the fitting fails.

    Notes
    -----
    The fitting process assumes a symmetric Gaussian distribution, typical for peak correlation distributions.
    This method uses curve fitting techniques from scipy.optimize to find the best fit parameters.
    """
    shifts_bottom_quarter = len(x)//4
    shifts_top_quarter = 3*len(x)//4
    ccf_data = ccf_values.ravel()
    smoothed_ccf = smooth_array(ccf_data).reshape(ccf_values.shape)
    region_of_interest = smoothed_ccf[shifts_bottom_quarter:shifts_top_quarter, shifts_bottom_quarter:shifts_top_quarter]
    index_flat = np.argmax(np.abs(region_of_interest))
    index_2d = np.unravel_index(index_flat, region_of_interest.shape)
    max_smoothed_ccf = region_of_interest[index_2d]

    # Initial guess for the Gaussian parameters
    if max_smoothed_ccf > 0:
        p0 = [1, 0, 0, 1, 1]  # Parameters for a regular Gaussian
    else:
        p0 = [-1, 0, 0, 1, 1]  # Parameters for an inverted Gaussian

    try:
        popt, pcov = curve_fit(gaussian_2d, (x.ravel(), y.ravel()), ccf_data, p0=p0)
        results = _extract_fit_results_2d(ccf_values, x, y, popt, pcov, max_smoothed_ccf)
    except RuntimeError:
        results = None

    return results


def _extract_fit_results_2d(ccf_values, x, y, popt, pcov, max_smoothed_ccf):
    """
    Extracts and organizes the results from the 2D Gaussian fitting of the cross-correlation function values.
    This function compiles key information including the fitted parameters, the peak location, the standard
    deviation of the cross-correlation values, and goodness of fit measures.

    Parameters
    ----------
    ccf_values : numpy.ndarray
        The 2D array of cross-correlation function values derived from comparing two datasets.
    x : numpy.ndarray
        The x-coordinates of the meshgrid over which the correlation was computed, representing shifts in the x direction.
    y : numpy.ndarray
        The y-coordinates of the meshgrid over which the correlation was computed, representing shifts in the y direction.
    popt : numpy.ndarray
        The optimal values for the parameters of the Gaussian function determined by the fitting process. Typically,
        this will include the amplitude, x and y position of the center, and the standard deviations in x and y.
    pcov : numpy.ndarray
        The covariance matrix associated with the fitted parameters, providing a statistical measure of the fitting accuracy.
    max_smoothed_ccf : float
        The maximum value obtained from a smoothed version of the cross-correlation function, indicating the peak correlation
        observed after reducing noise.

    Returns
    -------
    results : dict
        A dictionary containing comprehensive details of the Gaussian fitting results, including:
        - 'ccf_values': The original matrix of cross-correlation function values.
        - 'shifts': A tuple of arrays (x, y) representing the shift values in the x and y directions.
        - 'gaussian_params': The fitted parameters of the Gaussian model.
        - 'gaussian_peak': The coordinates (x, y) of the peak of the Gaussian function.
        - 'amplitude': The maximum amplitude of the cross-correlation function.
        - 'peak_location': The coordinates (x, y) of the highest peak in the cross-correlation matrix.
        - 'ccf_sigma': A tuple containing the standard deviations of the cross-correlation function along the peak row and column.
        - 'goodness_of_fit': An array of standard errors for the fitted Gaussian parameters, derived from the covariance matrix.
        - 'max_smoothed_ccf': The maximum value of the smoothed cross-correlation function, used for assessing peak correlation quality.

    Notes
    -----
    The function is intended to provide a detailed summary of the Gaussian fitting to aid in further analysis or reporting.
    It assumes the input parameters are correctly computed and formatted. Errors in input data or fitting parameters can
    significantly affect the reliability of the results.
    """

    peak_x, peak_y = x.ravel()[np.argmax(np.abs(ccf_values))], y.ravel()[np.argmax(np.abs(ccf_values))]
    # Find the index of the peak in the flattened array
    peak_index = np.argmax(np.abs(ccf_values))

    # Convert the flattened index to 2D indices
    peak_row, peak_col = np.unravel_index(peak_index, ccf_values.shape)

    # Calculate sigma_x and sigma_y from the ccf values
    ccf_sigma_x = np.std(ccf_values[peak_row, :])
    ccf_sigma_y = np.std(ccf_values[:, peak_col])

    results = {
        'ccf_values': ccf_values,
        'shifts': (x, y),
        'gaussian_params': popt,
        'gaussian_peak': (popt[1], popt[2]), # Peak position of the Gaussian
        'amplitude': max(ccf_values.ravel(), key=abs),  # Max amplitude of the CCF values
        'peak_location': (peak_x, peak_y), # Location of peak CCF value
        'ccf_sigma': (ccf_sigma_x, ccf_sigma_y), # Standard deviation of the central slice of the CCF
        'goodness_of_fit': np.sqrt(np.diag(pcov)), # Standard error of the estimated parameters
        'max_smoothed_ccf': max_smoothed_ccf # Max value after smoothing
    }
    return results


def process_ccf(array1, array2, roi_mask):
    """
    Performs cross-correlation analysis on 1D or 2D arrays with optional region of interest (ROI) masking.
    This function calculates the cross-correlation function (CCF) and fits a Gaussian model to it.
    
    Parameters
    ----------
    array1 : numpy.array
        The first input array for correlation analysis. Must be of the same dimension as array2.
    array2 : numpy.array
        The second input array for correlation analysis. Must be of the same dimension as array1.
    roi_mask : numpy.array, optional
        An optional mask to specify the region of interest within the arrays. If not provided, the entire
        array is considered.

    Returns
    -------
    results : dict
        A dictionary containing the results of the CCF analysis and Gaussian fitting. This includes CCF values,
        Gaussian parameters, and other relevant metrics depending on whether the analysis was 1D or 2D.

    Raises
    ------
    ValueError
        If the dimensions of the input arrays are neither 1D nor 2D.
    """
    max_shift = 20  # Maximum shift for correlation analysis
    dimension = len(array1.shape)  # Determine the dimensionality of the input arrays
    results = {}

    if dimension == 1:
        # Perform 1D correlation analysis and Gaussian fitting
        ccf_values, shifts = _compute_1d_correlation(array1, array2, max_shift, roi_mask)
        results = fit_gaussian_1d(ccf_values, shifts)
    elif dimension == 2:
        # Perform 2D correlation analysis and Gaussian fitting
        ccf_values, x, y = _compute_2d_correlation(array1, array2, max_shift, roi_mask)
        results = fit_gaussian_2d(ccf_values, x, y)
    else:
        raise ValueError("Array dimensions not supported. Only 1D or 2D arrays are allowed.")

    return results


def plot_1d_results(results):
    """
    Plots the 1D cross-correlation function (CCF) and its Gaussian fit. This visualization includes the
    original CCF values and the Gaussian model overlaid to show the fitting quality.

    Parameters
    ----------
    results : dict
        A dictionary containing the results of the 1D CCF analysis. This must include 'shifts', 'ccf_values',
        'gaussian_params', and 'r_squared' among other possible keys.
    """
    # Create a figure with specified size
    plt.figure(figsize=(10, 6))
    # Plot the original CCF values
    plt.plot(results['shifts'], results['ccf_values'], label='CCF')
    # Plot the fitted Gaussian curve over the CCF values
    plt.plot(results['shifts'], gaussian_1d(results['shifts'], *results['gaussian_params']), label='Fitted Gaussian')
    # Labeling the axes and setting the title with R-squared value
    plt.xlabel('Shift')
    plt.ylabel('CCF Value')
    plt.title(f'1D CCF with Fitted Gaussian')
    # Display legend and show the plot
    plt.legend()
    plt.show()


def plot_2d_results(results):
    """
    Plots the 2D cross-correlation function (CCF) and its Gaussian fit. The function generates two subplots:
    one for the original CCF and another for the fitted Gaussian function, both with colorbars to assist
    in interpretation.

    Parameters
    ----------
    results : dict
        A dictionary containing the results of the 2D CCF analysis. This includes 'shifts', 'ccf_values',
        and 'gaussian_params' among other possible keys.
    """

    # Extract shifts and CCF values from the results
    x, y = results['shifts']
    ccf_values = results['ccf_values']

    # Calculate the fitted Gaussian values to match the shape of CCF values
    fitted_gaussian = gaussian_2d((x, y), *results['gaussian_params']).reshape(ccf_values.shape)

    # Create subplots for both CCF and fitted Gaussian
    fig, ax = plt.subplots(1, 2, figsize=(12, 6))

    # Plot the original 2D CCF with colorbar
    ccf_img = ax[0].imshow(ccf_values, origin='lower', extent=[x.min(), x.max(), y.min(), y.max()])
    ax[0].set_title('2D CCF')
    fig.colorbar(ccf_img, ax=ax[0])

    # Plot the fitted Gaussian function with colorbar
    gaussian_img = ax[1].imshow(fitted_gaussian, origin='lower', extent=[x.min(), x.max(), y.min(), y.max()])
    ax[1].set_title('Fitted Gaussian Function')
    fig.colorbar(gaussian_img, ax=ax[1])

    plt.tight_layout()  # Adjust layout to prevent overlap
    plt.show()


def prepare_1d_result_dfs(results): 
    """
    Prepares pandas DataFrames for both the raw CCF values and the fitted Gaussian parameters from the results 
    of 1D cross-correlation analysis. This facilitates easier analysis and visualization of the CCF fitting results.

    Parameters
    ----------
    results : dict
        A dictionary containing the results of the 1D CCF analysis, including Gaussian fitting parameters and their 
        goodness of fit, among other metrics.

    Returns
    -------
    fitted_params_df : pandas.DataFrame
        DataFrame containing the names and values of the fitted Gaussian parameters along with their covariance.
    raw_params_df : pandas.DataFrame
        DataFrame containing the names and values of raw CCF metrics, including peak amplitude and location.

    Notes
    -----
    Ensure that all expected keys are present in the `results` dictionary to avoid `KeyError`.
    """

    # Create DataFrame for fitted Gaussian parameters
    fitted_params_df = pd.DataFrame({
        'Parameter': ['Amplitude', 'mu', 'sigma'],
        'Value': list(results['gaussian_params']),
        'Covariance': list(results['goodness_of_fit'])
    })

    #  Create Dataframe for raw CCF parameters
    raw_params_df = pd.DataFrame({
        'Parameter': ['Amplitude', 'Smoothed CCF Amplitude', 'mu', 'sigma'],
        'Value': [
            results['amplitude'],
            results['max_smoothed_ccf'],
            results['peak_location'], 
            results['ccf_sigma']
        ]
    })

    return fitted_params_df, raw_params_df


def prepare_2d_result_dfs(results):
    """
    Prepares pandas DataFrames for both the raw CCF values and the fitted Gaussian parameters from the results 
    of 2D cross-correlation analysis. These DataFrames organize the CCF and Gaussian fitting results, making them 
    more accessible for further analysis and reporting.

    Parameters
    ----------
    results : dict
        A dictionary containing the results of the 2D CCF analysis, including Gaussian fitting parameters and their 
        goodness of fit, among other metrics.

    Returns
    -------
    fitted_params_df : pandas.DataFrame
        DataFrame containing the names and values of the fitted Gaussian parameters along with their covariance.
    raw_params_df : pandas.DataFrame
        DataFrame containing the names and values of raw CCF metrics, such as peak amplitude and locations along 
        both axes.

    Notes
    -----
    This function assumes that the `results` dictionary contains all necessary keys and values. Missing keys 
    will result in `KeyError`.
    """

    # Creating DataFrame for fitted Gaussian parameters
    fitted_params_df = pd.DataFrame({
        'Parameter': ['Amplitude', 'mu_x', 'mu_y', 'sigma_x', 'sigma_y'],
        'Value': list(results['gaussian_params']),
        'Covariance': list(results['goodness_of_fit'])
    })

    # Creating DataFrame for raw CCF parameters
    raw_params_df = pd.DataFrame({
        'Parameter': ['Amplitude', 'Smoothed CCF Amplitude', 'mu_x', 'mu_y', 'sigma_x', 'sigma_y'],
        'Value': [
            results['amplitude'],
            results['max_smoothed_ccf'],
            results['peak_location'][0],
            results['peak_location'][1],
            results['ccf_sigma'][0],
            results['ccf_sigma'][1]
        ]
    })

    return fitted_params_df, raw_params_df


def ccf_analysis(image1, image2, roi_mask, label_flag=False):
    """
    Performs cross-correlation function (CCF) analysis on two images and processes the results depending on
    the dimensionality (1D or 2D) of the images. It optionally skips the plotting of results based on the label_flag,
    which is useful for batch processing or when visual output is unnecessary.

    Parameters
    ----------
    image1 : numpy.ndarray
        The first input image for CCF analysis. Must be of the same dimension as image2.
    image2 : numpy.ndarray
        The second input image for CCF analysis. Must be of the same dimension as image1.
    roi_mask : numpy.ndarray
        A binary or labeled array indicating the region of interest (ROI) within the images.
    label_flag : bool, optional
        If True, skips the plotting of results. This is particularly useful for automated batch processing
        where visual output is not required. Default is False.

    Returns
    -------
    fitted_params_df : pandas.DataFrame
        DataFrame containing the fitted Gaussian parameters and their covariance, structured for further analysis.
    raw_params_df : pandas.DataFrame
        DataFrame containing the raw CCF parameters and values, providing the basic outputs of the CCF analysis.

    Notes
    -----
    The function handles different dimensionalities by checking the shape of `image1`. If `process_ccf`
    returns None, it indicates an unsuccessful fit, possibly due to inadequate correlation, and results
    in empty DataFrames being returned. Ensure that `process_ccf` and other utility functions are correctly
    implemented and that the data passed to them conforms to expected structures.
    """

    # Perform CCF analysis and Gaussian fitting
    result = process_ccf(image1, image2, roi_mask)

    # Prepare DataFrames for the results and plot the CCF analysis
    if result is None:
        napari_show_warning("Fit was not successful. Likely no correlation between the images.")
        fitted_params_df = pd.DataFrame()
        raw_params_df = pd.DataFrame()
    else:
        if len(image1.shape) == 1:
            # Prepare DataFrames for 1D analysis results
            fitted_params_df, raw_params_df = prepare_1d_result_dfs(result)
            if not label_flag:
                # Plot the 1D results
                plot_1d_results(result)

        elif len(image1.shape) == 2:
            # Prepare DataFrames for 2D analysis results
            fitted_params_df, raw_params_df = prepare_2d_result_dfs(result)
            if not label_flag:
                # Plot the 2D results and a central slice
                plot_2d_results(result)
                # Central slice of CCF
                fig, ax2 = plt.subplots(1, 1, figsize=(10, 6))
                ax2.plot(result['shifts'][0][0, :], result['ccf_values'][:, 20])
                ax2.set_title('Central Slice of 2D CCF')
                ax2.set_xlabel('Shift')
                ax2.set_ylabel('CCF Value')
                plt.show()

    return fitted_params_df, raw_params_df


def run_ccf_analysis(image_layer1, image_layer2, roi_mask_layer, data_instance):
    """
    Orchestrates the cross-correlation function (CCF) analysis for pairs of image layers, handling different types of
    ROI masks (binary or labeled) and integrating results into a specified data management object. It supports handling
    multiple ROI labels and aggregates results in a consistent format for analysis and visualization.

    Parameters
    ----------
    image_layer1 : napari.layers.Image
        The first image layer containing data for CCF analysis.
    image_layer2 : napari.layers.Image
        The second image layer containing data for CCF analysis.
    roi_mask_layer : napari.layers.Labels or None
        The image layer containing the ROI mask data. Can be None if no ROI is to be applied.
    data_instance : object
        An object that stores and manages the results of the CCF analysis, typically having a 'data_repository' attribute
        for storing results data.

    Raises
    ------
    ValueError
        If the input images do not have the same shape, or if the ROI mask does not match the dimensions of the images.

    Notes
    -----
    The function processes labeled masks by isolating each label into its own binary mask and performs CCF analysis
    separately for each. The results are then aggregated. For binary masks or the absence of a mask, analysis is done
    directly. Results are displayed in a custom dialog and stored in the provided data instance.

    The function updates the data_instance.data_repository with two key DataFrames:
    - CCF_fitted_params_df: Contains the fitted Gaussian parameters
    - CCF_raw_params_df: Contains the raw CCF parameters
    """

    # Get the image data
    image1 = image_layer1.data
    image2 = image_layer2.data
    roi_mask = roi_mask_layer.data if roi_mask_layer is not None else None

    # Validate input shapes and initiate analysis
    if image1.shape != image2.shape:
        raise ValueError("Input images must have the same shape.")
    if roi_mask is not None and roi_mask.shape != image1.shape:
        raise ValueError("ROI mask must have the same shape as the input images.")

    label_flag = False  # Initialize flag for labeled ROI processing

    # Initialize empty DataFrames to concatenate results
    concatenated_fitted_params_df = pd.DataFrame()
    concatenated_raw_params_df = pd.DataFrame()

    # Determine if the roi_mask is binary or labeled
    if roi_mask is not None and np.unique(roi_mask).size > 2:  # Labeled mask
        unique_labels = np.unique(roi_mask)[1:]  # Exclude 0 (background)
        for label in unique_labels: # Iterate over each label
            specific_roi_mask = (roi_mask == label).astype(bool)
            # Check if the label is greater than 1, so we only create one plot
            if label > 1:
                label_flag = True

            # Run the analysis for each label
            fitted_params_df, raw_params_df = ccf_analysis(image1, image2, specific_roi_mask, label_flag=label_flag)

            # If fitted_params_df is empty, skip the concatenation
            if fitted_params_df.empty:
                continue
            
            # Merge the results for each label after the first iteration
            if label_flag:
                concatenated_fitted_params_df = pd.merge(concatenated_fitted_params_df, fitted_params_df, on='Parameter', how='outer', suffixes=('', f'_{label}'))
                concatenated_raw_params_df = pd.merge(concatenated_raw_params_df, raw_params_df, on='Parameter', how='outer', suffixes=('', f'_{label}'))
            # Concatenate the results for the first label
            else:
                # Concatenate the results
                concatenated_fitted_params_df = pd.concat([concatenated_fitted_params_df, fitted_params_df], ignore_index=True)
                concatenated_raw_params_df = pd.concat([concatenated_raw_params_df, raw_params_df], ignore_index=True)
    # Binary mask or no mask, run the analysis as before
    else:
        label = 1 # Default label for binary mask
        roi_mask = roi_mask.astype(bool) if roi_mask is not None else None
        concatenated_fitted_params_df, concatenated_raw_params_df = ccf_analysis(image1, image2, roi_mask, label_flag=label_flag)
    
    # Prepare the DataFrame for display and storage
    if not concatenated_fitted_params_df.empty:
        concatenated_fitted_params_df.set_index('Parameter', inplace=True)
        concatenated_fitted_params_df = concatenated_fitted_params_df.round(4)
    else:
        concatenated_fitted_params_df = None
    # Prepare the DataFrame for display and storage
    if not concatenated_raw_params_df.empty:
        concatenated_raw_params_df.set_index('Parameter', inplace=True)
        concatenated_raw_params_df = concatenated_raw_params_df.round(4)
    else:
        concatenated_raw_params_df = None
        
    # Display the results in a dialog
    tables_info = [
        ("Fitted Gaussian Parameters", concatenated_fitted_params_df),
        ("Raw CCF Parameters", concatenated_raw_params_df)
    ]
    
    window_title = "Cross-Correlation Function Analysis"
    show_dataframes_dialog(window_title, tables_info)

    # Add the results to the data instance
    data_instance.data_repository["CCF_fitted_params_df"] = concatenated_fitted_params_df
    data_instance.data_repository["CCF_raw_params_df"] = concatenated_raw_params_df


# Auto-correlation function (ACF) analysis tools

def calculate_indices_and_plot_limits(acf_shape, lower_limit, upper_limit):
    """
    Calculate the indices for accessing array elements based on specified limits and determine
    the plot limits for visualization within an autocorrelation function (ACF) array.

    This function translates a coordinate system centered around zero (used for specifying limits) 
    to an array index system, ensuring the indices and plot limits do not exceed the dimensions 
    of the ACF or a default plotting range. It effectively manages the translation from a 
    potentially negative data range to positive-only index space used in array slicing.

    Parameters
    ----------
    acf_shape : tuple of int
        Shape of the ACF array, expected as (height, width).
    lower_limit : int or None
        The lower bound for both x and y indices in the coordinate system centered around zero. 
        If None, defaults to half the negative shape dimension to center the view.
    upper_limit : int or None
        The upper bound for both x and y indices in the coordinate system centered around zero. 
        If None, defaults to half the positive shape dimension to center the view.

    Returns
    -------
    tuple
        - (tuple of int): Lower and upper x indices (inclusive) for ACF array slicing.
        - (tuple of int): Lower and upper y indices (inclusive) for ACF array slicing.
        - (tuple of int): Lower and upper x limits for plotting.
        - (tuple of int): Lower and upper y limits for plotting.

    Notes
    -----
    This function is designed to adapt the indices for practical use in Python arrays, where indices must 
    be non-negative. The plot limits are set with a default range but adapt based on the provided limits 
    to enhance visualization flexibility.
    """

    # Determine the midpoints of the ACF array (effectively the center of the array)
    mid_x, mid_y = acf_shape[0] // 2, acf_shape[1] // 2

    # Set a default plotting limit of 50 units in either direction from the center
    default_plot_limit = 50

    # Compute the lower indices and plotting limits for x and y
    if lower_limit is not None:
        lower_x = max(lower_limit, -mid_x)
        lower_y = max(lower_limit, -mid_y)
        plot_lower_x = max(lower_limit, -default_plot_limit)
        plot_lower_y = max(lower_limit, -default_plot_limit)
    else:
        lower_x, lower_y = -mid_x, -mid_y
        plot_lower_x, plot_lower_y = -default_plot_limit, -default_plot_limit

    # Compute the upper indices and plotting limits for x and y
    if upper_limit is not None:
        upper_x = min(upper_limit, mid_x)
        upper_y = min(upper_limit, mid_y)
        plot_upper_x = min(upper_limit, default_plot_limit)
        plot_upper_y = min(upper_limit, default_plot_limit)
    else:
        upper_x, upper_y = mid_x, mid_y
        plot_upper_x, plot_upper_y = default_plot_limit, default_plot_limit

    # Convert plot indices from coordinate space (centered on zero) to array index space
    lower_x_idx = int(lower_x + mid_x)
    upper_x_idx = int(upper_x + mid_x)
    lower_y_idx = int(lower_y + mid_y)
    upper_y_idx = int(upper_y + mid_y)


    return (lower_x_idx, upper_x_idx), (lower_y_idx, upper_y_idx), (plot_lower_x, plot_upper_x), (plot_lower_y, plot_upper_y)





def calculate_autocorrelation(image):
    """
    Calculate the autocorrelation of an image using Fourier transforms. This method exploits the 
    Wiener-Khinchin theorem, which states that the autocorrelation function of a signal can be 
    obtained by taking the inverse Fourier transform of the power spectrum (magnitude squared 
    of the Fourier transform) of the signal.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for which the autocorrelation is to be computed. This should be a 2D numpy 
        array representing pixel intensities.

    Returns
    -------
    shifted_autocorrelation : numpy.ndarray
        The shifted autocorrelation function of the input image, normalized to [0, 1]. The zero-frequency 
        component is shifted to the center of the array, providing a more intuitive visual representation 
        of the autocorrelation function.

    Notes
    -----
    The autocorrelation function computed here provides a measure of how the image correlates with itself
    as it is translated over itself. High values in the autocorrelation function indicate high similarity 
    or redundancy at the corresponding shift values.
    """

    # Calculate the Fourier transform of the image and extract the real part
    fourier_transform = np.real(np.fft.fft2(image))
    # Compute the power spectrum as the square of the magnitude of the Fourier transform
    power_spectrum = fourier_transform**2
    # Normalize the power spectrum to its maximum value
    normalized_power_spectrum = power_spectrum / np.max(power_spectrum)
    # Perform an inverse Fourier transform to get the autocorrelation function
    autocorrelation_function = np.fft.ifft2(normalized_power_spectrum)
    # Normalize the autocorrelation function to range from 0 to 1
    acf_min, acf_max = np.min(autocorrelation_function), np.max(autocorrelation_function)
    normalized_autocorrelation_function = (autocorrelation_function - acf_min) / (acf_max - acf_min)
    # Shift the zero-frequency component to the center of the spectrum
    shifted_autocorrelation = np.real(np.fft.fftshift(normalized_autocorrelation_function))

    return shifted_autocorrelation


def plot_2d_autocorrelation(autocorrelation, x_lims, y_lims, title="2D Autocorrelation Function"):
    """
    Plot a 2D visualization of the autocorrelation function, highlighting spatial relationships and symmetries.

    Parameters
    ----------
    autocorrelation : numpy.ndarray
        The autocorrelation array to be plotted, expected to be a 2D numpy array representing 
        autocorrelation values.
    x_lims : tuple
        The x-axis limits for the plot, specified as a tuple (min, max) to define the visible range.
    y_lims : tuple
        The y-axis limits for the plot, specified as a tuple (min, max) to define the visible range.
    title : str, optional
        The title of the plot. Default is "2D Autocorrelation Function".

    Notes
    -----
    The plot is configured with a color map and boundaries defined by the shape of the autocorrelation 
    array, centered around the middle of the array. It uses matplotlib for plotting.
    """

    # Define the extents for the plot considering the shape of the autocorrelation
    acf_range = autocorrelation.shape
    plot_extents = [-acf_range[0]/2 - 0.5, acf_range[0]/2 + 0.5, -acf_range[1]/2 - 0.5, acf_range[1]/2 + 0.5]
    # Create a figure with specified size
    plt.figure(figsize=(7, 7))
    # Plot the 2D autocorrelation function
    plt.imshow(autocorrelation, extent=plot_extents, cmap='CMRmap')
    # Set the axis limits and labels
    plt.xlim(x_lims[0], x_lims[1])
    plt.ylim(y_lims[0], y_lims[1])
    plt.xticks(fontsize=8)
    plt.minorticks_on()
    plt.yticks(fontsize=8)
    plt.minorticks_on()
    plt.xlabel('X (px)',fontsize=14)
    plt.ylabel('Y (px)',fontsize=14)
    # Add a colorbar to the plot
    cbar=plt.colorbar(shrink=0.75)
    cbar.minorticks_on()
    cbar.ax.tick_params(labelsize=7)
    plt.title(title, fontsize=16)
    # Display the plot with a tight layout
    plt.tight_layout()
    plt.show()

def plot_1d_auto_correlation(autocorrelation, x_lims, gaussian_params_df, title="Central Slice of ACF"):
    """
    Plot a 1D slice of the autocorrelation function along with a fitted Gaussian curve to analyze
    the distribution along a single dimension.

    Parameters
    ----------
    autocorrelation : ndarray
        The autocorrelation array, expected to be 2D, from which a central slice will be extracted and plotted.
    x_lims : tuple
        The x-axis limits for the plot, specified as a tuple (min, max) to define the visible range.
    gaussian_params_df : DataFrame
        DataFrame containing the parameters for the Gaussian fit, specifically values for amplitude, mean (mu),
        and standard deviation (sigma) of the Gaussian.
    title : str, optional
        The title of the plot. Default is "Central Slice of ACF".

    Notes
    -----
    This function plots both the actual data from the central slice of the autocorrelation function and 
    the Gaussian fit, facilitating comparisons between the model and empirical data.
    """

    # Extract the range of the autocorrelation function and define the x-axis data
    acf_range = autocorrelation.shape
    x_data = np.linspace(-acf_range[1]/2 - 0.5, acf_range[1]/2 + 0.5, acf_range[1])
    # Extract Gaussian parameters from DataFrame
    amp = gaussian_params_df['Value'].values[0]
    mu = gaussian_params_df['Value'].values[1]
    sigma = gaussian_params_df['Value'].values[2]
    offset = gaussian_params_df['Value'].values[3]
    # Compute the Gaussian function values
    fitted_gaussian = amp * np.exp(-((x_data - mu) ** 2) / (2 * sigma ** 2)) + offset
    # Create a figure with specified size
    plt.figure(figsize=(8, 4))
    # Plot the fitted Gaussian curve
    plt.plot(x_data, fitted_gaussian, 'r', ls='--', label='Fitted Gaussian')
    # Plot the central slice of the autocorrelation function
    plt.plot(x_data, np.real(autocorrelation[acf_range[0]//2, :]), 'k', label='ACF', lw=3)
    # Set the axis limits and labels
    plt.xlim(x_lims[0], x_lims[1])
    plt.ylim(0, 1.1)
    plt.xticks(fontsize=8)
    plt.minorticks_on()
    plt.yticks(fontsize=8)
    plt.minorticks_on()
    plt.xlabel('X (px)',fontsize=10)
    plt.ylabel('Spatial Autocorrelation',fontsize=10)
    plt.title(title)
    plt.legend()
    # Display the plot with a tight layout
    plt.tight_layout()
    plt.show()

def plot_acf_image(image, title="Image", cmap='CMRmap'):
    """
    Plot an image using a specified colormap, providing visual insights into the spatial structures of the image.

    Parameters
    ----------
    image : ndarray
        The image to plot, typically a 2D numpy array of pixel intensities.
    title : str, optional
        The title for the plot. Default is "Image".
    cmap : str, optional
        The colormap to use for the plot. Default is 'CMRmap', which is suitable for highlighting features in 
        autocorrelation data.

    Notes
    -----
    This plot function is designed for general-purpose image visualization with an emphasis on flexibility 
    in colormap choice. It employs matplotlib's imshow function for rendering.
    """
    plt.figure(figsize=(7, 7))
    plt.imshow(image, cmap=cmap)
    plt.title(title)
    plt.xticks(fontsize=8)
    plt.minorticks_on()
    plt.yticks(fontsize=8)
    plt.minorticks_on()
    plt.xlabel('X (px)',fontsize=14)
    plt.ylabel('Y (px)',fontsize=14)
    cbar=plt.colorbar()
    cbar.ax.tick_params(labelsize=7)
    plt.show()




def gaussian_1d_offset(x, amplitude, mean, stddev, dc_offset):
    """
    Computes a one-dimensional Gaussian function with an offset, which is commonly used to model distributions 
    of data where a non-zero baseline might be present due to background noise or other baseline shifts.

    Parameters
    ----------
    x : numpy.ndarray
        The input values over which the Gaussian function is computed.
    amplitude : float
        The peak amplitude of the Gaussian function. This is the height of the peak above the dc_offset.
    mean : float
        The position (mean or center) of the peak of the Gaussian curve.
    stddev : float
        The standard deviation of the Gaussian function, describing the width of the bell-shaped curve.
    dc_offset : float
        A constant value added to the entire function, representing a baseline offset from zero.

    Returns
    -------
    numpy.ndarray
        The computed Gaussian function values for each element in x, adjusted by the dc_offset.
    """
    # Precompute constant part of the expression
    exp_component = -1 / (2 * stddev ** 2)
    return amplitude * np.exp(exp_component * (x - mean) ** 2) + dc_offset

def fit_gaussian_1d_acf(acf_values, shifts):
    """
    Fits a Gaussian function to 1D autocorrelation function (ACF) values. This method is typically used to determine 'cluster'
    sizes in the image data, where the ACF values are indicative of the spatial distribution of pixel intensities. In reality it
    is a noisy piecewise gaussian function, but this offset allows us to look at different parts of the ACF without such a 
    complicated model.

    Parameters
    ----------
    Parameters
    ----------
    acf_values : numpy.ndarray
        The autocorrelation function values obtained by correlating a signal with itself at varying shifts.
    shifts : numpy.ndarray
        The corresponding values of pixels at which the autocorrelation was computed. These 'shifts' are used as 
        the x-values for Gaussian fitting.

    Returns
    -------
    results : dict or None
        A dictionary containing the results of the Gaussian fitting (parameters and diagnostics) or None if the fitting fails.
    """
    shifts_bottom_quarter = len(shifts)//4
    shifts_top_quarter = 3*len(shifts)//4
    smoothed_ccf = smooth_array(acf_values)  # Smooth CCF values for better fitting
    max_ccf = max(smoothed_ccf[shifts_bottom_quarter:shifts_top_quarter], key=abs)  # Find maximum CCF value for initial guess

    # Determine initial guess parameters for Gaussian fitting
    if max_ccf > 0:
        p0 = [1, 0, 1, 0]  # Assume positive peak
    else:
        p0 = [-1, 0, 1, 0]  # Assume negative peak for inverted Gaussian

    try:
        # Perform Gaussian fitting
        popt, pcov = curve_fit(gaussian_1d_offset, shifts, acf_values, p0=p0)
        # Compile results into a dictionary
        results = {
            'gaussian_params': popt,
            'goodness_of_fit': np.sqrt(np.diag(pcov)),  # Standard error of the estimated parameters
        }
    except RuntimeError:
        results = None  # Fitting failed

    return results


def autocorrelation_analysis(image, roi_mask, lower_limit, upper_limit, micron_resolution, label_flag=False):
    """
    Performs detailed autocorrelation analysis on an image, optionally utilizing a region of interest (ROI),
    and provides outputs including Gaussian fit parameters for both 1D and 2D analyses.

    This function calculates the 2D autocorrelation of the image or its ROI, extracts specific regions based on given limits
    for further analysis, fits Gaussian models to these extracted regions in both 1D (central horizontal slice) and 2D,
    and plots these results.

    Parameters
    ----------
    image : numpy.ndarray
        The image data on which autocorrelation analysis is performed. Should be a 2D numpy array.
    roi_mask : numpy.ndarray or None
        A binary mask defining the ROI within the image. If None, the entire image is analyzed.
    lower_limit : int
        The lower limit for calculating indices and plot limits in the autocorrelation function. This affects the
        range of autocorrelation data to be analyzed and visualized.
    upper_limit : int
        The upper limit for the same purposes as the lower limit.
    micron_resolution : float
        The resolution of the image in microns per pixel. This is used to scale the dimensions of the results
        in the output, converting pixel measurements to microns.
    label_flag : bool
        If False, results of the analysis (such as the 2D autocorrelation function and Gaussian fits) are plotted.
        If True, plotting is skipped; useful for batch processing or when visualization is not required.

    Returns
    -------
    acf_values_df : pandas.DataFrame
        DataFrame of the autocorrelation values, suitable for further analysis or export.
    fitted_params_df_2d : pandas.DataFrame
        DataFrame containing the parameters from the 2D Gaussian fit, along with their covariances, and scaled
        object dimensions in microns.
    fitted_params_df_1d : pandas.DataFrame
        DataFrame containing the parameters from the 1D Gaussian fit, along with their covariances, and the scaled
        object diameter in microns.

    Notes
    -----
    The function is designed to handle various types of image data and can accommodate different analysis scenarios
    through its parameters. It integrates several computational steps such as ROI application, autocorrelation computation,
    Gaussian fitting, and optional visualization, providing a comprehensive tool for image analysis.

    This method is typically used to determine 'cluster' or (blob like) object sizes in the image data, where the ACF values 
    are indicative of the spatial distribution of pixel intensities. It is useful in quantifying the size of objects which are 
    not well defined in terms of their boundary. For condensates, many kinds of aggregates, clumps, clusters, and other objects, 
    especially in in-cellulo data, are not well defined spherical droplets and therefore this becomes an invaluable analysis tool; 
    particularly useful for 'pre-determining' the size of objects before using other PyCAT methods to segment them.   
    """

    # Deprecated analysis step for enhancing image contrast 
    #image -= np.mean(image)

    # Apply the ROI mask to the image if provided, else use the full image
    if roi_mask is not None:
        # Crop the image to the bounding box defined by the mask
        masked_array, _, _ = crop_bounding_box(image, roi_mask)
    else:
        masked_array = image.copy()

    # Calculate the 2D autocorrelation of the masked or full image
    acf_values = calculate_autocorrelation(masked_array)
    acf_range = acf_values.shape

    # Calculate indices and plot limits for visualizing the autocorrelation
    x_indices, y_indices, x_plot_limits, y_plot_limits = calculate_indices_and_plot_limits(acf_range, lower_limit, upper_limit)

    # Extract a specific region from the autocorrelation array based on the calculated indices
    limited_acf_values = acf_values[x_indices[0]:x_indices[1], y_indices[0]:y_indices[1]]

    # Get the central horizontal slice of the limited ACF for 1D analysis
    central_acf_slice = limited_acf_values[limited_acf_values.shape[0] // 2, :]
    central_x_data = np.linspace(x_indices[0] - acf_range[1] // 2, x_indices[1] - acf_range[1] // 2, limited_acf_values.shape[1])

    # Create mesh grids for x and y coordinates in the 2D limited ACF analysis
    x = np.linspace(x_indices[0] - acf_range[0] // 2, x_indices[1] - acf_range[0] // 2, limited_acf_values.shape[0])
    y = np.linspace(y_indices[0] - acf_range[1] // 2, y_indices[1] - acf_range[1] // 2, limited_acf_values.shape[1])
    xx, yy = np.meshgrid(x, y)

    # Define the full width at half maximum (FWHM) conversion factor for Gaussian fits
    fwhm = 2 * np.sqrt(2 * np.log(2))
    scale_factor = micron_resolution * fwhm

    # Perform Gaussian fitting to the 1D and 2D autocorrelation data
    acf_1d_results = fit_gaussian_1d_acf(central_acf_slice, central_x_data)
    acf_2d_results = fit_gaussian_2d(limited_acf_values, xx, yy)

    # Handle results of 2D Gaussian fitting
    if acf_2d_results is None:
        napari_show_warning("2D ACF fit was not successful.")
        fitted_params_df_2d = pd.DataFrame()
    else:
        fitted_params_df_2d = pd.DataFrame({
            'Parameter': ['Amplitude', 'mu_x', 'mu_y', 'sigma_x', 'sigma_y', 'Object Diameter x (um)', 'Object Diameter y (um)'],
            'Value': list(acf_2d_results['gaussian_params']) + [scale_factor * acf_2d_results['gaussian_params'][3], scale_factor * acf_2d_results['gaussian_params'][4]],
            'Covariance': list(acf_2d_results['goodness_of_fit']) + [np.nan, np.nan]
        })
        if not label_flag:
            plot_2d_autocorrelation(acf_values, x_plot_limits, y_plot_limits)

    # Handle results of 1D Gaussian fitting
    if acf_1d_results is None:
        napari_show_warning("1D ACF fit was not successful.")
        fitted_params_df_1d = pd.DataFrame()
    else:
        fitted_params_df_1d = pd.DataFrame({
            'Parameter': ['Amplitude', 'mu', 'sigma', 'DC Offset', 'Object Diameter (um)'],
            'Value': list(acf_1d_results['gaussian_params']) + [scale_factor * acf_1d_results['gaussian_params'][2]],
            'Covariance': list(acf_1d_results['goodness_of_fit']) + [np.nan]
        })
        if not label_flag:
            plot_1d_auto_correlation(acf_values, x_plot_limits, fitted_params_df_1d)

    # Create a DataFrame from the autocorrelation values for easy export and analysis
    acf_values_df = pd.DataFrame(acf_values)

    return acf_values_df, fitted_params_df_2d, fitted_params_df_1d


def run_autocorrelation_analysis(image_layer, roi_mask_layer, lower_lim_input, upper_lim_input, data_instance):
    """
    Coordinates the autocorrelation analysis of an image using optional ROI masks and manages the presentation
    and storage of results within a specified data framework. This function extracts image and ROI mask data,
    handles GUI inputs for analysis bounds, and performs autocorrelation analysis with proper Gaussian fitting.
    The results are displayed in a custom dialog and stored for later retrieval.

    Parameters
    ----------
    image_layer : napari.layers.Image
        An object containing the image data to be analyzed. Expected to provide access to 2D numpy array data.
    roi_mask_layer : napari.layers.Labels or None
        An object containing the ROI mask data, if applicable. If None, no ROI mask is applied.
    lower_lim_input : QLineEdit (textbox)
        GUI input element for specifying the lower limit of index calculations, affecting the range of autocorrelation analysis.
    upper_lim_input : QLineEdit (textbox)
        GUI input element for specifying the upper limit of index calculations, similar to lower_lim_input.
    data_instance : object
        A custom object for managing the storage and retrieval of analysis data, including results of the autocorrelation analysis.

    Raises
    ------
    ValueError
        If the image and ROI mask do not have the same shape, preventing proper analysis.

    Notes
    -----
    This function is capable of handling both labeled and binary ROI masks, adjusting its processing logic accordingly to provide 
    detailed and scalable analysis results. The results are displayed in a dialog for easy access and interpretation.This method is 
    typically used to determine 'cluster' or (blob like) object sizes in the image data. Since there are often objects of various 
    size domains, the function is really a piecewise gaussian, here we recommend using pre-processing and ROI masking to narrow 
    down the analysis to specific objects of interest. However, in the event that is not feasible, we provide the limit inputs 
    to allow the user to focus on specific size regimes of interest. You can run the function, look at the domains in the 1D ACF
    and then re-run with different limits to focus on specific size regimes.
    """

    # Extract the image and ROI mask data
    image = image_layer.data
    roi_mask = roi_mask_layer.data if roi_mask_layer is not None else None

    # Ensure the image and ROI mask have the same dimensions
    if roi_mask is not None and image.shape != roi_mask.shape:
        raise ValueError("Image and ROI mask must have the same shape.")

    # Convert GUI inputs to integer limits, handling empty inputs as None
    lower_lim = int(lower_lim_input.text()) if lower_lim_input.text() else None
    upper_lim = int(upper_lim_input.text()) if upper_lim_input.text() else None
                
    label_flag = False  # Initialize flag to control the plotting based on label presence

    # Initialize empty DataFrames to concatenate results for labeled analysis
    concatenated_fitted_params_df_2d = pd.DataFrame()
    concatenated_fitted_params_df_1d = pd.DataFrame()

    # Calculate the physical pixel size from stored data
    microns_per_pixel = np.sqrt(data_instance.data_repository["microns_per_pixel_sq"])

    # Process differently based on the presence and type of ROI mask
    if roi_mask is not None and np.unique(roi_mask).size > 2:  # Labeled mask scenario
        unique_labels = np.unique(roi_mask)[1:]  # Exclude background label
        for label in unique_labels:
            specific_roi_mask = (roi_mask == label).astype(bool)
            label_flag = label > 1

            # Run the analysis for each labeled region
            _, fitted_params_df_2d, fitted_params_df_1d = autocorrelation_analysis(image, specific_roi_mask, lower_lim, upper_lim, microns_per_pixel, label_flag=label_flag)

            # Concatenate the DataFrame results appropriately
            if not fitted_params_df_2d.empty: # if fitted_params_df is empty, skip the concatenation
                if label_flag:
                    concatenated_fitted_params_df_2d = pd.merge(concatenated_fitted_params_df_2d, fitted_params_df_2d, on='Parameter', how='outer', suffixes=('', f'_{label}'))
                else:
                    concatenated_fitted_params_df_2d = pd.concat([concatenated_fitted_params_df_2d, fitted_params_df_2d], ignore_index=True)
            if not fitted_params_df_1d.empty: # if fitted_params_df is empty, skip the concatenation
                if label_flag:
                    concatenated_fitted_params_df_1d = pd.merge(concatenated_fitted_params_df_1d, fitted_params_df_1d, on='Parameter', how='outer', suffixes=('', f'_{label}'))
                else:
                    concatenated_fitted_params_df_1d = pd.concat([concatenated_fitted_params_df_1d, fitted_params_df_1d], ignore_index=True)

    else: # Binary mask or no mask scenario
        label = 1
        roi_mask = roi_mask.astype(bool) if roi_mask is not None else None
        acf_values_df, concatenated_fitted_params_df_2d, concatenated_fitted_params_df_1d = autocorrelation_analysis(image, roi_mask, lower_lim, upper_lim, microns_per_pixel, label_flag=label_flag)    
  
    # Format and set DataFrame results
    if not concatenated_fitted_params_df_2d.empty:
        concatenated_fitted_params_df_2d.set_index('Parameter', inplace=True)
        concatenated_fitted_params_df_2d = concatenated_fitted_params_df_2d.round(4)
    else:
        concatenated_fitted_params_df_2d = None
    if not concatenated_fitted_params_df_1d.empty:
        concatenated_fitted_params_df_1d.set_index('Parameter', inplace=True)
        concatenated_fitted_params_df_1d = concatenated_fitted_params_df_1d.round(4)
    else:
        concatenated_fitted_params_df_1d = None
        
    # Display the results in a dialog
    tables_info = [("2D ACF Fitted Gaussian Parameters", concatenated_fitted_params_df_2d),
                   ("1D ACF Fitted Gaussian Parameters", concatenated_fitted_params_df_1d)]
    window_title = "Auto-Correlation Function Analysis"
    show_dataframes_dialog(window_title, tables_info)

    # Add the results to the data instance for storage and retrieval
    data_instance.data_repository["ACF_2d_fitted_params_df"] = concatenated_fitted_params_df_2d
    data_instance.data_repository["ACF_1d_fitted_params_df"] = concatenated_fitted_params_df_1d
    data_instance.data_repository["ACF_values_df"] = acf_values_df

