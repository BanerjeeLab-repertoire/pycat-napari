====================
PyCAT-Napari Roadmap
====================

This document outlines the desired features, improvements, and issues to address in future stable releases or PyCAT 2.0. The content is organized into categories for clarity and ease of reference.

Basic plans/outlines are included where I have done some brainstorming, and additional information/references are included where I have found relevant resources.


Core Functionalities
--------------------

File I/O
^^^^^^^^

**Napari Integrated File Opening**

* Explore integrating PyCAT's file I/O with Napari's native file I/O for seamless operations.

**Expanded File Support**

* 3D Image/Z-Stack
* Time Series
* Video

**Migration from AICSImageIO to BioIO**

* Replace ``imsave`` with ``BioIO``'s ``BioIO.save``
* `BioIO GitHub Repository <https://github.com/bioio-devs/bioio>`_
* Utilize ``BioIO`` for expanded metadata handling.

Steps for Migrating to BioIO
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Install Required BioIO Packages**

* Identify the file formats you need and install the corresponding BioIO plug-ins

   .. code-block:: bash

      pip install bioio bioio-czi bioio-imageio bioio-lif bioio-tifffile bioio-ome-zarr bioio-ome-tiff bioio-nd2

2. **Update Code to Use BioIO**

* Replace all imports of AICSImageIO with BioIO. For example

   .. code-block:: python

      from bioio import BioImage

* Update any AICSImage object creation with BioImage. Example

   .. code-block:: python

      image = BioImage("file.czi")

* Check the migration guide for detailed API changes.

3. **Test for Compatibility**

* Test application with all supported file formats to ensure BioIO behaves as expected.
* Validate that all the necessary dependencies are correctly installed for your use case.

4. **Update Environment Files**

* Update ``requirements.txt`` or ``environment.yaml`` files to reflect the new dependencies.

   .. code-block:: yaml

      dependencies:
        - bioio
        - bioio-czi
        - bioio-imageio
        - bioio-lif
        - bioio-tifffile
        - bioio-ome-zarr
        - bioio-ome-tiff
        - bioio-nd2

5. **Document Changes**

* Update the package's documentation to note the switch from AICSImageIO to BioIO, including installation instructions for the required plug-ins.
* `BioIO GitHub Repository <https://github.com/bioio-devs/bioio>`_
* `BioIO Migration Guide <https://bioio-devs.github.io/bioio/MIGRATION.html>`_
* `BioIO Overview <https://bioio-devs.github.io/bioio/OVERVIEW.html>`_

Image Segmentation
^^^^^^^^^^^^^^^^^^

**Configurable Segmentation Parameters**

Add inputs for

* Minimum Object Size
* Maximum Object Size  
* Point Spread Function (PSF) Size
* WBNS Noise Level
* Use these inputs throughout analyses to eliminate magic numbers.

**Segmentation Enhancements**

* **Watershed Splitting**

  * Separate function to split touching objects using OpenCV's watershed on binary masks.

* **Replace Watershed Labeling**

  * Use ``skimage.segmentation.random_walker`` as an alternative to watershed labeling, see more at `Random Walker Segmentation Documentation <https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_random_walker_segmentation.html>`_

**Improved Puncta Detection**

* Address issue where PyCAT segments are too small.

  * Reduce over-opening.
  * Apply dilation (e.g., ``dilation=1``) before returning puncta mask.

* Separate Condensate/Object Filter

  * Make the condensate/object filter a separate, configurable function and base its local region on the size of the objects (e.g., small objects look at 1 or 2 pixel perimeter, large condensates maybe 3-5 px).

**Expand Labels**

* Utilize ``skimage.segmentation.expand_labels`` for efficient label growth.
* Example usage - ``skimage.segmentation.expand_labels(label_image, distance=1)``

**Cell Segmentation Options**

* Model Selection for CellPose

  * Allow users to select different CellPose models via a dropdown menu.

* Universal Cell Segmentation

  * Possibly incorporate other advanced segmentation methods

    * `cellSAM Preprint <https://www.biorxiv.org/content/10.1101/2023.11.17.567630v2>`_ and Segment Anything Models (SAM) from Meta
    * `Nature Article 1 <https://www.nature.com/articles/s41592-024-02254-1>`_
    * `Nature Article 2 <https://www.nature.com/articles/s41592-024-02233-6>`_



Thresholding Methods
^^^^^^^^^^^^^^^^^^^^

**Local Thresholding Enhancements**

* Add various local thresholding methods.
  
  * `Local Otsu <https://sharky93.github.io/docs/dev/auto_examples/plot_local_otsu.html>`_
  * `Adaptive Gaussian Thresholding <https://medium.com/geekculture/image-thresholding-from-scratch-a66ae0fb6f09>`_
  * Implement AND/OR operations for combining threshold methods.


**Skimage Thresholding Tools**

* Incorporate ``skimage.filters.try_all_threshold``, then the user could select which method to use, much like Fiji.

  * **Available Methods:**

    * Isodata
    * Li
    * Mean
    * Minimum 
    * Otsu
    * Triangle
    * Yen

Background Removal
^^^^^^^^^^^^^^^^^^

**Gaussian Background Removal**

* Separate the functions for

  * Gaussian Background Removal
  * Rolling Ball (RB) Background Removal
  * Support mask use in BG removal so that in-painting can be used to avoid the 'rim' that is left by traditional Rolling Ball BG removal algorithms.

**Top Hat Filters**

* Implement a function to apply black/white top-hat filters with selectable parameters

  * Select layer
  * Choose between black vs. white top-hat filter
  * Define size (e.g., ball radius)
  * Add the filtered output to the viewer.


Performance Improvements
------------------------

**Speed & Efficiency Enhancements**

* **Bounding Box Cropping**

  * Implement the bounding box cropping function for all masked or per-cell analyses instead of processing the entire image.

* **Parallel Processing & GPU Acceleration**

  * Explore parallel processing techniques.
  * Utilize GPU acceleration where applicable.


**Visual Indicators**

* **Progress Bars**

  * Add progress bars or visual indicators for functions that are slower.
  * Utilize Napari's built-in tools for progress visualization.

**Multitasking**

* Allow users to perform other tasks while a slow function is running.
* Implement threading or asynchronous programming to offload heavy processing.
* Example Implementation

.. code-block:: python

   import threading
   
   def start_processing_thread(unique_labels):
       # Create a thread that runs the process_cells function
       processing_thread = threading.Thread(target=process_cells, args=(unique_labels,))
       processing_thread.start()


Advanced Analysis Tools
-----------------------

Colocalization & Correlation Analysis
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Costes Significance Testing**

* Modify the Costes method to scramble pixels in blocks roughly the size of the PSF since within that region they are not truly independent of each other.

**Plotting CFs**

* Improve handling of labeled masks by

  * Plotting only the first labeled object to avoid multiple pop-ups.
  * Potentially refining the plotting logic for better usability.
  * Could store plots for each label in a labeled/masked analysis.


**CCF Fitting**

* Implement fitting for 1D CCF and return fit results, including offsets for 2D analyses.

**Colocalization Filters Using Skimage Metrics**

* Incorporate metrics such as

  * ``skimage.metrics.mean_squared_error(image0, image1)``
  * ``skimage.metrics.structural_similarity(im1, im2, ...)``
  * ``skimage.metrics.normalized_mutual_information(image0, image1, ...)``
  * ``skimage.metrics.normalized_root_mse(image_true, image_test, ...)``


Plotting Tools
^^^^^^^^^^^^^^

**Improved Plotting Widget**

* Refactor the existing plotting widget for better usability.
* The plotting widget was incredibly difficult to make; it may require refactoring using an observer pattern or similar. Although it does mostly work now, it needs updating to function more expansively and intuitively.



Batch & Video Processing
------------------------

Framework Overview
^^^^^^^^^^^^^^^^^^

The framework I envision would not be all that dissimilar to CellProfiler. The goal should be, like the rest of the program, to keep it modular, expandable, customizable, and user-friendly. The user will have to determine their optimal segmentation algorithm on an example image or frame, measure the object sizes, PSF input, etc., then go into the video/batch UI.

1. **User Configurable Workflow**

* Allow users to determine the optimal segmentation algorithm on an example image or frame.
* Provide a series of dropdown menus organized for

  * Pre-processing steps
  * Upscaling/Deconvolution
  * Cell Segmentation
  * Analysis steps

2. **Modular/Expandable Design**

* Ensure each processing step is optional.
* Facilitate adding multiple pre-processing, upscaling, and analysis steps as needed.

3. **Execution**

* Implement a "Run on All" button to apply the configured workflow to

  * All images in a folder
  * All frames in a video/time series

Video Integration
^^^^^^^^^^^^^^^^^

**TrackPy for Particle Tracking**

* Integrate `TrackPy <http://soft-matter.github.io/trackpy/v0.6.1/>`_ for advanced particle tracking in videos.
* Link video segmentation to TrackPy by

  * Segmenting every frame like a batch process.
  * Formatting results into a DataFrame.
  * Passing the formatted DataFrame to TrackPy for particle 'linking' and tracking.

* Napari's built-in file I/O handles videos quite well and displays them in an intuitive and ideal way in the viewer, further reinforcing that PyCAT FileIO should be integrated directly into Napari (e.g., by forking the repo).

Machine Learning Integration
----------------------------

**ML-Based Classification/Segmentation**

* Develop machine learning classifiers for

  * Segmentation and detection tasks (e.g., identifying the presence of condensates).
  * Potentially incorporate ML for enhancing segmentation accuracy and efficiency.
  * Use the annotated output from PyCAT as sets of training and validation data

    * Incorporate human-in-the-loop analyzed data, user-free analyzed data, and synthetic data for more robust training and reinforcement.

Data Management & Output
------------------------

**Metadata Handling**

* Store metadata as a DataFrame.
* Provide options to save metadata alongside image data.
* Enable exporting images with updated metadata attached.

**Data Frame Organization**

* Organize DataFrame features/columns better.
* Consider rounding data or maintaining float precision based on analysis needs.

Miscellaneous Enhancements
--------------------------

**Error Handling**

* Implement improved and more informative error messages to assist users in troubleshooting.

**Additional Tools**

* PunctaTools

  * Implement features from the `PunctaTools <https://github.com/stjude/punctatools>`_ analysis pipeline, or collaborate with them to integrate it into PyCAT fully.

* Line Plots Functionality

  * Implement functionality for generating line plots from data in the plotting widget.

* Cytoplasm Analysis

  * Simplify and improve cytoplasm analysis methods.

* Partition Coefficients

  * Support bi-phasic and multi-phasic partition coefficients for more detailed analyses.

**Texture Analyses**

* Use Gaussian blur of minimum object size (e.g., 2 or 3 px) then analyze to reduce the effect of noise.

**LayerDataframeSelectionDialog**

* Default layer and DataFrame names could be passed to ``LayerDataframeSelectionDialog`` (for Save and Clear) based on the analysis method chosen.

**Mask Layer Operations Merging Functions (and, or, xor)**

* Make Mask merging functions similar to image merging operations (then rename the old ones Image Layer Operations, the new ones Mask Layer Operations) for combining masks using AND, OR, XOR methods.

Future Features & Research Integration
--------------------------------------

Advanced Methods
^^^^^^^^^^^^^^^^

**SpIDA (Spatial Intensity Distribution Analysis)**

* Incorporate advanced colocalization analysis methods like SpIDA.
* `PNAS Article <https://www.pnas.org/doi/10.1073/pnas.1018658108>`_

**Support for Advanced Analysis Types**

* Add support for

  * Time Series Analysis
  * Fluorescence Correlation Spectroscopy (FCS)
  * Fluorescence Cross-Correlation Spectroscopy (FCCS)
  * 3D Support and Z-Stacks
  * Video Analyses, Video Particle Tracking (VPT), Particle Motion Tracking (pMOT)
  * Integrate other Banerjee Lab code/analyses

Denoising & Morphological Operations
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Invariant Denoising**

* Implement invariant denoising techniques.
* `Invariant Denoising Example <https://scikit-image.org/docs/stable/auto_examples/filters/plot_j_invariant.html#sphx-glr-auto-examples-filters-plot-j-invariant-py>`_

**Morphological Reconstruction**

* Utilize morphological reconstruction methods.
* `Morphological Reconstruction Guide <https://www.mathworks.com/help/images/understanding-morphological-reconstruction.html>`_

**Anisotropic Diffusion Filters**

* Implement Anisotropic Diffusion (Perona-Malik Filter).

**Miscellaneous Skimage Functions**

* Potentially integrate the following Skimage functions for enhanced processing

  * ``skimage.util.view_as_blocks(arr_in, block_shape)`` - could be useful for Costes blocks.
  * ``skimage.segmentation.find_boundaries(...)``
  * ``skimage.segmentation.random_walker(...)``
  * ``skimage.filters.apply_hysteresis_threshold(...)``


Documentation & User Support
----------------------------

**Comprehensive Documentation**

* Continuously improve documentation to cover

  * How to use PyCAT features.
  * Explanations of underlying theories and methods.

**User Guides & Tutorials**

* Develop detailed user guides and tutorials to assist users in leveraging PyCAT's full capabilities.

**Background Information**

* Provide background information on key topics such as

  * Image processing techniques.
  * Colocalization analysis.
  * Particle tracking.

Known Issues
------------

**run_simple_multi_merge**

* Mean and Additive produce the same result for some unknown reason.



Local Thresholding - Work In Progress (WIP)
-------------------------------------------

Adaptive Gaussian Threshold Function
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import numpy as np
   from scipy import ndimage as ndi
   from skimage.filters import gaussian

   def adaptive_thresholdGaussian(img, block_size, c):
       # Check that the block size is odd and nonnegative
       assert block_size % 2 == 1 and block_size > 0, "block_size must be an odd positive integer"
       
       # Calculate the local threshold for each pixel using a Gaussian filter
       threshold_matrix = gaussian(img, sigma=block_size//2)
       threshold_matrix = threshold_matrix - c
       
       # Apply the threshold to the input image
       binary = np.zeros_like(img, dtype=np.uint8)
       binary[img >= threshold_matrix] = 255
       
       return binary

Adaptive Gaussian Threshold Function (Detailed)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import numpy as np
   from scipy import ndimage as ndi
   from skimage.filters import gaussian

   def adaptive_gaussian_threshold(image, blockSize, C):
       """
       Performs adaptive Gaussian thresholding on a grayscale image.
       
       Parameters:
       - image: numpy array, the input grayscale image.
       - blockSize: int, size of the local region to calculate the Gaussian weighted mean (must be an odd number).
       - C: int, a constant subtracted from the Gaussian weighted mean to calculate the threshold.
       
       Returns:
       - numpy array, the thresholded binary image.
       """
       # Ensure the blockSize is odd
       if blockSize % 2 == 0:
           raise ValueError("blockSize must be an odd number.")
           
       # Generate a Gaussian kernel
       kernel_size = blockSize
       sigma = 0.3 * ((kernel_size - 1) * 0.5 - 1) + 0.8
       gauss_kernel = gaussian(image, sigma=sigma, truncate=(kernel_size//2)/sigma)
       
       # Image dimensions
       rows, cols = image.shape
       
       # Pad the image to handle borders
       padded_image = np.pad(image, blockSize // 2, mode='edge')
       
       # Output image
       thresholded_image = np.zeros_like(image)
       
       for i in range(rows):
           for j in range(cols):
               # Calculate the local weighted mean
               local_sum = np.sum(padded_image[i:i+blockSize, j:j+blockSize] * gauss_kernel[i:i+blockSize, j:j+blockSize])
               local_mean = local_sum / np.sum(gauss_kernel[i:i+blockSize, j:j+blockSize])
               
               # Apply the threshold
               if image[i, j] > local_mean - C:
                   thresholded_image[i, j] = 255
               else:
                   thresholded_image[i, j] = 0
                   
       return thresholded_image



.. note::
   This roadmap is a living document and will be updated as development progresses and new requirements emerge. 
   If you'd like to contribute, please visit our :doc:`contributing` page to help work on implementing any of these or other useful features. 