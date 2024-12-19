Features
========

PyCAT is a comprehensive Python toolkit for analyzing biomolecular condensates and bioimages. It offers robust analysis methods, customizable pipelines, and visualization tools for in-depth biological insights.
This page is a reference for features and tools available in PyCAT.

Analysis Methods Available
--------------------------
PyCAT offers a wide range of analysis methods for quantifying bio-images.

**Region Properties Analysis** : Measure area, intensity, shape, texture, and other features of ROIs (regions of interest) like condensates, cells, etc.

**Object-Based Colocalization Analysis** : Object ROI mask comparisons for segmented object masks

* Mander's M1 value
* Mander's M2 value
* Jaccard Index
* Sorensen-Dice Coefficient
* Calculate Distance Between Objects
* Percent of Objects Non-coincident
* Mander's Colocalization Coefficient

**Pixel-Wise Correlation Analysis** : Correlation Analysis for images without distinctive/segmentable objects

* Pearson's R value
* Spearman's R value
* Kendall's Tau value
* Weighted Tau value
* Li's ICQ value
* Mander's Overlap Coefficient
* Mander's k1 value
* Mander's k2 value

**Modified Costes Analysis** : Automatically generated thresholds and statistical significance testing for correlation analyses
    
* Costes Automatic Thresholded M1 M2
* Calculate Costes Significance
* Perform Modified Costes Thresholding

**Correlation Function Analysis** : Auto- and Cross-Correlation Functions with Gaussian fits

* Fitted gaussian parameters (mu, sigma, etc)

Analysis Pipelines
------------------
PyCAT also provides several pre-configured analysis pipelines for common use cases.

**Condensate Analysis** : Tailored analysis for in-cellulo condensates

**Colocalization Analysis** : Object-based and pixel-wise correlation pipelines

**General ROI Analysis** : Exploratory pipeline with region property measurements

**Fibril Analysis** : Analyze beta-amyloid fibers and fibril structures



Toolbox Reference
-----------------
This reference table lists all of the individual functions available in the PyCAT toolbox along with brief descriptions.

.. list-table:: Toolbox Functions
   :widths: 30 70
   :header-rows: 1

   * - Function Category
     - Description
   * - **Image Processing Tools**
     - Tools for image pre-processing, enhancement, and noise reduction.
   * - Pre-processing
     - Applies top hat, LoG, and other image processing filters.
   * - Rescale Intensity
     - Rescales image intensity values to a desired range.
   * - Invert Intensity
     - Inverts pixel intensity values (e.g., dark to bright).
   * - Upscale Image
     - Increases image resolution while preserving structural features.
   * - RB Gauss BG removal
     - Rolling ball and Gaussian background removal.
   * - BG removal with edge enhancement
     - Removes background while enhancing peaks and edges through Gabor filtering. 
   * - WBNS
     - Wavelet Background and Noise Subtraction. 
   * - Wavelet noise reduction
     - Reduces noise using wavelet transforms for multi-scale denoising.
   * - Bilateral noise reduction
     - Smoothens images while preserving edges through bilateral filtering.
   * - CLAHE
     - Contrast-Limited Adaptive Histogram Equalization for local contrast enhancement.
   * - Peak and Edge enhancement
     - Enhances image peaks and edges via Gabor and LoG filtering.
   * - Morphological Gaussian filter
     - Smoothens images while applying morphological opening and Gaussian filtering.
   * - LoG enhancement
     - Laplacian-of-Gaussian enhancement for edge detection.
   * - Deblur by Pixel Reassignment
     - An advanced PSF and deblurring tool which upscales images in the process.


.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - **Image Segmentation Tools**
     - Tools for segmenting images into meaningful regions of interest (ROIs).
   * - Local Thresholding functions
     - Performs segmentation using local thresholding algorithms.
   * - Cellpose segmentation
     - Uses Cellpose, a deep learning-based method for cell segmentation.
   * - Random Forest classifier segmentation
     - Segments images using a Random Forest classifier model.
   * - Felzenszwalb segmentation and RAG region merging
     - Segments images using graph-based Felzenszwalb algorithms and merges regions.


.. list-table::
   :widths: 30 70
   :header-rows: 1

   * - **Label and Mask Tools**
     - Tools for working with binary masks and labeled mask regions.
   * - Binary Mask Morphological Operations
     - Applies morphological operations (e.g., dilation, erosion) to binary masks.
   * - Measure Binary Mask
     - Extracts measurements (area, intensity) from binary masks.
   * - Label Binary Mask
     - Assigns unique labels to connected components in binary masks.
   * - Convert Labels to Mask
     - Converts labeled regions into binary masks.
   * - Update Labels
     - Updates or modifies labels in labeled images.
   * - Region Props on Labeled Mask
     - Computes region properties (e.g., area, eccentricity) for labeled regions.


.. list-table::
   :widths: 30 70
   :header-rows: 1
   
   * - **Layer Operations**
     - Tools for managing and merging image layers.
   * - Simple Multi-layer merging
     - Merges multiple image layers into a single layer.
   * - Advanced 2-layer merging
     - Performs advanced merging operations between two image layers.


.. list-table::
   :widths: 30 70
   :header-rows: 1
   
   * - **Colocalization and Correlation Tools**
     - Tools for analyzing colocalization and correlation between regions or channels.
   * - Object-Based Colocalization
     - Performs object-based analysis with metrics like Manders, Sorensen-Dice, and object distances.
   * - Pixel-Wise Correlation
     - Computes pixel-wise correlations using Pearson, Weighted Tau, Li’s ICA, and other metrics.
   * - Correlation Function (ACF, CCF)
     - Calculates Auto-Correlation (ACF) and Cross-Correlation Functions (CCF).


.. list-table::
   :widths: 30 70
   :header-rows: 1
   
   * - **Data Visualization**
     - Tools for visualizing analysis results.
   * - Plotting Widget
     - Interactive widget for plotting and visualizing analysis outputs.



Cell Analysis Features
----------------------
This table lists the features measured in the cell_df data structure when using the condensate analysis pipeline and the cell analyzer and condensate analyzer functions.

.. list-table:: Features Measured in cell_df
   :widths: 20 80
   :header-rows: 1

   * - Feature
     - Description
   * - label
     - Unique identifier for each object (cell or ROI).
   * - area
     - Total area of the object in pixels.
   * - intensity_mean
     - Mean intensity of the object.
   * - axis_major_length
     - Length of the major axis of the object’s fitted ellipse.
   * - axis_minor_length
     - Length of the minor axis of the object’s fitted ellipse.
   * - eccentricity
     - Deviation from circularity; 0 for a circle, 1 for a line.
   * - perimeter
     - Perimeter length of the object in pixels.
   * - intensity_std_dev
     - Standard deviation of the object's intensity.
   * - intensity_median
     - Median intensity value of the object.
   * - intensity_total
     - Total summed intensity of the object.
   * - cell_micron_area
     - Object area in square microns, based on image resolution.
   * - image_resolution_um_per_px_sq
     - Image resolution in (um/px)^2
   * - cell_snr
     - Signal-to-noise ratio: mean cell intensity / std dev of background (non-cell) intensity.
   * - gaussian_snr_estimate
     - Gaussian SNR: mean cell intensity / Gaussian background noise estimate.
   * - contrast
     - Contrast measure for the object region.
   * - dissimilarity
     - Texture-based measure of intensity dissimilarity.
   * - homogeneity
     - Texture-based measure of regional intensity uniformity.
   * - ASM
     - Angular Second Moment; measures image uniformity.
   * - energy
     - Energy metric derived from the image region.
   * - correlation
     - Correlation measure between neighboring pixel intensities.
   * - 32_bit_entropy
     - Entropy of the object calculated from 32-bit float intensities.
   * - 8_bit_entropy
     - Entropy of the object calculated from 8-bit unsigned integer intensities.
   * - 8_bit_entropy_img_avg
     - Average entropy of the 8-bit image.
   * - img_kurtosis
     - Measure of the "tailedness" of the intensity distribution.
   * - standardized_sixth_moment
     - Standardized sixth moment of the intensity distribution.
   * - kurtosis_z_score
     - Z-score for kurtosis, indicating deviation from normality.
   * - p_val
     - p-value for statistical significance of kurtosis.
   * - lbp_mean
     - Mean of the local binary pattern (LBP) features.
   * - lbp_std
     - Standard deviation of LBP features.
   * - lbp_entropy
     - Entropy of the LBP features.
   * - puncta_micron_area_mean
     - Mean size of puncta within a cell, in square microns.
   * - puncta_micron_area_std
     - Standard deviation of puncta sizes within a cell, in square microns.
   * - puncta_ellipticity_mean
     - Mean ellipticity of all puncta within a cell.
   * - puncta_intensity_total
     - Total intensity: mean intensity * area of all puncta.
   * - puncta_intensity_dist_mean
     - Mean intensity of all puncta within the cell.
   * - number_of_puncta
     - Total number of puncta detected within the cell.
   * - cell_xor_puncta_int_mean
     - Mean intensity of the region inside the cell, excluding puncta regions.
   * - cell_xor_puncta_int_std
     - Standard deviation of intensity in the cell region, excluding puncta ROIs.
   * - cell_xor_puncta_int_total
     - Total intensity of the region inside the cell, excluding puncta ROIs.
   * - cell_xor_puncta_area
     - Area of the region inside the cell, excluding puncta ROIs (square microns).
   * - snr_test
     - Signal-to-noise ratio: mean puncta intensity / std dev of dilute phase intensity.
   * - partition_test
     - Partition coefficient: puncta intensity mean / cell XOR puncta intensity mean.
   * - partition_test_total_int
     - Partition coefficient: puncta total intensity / cell XOR puncta total intensity.
   * - spark_score
     - Total puncta intensity / total cell intensity.
   * - puncta_classifier
     - Binary classifier for puncta presence: 1 (puncta), 0 (none).


Puncta Analysis Features
------------------------
This table lists the features measured in the puncta_df data structure when using the condensate analysis pipeline and the condensate analyzer function.

.. list-table:: Features Measured in puncta_df
   :widths: 20 80
   :header-rows: 1

   * - Feature
     - Description
   * - label
     - Unique identifier for each punctum (object).
   * - area
     - Total area of the punctum in pixels.
   * - intensity_mean
     - Mean intensity of the punctum.
   * - axis_major_length
     - Length of the major axis of the punctum’s fitted ellipse.
   * - axis_minor_length
     - Length of the minor axis of the punctum’s fitted ellipse.
   * - eccentricity
     - Deviation from circularity; 0 for a circle, 1 for a line.
   * - perimeter
     - Perimeter length of the punctum in pixels.
   * - ellipticity
     - Measure of elongation: `1 - axis_minor_length / axis_major_length`.  
       Higher values indicate more elongated puncta.
   * - circularity
     - Measure of shape compactness, normalized for irregular shapes:  
       `4 * pi * area / (perimeter ** 2)`.  
       Note: Normalized to account for the "coast of England" fractal paradox,  
       where irregular shapes cause perimeter to scale non-linearly with area.
   * - micron area
     - Area of the punctum in square microns, based on image resolution.
   * - cell label
     - Label of the corresponding cell to which the punctum belongs.