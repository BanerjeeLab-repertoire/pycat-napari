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

# Notification + debug shim: keeps the coefficients importable with no GUI stack.
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.general_utils import debug_log

# Diagnostics from the most recent colocalization run: threshold sensitivity, the
# calibrated spatial null, and the replication-unit note. Published here so they can be
# stored with the results without changing the analysis function's return signature.
LAST_COLOC_DIAGNOSTICS = {}
# Qt is imported lazily inside the dialog helper below: the colocalization
# COEFFICIENTS in this module are pure numpy/scipy and must be testable headlessly.
try:
    from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QCheckBox,
                                 QPushButton, QLabel)
    _QT_AVAILABLE = True
except Exception:  # pragma: no cover - headless
    _QT_AVAILABLE = False
    class _NoQt:
        def __init__(self, *a, **k):
            raise RuntimeError('This dialog requires PyQt5, which is not available. '
                               'The colocalization coefficients work headlessly.')
    QDialog = QVBoxLayout = QHBoxLayout = QCheckBox = QPushButton = QLabel = _NoQt
from pycat.utils.notify import show_warning as napari_show_error

# The analysis orchestration below calls the coefficients / thresholding / null tests that were split into
# sibling coloc modules (coloc_decomposition); import them from there rather than through the shim (cycle).
from pycat.toolbox.coloc.metrics import (
    pearsons_correlation, spearman_r_calculation, kendall_tau_calculation, weighted_tau_calculation,
    manders_overlap, manders_k1_calculation, manders_k2_calculation, li_intensity_correlation)
from pycat.toolbox.coloc.thresholding import costes_thresholding, manders_threshold_sensitivity
from pycat.toolbox.coloc.nulls import spatial_null_test, perform_costes_test

# Local application imports
# ui_utils pulls in Qt -> imported at CALL time in the functions that display tables.

# N5a glossary — the "M1"-family coloc labels a merged results table can show side by side look alike but
# measure DIFFERENT things. The arithmetic is correct (A3 fixed the channel cross-referencing); this documents
# how the labels differ so a reader is never left guessing. Each output label now carries a provenance suffix
# (added here and in coloc/object_based.py) matching this map:
_M1_FAMILY_LABEL_GLOSSARY = """Coloc "M1"-family output labels, and how they differ:

  'Costes Automatic Thresholded M1/M2 (intensity, auto-threshold)'
      INTENSITY-based Manders coefficient computed AFTER Costes' automatic threshold (the largest threshold at
      which the below-threshold pixels are uncorrelated). M1 = fraction of channel-1 intensity in pixels where
      channel 2 is above ITS Costes threshold; M2 is the symmetric ch2-vs-ch1 quantity.
      Ref: Costes et al., Biophys. J. 86:3993 (2004); Manders et al., J. Microsc. 169:375 (1993).
  "Mander's M1/M2 (object overlap)"   [coloc/object_based.py]
      OBJECT-based overlap fraction from BINARY segmentation masks (not intensities): M1 = fraction of object-1
      area that overlaps any object-2, within the ROI. A morphological quantity, blind to intensity.
  "Mander's k1/k2 value"
      Intensity co-localisation *split* coefficients (k1 = sum(ch1*ch2)/sum(ch1^2)); they weight overlap by the
      OTHER channel's intensity and are NOT bounded to [0,1] like M1/M2. Ref: Manders et al. (1993).
  "Mander's Overlap Coefficient"
      The single symmetric MOC = sum(ch1*ch2)/sqrt(sum(ch1^2)*sum(ch2^2)); one number for both channels, not a
      per-channel split. Ref: Manders et al. (1993).
"""







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
        section_label.setWordWrap(True)
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

    # ── Threshold sensitivity, reported automatically for THRESHOLDED measures ──
    #
    # Manders' M1/M2 (and the Costes-thresholded variants) are DEFINED by a threshold,
    # so a single number silently hides how much the answer hinges on where the cut
    # landed. Measured on synthetic images with a known partial overlap and a +/-30%
    # threshold perturbation, M1 ranged 0.13 to 0.93 on dim, diffuse signal -- i.e. the
    # SAME image supports almost any conclusion depending on the cut. On clean,
    # well-separated data the same test correctly reports a spread of 0.00.
    #
    # This does not produce a better number; it produces an honest one. It is run
    # whenever a threshold-dependent method is selected, so the user cannot report M1
    # without being told whether it is solid.
    _THRESHOLDED = {"Mander's Overlap Coefficient", "Mander's k1 value",
                    "Mander's k2 value", "Costes Automatic Thresholded M1 & M2",
                    "Perform Modified Costes Thresholding"}
    threshold_sensitivity = None
    if _THRESHOLDED & set(selected_methods):
        try:
            threshold_sensitivity = manders_threshold_sensitivity(
                image1, image2, roi_mask)
            if not threshold_sensitivity.get('stable', True):
                napari_show_warning(
                    "Colocalization: " + threshold_sensitivity['verdict'])
        except Exception as _e:
            debug_log("coloc: threshold sensitivity failed", _e)
            threshold_sensitivity = None

    # ── A CALIBRATED significance test, alongside the parametric one ────────────
    #
    # The p-value that ships with each coefficient comes from scipy over FLATTENED
    # PIXELS, and its null assumes the samples are INDEPENDENT. Adjacent pixels in a
    # microscopy image are not -- the PSF correlates them -- so the n in that p-value
    # (65,536 for a 256x256 ROI) is a fiction.
    #
    # Measured on two channels INDEPENDENT BY CONSTRUCTION, each blurred by a realistic
    # PSF, where the truth is "not significant":
    #
    #     pixel p-value (what is reported)        83% FALSE POSITIVES
    #     pixel-scrambling null                   85% FALSE POSITIVES  <- naive fix fails
    #     block-shuffled null (block = corr len)  10%  <- calibrated (target 5%)
    #
    # and the block null still detects 100% of genuine colocalization, so this is not a
    # matter of making everything non-significant.
    #
    # Pixel scrambling is NOT the fix: destroying the autocorrelation makes the null far
    # too narrow, so it fails exactly as badly as the parametric p. The block size is set
    # from the MEASURED correlation length of the image, not guessed.
    spatial_null = None
    _CORRELATION = {"Pearson's R value", "Spearman's R value", "Li's ICQ value",
                    "Kendall's Tau value", "Weighted Tau value"}
    if _CORRELATION & set(selected_methods):
        try:
            spatial_null = spatial_null_test(image1, image2, roi_mask,
                                             n_permutations=200)
            napari_show_warning("Colocalization: " + spatial_null['verdict'])
        except Exception as _e:
            debug_log("coloc: spatial null failed", _e)
            spatial_null = None

    # ── Replication unit ───────────────────────────────────────────────────────
    #
    # Pixels within one cell are not biological replicates, and neither are objects
    # within one cell. A coefficient computed over one ROI is ONE observation, whatever
    # its pixel count. The correct inferential unit is the cell, the field or the
    # experiment -- depending on the question -- and the n for any statistical claim is
    # the number of those, not the number of pixels.
    #
    # This output is indexed by METHOD only: it carries no cell/field/experiment column,
    # so nothing here can distinguish one cell measured well from ten cells measured
    # once. Say so, rather than let a pixel count masquerade as a sample size.
    # Attached to the RESULT rather than fired as a warning on every run: a warning that
    # always fires is a warning nobody reads. This travels with the number, so it is
    # present at the point the number is USED.
    replication_note = (
        "This is ONE observation from ONE ROI. Pixels within a cell are not biological "
        "replicates, and neither are objects within a cell -- a coefficient over one ROI "
        "is a single observation whatever its pixel count. The n for any statistical "
        "claim is the number of CELLS or FIELDS, not the number of pixels. Aggregate "
        "coefficients across cells and test at that level.")

    # Published on the module so the runner can store them WITHOUT changing this
    # function's return signature (it is called from several places).
    global LAST_COLOC_DIAGNOSTICS
    LAST_COLOC_DIAGNOSTICS = dict(
        threshold_sensitivity=threshold_sensitivity,
        spatial_null=spatial_null,
        replication_note=replication_note,
    )

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
        # Manders thresholded coefficients CROSS-REFERENCE the channels: M1 is the fraction of channel-1
        # intensity in pixels where channel 2 is above ITS threshold (and vice versa), restricted to the
        # ROI. The old code summed image1 where image1 > thresh1 (self-referential) -- identically ~1 for
        # any threshold at the noise floor, so it was not a colocalization coefficient at all.
        if roi_mask is not None:
            roi = roi_mask > 0
        else:
            roi = np.ones(image1.shape, dtype=bool)
        if np.isnan(thresh1) or np.isnan(thresh2):
            costes_m1 = costes_m2 = np.nan
        else:
            above2 = roi & (image2 > thresh2)          # channel-2 positive pixels
            above1 = roi & (image1 > thresh1)          # channel-1 positive pixels
            denom1 = np.sum(image1[roi])
            denom2 = np.sum(image2[roi])
            costes_m1 = np.round(np.sum(image1[above2]) / denom1, 4) if denom1 > 0 else np.nan
            costes_m2 = np.round(np.sum(image2[above1]) / denom2, 4) if denom2 > 0 else np.nan
        # Append results to the table
        # Provenance suffix (N5a): distinguishes this INTENSITY-based, auto-thresholded Costes M1/M2 from the
        # object-overlap Manders M1/M2 (coloc/object_based.py) and the thresholded Manders k1/k2 — see the
        # _M1_FAMILY_LABEL_GLOSSARY at the top of this module. A suffix, not a rename: the "...M1"/"...M2" stem
        # is preserved so older saved tables remain recognisable.
        row_m1 = {'Method': 'Costes Automatic Thresholded M1 (intensity, auto-threshold)',
                  'Coefficient': costes_m1, 'P-Value': np.nan}
        row_m2 = {'Method': 'Costes Automatic Thresholded M2 (intensity, auto-threshold)',
                  'Coefficient': costes_m2, 'P-Value': np.nan}
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
    # Extract the image data from the layers. Colocalization is a 2D operation;
    # if an input is a lazy time-series / z-stack, np.asarray(...data) would
    # silently grab frame 0 (the _TiffPageStack trap), so pull the CURRENT VIEWER
    # FRAME explicitly instead (per-frame-over-time is a planned follow-on).
    from pycat.file_io.file_io import layer_is_stack, extract_2d_plane
    _any_stack = (layer_is_stack(image_layer1.data)
                  or layer_is_stack(image_layer2.data)
                  or (roi_mask_layer is not None and layer_is_stack(roi_mask_layer.data)))
    _cur = 0
    if _any_stack:
        try:
            _cur = int(viewer.dims.current_step[0])
        except Exception:
            _cur = 0
        try:
            from napari.utils.notifications import show_warning as _sw
            _sw(f"Pixel-wise coloc: input is a stack — measuring current frame "
                f"(t={_cur}). Step to the frame you want and re-run.")
        except Exception:
            pass
    image1 = extract_2d_plane(image_layer1.data, frame_index=_cur, dtype=None)
    image2 = extract_2d_plane(image_layer2.data, frame_index=_cur, dtype=None)
    roi_mask = (extract_2d_plane(roi_mask_layer.data, frame_index=_cur, dtype=None)
                if roi_mask_layer is not None else None)

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
    __import__("pycat.ui.ui_utils", fromlist=["show_dataframes_dialog"]).show_dataframes_dialog(window_title, tables_info)

    # Store the analysis results in the data instance for future access.
    # Store the diagnostics WITH the coefficients: the whole point is that the number
    # cannot be read without them.
    data_instance.data_repository["PWCCA_diagnostics"] = dict(LAST_COLOC_DIAGNOSTICS)
    data_instance.data_repository["PWCCA_coefficient_df"] = concatenated_table1_data
    data_instance.data_repository["PWCCA_costes_thresh_coefficient_df"] = concatenated_table2_data
    data_instance.data_repository["PWCCA_correlation_matrix_df"] = concatenated_correlation_matrix_table
