"""
Image Feature Analysis Module for PyCAT

This module provides functions for analyzing image features, including texture, intensity, and shape properties. 
It supports the calculation of Gray Level Co-occurrence Matrix (GLCM) features, image entropy, kurtosis, and Local
Binary Pattern (LBP) features. These functions provide insights into the texture and statistical properties of images
and segmented regions, enabling detailed analysis and comparison of image data.

This is also the module where the cell and puncta analysis functions are located. These functions are designed to
analyze segmented cells and puncta within cells, calculating various properties and statistics to provide insights
into cell and sub-cellular object characteristics. The functions integrate image processing, segmentation, and feature
calculation to facilitate comprehensive analysis of biological images. The results are stored in a data repository
for further analysis and visualization. 

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np
import pandas as pd
import skimage as sk
import scipy.stats as stats
from napari.utils.notifications import show_warning as napari_show_warning

# Local application imports
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func
from pycat.utils.general_utils import dtype_conversion_func, crop_bounding_box, create_overlay_image
from pycat.utils.math_utils import remove_outliers_iqr
from pycat.ui.ui_utils import show_dataframes_dialog
from pycat.toolbox.image_processing_tools import apply_rescale_intensity




# Image feature functions 

def calculate_glcm_features(image, object_size, roi_mask=None, min_object_size=3):
    """
    Calculates Gray Level Co-occurrence Matrix (GLCM) features for an image using specified object size parameters
    and an optional region of interest (ROI) mask.

    This function processes the image to extract texture features based on GLCM. It supports customization of the 
    analysis area through an ROI mask and adjusts the calculation detail level using object size specifications.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for which to calculate GLCM features. Expected to be in a compatible format.
    object_size : int
        Defines the scale of the object in the image to determine the calculation granularity.
    roi_mask : numpy.ndarray, optional
        A binary mask that specifies the region of interest within the image. If None, the entire image is analyzed.
    min_object_size : int, optional
        The minimum size for objects when calculating distances in GLCM, defaults to 3.

    Returns
    -------
    features_df : pandas.DataFrame
        A DataFrame containing the calculated GLCM features for each combination of distance and angle.

    Notes
    -----
    The function performs several preprocessing steps on the image, including rescaling and conversion to 8-bit.
    It computes GLCM over a range of distances and angles based on the specified object size and extracts features 
    such as contrast, dissimilarity, homogeneity, ASM, energy, and correlation from the GLCM. The results are averaged 
    over all angles and distances and returned in a DataFrame for easy analysis.
    """
    
    # Handle ROI mask; create a full mask if none is provided
    roi_mask = np.ones(image.shape).astype(bool) if roi_mask is None else roi_mask.astype(bool)

    # Rescale the image and convert to 8-bit for compatibility with GLCM calculations
    img = dtype_conversion_func(image, output_bit_depth='float32')
    scaled_img = apply_rescale_intensity(img)
    image_8bit = dtype_conversion_func(scaled_img, output_bit_depth='uint8')

    # Apply the mask to the image
    masked_image = crop_bounding_box(image_8bit, roi_mask)[0]

    # Setup distances and angles for GLCM calculations
    max_object_size = 2 * object_size + 1
    distances = np.arange(min_object_size, max_object_size + 1)
    angles = np.array([0, np.pi/8, np.pi/4, 3*np.pi/8, np.pi/2, 5*np.pi/8, 3*np.pi/4, 7*np.pi/8])

    # Compute GLCM and its properties
    glcm = sk.feature.graycomatrix(masked_image, distances, angles, symmetric=True, normed=True)
    properties = ['contrast', 'dissimilarity', 'homogeneity', 'ASM', 'energy', 'correlation']
    features_values = {prop: sk.feature.graycoprops(glcm, prop).mean(axis=(0, 1)) for prop in properties}

    # Convert features to DataFrame
    features_df = pd.DataFrame([features_values])

    return features_df


def calculate_image_entropy(image, ball_radius, roi_mask=None):
    """
    Calculates the entropy of an image using a local neighborhood defined by a specified ball radius, and optionally,
    within a region of interest (ROI). This function processes the image at multiple bit-depths to derive entropy
    measures that reflect the complexity and texture variations across the image.

    Parameters
    ----------
    image : numpy.ndarray
        The input image on which entropy calculations are to be performed. Expected to be in a compatible format.
    ball_radius : int
        The radius of the structuring element (ball) used to calculate local entropy within the image.
    roi_mask : numpy.ndarray, optional
        A binary mask that defines the region of interest within the image. If None, the entire image is considered.

    Returns
    -------
    entropy_df : pandas.DataFrame
        A DataFrame containing the calculated entropy values for the image at different bit-depths and local entropy
        averaged over the specified ROI.

    Notes
    -----
    The entropy calculation includes:
    - Conversion of the image to 8-bit and 32-bit formats for entropy analysis.
    - Calculation of global entropy for both 8-bit and 32-bit images to assess overall randomness.
    - Calculation of mean local entropy using a ball structuring element, which highlights local variations in image texture.
    The results are presented in a DataFrame, facilitating easy comparison and further analysis.
    """

    # Handle ROI mask; create a full mask if none is provided
    roi_mask = np.ones(image.shape).astype(bool) if roi_mask is None else roi_mask.astype(bool)

    # Rescale the image and convert to 8-bit for compatibility with entropy calculations
    img = dtype_conversion_func(image, output_bit_depth='float32')
    scaled_img = apply_rescale_intensity(img)
    image_8bit = dtype_conversion_func(scaled_img, output_bit_depth='uint8')

    # Apply the mask and calculate entropies for 32-bit float image
    masked_img, cropped_mask, _ = crop_bounding_box(img, roi_mask)
    bit32_entropy = sk.measure.shannon_entropy(masked_img)
    # Apply the mask and calculate entropies for 8-bit uint image
    masked_image_8bit, _, _ = crop_bounding_box(image_8bit, roi_mask)
    bit8_entropy = sk.measure.shannon_entropy(masked_image_8bit)

    # Calculate mean local rank entropy with specified ball radius
    footprint = sk.morphology.disk(ball_radius)
    bit8_rank_entropy = sk.filters.rank.entropy(masked_image_8bit, mask=cropped_mask, footprint=footprint)
    mean_entropy = np.mean(bit8_rank_entropy[cropped_mask])

    # Compile entropy data into DataFrame
    entropy_data = {
        '32_bit_entropy': [bit32_entropy],
        '8_bit_entropy': [bit8_entropy],
        '8_bit_entropy_img_avg': [mean_entropy]
    }
    
    entropy_df = pd.DataFrame(entropy_data)

    return entropy_df


def calculate_image_kurtosis(image, roi_mask=None):
    """
    Calculate the kurtosis of pixel intensity values within an image, optionally
    within a specified region of interest (ROI). The function applies data type conversion,
    intensity scaling, outlier removal, and finally computes the kurtosis and other
    statistical measures.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array. The image can be of any dimensional shape but must be
        compatible with the provided `roi_mask` if used.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same shape as `image` that defines the ROI within the image.
        If None (default), the entire image is considered as the ROI.

    Returns
    -------
    kurtosis_df : pandas.DataFrame
        A pandas DataFrame containing the calculated kurtosis, standardized sixth moment,
        kurtosis z-score, and p-value of the kurtosis test. Each of these metrics provides
        insights into the distribution of pixel intensities within the specified ROI.

    Notes
    -----
    The function internally converts the image to 32-bit floating point for processing,
    and then to 16-bit unsigned integer for compatibility with kurtosis calculations. It also
    applies an intensity rescaling and outlier removal based on the interquartile range. The Laplace 
    distribution has a heavier tail (higher kurtosis) than the normal distribution. The uniform 
    distribution (which has negative kurtosis) has the thinnest tail.
    """

    # Handle ROI mask; create a full mask if none is provided
    roi_mask = np.ones(image.shape).astype(bool) if roi_mask is None else roi_mask.astype(bool)

    # Rescale the image and convert to 16-bit for compatibility with kurtosis calculations
    img = dtype_conversion_func(image, output_bit_depth='float32')
    scaled_img = apply_rescale_intensity(img)
    image_16bit = dtype_conversion_func(scaled_img, output_bit_depth='uint16')

    # Apply ROI mask and remove outliers based on IQR
    image_iqr = remove_outliers_iqr(image_16bit[roi_mask])

    # Calculate standard deviation and handle cases of very low variance
    std_dev = np.std(image_iqr)
    if std_dev < 2:
        img_kurtosis = np.nan
        kurtosis_z_score, p_val = np.nan, np.nan
        standardized_sixth_moment = np.nan
    else:
        # Calculate kurtosis and related statistics (kurtosis z-score and p-value, standardized sixth moment)
        img_kurtosis = stats.kurtosis(image_iqr)
        # z-score and p-value of the kurtosis test tell if the kurtosis result is close enough to normal
        kurtosis_z_score, p_val = stats.kurtosistest(image_iqr)
        # Standardized sixth moment is the hyper-kurtosis of a distribution (higher order version of kurtosis)
        sixth_moment = stats.moment(image_iqr, moment=6)
        standardized_sixth_moment = sixth_moment / (std_dev**6)

    # Compile results into a dictionary and then a DataFrame
    kurtosis_data = {
        'img_kurtosis': [img_kurtosis],
        'standardized_sixth_moment': [standardized_sixth_moment],
        'kurtosis_z_score': [kurtosis_z_score],
        'p_val': [p_val]
    }
    # Create a DataFrame from the calculated kurtosis data
    kurtosis_df = pd.DataFrame(kurtosis_data)

    return kurtosis_df


def calculate_lbp_features(image, roi_mask=None, min_object_size=3):
    """
    Calculate Local Binary Pattern (LBP) features of an image, within an optional region
    of interest (ROI). This function processes the image to compute LBP histograms and
    derives statistical measures from these histograms, such as mean, standard deviation,
    and entropy of the LBP distribution.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array. The image can be of any dimensional shape.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same shape as `image` that defines the ROI within the image.
        If None (default), the entire image is considered as the ROI.
    min_object_size : int, optional
        The minimum size of objects to consider within the image for LBP calculations.
        Defaults to 3.

    Returns
    -------
    lbp_features_df : pandas.DataFrame
        A pandas DataFrame containing statistical measures derived from the LBP histogram,
        including mean, standard deviation, and entropy.

    Notes
    -----
    The function performs several preprocessing steps on the image: conversion to 32-bit 
    floating point for numerical stability, rescaling of intensity values, conversion to 
    8-bit for compatibility with LBP computation, and application of the ROI mask. The LBP 
    histogram is computed within the ROI, and the resulting features provide insights into 
    the texture of the image within this region.
    """

    # Handle ROI mask; create a full mask if none is provided
    roi_mask = np.ones(image.shape).astype(bool) if roi_mask is None else roi_mask.astype(bool)

    # Rescale the image and convert to 8-bit for compatibility with LBP calculations
    img = dtype_conversion_func(image, output_bit_depth='float32')
    scaled_img = apply_rescale_intensity(img)
    image_8bit = dtype_conversion_func(scaled_img, output_bit_depth='uint8')

    # Apply the ROI mask and crop the image to the bounding box of the ROI
    masked_image_8bit, cropped_mask, _ = crop_bounding_box(image_8bit, roi_mask)

    # Calculate LBP for the masked and cropped image
    lbp = sk.feature.local_binary_pattern(masked_image_8bit, P=8, R=min_object_size)
    # Compute histogram of LBP values within the ROI
    lbp_hist, _ = np.histogram(lbp[cropped_mask].ravel(), bins=np.arange(257))

    # Derive statistical measures from the LBP histogram
    lbp_features = {
        'lbp_mean': lbp_hist.mean(),
        'lbp_std': lbp_hist.std(),
        'lbp_entropy': sk.measure.shannon_entropy(lbp_hist),
        # Additional statistical measures could be included here
    }

    # Convert the LBP features dictionary to a DataFrame for easier handling and integration
    lbp_features_df = pd.DataFrame([lbp_features])

    return lbp_features_df


def calculate_image_features(image, data_instance, roi_mask=None):
    """
    Calculate a comprehensive set of features for an image, combining various texture
    and statistical analyses. This function orchestrates the computation of Gray Level
    Co-occurrence Matrix (GLCM) features, image entropy, kurtosis, and Local Binary Pattern
    (LBP) features. It requires a data instance object for accessing global parameters
    relevant to the feature calculations.

    Parameters
    ----------
    image : numpy.ndarray
        The input image array. This is the primary data on which all feature calculations
        are based.
    data_instance : object
        An object containing a data repository with parameters like `object_size` and
        `ball_radius` which are used in the feature calculation processes.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same shape as `image` to define the region of interest within
        the image. If None (default), the entire image is considered.

    Returns
    -------
    image_features_df : pandas.DataFrame
        A pandas DataFrame aggregating the features calculated by the individual
        functions. This DataFrame provides a comprehensive overview of the image's
        texture and statistical properties.

    Notes
    -----
    The function leverages other specialized feature calculation functions, each focusing
    on a different aspect of image analysis. The resulting feature DataFrames from these
    functions are concatenated to form a single, comprehensive DataFrame that encompasses
    a wide range of image characteristics. This approach allows for a holistic analysis
    of the image based on multiple feature sets.
    """
    # Extract required parameters from the data_instance
    object_size = data_instance.data_repository['object_size']
    ball_radius = data_instance.data_repository['ball_radius']

    # Calculate individual feature sets
    glcm_features_df = calculate_glcm_features(image, object_size, roi_mask=roi_mask)
    entropy_df = calculate_image_entropy(image, ball_radius, roi_mask=roi_mask)
    kurtosis_df = calculate_image_kurtosis(image, roi_mask=roi_mask)
    lbp_features_df = calculate_lbp_features(image, roi_mask=roi_mask, min_object_size=object_size)

    # Concatenate all feature DataFrames into a single DataFrame
    image_features_df = pd.concat([glcm_features_df, entropy_df, kurtosis_df, lbp_features_df], axis=1)

    return image_features_df


# Cell and puncta analysis functions

def cell_analysis_func(image, cell_masks, omission_mask, data_instance):
    """
    Analyzes segmented cells in a greyscale image by calculating various intensity and shape statistics
    for each cell. It leverages a binary mask to identify cell regions and performs statistical analysis
    on these regions to generate insights into cell characteristics. Optionally, an omission mask can be 
    provided to exclude specific areas from the analysis.

    Parameters
    ----------
    image : numpy.ndarray
        The original grayscale image from which cell properties are derived.
    cell_masks : numpy.ndarray
        A binary mask indicating regions identified as cells for segmentation and analysis.
    omission_mask : numpy.ndarray, optional
        A mask that specifies regions to be excluded from the analysis, if provided.
    data_instance : object
        An object that contains relevant analysis parameters, such as cell diameter and pixel size, stored within
        a data repository.

    Returns
    -------
    labeled_cells : numpy.ndarray
        An image with unique labels for each segmented cell, facilitating individual analysis.
    final_df : pandas.DataFrame
        A DataFrame containing statistical information about each segmented cell, including metrics
        like cell area, average intensity, eccentricity, and others derived from the segmented regions.

    Notes
    -----
    This function integrates multiple image processing and analysis steps:
    - Converts the cell mask to a binary format and applies contour detection based on a minimum cell area.
    - Enhances cell contours using morphological operations to improve segmentation accuracy.
    - Optionally applies an omission mask to exclude certain areas from analysis.
    - Calculates statistical metrics such as intensity mean, standard deviation, median, and total intensity,
    along with additional features for each segmented cell.
    - Utilizes background noise estimations to compute signal-to-noise ratios (SNRs) for the cells.
    - Aggregates all computed data into a comprehensive DataFrame that includes intensity statistics and additional
    calculated features, enhancing insights into cell characteristics within the analyzed image.
    """

    unique_labels = np.unique(cell_masks)[1:] # Skip the background label (0)
    is_labeled_mask = np.max(cell_masks) > 1  # Check if the mask is already labeled

    # Calculate the minimum size of a cell based on the typical cell diameter
    cell_diameter = data_instance.data_repository['cell_diameter']
    min_area = (np.pi*(cell_diameter/2)**2)//10

    if omission_mask is not None:
        binary_omission_mask = (omission_mask > 0).astype(bool)
        omission_contour_mask = opencv_contour_func(binary_omission_mask).astype(bool)

    labeled_cells = np.zeros_like(cell_masks)
    for label in unique_labels:
        # Convert the cell mask to binary for processing
        binary_cell_masks = (cell_masks == label).astype(bool)

        # Apply contour detection to the binary cell mask 
        cell_contour_mask = opencv_contour_func(binary_cell_masks, min_area)
        
        # Apply morphological operations to refine cell contours
        cell_contour_mask = binary_morph_operation(cell_contour_mask, iterations=7, element_size=2, element_shape='Diamond', mode='Opening')

        # Apply the omission mask to the cell contour mask if provided
        if omission_mask is not None:
            cell_contour_mask = cell_contour_mask & ~omission_contour_mask
        
        labeled_cells[cell_contour_mask] = label

    # Calculate estimates of the background noise
    img_bg_noise = np.std(image[labeled_cells == 0]) # std dev of the 'backgroud' as an estimate
    gaussian_bg_noise_est = sk.restoration.estimate_sigma(image) # skimage gaussian noise estimate

    if not is_labeled_mask:
        # Label the segmented cells
        labeled_cells = sk.measure.label(labeled_cells)

    # Measure region properties of segmented cells
    properties = ('label', 'area', 'intensity_mean', 'axis_major_length', 'axis_minor_length', 'eccentricity', 'perimeter')
    df = pd.DataFrame(sk.measure.regionprops_table(labeled_cells, intensity_image=image, properties=properties))

    # Initialize lists to store intensity statistics and additional features for each cell
    std_intensity_list = []
    med_intensity_list = []
    total_intensity_list = []
    feature_dfs_list = []
    
    # Iterate through each cell to calculate additional statistics and features
    for label in np.unique(labeled_cells)[1:]:
        single_cell_mask = (labeled_cells == label)
        std_intensity_list.append(np.std(image[single_cell_mask]))
        med_intensity_list.append(np.median(image[single_cell_mask]))
        total_intensity_list.append(np.sum(image[single_cell_mask]))

        # Calculate additional texture and intensity related features for the cell
        single_cell_features_df = calculate_image_features(image, data_instance, roi_mask=single_cell_mask)
        feature_dfs_list.append(single_cell_features_df)

    # Add columns for the standard deviation, median, and total intensity values to the dataframe
    df['intensity_std_dev'] = std_intensity_list
    df['intensity_median'] = med_intensity_list
    df['intensity_total'] = total_intensity_list

    # Convert cell area to microns squared using pixel size data
    df['cell_micron_area'] = df['area'] * data_instance.data_repository['microns_per_pixel_sq']

    # Add resolution information for contextual understanding
    df['image_resolution_um_per_px_sq'] = data_instance.data_repository['microns_per_pixel_sq']

    # Calculate SNR based on background noise and Gaussian noise estimation
    df['cell_snr'] = df['intensity_mean'] / img_bg_noise
    df['gaussian_snr_estimate'] = df['intensity_mean'] / gaussian_bg_noise_est

    # Combine all additional features into one DataFrame
    all_features_df = pd.concat(feature_dfs_list, ignore_index=True)
    # Merge the features DataFrame with the cell statistics DataFrame
    final_df = pd.concat([df, all_features_df], axis=1)

    return labeled_cells, final_df


def run_cell_analysis_func(mask_layer, omit_mask_layer, image_layer, data_instance, viewer):
    """
    Orchestrates comprehensive cell analysis by leveraging mask and image layers in Napari. It ensures mask and image 
    compatibility, performs cell segmentation and analysis, and visualizes results in the viewer, while saving the data
    for further use in the active data class' data repository.

    Parameters
    ----------
    mask_layer : napari.layers.Labels
        The layer containing binary mask data that differentiates cell regions from the background.
    omit_mask_layer : napari.layers.Labels, optional
        An optional layer containing masks of regions to omit from the analysis, if provided.
    image_layer : napari.layers.Image
        The layer containing the original grayscale image data used for analysis.
    data_instance : object
        An object encapsulating a repository for storing analysis results and other relevant data.
    viewer : napari.Viewer
        The viewer object used for displaying analysis results, including labeled segmented cells and statistics.

    Notes
    -----
    This function serves as the main entry point for conducting cell analysis. It ensures that the provided
    mask and image layers are compatible in shape. It then proceeds to segment and analyze the cells based on
    the provided mask, storing the results for future reference. The segmented cells are visualized in the viewer,
    and a dialog is presented with the cell statistics, facilitating an interactive analysis experience.
    """

    # Extract the actual data from the provided mask and image layers
    cell_masks = mask_layer.data
    omission_mask = omit_mask_layer.data if omit_mask_layer is not None else None
    image = image_layer.data

    # Check for shape compatibility between mask and image layers
    if cell_masks.shape != image.shape:
        raise ValueError("Mask and image layers must have the same shape!")
    if omission_mask is not None and omission_mask.shape != cell_masks.shape:
        raise ValueError("Omission mask and cell mask layers must have the same shape!")
    
    # Perform cell analysis and retrieve the labeled cell masks and cell statistics
    labeled_cell_masks, cell_df = cell_analysis_func(image, cell_masks, omission_mask, data_instance)

    # Store the cell statistics in the data repository
    data_instance.data_repository['cell_df'] = cell_df
    #data_instance._notify(f"cell_df has been set!")

    # Add the labeled cell masks to the viewer for visualization
    viewer.add_labels(labeled_cell_masks, name='Labeled Cell Mask')

    # Display the cell statistics in a popup window
    tables_info = [("Cell Statistics", cell_df)]
    window_title = "Cell Analysis"
    show_dataframes_dialog(window_title, tables_info)


def puncta_analysis_func(puncta_masks, image, labeled_cells, data_instance):
    """
    Analyzes sub-cellular objects within segmented cells, calculating properties of puncta such as area, intensity,
    and shape metrics. It associates puncta with their respective cells, computes various statistics
    on the puncta distribution within cells, and updates the cell DataFrame with these statistics.

    Parameters
    ----------
    puncta_masks : numpy.ndarray
        A binary mask indicating the locations of puncta within the image.
    image : numpy.ndarray
        The original greyscale image from which puncta properties are to be measured.
    labeled_cells : numpy.ndarray
        An image with cells labeled by unique integers, used to associate puncta with specific cells.
    data_instance : object
        An object that provides access to a data repository for storing and retrieving analysis results.

    Returns
    -------
    cell_labeled_puncta : numpy.ndarray
        An image where puncta are labeled according to the cell they belong to.

    Notes
    -----
    This function iterates through each labeled cell, identifying and labeling puncta within each cell.
    It calculates puncta properties (area, intensity mean, etc.) and custom metrics such as ellipticity
    and circularity. These metrics, along with cell-specific puncta statistics (like total puncta intensity
    and mean puncta area), are stored in the cell DataFrame. This function is designed to work as part of
    a larger analysis pipeline, specifically after cell segmentation and labeling have been performed.
    """
    # Initialize an array to store puncta labels corresponding to their cells
    cell_labeled_puncta = np.zeros_like(labeled_cells)
    # Define the properties to measure for each object and create an empty list to store additional properties
    properties = ('label', 'area', 'intensity_mean', 'axis_major_length', 'axis_minor_length', 'eccentricity', 'perimeter')
    puncta_prop_list = []
    
    # Iterate over each labeled cell to analyze puncta within
    for label in np.unique(labeled_cells)[1:]:  # Skip the background label (0)
        # Create a binary mask for the current cell
        cell_mask_holder = np.zeros_like(labeled_cells)
        cell_mask_holder[labeled_cells == label] = 1
        cell_mask_holder = cell_mask_holder.astype(bool)

        # Identify puncta within the current cell
        puncta_mask_holder = (puncta_masks * cell_mask_holder).astype(bool)

        # Label puncta in the output array with the cell's label
        cell_labeled_puncta[puncta_mask_holder] = label

        # Create a mask excluding puncta to analyze 'nucleoplasm' (dilute phase) properties
        cell_xor_puncta_mask = cell_mask_holder & ~puncta_mask_holder

        # Label individual puncta within the cell for property measurements
        labeled_puncta = sk.measure.label(puncta_mask_holder)

        # Measure properties of labeled puncta
        df = pd.DataFrame(sk.measure.regionprops_table(labeled_puncta, intensity_image=image, properties=properties))

        # Calculate and add custom puncta properties to the DataFrame (ellipticity, circularity, and micron area)
        df['ellipticity'] = 1 - (df['axis_minor_length'] / df['axis_major_length'])
        circularity = 4 * np.pi * df['area'] / (df['perimeter']**2)
        df['circularity'] = (circularity - np.min(circularity)) / (np.max(circularity) - np.min(circularity))
        df['micron area'] = df['area'] * data_instance.data_repository['microns_per_pixel_sq']
        df['cell label'] = label

        # Compute and add cell-specific puncta statistics to the DataFrame
        puncta_total_int = (df['intensity_mean'] * df['area']).sum()
        num_puncta = np.max(labeled_puncta)
        puncta_area_mean = df['area'].mean() * data_instance.data_repository['microns_per_pixel_sq']
        puncta_area_std = df['area'].std() * data_instance.data_repository['microns_per_pixel_sq']
        puncta_int_dist_mean = df['intensity_mean'].mean()
        mean_ellipticity = df['ellipticity'].mean()

        # Calculate 'nucleoplasm' (dilute phase) statistics excluding puncta
        cell_xor_puncta_int_mean = np.mean(image[cell_xor_puncta_mask])
        cell_xor_puncta_area = np.sum(cell_xor_puncta_mask) * data_instance.data_repository['microns_per_pixel_sq']
        cell_xor_puncta_int_std = np.std(image[cell_xor_puncta_mask])
        cell_xor_puncta_int_total = cell_xor_puncta_int_mean * np.sum(cell_xor_puncta_mask)
        snr_test = puncta_int_dist_mean / cell_xor_puncta_int_std # SNR calculated as mean puncta int/ std of dilute phase
        partition_test = puncta_int_dist_mean / cell_xor_puncta_int_mean # Partition coefficient from mean intensities
        partition_test_total_int = puncta_total_int / cell_xor_puncta_int_total # Standard partition coefficient from total intensities

        # Calculate total cell intensity and compute the spark score
        cell_total_int = np.sum(image[cell_mask_holder])
        spark_score = puncta_total_int / cell_total_int

        cell_df = data_instance.get_data('cell_df') # Retrieve the cell DataFrame from the data instance
        # Update the cell DataFrame with puncta statistics for the current cell
        cell_df.loc[cell_df['label'] == label, 'puncta_micron_area_mean'] = puncta_area_mean if not np.isnan(puncta_area_mean) else 0
        cell_df.loc[cell_df['label'] == label, 'puncta_micron_area_std'] = puncta_area_std if not np.isnan(puncta_area_std) else 0
        cell_df.loc[cell_df['label'] == label, 'puncta_ellipticity_mean'] = mean_ellipticity if not np.isnan(mean_ellipticity) else 0
        cell_df.loc[cell_df['label'] == label, 'puncta_intensity_total'] = puncta_total_int if not np.isnan(puncta_total_int) else 0
        cell_df.loc[cell_df['label'] == label, 'puncta_intensity_dist_mean'] = puncta_int_dist_mean if not np.isnan(puncta_int_dist_mean) else 0
        cell_df.loc[cell_df['label'] == label, 'number_of_puncta'] = num_puncta if not np.isnan(num_puncta) else 0
        cell_df.loc[cell_df['label'] == label, 'cell_xor_puncta_int_mean'] = cell_xor_puncta_int_mean if not np.isnan(cell_xor_puncta_int_mean) else 0
        cell_df.loc[cell_df['label'] == label, 'cell_xor_puncta_int_std'] = cell_xor_puncta_int_std if not np.isnan(cell_xor_puncta_int_std) else 0
        cell_df.loc[cell_df['label'] == label, 'cell_xor_puncta_int_total'] = cell_xor_puncta_int_total if not np.isnan(cell_xor_puncta_int_total) else 0
        cell_df.loc[cell_df['label'] == label, 'cell_xor_puncta_area'] = cell_xor_puncta_area if not np.isnan(cell_xor_puncta_area) else 0
        cell_df.loc[cell_df['label'] == label, 'snr_test'] = snr_test if not np.isnan(snr_test) else 0
        cell_df.loc[cell_df['label'] == label, 'partition_test'] = partition_test if not np.isnan(partition_test) else 0
        cell_df.loc[cell_df['label'] == label, 'partition_test_total_int'] = partition_test_total_int if not np.isnan(partition_test_total_int) else 0
        cell_df.loc[cell_df['label'] == label, 'spark_score'] = spark_score if not np.isnan(spark_score) else 0
        cell_df.loc[cell_df['label'] == label, 'puncta_classifier'] = 1 if num_puncta > 0 else 0
        # Store the updated cell_df back into the data class
        data_instance.set_data('cell_df', cell_df)

        # Append the puncta properties DataFrame to a list for later concatenation
        puncta_prop_list.append(df)

    # Concatenate all puncta properties DataFrames and store in the data instance
    puncta_df = pd.concat(puncta_prop_list, ignore_index=True)
    data_instance.set_data('puncta_df', puncta_df)

    return cell_labeled_puncta


def run_puncta_analysis_func(puncta_mask_layer, image_layer, data_instance, viewer):
    """
    Orchestrates the workflow for analyzing puncta within labeled cells in an image. This function assumes
    that cell segmentation has been previously conducted and labeled cell masks are available. It utilizes
    the puncta mask and the original image to perform puncta analysis, integrating the results with the cell
    data, and then updates the viewer with new layers showing the analysis results.

    Parameters
    ----------
    puncta_mask_layer : napari.layers.Labels
        Layer containing the binary masks of puncta. Each punctum is represented as a distinct region
        in the binary mask.
    image_layer : napari.layers.Image
        Layer containing the original image data used for intensity measurements of puncta and cells.
    data_instance : object
        An instance containing a data repository where analysis results are stored and retrieved.
    viewer : napari.Viewer
        The viewer object used for visualizing analysis results. New layers will be added to this viewer
        to display the outcomes of the puncta analysis.

    Raises
    ------
    ValueError
        If the cell segmentation and puncta masks are not available, an error is raised to indicate the missing data.

    Notes
    -----
    This function directly modifies the viewer by adding new layers to visualize the results of the puncta
    analysis. It requires that the cell segmentation has been previously done to correctly associate puncta
    with their respective cells. It raises warnings if the prerequisites are not met, ensuring the user is
    aware of the expected workflow.
    """

    # Retrieve the image data and puncta masks from the provided layers
    image = image_layer.data
    puncta_masks = puncta_mask_layer.data

    # Labeled Cell Mask is created by the cell analyzer, if it is not in the viewer the function
    # will run on the entire image, however this is not the desired behavior hence we warn the user
    if 'Labeled Cell Mask' in viewer.layers:
        labeled_cells = viewer.layers['Labeled Cell Mask'].data
        # Attempt to retrieve the cell DataFrame from the data repository, if it exists
        cell_df = data_instance.get_data('cell_df', pd.DataFrame())
    else:
        # Warning message to inform the user about the preferred workflow
        napari_show_warning("Warning: This function is intended to be used after running Cell Analyzer.\n"
              "Ignore this warning if you intend on segmenting the entire image.\n"
              "Note that this may cause unintended behavior."
             )
        # Fallback behavior: create a dummy cell mask covering the entire image
        labeled_cells = np.ones_like(image).astype(int)
        labeled_cells[0:2, 0:2] = 0  # Ensure at least two labels exist
        cell_df = pd.DataFrame()  # Initialize an empty DataFrame for cell data

    # Check if prerequisites are met before proceeding
    if labeled_cells is None or puncta_masks is None:
        raise ValueError("Please ensure both cell segmentation and puncta masks are available.")
    if labeled_cells.shape != puncta_masks.shape:
        raise ValueError("Cell and puncta masks must have the same shape.")

    # Perform puncta analysis
    cell_labeled_puncta = puncta_analysis_func(puncta_masks, image, labeled_cells, data_instance)

    # Update the viewer with new layers showing the results of the puncta analysis
    viewer.add_labels(cell_labeled_puncta.astype(int), name="Cell Labeled Puncta Mask")

    # Create a side-by-side image of the original image and an overlay of the segmented puncta mask and the image
    cell_mask = (labeled_cells > 0).astype(bool)
    green_channel = dtype_conversion_func(image, output_bit_depth='uint16')
    green_channel = apply_rescale_intensity(green_channel)
    sbs_overlay = create_overlay_image(green_channel, puncta_masks * cell_mask, alpha=0.65)
    sbs_overlay = dtype_conversion_func(sbs_overlay, output_bit_depth='uint16')
    viewer.add_image(sbs_overlay, name=f"Overlay Image")

    # Retrieve the updated puncta and cell DataFrames from the data repository
    cell_df = data_instance.data_repository['cell_df']
    puncta_df = data_instance.data_repository['puncta_df']
    # Display the puncta and cell statistics in a popup window
    tables_info = [("Cell Statistics", cell_df), ("Condensate Statistics", puncta_df)]
    window_title = "Analysis Results"
    show_dataframes_dialog(window_title, tables_info)
