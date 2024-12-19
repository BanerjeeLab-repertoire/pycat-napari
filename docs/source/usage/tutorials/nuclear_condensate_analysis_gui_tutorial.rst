Nuclear Condensate Analysis Tutorial (GUI)
==========================================

This tutorial walks through a complete (in-cellulo) nuclear condensate analysis workflow using PyCAT-Napari. You'll learn how to load images, process them, perform segmentation, and analyze condensates.

If you have not already installed PyCAT-Napari, please see the :doc:`../../installation` guide.
Once you have PyCAT-Napari installed, you can launch the GUI by running the following command in your terminal:

.. code-block:: bash

   run-pycat


Interface Overview
------------------

.. image:: ../../_static/screenshots/Viewer_and_menu_highlights.png
   :alt: PyCAT main interface with key areas labeled
   :width: 800px

After you launch PyCAT-Napari, a blank Napari viewer with added menu items on the right will open up for you. In the image above the added menus have been expanded and color coded.

The key interface elements are:

1. 🟦 **Analysis Methods** (Blue): provides pre-made pipelines offering tools and outputs depending on the given method that you choose.
2. 🟩 **Toolbox** (Green): is a menu full of all of the individual functions and tools in PyCAT, for novel algorithm experimentation and analysis workflow customization.
3. 🟥 **Open/Save File(s)** (Red): handles image and data input/output for PyCAT, using AICSImageIO to read various microscope and metadata formats, and storing the information in PyCAT’s internal data structure.

.. note::
   You must use this and not the integrated Napari IO under the typical file open file save or the integrated drag and drop feature since they are not integrated with PyCATs internal data structure. 

4. 🟨 **Layer Tools** (Yellow): where users can easily add or remove various layers such as images, shapes, and labels from the viewer. This feature allows for quick management of the visual elements, including the ability to hide or show layers using the eye icon.
5. 🟪 **Shape and Label Tools** (Magenta): which include node tools for manipulating shape layers, as well as paint brush, eraser, and bucket tools for label layers. Users can also apply colormaps to images and change opacity to view overlapping images. 

PyCAT-Napari integrates seamlessly with the Napari interface, providing users with a powerful and intuitive environment for image analysis. 
Napari's interface is designed to be user-friendly, resembling popular pixel or raster photo editors like MSPaint or Photoshop. 
So, if you've ever used a photo editor, the tools should be simple enough to acclimate to. 


Getting Started
---------------

Once you have the application open, choose your analysis method from the menu. This populates the dock with a pre made analysis pipeline, even if you're doing your own algorithmic exploration, it is recomended to use general analysis for more robust integration with the internal PyCAT data structure. 

PyCAT excels at in-cellulo nuclear condensate analysis. An example pair of images are included in the folder assets/example. 

Loading Your Data
^^^^^^^^^^^^^^^^^

1. Navigate to ``Open/Save File(s) > Open 2D Image(s)``

   * Multiple files can be loaded simultaneously, multi-channel images or multiple selected files will be added the the viewer as separate layers
   * Assign names to each channel in a dialog box for easier layer tracking

.. note::
   Always use PyCAT's file menu rather than napari's native file handling to ensure proper integration with PyCAT's data structure.

Supported formats include:

* TIFF
* CZI
* PNG
* JPG

.. image:: ../../_static/screenshots/opened_image_in_viewer.png
   :alt: PyCAT main interface with image loaded
   :width: 800px

Initial Measurements
--------------------

Drawing Measurement Lines
^^^^^^^^^^^^^^^^^^^^^^^^^

After loading your images, you'll see two shape layers for measurements:

1. **Cell Diameter**: For measuring cell or nuclei diameters (or primary objects)
2. **Object Diameter**: For measuring condensate or subcellular object diameters (secondary Objects)

.. image:: ../../_static/screenshots/measuring_lines.png
   :alt: PyCAT object measurement layers
   :width: 800px


To measure:

1. Draw lines across representative objects in each layer
2. Click "Measure Lines" to calculate dimensions
3. Results will show in both pixels and microns (if metadata is available)

Image Processing
----------------

**(Optionally) Upscale Images**

* If you are upscaling your images, you can multi-select the layers in the viewer and then click ``Run Upscaling`` button

  * Upscaling can be useful for segmentation and pre-processing algorithms, however, it can also introduce noise artifacts, and should be considered appropriately and applied consistently. 
  * You can always run upscaling without updating the data class (checkbox) in order to test how processing and segmentation perform comparatively

Preprocessing Steps
^^^^^^^^^^^^^^^^^^^

Preprocessing is crucial for enhancing image quality and making downstream segmentation more effective. PyCAT's integrated preprocessing function consists of:

* **White top-hat filtering** to reduce the intensity of everything larger than the objects of interest (eg the background fluorescence)
* **Laplacian of Gaussian** enhancement to sharpen the edges of the objects of interest
* **Wavelet-based noise subtraction** to reduce noise while preserving features
* **Gaussian smoothing** for smoothing any artifacts introduced by the previous steps
* **Contrast-limited adaptive histogram equalization (CLAHE)** for boosting contrast without overexposure

Select your target image layer (highlighted in blue in the layers panel on the left side) and apply preprocessing. 
In the example, we do this on the upscaled GFP image. 

Background Removal
^^^^^^^^^^^^^^^^^^

The integrated background removal function in this analysis pipeline consists of:

* **Rolling ball background removal** to locally remove the background from the image
* **Gaussian background subtraction/division** to globally remove the background from the image and enhance uneven illumination
* **Peak and edge enhancement** applies a gabor filter and LoG attentuation filter to enhance texture and features

Other background removal techniques are available in the toolbox. 
Select the method that works best for your data, depending on its unique challenges.

.. image:: ../../_static/screenshots/preprocessed_images.png
   :alt: PyCAT preprocessed and background removed image
   :width: 800px

Segmentation
------------

Primary Mask Generation
^^^^^^^^^^^^^^^^^^^^^^^

1. Select your nuclear stain image (DAPI/Hoechst)
2. Choose between:

* Cellpose (recommended for most cases).

  * Select the layer you want to operate on from the dropdown and click ``Run Cellpose``

* Random Forest (for custom classifier training).

  * To use the random forest classifier for primary object (cell/nucleus) segmentation, you must first train the classifier. 

    * To do this, add a blank labels layer to the viewer, then using the paint brush, provide labels=1 for the background example pixels and labels=2 for the object pixels.
     
      * In the image above you can see the lines painted on for BG (red) and object (blue).
      * You can also use the bucket tool to fill in large areas with the same label. 
      * Once you are happy with the labeling, choose the labels layer in the dropdown and the nuclear stain image in the other dropdown and click ``Run RF Classifier`` to train the classifier. 

* Once the primary segmentation method has completed, the segmentation results will be added as a new layer in the viewer. 


Cell Analysis
^^^^^^^^^^^^^

After segmenting the cells/nuclei, you can measure their properties with the cell analyzer. 
The cell analyzer widget allows for a binary or labeled mask to be selected, an image to measure the properties from, and an optional mask to exclude certain structures from the analysis. 

1. Select your measurement image from the dropdown (in this example the upscaled GFP image)
2. Select the primary mask you want to measure from the dropdown (Cellpose or RF Classifier Segmentation mask) 
3. (Optional) Create exclusion masks for unwanted structures

   * Similarly to the RF classifier, you can create exclusion masks by adding a blank labels layer and painting the areas you want to exclude with label=2. 
   * You can also use the bucket tool to fill in large areas with the same label. 

     * You may want to do this to exclude nucleoli, cytoplasm, etc.

   * Once you're happy with your exclusion mask you can selecy it from the dropdown or select None if you don't want to use one

4. Click ``Run Cell Analyzer``

After the cell analyzer has completed, you will get a popup with the cell data frame (which is stored in the PyCAT data structure for later exporting) and a new layer in the viewer with the labeled cell mask. 

.. image:: ../../_static/screenshots/cell_analyzer.png
   :alt: PyCAT cell segmentation and analysis
   :width: 800px

Condensate Analysis
-------------------

Segmenting Condensates
^^^^^^^^^^^^^^^^^^^^^^^

For analyzing biomolecular condensates, PyCAT lets you segment these structures with just a few clicks. 

1. Select your most processed image for segmentation (in this example the background removed, preprocessed, upscaled GFP image)
2. Select your measurement image (typically the upscaled original, in this example the upscaled GFP image)
3. Click ``Run Condensate Segmentation``

The condensate segmentation function utilizes the labeled cell mask for per-cell processing, applies another level of backgroud removal and filtering, and then segments the condensates. 
The segmentation algorithm utilizes a multi-faceted, multi-step approach to segmenting the condensates, including Felzenszwalb segmentation and RAG region merginig, then a final segmentation is done using local thresholding methods Niblack and Sauvola. 
After the segmentation, the total puncta mask is generated, which is the over-segmented, unfiltered result. 
In addition, the segmentation results are put through a multi-layered object filtering process to generate a refined puncta mask. 
The object filter utilizes statistical metrics of the neighboring area around each object to determine if it is a true object or a flase positive. 
The filter was designed to balance removing false positives with overfiltering and false negatives. 

This generates two masks and adds them to the viewer.

* **Total Puncta Mask**: Unfiltered segmentation
* **Refined Puncta Mask**: Filtered for accuracy

At this point, you can visualize the efficacy of the segmentation, and make final manual edits to the masks if needed. 

.. image:: ../../_static/screenshots/condensate_segmentation.png
   :alt: PyCAT condensate segmentation
   :width: 800px

Analyzing Results
^^^^^^^^^^^^^^^^^

After the segmentation, you can analyze the results with the condensate analyzer. 

1. Select your measurement image from the dropdown (in this example the upscaled GFP image)
2. Select the mask you want to measure from the dropdown (Total Puncta Mask or Refined Puncta Mask, in this example the refined puncta mask)
3. Click ``Run Condensate Analyzer``

After the condensate analyzer has completed, you will get a popup with the condensate data frame (which is stored in the PyCAT data structure for later exporting) and a new layer in the viewer with the labeled condensate mask. 

The analysis produces:

* Cell-level metrics in the Cell Data Frame

  * Cumulative metrics for the condensates are also added to the cell data frame

* Individual object metrics in the Puncta Data Frame

* Visualization layers showing:

  * Labeled puncta (sharing parent cell labels)
  * Side-by-side image for comparison of raw and segmented data

Saving Results
--------------

When your analysis is complete:

1. Navigate to ``Open/Save File(s) > Save and Clear``
2. Select items to export:

* Images (as .tiff)
* Masks (as .png)
* Data Frames (as .csv)

3. Choose to clear:

* Only saved items - only the items you selected to save will be cleared. This option is useful if you want to try other analysis methods or pipelines without losing your preprocessing steps. 
* All items - clears all items from the viewer and resets the internal data structure so that you can analyze your next image. 

.. image:: ../../_static/screenshots/save_and_clear_popup.png
   :alt: PyCAT save and clear dialog
   :width: 800px

.. tip::
   Always save your results before clearing the viewer to ensure no data is lost.

Next Steps
----------

Now that you've completed a basic analysis, you might want to:

* Experiment with different preprocessing parameters

  * You can apply preprocessing and background removal in different orders, or multiple times each, or different object sizes or try diffeerent methods altogether.  

* Try alternative segmentation methods

  * You can try different segmentation methods, or different parameters for the same method, or even try to implement your own. 

* Explore additional analysis tools in the Toolbox
* The toolbox is full of tools for advanced analysis, including processing, segmentation, feature extraction, and statistical analysis. 
* The github repo has example outputs from this analysis for comparison, under the assets/example analysis images/ folder. 
* Check back soon for more tutorials!

Need Help?
----------

If you encounter issues:

* Check our Troubleshooting Tips in the :doc:`../../installation` guide
* Visit our GitHub Issues page
* Reach out to us directly for urgent help
