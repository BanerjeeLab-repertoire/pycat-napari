"""
Object Based Colocalization Analysis (OBCA) Module for PyCAT 

This module contains functions for calculating colocalization coefficients and distances between objects in two masks. It 
also provides a dialog for selecting analysis methods and orchestrates the object-based colocalization analysis workflow. 
The functions support the calculation of Mander's M1 and M2 coefficients, Jaccard Index, Sorensen-Dice Coefficient, and
object-based distance analysis. The results are displayed in tabulated format for easy interpretation and further analysis.

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
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QHBoxLayout, QPushButton

# Local application imports
from pycat.ui.ui_utils import show_dataframes_dialog


def manders_coloc(image1, mask2, roi_mask):
    """
    Calculates Mander's Colocalization Coefficient (MCC) for two images within a specified region of interest (ROI).
    MCC is a measure of the fraction of total intensity from one image (image1) that overlaps with the pixels
    identified in a second mask (mask2). This coefficient ranges from 0 (no colocalization) to 1 (complete colocalization).

    Parameters
    ----------
    image1 : numpy.ndarray
        A 2D array representing the first image, where the colocalization is to be calculated.
    mask2 : numpy.ndarray
        A 2D binary or label array representing the second image or mask, must have the same dimensions as image1.
    roi_mask : numpy.ndarray, optional
        A boolean array of the same size as image1 and mask2 defining the ROI. If None, the entire image is considered.

    Returns
    -------
    mcc : float
        The Mander's Colocalization Coefficient rounded to 4 decimal places.

    Raises
    ------
    ValueError
        If any of the inputs are not 2D or if their dimensions do not match.

    Note
    -----
    The function utilizes `skimage.measure.label` for calculation if `mask2` is not binary, ensuring it processes
    correctly labeled regions for accurate MCC calculation.
    """
    
    if image1.ndim != 2:
        raise ValueError('Image must be 2D for Manders Colocalization Coefficient calculation.')
    
    # Utilizing skimage to calculate the Mander's Colocalization Coefficient
    mcc = sk.measure.manders_coloc_coeff(image1, mask2, roi_mask)

    # Returning the coefficient rounded to 4 decimal places
    return np.round(mcc, 4)


def run_manders_coloc(image_layer1, mask_layer2, roi_mask_layer, data_instance):
    """
    Orchestrates the calculation of Mander's Colocalization Coefficient (MCC) using provided image data layers,
    potentially within a specified region of interest (ROI), and updates the data instance with the results.

    This function retrieves image data from provided layers, validates their shapes, executes the MCC calculation,
    and displays the result in a tabulated format. It also stores the results in the provided data instance for future
    reference.

    Parameters
    ----------
    image_layer1 : napari.layers.Image
        The first image layer containing image data for the MCC calculation. Expected to have a .data attribute with 2D image information.
    mask_layer2 : napari.layers.Labels
        The second image layer containing a mask or another image as input for MCC. Expected to have a .data attribute.
    roi_mask_layer : napari.layers.Labels, optional
        An optional layer specifying the ROI for the MCC calculation. If None, the entire image is considered.
        If provided, expected to be a boolean mask with the same dimensions as image1 and mask2.
    data_instance : object
        An object (e.g., a data handling class instance) that stores the results. This object should have a
        'data_repository' attribute where the MCC results are saved.

    Raises
    ------
    ValueError
        If the input images or the ROI mask (if provided) do not have matching shapes, indicating they cannot be
        directly compared or analyzed together.

    Notes
    -----
    The MCC calculation is sensitive to the exact alignment and matching dimensions of the input layers.
    Results are visualized in a dialog window and stored within the provided data_instance object under
    the key 'manders_coloc_df'.
    """
    
    # Extracting the image data from the layers
    image1 = image_layer1.data
    mask2 = mask_layer2.data
    roi_mask = roi_mask_layer.data.astype(bool) if roi_mask_layer is not None else None

    # Validating the shapes of the input images and the ROI mask, if provided
    if image1.shape != mask2.shape:
        raise ValueError("Input image and mask must have the same shape.")

    if roi_mask is not None and roi_mask.shape != image1.shape:
        raise ValueError("ROI mask must have the same shape as the input images.")
    
    # Performing the MCC calculation
    mcc = manders_coloc(image1, mask2, roi_mask)

    # Preparing the results in a DataFrame for display and storage
    mcc_df = pd.DataFrame({'Manders Colocalization Coefficient': [mcc]})

    # Preparing the data for display in a table
    tables_info = [("Manders Colocalization Coefficient", mcc_df)]
    window_title = "Manders Colocalization Coefficient"
    show_dataframes_dialog(window_title, tables_info)

    # Storing the results in the data instance for later access
    data_instance.data_repository["manders_coloc_df"] = mcc_df


def manders_m1_calculation(mask1, mask2, roi_mask):
    """
    Calculates Mander's M1 coefficient, which measures the overlap of mask1 over mask2, normalized by the total
    intensity of mask1. The calculation can be restricted to a region of interest (ROI) if an ROI mask is provided.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first mask array.
    mask2 : numpy.ndarray
        The second mask array.
    roi_mask : numpy.ndarray, optional
        A mask defining the ROI for the calculation. Must be the same shape as mask1 and mask2.

    Returns
    -------
    m1 : float 
        Mander's M1 coefficient, rounded to 4 decimal places. Returns 0.0 if the sum of mask1 within the ROI is 0.

    Notes
    -----
    This function focuses the M1 calculation within the ROI if provided. If the ROI mask is not specified, the entire
    area of the masks is considered for the calculation.
    """
    # Apply ROI mask if provided
    if roi_mask is not None:
        mask1 = mask1 * roi_mask
        mask2 = mask2 * roi_mask

    # Avoid division by zero by returning 0.0 if mask1 sums to zero
    if np.sum(mask1) == 0:
        return 0.0  
    
    m1 = np.sum(mask1 * mask2) / np.sum(mask1)  # Calculate M1

    return np.round(m1, 4)  # Return M1 rounded to 4 decimal places


def manders_m2_calculation(mask1, mask2, roi_mask):
    """
    Calculates Mander's M2 coefficient, which measures the overlap of mask2 over mask1, normalized by the total
    intensity of mask2. The calculation can be restricted to a region of interest (ROI) if an ROI mask is provided.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first mask array.
    mask2 : numpy.ndarray
        The second mask array.
    roi_mask : numpy.ndarray, optional
        A mask defining the ROI for the calculation. Must be the same shape as mask1 and mask2.

    Returns
    -------
    m2 : float 
        Mander's M2 coefficient, rounded to 4 decimal places. Returns 0.0 if the sum of mask2 within the ROI is 0.

    Notes
    -----
    This function focuses the M2 calculation within the ROI if provided. If the ROI mask is not specified, the entire
    area of the masks is considered for the calculation.
    """
    # Apply ROI mask if provided
    if roi_mask is not None:
        mask1 = mask1 * roi_mask
        mask2 = mask2 * roi_mask

    # Avoid division by zero by returning 0.0 if mask2 sums to zero
    if np.sum(mask2) == 0:
        return 0.0 
    
    m2 = np.sum(mask1 * mask2) / np.sum(mask2)  # Calculate M2

    return np.round(m2, 4)  # Return M2 rounded to 4 decimal places


def jaccard_index_calculation(mask1, mask2, roi_mask):
    """
    Calculates the Jaccard Index, a measure of the overlap between two masks normalized by their union.
    The calculation can be restricted to a region of interest (ROI) if an ROI mask is provided.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first mask array.
    mask2 : numpy.ndarray
        The second mask array.
    roi_mask : numpy.ndarray, optional
        A mask defining the ROI for the calculation. Must be the same shape as mask1 and mask2.

    Returns
    -------
    jaccard_index : float
        The Jaccard Index, rounded to 4 decimal places. Returns np.nan if both masks sum to zero within the ROI.

    Notes
    -----
    This function focuses the Jaccard Index calculation within the ROI if provided. If the ROI mask is not specified,
    the entire area of the masks is considered for the calculation.
    """
    # Apply ROI mask if provided
    if roi_mask is not None:
        mask1 = mask1 * roi_mask
        mask2 = mask2 * roi_mask

    # Return np.nan if both masks sum to zero, indicating no overlap
    if np.sum(mask1) == 0 and np.sum(mask2) == 0:
        return np.nan 
    
    # Calculate Jaccard Index
    jaccard_index = np.sum(mask1 * mask2) / np.sum((mask1 + mask2) > 0)

    return np.round(jaccard_index, 4)  # Return Jaccard Index rounded to 4 decimal places


def sorensen_dice_coefficient_calculation(mask1, mask2, roi_mask):
    """
    Calculates the Sorensen-Dice coefficient, a measure of the overlap between two masks normalized by the size
    of each mask. The calculation can be restricted to a region of interest (ROI) if an ROI mask is provided.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first mask array.
    mask2 : numpy.ndarray
        The second mask array.
    roi_mask : numpy.ndarray, optional
        A mask defining the ROI for the calculation. Must be the same shape as mask1 and mask2.

    Returns
    -------
    dice_coefficient : float
        The Sorensen-Dice coefficient, rounded to 4 decimal places. Returns np.nan if both masks sum to zero within the ROI.

    Notes
    -----
    This function focuses the Dice coefficient calculation within the ROI if provided. If the ROI mask is not specified,
    the entire area of the masks is considered for the calculation.
    """
    # Apply ROI mask if provided
    if roi_mask is not None:
        mask1 = mask1 * roi_mask
        mask2 = mask2 * roi_mask

    # Return np.nan if both masks sum to zero, indicating no overlap
    if np.sum(mask1) == 0 and np.sum(mask2) == 0:
        return np.nan
    
    # Calculate Dice Coefficient
    dice_coefficient = 2 * np.sum(mask1 * mask2) / (np.sum(mask1) + np.sum(mask2))

    return np.round(dice_coefficient, 4)  # Return Dice coefficient rounded to 4 decimal places


def calculate_centroid_distance(region1, region2):
    """
    Calculates the Euclidean distance between the centroids of two regions. Each region is expected to have a
    'centroid' attribute which represents the geometric center of the region.

    Parameters
    ----------
    region1 : object
        The first region, an object that must have a 'centroid' attribute.
    region2 : object
        The second region, an object that must have a 'centroid' attribute.

    Returns
    -------
    float
        The Euclidean distance between the centroids of the two regions.
    """
    # Calculate and return the Euclidean distance between the two centroids.
    return np.linalg.norm(np.array(region1.centroid) - np.array(region2.centroid))


def process_single_pairs(single_pairs_df, labels1, labels2):
    """
    Processes single pairs of regions from two sets of labels to calculate the distances between their centroids.
    This function iterates through a DataFrame containing unique pairings of labels and calculates the centroid
    distance for each pair using the 'calculate_centroid_distance' function.

    Parameters
    ----------
    single_pairs_df : pandas.DataFrame
        A DataFrame containing rows of unique pairings between masks. Expected to have columns 'Mask 1 Labels' and
        'Mask 2 Labels' indicating labels from labels1 and labels2 respectively.
    labels1 : numpy.ndarray
        Labeled image where each label corresponds to a distinct region in the first mask.
    labels2 : numpy.ndarray
        Labeled image where each label corresponds to a distinct region in the second mask.

    Returns
    -------
    distances : list
        A list of distances between centroids of each pair of regions.
    """
    # Precompute region properties for both sets of labels.
    regions1 = sk.measure.regionprops(labels1)
    regions2 = sk.measure.regionprops(labels2)

    distances = []  # Initialize a list to store distances.
    # Iterate over each row in the DataFrame to calculate distances.
    for _, row in single_pairs_df.iterrows():
        # Fetch regions based on labels.
        region1 = regions1[int(row['Mask 1 Labels']) - 1]
        region2 = regions2[int(row['Mask 2 Labels']) - 1]
        # Calculate distance and append to the list.
        distance = calculate_centroid_distance(region1, region2)
        distances.append(distance)
    return distances


def process_multiple_pairs(multiple_pairs_df, labels1, labels2):
    """
    Processes multiple pairs of regions to calculate the average distances between their centroids. This function
    is particularly useful for cases where multiple pairings exist for a single label in the first mask. It
    calculates an average distance for each unique label in mask1 across all its pairings.

    Parameters
    ----------
    multiple_pairs_df : pandas.DataFrame
        A DataFrame containing rows of pairings between masks, potentially including multiple pairs for a single
        label from mask1. Expected to have a column 'Mask 1 Labels'.
    labels1 : numpy.ndarray
        Labeled image where each label corresponds to a distinct object or region.
    labels2 : numpy.ndarray
        Labeled image where each label corresponds to a distinct object or region.

    Returns
    -------
    distances : list
        A list of average distances for each unique label in mask1 across its pairings. The averages are calculated
        from all pairings where the label from mask1 is involved.
    """
    # Precompute region properties for both sets of labels.
    regions1 = sk.measure.regionprops(labels1)
    regions2 = sk.measure.regionprops(labels2)

    distances = []  # Initialize a list to store average distances.
    # Iterate over each unique label in mask1.
    for label1 in multiple_pairs_df['Mask 1 Labels'].unique():
        # Filter pairs for the current label.
        relevant_pairs = multiple_pairs_df[multiple_pairs_df['Mask 1 Labels'] == label1]
        temp_distances = []  # Temporarily store distances for the current label.
        for _, row in relevant_pairs.iterrows():
            # Fetch regions based on labels.
            region1 = regions1[int(label1) - 1]
            region2 = regions2[int(row['Mask 2 Labels']) - 1]
            # Calculate distance and add to the temporary list.
            temp_distances.append(calculate_centroid_distance(region1, region2))
        # Calculate and append the average distance for the current label.
        if temp_distances:
            distances.append(np.mean(temp_distances))
    return distances


def categorize_pairings(overlap_df):
    """
    Categorizes the pairings of labels between two masks into single pairs and multiple pairs based on the uniqueness
    of the pairings within a provided DataFrame. Single pairs are unique pairings where each label from mask1 and mask2
    appears only once. Multiple pairs are pairings where at least one label from either mask appears in multiple pairings.

    Parameters
    ----------
    overlap_df : pandas.DataFrame
        A DataFrame containing overlap information between two masks. It should have columns 'Mask 1 Labels',
        'Mask 2 Labels', and 'Overlap Area'. Rows with missing 'Mask 2 Labels' are excluded from the analysis.

    Returns
    -------
    single_pairs_df : pandas.DataFrame
        A DataFrame containing only single pairs where each label pair is unique.
    multiple_pairs_df : pandas.DataFrame    
        A DataFrame containing multiple pairs where one or more labels appear in more than one pairing.

    Notes
    -----
    The function first eliminates any rows in the overlap_df that have missing values in 'Mask 2 Labels', which
    could represent incomplete pairings. It then identifies single pairs by checking for unique occurrences of
    'Mask 1 Labels' and 'Mask 2 Labels'. All remaining pairings that do not fit into single pairs are classified as
    multiple pairs. This categorization helps in subsequent analyses where the distinction between unique and
    repeated label pairings affects the computation of distances or other metrics.
    """
    # Filter out rows with np.nan in 'Mask 2 Labels'.
    overlap_df = overlap_df.dropna(subset=['Mask 2 Labels'])

    # Identify unique pairings in 'Mask 1 Labels'.
    unique_mask1 = overlap_df[overlap_df['Mask 1 Labels'].isin(overlap_df['Mask 1 Labels'].drop_duplicates(keep=False))]

    # Further refine to get single pairs by ensuring 'Mask 2 Labels' are also unique.
    single_pairs_df = unique_mask1[unique_mask1['Mask 2 Labels'].isin(unique_mask1['Mask 2 Labels'].drop_duplicates(keep=False))]

    # Identify multiple pairs as those not included in the single pairs.
    multiple_pairs_df = overlap_df[~overlap_df.isin(single_pairs_df).all(axis=1)]

    return single_pairs_df, multiple_pairs_df


def object_based_distance_analysis(mask1, mask2, roi_mask):
    """
    Performs an object-based distance analysis between two binary masks, optionally within a specified region of
    interest (ROI). This analysis involves labeling the objects in both masks, calculating overlaps, and computing
    distances between centroids of overlapping objects. Additionally, it calculates the percentage of objects in
    either mask that do not overlap with any object in the other mask.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first binary mask where objects are to be analyzed.
    mask2 : numpy.ndarray
        The second binary mask where objects are to be analyzed.
    roi_mask : numpy.ndarray, optional
        A binary mask defining the region of interest for the analysis. If provided, the analysis is restricted
        to this region.

    Returns
    -------
    final_mean_distance : float
        The mean distance between corresponding objects in the two masks, averaged across all pairs of overlapping
        objects. Returns np.nan if no valid comparisons can be made.
    percentage_non_coincident : float
        The percentage of objects in either mask that do not overlap with any object in the other mask, expressed
        as a decimal between 0 and 1.

    Notes
    -----
    The function processes masks by first applying the ROI mask if provided. It then labels each distinct object
    in the masks. Overlaps between objects from different masks are identified, and distances between their centroids
    are calculated. The function handles single and multiple overlapping pairs differently, using specialized
    functions to calculate distances for these scenarios.

    If either mask is entirely zero or if no objects overlap, the function will return np.nan for the mean distance
    and 1.0 for the percentage of non-coinciding objects.
    """
    # Apply ROI mask if provided.
    if roi_mask is not None:
        mask1 = mask1 * roi_mask
        mask2 = mask2 * roi_mask

    # Return NaN if either mask is entirely zero, indicating no objects to compare.
    if np.sum(mask1) == 0 or np.sum(mask2) == 0:
        return np.nan, 1.0  

    # Label the objects in both masks.
    labels1 = sk.measure.label(mask1)
    labels2 = sk.measure.label(mask2)

    # Unique labels in masks
    unique_labels1 = np.unique(labels1)[1:]
    unique_labels2 = np.unique(labels2)[1:]

    # Dictionary to track overlap between objects.
    overlap_dict = {'Mask 1 Labels': [], 'Mask 2 Labels': [], 'Overlap Area': []}

    # Analyze overlaps between objects in the two masks.
    for obj_id in unique_labels1:
        holder_mask = labels1 == obj_id
        for obj_id2 in unique_labels2:
            overlap = holder_mask * (labels2 == obj_id2)
            if np.sum(overlap) > 0:
                overlap_dict['Mask 1 Labels'].append(obj_id)
                overlap_dict['Mask 2 Labels'].append(obj_id2)
                overlap_dict['Overlap Area'].append(np.sum(overlap))

    # Convert overlap data to DataFrame.
    overlap_df = pd.DataFrame(overlap_dict)

    # Unique labels in overlap DataFrame
    unique_overlap_labels1 = overlap_df['Mask 1 Labels'].unique()
    unique_overlap_labels2 = overlap_df['Mask 2 Labels'].unique()

    # Non-coincident labels
    non_coincident_labels1 = set(unique_labels1) - set(unique_overlap_labels1)
    non_coincident_labels2 = set(unique_labels2) - set(unique_overlap_labels2)

    # Count of non-coincident objects
    count_non_coincident1 = len(non_coincident_labels1)
    count_non_coincident2 = len(non_coincident_labels2)
    non_coincident_objects = count_non_coincident1 + count_non_coincident2

    # Calculate the percentage of non-coincident objects.
    percentage_non_coincident = (non_coincident_objects) / (np.max(labels1)+np.max(labels2))

    # Categorize and process pairings.
    single_pairs_df, multiple_pairs_df = categorize_pairings(overlap_df)
    single_distances = process_single_pairs(single_pairs_df, labels1, labels2)
    multiple_distances = process_multiple_pairs(multiple_pairs_df, labels1, labels2)

    # Combine distances and calculate the final mean distance.
    all_distances = single_distances + multiple_distances
    if len(all_distances) == 0:
        final_mean_distance = np.nan
    else:
        final_mean_distance = np.mean(all_distances)

    return final_mean_distance, percentage_non_coincident


class obcaDialog(QDialog):
    """
    A dialog class for selecting correlation and distance analysis methods in an object-based colocalization
    analysis (OBCA) context. It provides a user-friendly interface where users can choose from a predefined list
    of methods, organized into sections for different types of analyses.

    Attributes
    ----------
    checkboxes : list of QCheckBox
        A list containing all the QCheckBox widgets in the dialog, representing available analysis methods.

    Parameters
    ----------
    parent : QWidget, optional
        The parent widget of this dialog, typically the main window of the application. Defaults to None.
    """

    def __init__(self, parent=None):
        """
        Initializes the obcaDialog with sections for selecting various correlation and distance analysis methods,
        and includes buttons for selecting or deselecting all options.

        Parameters
        ----------
        parent : QWidget, optional
            The parent widget of the dialog. Defaults to None.
        """
        super().__init__(parent)
        self.setWindowTitle('Select Correlation Analysis Methods')
        self.checkboxes = []

        # Main layout of the dialog
        self.layout = QVBoxLayout(self)

        # Section for Object-Based Colocalization Analysis (OBCA) methods
        obca_methods = ["Mander's M1 value", "Mander's M2 value", "Jaccard Index", "Sorensen-Dice Coefficient"]
        self.layout.addLayout(self._create_section_layout("Object Based Colocalization Analysis", obca_methods))

        # Section for Distance Analysis methods
        self.layout.addLayout(self._create_section_layout("Distance Analysis", ["Calculate Distance Between Objects"]))

        # Layout for Select All and Deselect All buttons
        self.layout.addLayout(self._create_selection_buttons())

        # Layout for OK and Cancel action buttons
        self.layout.addLayout(self._create_action_buttons())

    def _create_section_layout(self, title, options):
        """
        Creates a QVBoxLayout containing checkboxes for a given section, each labeled according to the options
        provided.

        Parameters
        ----------
        title : str
            The title of the section.
        options : list of str
            A list of strings that describe each checkbox option within the section.

        Returns
        -------
        QVBoxLayout
            A layout containing a label for the section and one checkbox per option.
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
        Creates a QHBoxLayout with 'Select All' and 'Deselect All' buttons to facilitate bulk selection of
        analysis methods.

        Returns
        -------
        QHBoxLayout
            A layout containing the 'Select All' and 'Deselect All' buttons.
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
        Creates a QHBoxLayout with 'OK' and 'Cancel' buttons. The 'OK' button saves the selection and closes
        the dialog, whereas the 'Cancel' button discards any changes and closes the dialog.

        Returns
        -------
        QHBoxLayout
            A layout containing the 'OK' and 'Cancel' buttons.
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
        """Selects all checkboxes within the dialogue."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(True)

    def deselect_all(self):
        """Deselects all checkboxes within the dialogue."""
        for checkbox in self.checkboxes:
            checkbox.setChecked(False)

    def get_selected_methods(self):
        """
        Retrieves a list of the analysis methods selected by the user.

        Returns
        -------
        list of str
            A list of the names of the selected analysis methods.
        """
        return [checkbox.text() for checkbox in self.checkboxes if checkbox.isChecked()]


def object_based_colocalization_analysis(object_mask1, object_mask2, roi_mask, method_selections, data_instance):
    """
    Performs object-based colocalization analysis on two object masks, optionally within a specified region of interest (ROI),
    based on selected methods. This analysis can include various correlation coefficients and distance measurements
    between objects identified in the masks.

    Parameters
    ----------
    object_mask1 : numpy.ndarray
        The first object mask for analysis, where each distinct object is labeled with a unique identifier.
    object_mask2 : numpy.ndarray
        The second object mask for analysis, structured similarly to the first mask.
    roi_mask : numpy.ndarray, optional
        An optional mask defining the region of interest for the analysis. If provided, the analysis is restricted
        to this region.
    method_selections : list of str
        A list of selected methods for colocalization analysis, which can include various correlation coefficients and distance calculations.
    data_instance : object
        An instance containing data repositories, which should include 'microns_per_pixel_sq' for distance calculations if distance analysis is selected.

    Returns
    -------
    table1_data : pandas.DataFrame
        A DataFrame containing the results of the selected correlation coefficient analyses.
    table2_data : pandas.DataFrame
        A DataFrame containing the results of the distance analysis, including the mean distance between objects in pixels and microns,
        along with the percentage of non-coincident objects.

    Notes
    -----
    The function processes each selected method for correlation coefficients and performs distance analysis if selected.
    For distance measurements, the function uses 'microns_per_pixel_sq' from the data instance to convert distance from pixels to microns,
    providing a practical measure for biological analysis. The output tables encapsulate the results of the selected analyses,
    facilitating further statistical processing or visualization.
    """
    
    selected_methods = method_selections.copy()

    # Mapping of method names to their respective function implementations.
    method_functions = {
        "Mander's M1 value": manders_m1_calculation,
        "Mander's M2 value": manders_m2_calculation,
        "Jaccard Index": jaccard_index_calculation,
        "Sorensen-Dice Coefficient": sorensen_dice_coefficient_calculation,
        "Calculate Distance Between Objects": object_based_distance_analysis
    }

    # Initialize variables for output data.
    table1_data = None
    table2_data = None  # Used only if distance analysis is selected.

    # Check if Distance Analysis is one of the selected methods.
    distance_analysis_selected = "Calculate Distance Between Objects" in selected_methods
    if distance_analysis_selected:
        # Remove it from the list to process correlation coefficients separately.
        selected_methods.remove("Calculate Distance Between Objects")

    # Process each selected method for correlation coefficients.
    table1_data = process_obca_methods(selected_methods, method_functions, object_mask1, object_mask2, roi_mask)

    # Perform distance analysis if selected.
    if distance_analysis_selected:
        mean_distance, percent_noncoincident = method_functions["Calculate Distance Between Objects"](object_mask1, object_mask2, roi_mask)
        microns_per_pixel_sq = data_instance.data_repository['microns_per_pixel_sq']
        microns_per_pixel = np.sqrt(microns_per_pixel_sq)

        # Compile results into a DataFrame.
        table2_data = {
            'Metric': [
                'Mean Distance Between Objects (px)',
                'Mean Distance Between Objects (um)',
                'Percent Non-Coincident Objects'
            ],
            'Value': [
                mean_distance,
                mean_distance * microns_per_pixel,
                percent_noncoincident
            ]
        }
        table2_data = pd.DataFrame(table2_data)

    return table1_data, table2_data


def process_obca_methods(selected_methods, method_functions, mask1, mask2, roi_mask):
    """
    Processes a list of selected colocalization analysis methods for two object masks, potentially within a specified
    region of interest (ROI). Each method is applied to the masks, and the results are compiled into a DataFrame.

    Parameters
    ----------
    selected_methods : list of str
        A list of the names of methods to be applied. These methods should be keys in the `method_functions` dictionary.
    method_functions : dict
        A dictionary mapping each method name to a function that implements the corresponding analysis. Each function
        is expected to take two masks (and optionally an ROI mask) and return a single numerical result (coefficient).
    mask1 : numpy.ndarray
        The first object mask for analysis, where each distinct object is labeled with a unique identifier.
    mask2 : numpy.ndarray
        The second object mask for analysis, structured similarly to the first mask.
    roi_mask : numpy.ndarray, optional
        An optional mask defining the region of interest for the analysis. If provided, each method's analysis will be
        restricted to this area.

    Returns
    -------
    data_table1 : pandas.DataFrame
        A DataFrame with columns 'Method' and 'Coefficient'. Each row corresponds to one of the selected methods and
        contains the name of the method along with the calculated coefficient resulting from its application to the
        masks. The coefficients are derived from each method's specific analysis procedure, which may include 
        measurements of overlap, distance, or other statistical correlations.

    Notes
    -----
    The function iterates over the list of selected methods. For each method, if it is included in the `method_functions`
    dictionary, the corresponding function is called with the masks and the ROI mask if provided. The results are 
    then collected in a DataFrame which is returned. This DataFrame serves as a concise summary of the results from
    applying the selected colocalization analysis methods to the input masks.
    """
    selected_methods_copy = selected_methods.copy()
    data_table1 = pd.DataFrame(columns=['Method', 'Coefficient'])

    # Iterate through the selected methods, applying each to the masks.
    for method in selected_methods_copy:
        if method in method_functions:
            # Calculate the coefficient for the current method.
            coeff = method_functions[method](mask1, mask2, roi_mask)
            # Append the result to the DataFrame.
            row = {'Method': method, 'Coefficient': coeff}
            data_table1 = pd.concat([data_table1, pd.DataFrame([row])], ignore_index=True)

    return data_table1

def run_obca(mask_layer1, mask_layer2, roi_mask_layer, data_instance):
    """
    Executes the Object-Based Colocalization Analysis (OBCA) using two provided image mask layers and an optional
    Region of Interest (ROI) mask layer. This function manages the workflow from user interaction for method
    selection to processing the data and updating the provided data instance with analysis results.

    Parameters
    ----------
    mask_layer1 : MaskLayer
        The first image mask layer for analysis, containing image data where objects are presumably labeled.
    mask_layer2 : MaskLayer
        The second image mask layer for analysis, structured similarly to the first.
    roi_mask_layer : MaskLayer, optional
        An optional layer containing the ROI mask data. If provided, analysis is restricted to this region.
    data_instance : DataInstance
        The data instance in which the analysis results will be stored. It should have a 'data_repository' attribute
        to store DataFrames of results.

    Raises
    ------
    ValueError
        If the shapes of mask_layer1 and mask_layer2 do not match, or if the shape of roi_mask_layer does not match
        the shapes of mask_layer1 and mask_layer2 when roi_mask_layer is not None.

    Notes
    -----
    The function initiates by displaying a dialog for the user to select methods for analysis. Depending on the user's
    selections, it processes the masks using the chosen colocalization analysis methods. Results are compiled into
    DataFrames and are displayed to the user. Additionally, these results are stored in the data instance for future
    reference. The function does not return any value but ensures the results are accessible via the data instance.
    """
    # Extract data from layers.
    mask1 = mask_layer1.data
    mask2 = mask_layer2.data
    roi_mask = roi_mask_layer.data if roi_mask_layer is not None else None

    # Validate input masks' shapes.
    if mask1.shape != mask2.shape:
        raise ValueError("Input masks must have the same shape.")
    if roi_mask is not None and roi_mask.shape != mask1.shape:
        raise ValueError("ROI mask must have the same shape as the input masks.")

    # Display dialog for user to select methods for analysis.
    dialog = obcaDialog()
    result = dialog.exec_()

    if result == QDialog.Accepted:
        # User accepted the dialog; proceed with the selected methods.
        method_selections = dialog.get_selected_methods()
    elif result == QDialog.Rejected:
        # User rejected the dialog; exit the function.
        return

    # Initialize empty DataFrames for storing analysis results
    concatenated_table1_data = pd.DataFrame()
    concatenated_table2_data = pd.DataFrame()


    label_flag = False  # Flag to check if the ROI mask has labels beyond binary
    # Determine if the roi_mask is binary or labeled and proceed accordingly.
    if roi_mask is not None and np.unique(roi_mask).size > 2:  # Labeled mask
        unique_labels = np.unique(roi_mask)[1:]  # Exclude background
        
        for label in unique_labels:
            specific_roi_mask = (roi_mask == label).astype(bool)

            if label > 1:
                label_flag = True

            # Running the analysis for each label in the labeled ROI mask
            table1_data, table2_data = object_based_colocalization_analysis(mask1, mask2, specific_roi_mask, method_selections, data_instance)

            # Merging or concatenating analysis results into the final tables
            if label_flag:
                # Merging results when multiple labels are present
                concatenated_table1_data = pd.merge(concatenated_table1_data, table1_data, on='Method', how='outer', suffixes=('', f'_{label}')) if table1_data is not None else concatenated_table1_data
                concatenated_table2_data = pd.merge(concatenated_table2_data, table2_data, on='Metric', how='outer', suffixes=('', f'_{label}')) if table2_data is not None else concatenated_table2_data
            else:
                # Concatenating results for the first label
                concatenated_table1_data = pd.concat([concatenated_table1_data, table1_data], ignore_index=True)
                concatenated_table2_data = pd.concat([concatenated_table2_data, table2_data], ignore_index=True)
    else:
        # Handling binary mask or no mask scenarios
        roi_mask = roi_mask.astype(bool) if roi_mask is not None else None
        table1_data, table2_data = object_based_colocalization_analysis(mask1, mask2, roi_mask, method_selections, data_instance)
        # Concatenate the results
        concatenated_table1_data = pd.concat([concatenated_table1_data, table1_data], ignore_index=True)
        concatenated_table2_data = pd.concat([concatenated_table2_data, table2_data], ignore_index=True)

    # Finalizing the tables by setting indexes and rounding
    if not concatenated_table1_data.empty:
        concatenated_table1_data.set_index('Method', inplace=True)
        concatenated_table1_data = concatenated_table1_data.round(4)
    else:
        concatenated_table1_data = None
    if not concatenated_table2_data.empty:
        concatenated_table2_data.set_index('Metric', inplace=True)
        concatenated_table2_data = concatenated_table2_data.round(4)
    else:
        concatenated_table2_data = None

    # Packaging the results for presentation
    tables_info = [
        ("Correlation Coefficient Table", concatenated_table1_data),
        ("Distance Analysis Table", concatenated_table2_data)
    ]

    # Displaying the results in a dialog
    window_title = "Object-Based Colocalization Analysis"
    show_dataframes_dialog(window_title, tables_info)

    # Storing the analysis results in the data instance
    data_instance.data_repository["OBCA_coefficient_df"] = concatenated_table1_data
    data_instance.data_repository["OBCA_distance_df"] = concatenated_table2_data




