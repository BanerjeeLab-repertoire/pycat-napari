"""Cellpose wrapping + random-forest pixel classification - split out of segmentation_tools (1.6.241).

cellpose_segmentation wraps the optional Cellpose dependency (torch/cellpose imported LAZILY, and the
version-aware cyto2-vs-cpsam model build preserved exactly); train_and_apply_rf_classifier is the
RandomForest pixel classifier with refine_labels_with_contours post-processing. Moved VERBATIM - the
optional-import guard and when-cellpose-is-required behaviour are unchanged. The module owns the cellpose
GPU/model caches it manages. Imports opencv_watershed_func from the watershed family.
"""
from __future__ import annotations

import numpy as np
import skimage as sk
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
from pycat.utils.general_utils import dtype_conversion_func
from sklearn.ensemble import RandomForestClassifier
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func, extend_mask_to_edges
from pycat.toolbox.image_processing_tools import apply_rescale_intensity
from pycat.toolbox.segmentation.watershed import opencv_watershed_func


# Cache GPU availability at module load time  --  avoids re-initializing the
# CUDA context on every Cellpose call. The actual check is deferred until
# first use so module import stays fast.
_CELLPOSE_USE_GPU = None
_CELLPOSE_GPU_BACKEND = None   # 'cuda', 'mps', or None — set by _get_cellpose_gpu
_WARNED_CPU = False


def _get_cellpose_gpu():
    """Return True if Cellpose should run on a GPU (CUDA or Apple MPS).

    Checks CUDA first (NVIDIA), then Apple Metal (MPS) on Apple Silicon Macs.
    Cellpose 3.x accepts gpu=True and uses whichever accelerator torch exposes.
    The detected backend is cached in the module-level _CELLPOSE_GPU_BACKEND so
    the CPU-warning message can name the right install path per platform.
    """
    global _CELLPOSE_USE_GPU, _CELLPOSE_GPU_BACKEND
    if _CELLPOSE_USE_GPU is None:
        _CELLPOSE_GPU_BACKEND = None
        try:
            import torch
            if torch.cuda.is_available():
                _CELLPOSE_USE_GPU = True
                _CELLPOSE_GPU_BACKEND = 'cuda'
            elif getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
                # Apple Silicon Metal Performance Shaders backend.
                _CELLPOSE_USE_GPU = True
                _CELLPOSE_GPU_BACKEND = 'mps'
            else:
                _CELLPOSE_USE_GPU = False
        except Exception:
            _CELLPOSE_USE_GPU = False
    return _CELLPOSE_USE_GPU


# ---------------------------------------------------------------------------
# Cellpose version awareness  --  cyto2 (Cellpose <4 CNN) vs cpsam (Cellpose >=4)
# ---------------------------------------------------------------------------
# Cellpose 4 (Cellpose-SAM) removed the legacy cyto/cyto2/cyto3 weights: passing
# pretrained_model='cyto2' there is silently ignored and cpsam is loaded instead
# (a large ViT-L transformer that is very slow on CPU). The two model families
# require different Cellpose versions and cannot coexist in one environment, so
# PyCAT pins cellpose<4 by default (fast cyto2 CNN) and adapts automatically if a
# newer Cellpose is installed.

_CELLPOSE_MODEL_CACHE = {}


def _cellpose_major_version():
    """Return the installed Cellpose major version as an int (0 if unknown)."""
    try:
        import cellpose
        return int(str(cellpose.version).split('.')[0])
    except Exception:
        return 0


def available_cellpose_models():
    """
    List the segmentation model names valid for the INSTALLED Cellpose version.
    Cellpose <4 exposes the legacy CNNs (cyto2 default); Cellpose >=4 exposes
    only the SAM/DINO models (cpsam default).
    """
    if _cellpose_major_version() >= 4:
        return ['cpsam']
    return ['cyto2', 'cyto', 'nuclei']


def default_cellpose_model():
    """The preferred default model for the installed Cellpose version."""
    return available_cellpose_models()[0]


def _build_cellpose_model(model_name):
    """
    Build (and cache) a Cellpose model using the correct API for the installed
    version. On Cellpose <4 the builtin name goes through `model_type`; on
    Cellpose >=4 it goes through `pretrained_model`.
    """
    gpu = _get_cellpose_gpu()
    key = (model_name, gpu, _cellpose_major_version())
    if key in _CELLPOSE_MODEL_CACHE:
        return _CELLPOSE_MODEL_CACHE[key]

    # First use this session: load the cached weights from disk into memory.
    # (Downloaded once to ~/.cellpose on first ever run; not re-downloaded.)
    try:
        import logging as _logging
        _logging.getLogger('pycat').info(
            "Loading Cellpose model '%s' weights from cache into memory "
            "(first use this session)...", model_name)
    except Exception:
        pass

    # ── ``models`` was imported INSIDE the Cellpose-4 branch only ────────────────
    #
    # ``from cellpose import models`` sat inside ``if _cellpose_major_version() >= 4:``, and the
    # ``else`` branch — **every Cellpose 3.x install** — then called ``models.CellposeModel(...)``
    # with nothing having imported it.
    #
    #     UnboundLocalError: cannot access local variable 'models'
    #
    # **Cell segmentation was completely dead on Cellpose < 4**, which is what most users have.
    # The import belongs **above** the branch, where both paths can see it.
    #
    # *(Reported by Meet, 2026-07-13. Reproduced by stubbing cellpose 3.1.0 — the traceback is
    # identical.)*
    from cellpose import models

    if _cellpose_major_version() >= 4:
        # Cellpose 4+: legacy names don't exist; fall back to cpsam explicitly.
        name = model_name if model_name in available_cellpose_models() else 'cpsam'
        if model_name == 'nuclei' and name != 'nuclei':
            # The dedicated nuclei CNN doesn't exist in Cellpose 4 (SAM is a
            # single unified model). Tell the user rather than silently ignoring.
            try:
                napari_show_warning(
                    "The Cellpose 'nuclei' model isn't available in Cellpose 4 "
                    "(Cellpose-SAM is a single unified model). Using the default "
                    "model instead. For a dedicated nuclei model, install "
                    "cellpose<4 (pip install 'cellpose<4').")
            except Exception:
                pass
        model = models.CellposeModel(gpu=gpu, pretrained_model=name)
    else:
        # Cellpose <4: builtin CNNs are selected via model_type.
        try:
            model = models.CellposeModel(gpu=gpu, model_type=model_name)
        except TypeError:
            # Very old API fallback
            model = models.CellposeModel(gpu=gpu, pretrained_model=model_name)
    _CELLPOSE_MODEL_CACHE[key] = model
    return model


@tags_layer('cellpose', role='labels', inputs=('image',),
            summary='Cellpose deep-learning segmentation', target='cell')
def cellpose_segmentation(image, object_diameter, model_name=None, postprocess=True):
    """
    Perform cell segmentation on an image using Cellpose, a deep-learning-based method for cell/nucleus segmentation.

    This function processes an input image to enhance its features and applies the Cellpose deep learning model
    for cell and nucleus segmentation. It focuses on segmenting the image into distinct cell or nucleus areas.
    The `object_diameter` parameter is used to determine the scale of the objects to be segmented.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for cell segmentation, expected to be in a format compatible with Cellpose.
    object_diameter : int
        The approximate diameter (in pixels) of the cells or nuclei to be segmented in the image. This value scales
        the segmentation process.

    Returns
    -------
    mask : numpy.ndarray
        A binary mask of the segmented cells/nuclei in the input image, refined to enhance separation between adjacent
        objects and extend segmentation to image edges.

    Notes
    -----
    - Cellpose model 'cyto2' is used by default for broader applicability in cell and nucleus segmentation.
    - The input image is processed through several steps including dynamic range conversion, adaptive histogram
      equalization, denoising, and intensity rescaling to optimize it for segmentation.
    - Ensure that the Cellpose library is installed and properly configured in your environment. For more information
      on Cellpose, see: https://cellpose.readthedocs.io/en/latest/.
    - This function assumes the availability of several skimage and custom preprocessing functions to prepare the
      image for segmentation.
    """
    
    # Select the model for the installed Cellpose version (default cyto2 on
    # Cellpose <4, cpsam on Cellpose >=4). The model is cached across calls so
    # weights are not reloaded every segmentation.
    if model_name is None:
        model_name = default_cellpose_model()
    model = _build_cellpose_model(model_name)

    # Warn CPU-only users once per session  --  Cellpose is much slower without a
    # CUDA GPU, and the large Cellpose-SAM (cpsam) model on Cellpose >= 4 can
    # take minutes per image on CPU.
    global _WARNED_CPU
    if not _get_cellpose_gpu() and not _WARNED_CPU:
        _WARNED_CPU = True
        import sys as _sys
        _is_mac = _sys.platform == 'darwin'
        if _cellpose_major_version() >= 4:
            if _is_mac:
                napari_show_warning(
                    "Cellpose is running on CPU. The Cellpose-SAM model is very "
                    "slow on CPU -- expect minutes per image. On Apple Silicon, "
                    "install a PyTorch build with MPS support and PyCAT will use "
                    "the Apple GPU automatically; or switch to the faster cyto2 "
                    "model (cellpose<4). See the README GPU section.")
            else:
                napari_show_warning(
                    "Cellpose is running on CPU (no CUDA GPU detected). The "
                    "Cellpose-SAM model is very slow on CPU -- expect minutes per "
                    "image. For speed, install CUDA PyTorch or switch to the cyto2 "
                    "model (cellpose<4). See the README GPU section.")
        else:
            if _is_mac:
                napari_show_warning(
                    "Cellpose is running on CPU. Segmentation will be slower than "
                    "on GPU. On Apple Silicon, install a PyTorch build with MPS "
                    "support (the default recent torch wheels include it) and PyCAT "
                    "will use the Apple GPU automatically. There is no CUDA on Mac.")
            else:
                napari_show_warning(
                    "Cellpose is running on CPU (no CUDA GPU detected). Segmentation "
                    "will be slower than on GPU. To enable GPU acceleration, install "
                    "CUDA PyTorch: pip install torch torchvision --index-url "
                    "https://download.pytorch.org/whl/cu118")

    # Preprocess the image to improve segmentation quality.
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing
    img = sk.exposure.equalize_adapthist(img, kernel_size=object_diameter//2, clip_limit=0.0025)
    img = sk.restoration.denoise_wavelet(img)
    img = apply_rescale_intensity(img, out_min=0.0, out_max=1.0)

    image_preprocessed = dtype_conversion_func(img, 'uint16') # Convert the image to uint16 for Cellpose
    # Apply Cellpose model to segment cells/nuclei. Cellpose >=4 ignores the
    # `channels` argument (SAM is channel-order invariant); Cellpose <4 uses it.
    if _cellpose_major_version() >= 4:
        masks, flows, styles = model.eval(image_preprocessed, diameter=object_diameter)
    else:
        masks, flows, styles = model.eval(image_preprocessed, diameter=object_diameter, channels=[0,0])

    # When postprocess=False, return Cellpose's instance labels UNCHANGED. The
    # post-processing below (binarize → generic watershed → 7× morphological
    # opening → relabel) discards Cellpose's learned per-object boundaries and
    # replaces them with a harsh generic morphology pipeline — which degrades
    # otherwise-good Cellpose output. The time-series path passes postprocess=False
    # so it uses Cellpose's masks as-is. The legacy 2D path keeps postprocess=True
    # for backward compatibility (its downstream steps expect the refined masks).
    masks = np.asarray(masks).astype(np.uint16)
    if not postprocess:
        return masks

    # Post-process segmentation masks to improve results.
    binary_mask = masks > 0  # Binary version for morphological operations
    # Split objects that are erroneously connected. deprecated method replaced by cv2 binary watershed
    #split_mask = split_touching_objects(mask, sigma=object_diameter//4) 
    split_mask = opencv_watershed_func(binary_mask)
    refined_binary = binary_morph_operation(split_mask, iterations=7, element_size=3, element_shape='Disk', mode='Opening')
    refined_binary = extend_mask_to_edges(refined_binary, 3)  # Extend the mask to eliminate the empty border cellpose leaves

    # Re-label the refined binary mask so each cell retains a unique integer ID.
    # This is required for per-cell analyses (SACF, cell analyzer, etc.).
    labeled_mask = sk.measure.label(refined_binary)

    return labeled_mask

def run_cellpose_segmentation(image_layer, data_instance, viewer):
    """
    Applies cell segmentation to an image layer using Cellpose and displays the results in the Napari viewer.

    Retrieves the necessary parameters from provided objects, executes cell segmentation with `cellpose_segmentation`,
    and integrates the resulting mask into the viewer as a new layer.

    Parameters
    ----------
    import napari
    image_layer : napari.layers.Image
        The image layer to be segmented.
    data_instance : object
        An object containing a data repository with segmentation parameters, such as 'cell_diameter'.
    viewer : napari.Viewer
        The viewer object where the segmentation results will be displayed.
    """
    
    # Retrieve the image data and cell diameter from the data instance
    image = image_layer.data
    object_diameter = data_instance.data_repository['cell_diameter']
    model_name = data_instance.data_repository.get('cellpose_model', None)
    # Refine (post-process) Cellpose masks only if the user opted in. Default
    # False → use Cellpose's instance masks directly (preserves learned
    # boundaries); True → legacy watershed + morphology cleanup.
    refine = bool(data_instance.data_repository.get('cellpose_refine', False))

    # Perform cell segmentation using Cellpose.
    cell_masks = cellpose_segmentation(image, object_diameter,
                                       model_name=model_name,
                                       postprocess=refine)
    
    # Add the segmentation results as a new label layer to the viewer.
    viewer.add_labels(cell_masks, name=f"Cellpose Segmentation on {image_layer.name}")


def train_and_apply_rf_classifier(image, training_labels, object_diameter):
    """
    Trains and applies a Random Forest classifier to segment an image based on training labels.

    The function enhances the input image using adaptive histogram equalization and denoising techniques
    before training a Random Forest classifier. The classifier is then used to predict segmentation masks
    across the entire image. These masks are refined to improve the segmentation quality.

    Parameters
    ----------
    image : numpy.ndarray
        The input image for segmentation, expected to be in grayscale or compatible format.
    training_labels : numpy.ndarray
        The ground truth labels for training the classifier, must be the same dimensions as the image.
    object_diameter : int
        The approximate diameter of the target objects in pixels, used to tailor image preprocessing.

    Returns
    -------
    refined_masks : List[numpy.ndarray]
        A list of refined segmentation masks for each detected classification type, adjusted for segmentation 
        quality.

    Notes
    -----
    The segmentation process includes image preprocessing for feature enhancement, classifier training on specified
    regions, and applying this classifier to the whole image. The resulting masks are then refined through morphological
    operations and contour adjustments to produce the final segmented output.
    """
    
    # Image preprocessing for enhanced segmentation performance
    img = dtype_conversion_func(image, 'float32') # Convert image to float32 for processing
    img = sk.exposure.equalize_adapthist(img, kernel_size=object_diameter//2, clip_limit=0.0025)
    img = sk.restoration.denoise_wavelet(img)

    # Training data preparation
    training_img_pixels = img[training_labels != 0]
    training_label_pxs = training_labels[training_labels != 0]

    # Random Forest classifier initialization and training
    rf_classifier = RandomForestClassifier(n_estimators=500, max_depth=4, criterion='entropy', n_jobs=-1)
    rf_classifier.fit(training_img_pixels.reshape(-1, 1), training_label_pxs)

    # Segmentation using the trained classifier
    prediction_pixels = img.reshape(-1, 1)
    predicted_labels = rf_classifier.predict(prediction_pixels).reshape(img.shape)
    predicted_labels -= 1 # Shift labels to start from 0
    predicted_labels = predicted_labels.astype(np.uint8) # Convert to uint8 for compatibility

    # Refinement of predicted labels
    refined_labels = np.zeros_like(predicted_labels)
    for label in np.unique(predicted_labels)[1:]:  # Skip label 0 (background)
        label_mask = predicted_labels == label
        #label_mask = binary_morph_operation(label_mask, mode='Fill Holes')
        label_mask = binary_morph_operation(label_mask, iterations=3, element_size=5, element_shape='Disk', mode='Opening')
        label_mask = binary_morph_operation(label_mask, iterations=5, element_size=3, element_shape='Disk', mode='Closing')
        #label_mask = opencv_watershed_func(label_mask)
        refined_labels[label_mask] = label

    # Convert to binary mask and label connected components
    binary_mask = refined_labels > 0
    labeled_mask = sk.measure.label(binary_mask)
    # Remove small objects from the labeled mask
    min_area = (np.pi * (object_diameter / 2) ** 2) // 10
    labeled_mask = _remove_small_objects_compat(labeled_mask, min_area)
    binary_mask = labeled_mask > 0 
    # Use the binary mask to remove the small objects from the refined labels
    refined_labels *= binary_mask

    # Extend mask to the edges and refine each label's mask
    refined_labels = extend_mask_to_edges(refined_labels, 3)
    refined_masks = refine_labels_with_contours(refined_labels, min_area)

    return refined_masks

@tags_layer('contour_refine', role='labels',
            summary='Label refinement against image contours')
def refine_labels_with_contours(refined_labels, min_area):
    """
    Refines segmentation masks for each label within a given input mask using contour detection and area filtering. 
    This function iterates over each unique label in the input mask, extracts contours for each label using the 
    specified minimum area criteria, and applies morphological operations to refine these contours.

    Parameters
    ----------
    refined_labels : numpy.ndarray
        The input mask containing different labels for segmented regions, typically obtained from segmentation algorithms.
    min_area : int
        The minimum area threshold for contours to be considered during the refinement process. Only contours larger 
        than this threshold are included.

    Returns
    -------
    refined_masks : List[numpy.ndarray]
        A list of refined masks for each label present in `refined_labels`. Each mask in the list corresponds to a 
        unique label and contains the refined contours for that label.

    Notes
    -----
    The function first segregates each label within the input mask and then applies `opencv_contour_func` to detect and
    draw contours that meet the specified area criteria. It further refines these contours using a binary morphological 
    operation (e.g., opening) to smooth edges and remove small artifacts. If no valid objects are found for a label after
    processing, a message is printed, and the label is skipped in the output. The resulting refined masks are returned as
    a list, one for each label, ensuring that the refined contours correspond to the initial segmented regions.
    """
    # Initialize an empty list to store the refined masks for each label
    refined_masks = []

    # Iterate over each unique label found in `refined_labels` (skip the background label, typically 0)
    for label in np.unique(refined_labels)[1:]:  # Skip background label
        # Create a binary mask for the current label
        binary_mask = (refined_labels == label)

        # Find contours in the binary mask
        current_label_mask = opencv_contour_func(binary_mask, min_area)

        # Final post-processing steps for the current label mask
        current_label_mask = binary_morph_operation(current_label_mask, mode='Opening', iterations=7, element_size=3, element_shape='Disk')
        if np.sum(current_label_mask) == 0:
            napari_show_warning(f"RF Label {label+1} has no valid objects.")
            continue
        current_label_mask[current_label_mask > 0] = label # Assign the label value to the refined mask
        refined_masks.append(current_label_mask) # Store the refined mask for the current label

    return refined_masks

def run_train_and_apply_rf_classifier(image_layer, label_layer, data_instance, viewer):
    """
    Facilitates the training and application of a Random Forest classifier on an image layer and displays the
    results in a Napari viewer.

    This function extracts the necessary data from the provided image and label layers, trains a Random Forest
    classifier based on the training labels, and applies this classifier to segment the image. The segmented results
    are then displayed as new layers in the viewer.

    Parameters
    ----------
    import napari
    image_layer : napari.layers.Image
        The layer containing the image data to be segmented.
    label_layer : napari.layers.Labels
        The layer containing label data used for training the classifier.
    data_instance : object
        An object containing additional parameters such as 'cell_diameter' needed for processing.
    viewer : napari.Viewer
        The viewer in which to display the segmented results.

    Notes
    -----
    - Multiple refined masks are displayed in separate layers if more than one valid object classification is found.
    """
    # Extract necessary data for segmentation
    object_diameter = data_instance.data_repository['cell_diameter']
    image = image_layer.data
    training_labels = label_layer.data

    # Train and apply the Random Forest classifier for segmentation
    output_mask_list = train_and_apply_rf_classifier(image, training_labels, object_diameter)

    # Display the segmentation results in the viewer
    if len(output_mask_list) == 0:
        napari_show_info("No valid objects were found.")
    elif len(output_mask_list) == 1:
        viewer.add_labels(output_mask_list[0].astype(int), name=f"Random Forest Segmentation on {image_layer.name}")
    else:
        for idx, output_mask in enumerate(output_mask_list):
            output_mask = output_mask.astype(int)
            output_mask[output_mask > 0] = idx + 1
            viewer.add_labels(output_mask, name=f"Random Forest Segmentation {idx+1} on {image_layer.name}")
