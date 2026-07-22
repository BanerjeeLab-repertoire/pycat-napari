"""Felzenszwalb (FZ) segmentation - split out of segmentation_tools (1.6.241).

felzenszwalb_segmentation_and_merging runs FZ superpixel oversegmentation then merges regions by mean
colour via a RAG (merge_mean_color / _weight_mean_color callbacks); fz_segmentation_and_binarization is
the binarising path used by subcellular segmentation. Moved VERBATIM - no scale/sigma/min_size change.
Imports local_thresholding_func from the local_thresholding family.
"""
from __future__ import annotations

import math
import numpy as np
import skimage as sk
import scipy.ndimage as ndi
from pycat.utils.tag_registry import tags_layer
from pycat.toolbox.label_and_mask_tools import binary_morph_operation, opencv_contour_func
from pycat.utils.general_utils import dtype_conversion_func, check_contrast_func
from pycat.toolbox.segmentation.local_thresholding import local_thresholding_func


def _weight_mean_color(graph, src, dst, n):
    """
    Callback to handle merging nodes by recomputing mean color.
    
    This function is a utility designed to facilitate the merging process
    in a Region Adjacency Graph (RAG) by calculating the weight of the edge
    that will connect the merged node to its neighbors. The weight is
    determined based on the absolute difference in mean color between the
    `dst` node and its neighbors (`n`). It assumes the mean color of `dst`
    has already been updated to reflect the merging.

    Parameters
    ----------
    graph : RAG
        The graph under consideration.
    src, dst : int
        The vertices in `graph` to be merged.
    n : int
        A neighbor of `src` or `dst` or both.

    Returns
    -------
    data : dict
        A dictionary with the `"weight"` attribute set as the absolute
        difference of the mean color between node `dst` and `n`.
    """
    # Calculate the difference in mean color between `dst` and neighbor `n`
    diff = graph.nodes[dst]['mean color'] - graph.nodes[n]['mean color']
    # Use numpy's linear algebra norm function to compute the Euclidean distance
    # This distance represents the absolute difference in mean color
    diff = np.linalg.norm(diff)
    # Return a dictionary with the calculated weight
    return {'weight': diff}

@tags_layer('merge_mean_color', role='labels',
            summary='Region merging by mean colour')
def merge_mean_color(graph, src, dst):
    """
    Callback called before merging two nodes of a mean color distance graph.
    
    Prior to merging two nodes in a RAG, this function updates the `dst` node's
    attributes to reflect the combined color information of both `src` and `dst`.
    This is crucial for accurately computing the mean color of the merged node,
    ensuring the graph's integrity and the accuracy of its color representation.

    Parameters
    ----------
    graph : RAG
        The graph under consideration.
    src, dst : int
        The vertices in `graph` to be merged.
    """
    # Add the `total color` of `src` to `dst` to reflect merging
    graph.nodes[dst]['total color'] += graph.nodes[src]['total color']
    # Similarly, combine the `pixel count` of both nodes
    graph.nodes[dst]['pixel count'] += graph.nodes[src]['pixel count']
    # Recalculate `mean color` of `dst` to account for the merged node's new color data
    graph.nodes[dst]['mean color'] = (graph.nodes[dst]['total color'] /
                                      graph.nodes[dst]['pixel count'])


@tags_layer('felzenszwalb', role='labels', inputs=('image',),
            summary='Felzenszwalb graph segmentation with merging')
def felzenszwalb_segmentation_and_merging(image, scale=7.0, sigma=0.5, min_size=2):
    """
    Performs image segmentation using Felzenszwalb's method followed by merging based on color similarity.

    This function applies an initial segmentation to the input image using Felzenszwalb's efficient graph-based
    segmentation algorithm. It then constructs a Region Adjacency Graph (RAG) from the initial segments and
    merges segments based on the similarity of their mean color (intensity for grayscale). The merging process is controlled by comparing
    the color distance between segments against a threshold derived from the image's standard deviation.

    Parameters
    ----------
    image : numpy.ndarray
        The input image to segment. Can be a grayscale or RGB image.
    scale : float, optional
        The scale parameter influences the size of the clusters in the initial segmentation. Higher values result in larger clusters. 
        This controls how aggressively pixels are merged together in the initial segmentation. Defaults to 7.0.
    sigma : float, optional
        The standard deviation for the Gaussian kernel used in smoothing the image before segmenting. This preprocessing step can help 
        reduce noise and improve the quality of segmentation. Defaults to 0.5.
    min_size : int, optional
        The minimum size of final segments. Smaller segments are merged during post-processing to ensure that every segment is at least 
        this size. Defaults to 2.

    Returns
    -------
    segmented_img : numpy.ndarray
        The segmented image with segments represented by the average color (or intensity) of their respective pixels, returned in the 
        same data type as the input.

    Notes
    -----
    - 'scale' directly influences how aggressively pixels are merged in the initial segmentation, correlating with the 'k' parameter in Felzenszwalb's paper.
    - Appropriate selection of 'scale', 'sigma', and 'min_size' is crucial for optimal segmentation results, depending on the image's specific characteristics.
    """
    # Store the input image's data type to convert back at the end
    input_dtype = str(image.dtype)

    # Convert input image to float32 for processing; necessary for certain operations and ensures consistency
    img = dtype_conversion_func(image, output_bit_depth='float32')

    # Apply Felzenszwalb's segmentation algorithm to the image
    # This step segments the image into regions based on pixel similarity and the specified parameters
    segments_fz = sk.segmentation.felzenszwalb(img, scale=scale, sigma=sigma, min_size=min_size)

    # Construct a Region Adjacency Graph (RAG) from the initial segmentation
    # The RAG represents how segments are connected and allows for merging based on further criteria
    g = sk.graph.rag_mean_color(img, segments_fz, mode='similarity')
    
    # Define a threshold for merging segments based on color similarity
    # This threshold is set dynamically based on the square of the normalized float image's standard deviation (so it will be a sub 1 value)
    threshold = (np.std(img)**2)/2

    # Merge segments hierarchically based on their mean color similarity
    # `merge_func` determines how the color information is combined when segments are merged
    labels = sk.graph.merge_hierarchical(segments_fz, g, thresh=threshold, rag_copy=False,
                                         in_place_merge=True,
                                         merge_func=merge_mean_color,
                                         weight_func=_weight_mean_color)

    # Convert the merged segment labels into a segmented image with averaged colors
    # The `label2rgb` function assigns the average color of a segment to all its pixels
    merged_fz = sk.color.label2rgb(labels, img, kind='avg', bg_label=0)
    segmented_img = merged_fz[..., 0]  # Extract the grayscale channel for simplicity

    # Convert the segmented image back to the original input data type for consistency with the input
    segmented_img = dtype_conversion_func(segmented_img, output_bit_depth=input_dtype)

    return segmented_img


def run_fz_segmentation_and_merging(scale_input, sigma_input, min_size_input, viewer):
    """
    Applies Felzenszwalb's segmentation and merging to an active image layer in a Napari viewer based on user-provided settings.
    This function allows for dynamic interaction, enabling users to adjust segmentation parameters in real-time.

    Parameters
    ----------
    scale_input : QLineEdit
        Input field for the scale parameter, affecting the size of the initial segmentation clusters.
    sigma_input : QLineEdit
        Input field for the sigma parameter, controlling the degree of Gaussian smoothing prior to segmentation.
    min_size_input : QLineEdit
        Input field for the minimum size of the segments to be considered in the final output.
    viewer : napari.viewer.Viewer
        Viewer instance where the segmented image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected.
    """

    # Check for an active image layer in the viewer
    active_layer = viewer.layers.selection.active
    import napari
    if active_layer is None or not isinstance(active_layer, napari.layers.Image):
        raise ValueError("No active image layer selected")

    image = active_layer.data  # Extract the image data from the active layer

    # Read scale, sigma, and min_size from inputs, defaulting to preset values if empty
    scale = float(scale_input.text()) if scale_input.text() else 7.0
    sigma = float(sigma_input.text()) if sigma_input.text() else 0.5
    min_size = int(min_size_input.text()) if min_size_input.text() else 2

    # Apply the segmentation and merging process to the selected image layer
    segmented_img = felzenszwalb_segmentation_and_merging(image, scale=scale, sigma=sigma, min_size=min_size)

    # Display the segmented image in the viewer
    from pycat.ui.ui_utils import add_image_with_default_colormap
    add_image_with_default_colormap(segmented_img, viewer, name=f"Felzenszwalb Segmented {active_layer.name}")


@tags_layer('felzenszwalb_binary', role='mask', inputs=('image',),
            summary='Felzenszwalb segmentation, binarised')
def fz_segmentation_and_binarization(image, mask, ball_radius, rim_close_radius=5,
                                     rim_close_min_result_area=150):
    """
    Applies Felzenszwalb's segmentation method followed by additional processing to convert the segmented
    image into a refined binary mask. This involves contrast adjustments, morphological operations, and local
    thresholding to highlight distinct objects within a specified region of interest. Additionally, external 
    contours are detected and filled to ensure solid object representation in the binary mask.

    Parameters
    ----------
    image : numpy.ndarray
        The input grayscale image for segmentation.
    mask : numpy.ndarray
        A binary mask defining the region of interest where segmentation is focused.
    ball_radius : int
        The radius influencing the segmentation sensitivity and scale, particularly used in local thresholding.
    rim_close_radius : int, optional
        Radius of the morphological closing used to bridge fragmented rim
        pieces of large, hollowed condensates before hole-filling (see notes
        below). This is a small, FIXED scale independent of ball_radius --
        it corresponds to the small-scale (disk(1)) morphological/Gabor
        operations upstream that fragment a large condensate's rim, not to
        the size of the condensate itself. Do not scale this with
        ball_radius: at large ball_radius values that would bridge gaps of
        100+ px and erroneously merge unrelated, well-separated objects
        across the whole image. Increase only if real large-condensate rims
        are still visibly broken after this default; default is 5. Safe to
        tune upward even for densely-packed small puncta -- see
        rim_close_min_result_area, which prevents this from deforming or
        fusing small objects.
    rim_close_min_result_area : int, optional
        A closing/fill result is only kept where the resulting connected
        component is at least this large (px); smaller components revert to
        their pre-closing shape. This is what makes rim_close_radius safe to
        set generously for large-condensate bridging without deforming or
        fusing nearby, genuinely distinct small puncta -- closing a cluster
        of small puncta rarely produces a component this large, so they
        keep their original compact shape, while a real fragmented
        large-condensate rim reliably does. Default is 150 (well above a
        single ~7px-diameter punctum's area, well below a genuine large
        condensate's).

    Returns
    -------
    boolean_mask : numpy.ndarray
        A binary mask refined from the segmented image, highlighting detected objects within the region defined by the input mask.

    Notes
    -----
    - The process dynamically adjusts to the 'ball_radius' to ensure appropriate scale processing for different image details.
    - A correct 'ball_radius' is crucial for optimal segmentation and post-processing results.
    - The function assumes the input image has undergone basic preprocessing for noise reduction and contrast enhancement.
    - The binary mask is further processed through morphological operations and local thresholding to ensure a clean and usable output.
    """

    img = dtype_conversion_func(image, output_bit_depth='float32') # Convert image to float32 for processing
    object_radius = ball_radius / 1.5  # Adjust object radius based on ball_radius for segmentation scale
    
    # Perform initial segmentation with adjusted parameters
    fz_segmented_img = felzenszwalb_segmentation_and_merging(img, scale=object_radius, sigma=0.5, min_size=2)

    # Check image contrast and return empty mask if insufficient for segmentation
    contrast_flag = check_contrast_func(fz_segmented_img)
    if contrast_flag:
        return np.zeros_like(img, dtype=bool)
    
    clip_limit = 0.0025  # Adaptive histogram equalization parameter
    k_size = math.ceil(ball_radius * 4)  # Set a window size of ~ 2x larger than the object diameter for CLAHE
    # Enhance segmented image using adaptive histogram equalization
    segmented_img = sk.exposure.equalize_adapthist(fz_segmented_img, kernel_size=k_size, clip_limit=clip_limit)

    # Apply morphological operations to smooth the segmented image
    segmented_img = ndi.grey_dilation(segmented_img, footprint=sk.morphology.disk(1))
    segmented_img = ndi.grey_erosion(segmented_img, footprint=sk.morphology.disk(1))
    
    # Further smooth the image using Gaussian filtering
    segmented_img = ndi.gaussian_filter(segmented_img, sigma=0.5)
    #viewer.add_image(segmented_img, name='Segmented Image')

    # Refine segmentation into a binary mask using local thresholding
    segmented_mask = local_thresholding_func(segmented_img, int(ball_radius))

    # ── Absolute-brightness rescue for locally-uniform bright regions ──────
    # Niblack/Sauvola are LOCAL, CONTRAST-based thresholds: a pixel passes
    # only if it's bright relative to its immediate window_size=ball_radius
    # neighborhood. Deep inside a large, flat, saturated condensate, that
    # local neighborhood is essentially uniform -- local std collapses toward
    # 0, so even a pixel far brighter than the whole image's background can
    # fail the local test purely because its surroundings look like itself.
    # This is a structural blind spot, independent of anything upstream: it
    # persists even after the object is correctly preserved through
    # pre-processing and enhancement, and explains large condensates being
    # segmented as a thin rim/partial coverage rather than their full extent.
    # A coarse, scale-independent ABSOLUTE brightness criterion (Otsu on this
    # image/ROI) is OR-combined in to rescue exactly this case: pixels that
    # are clearly bright relative to the whole image, even where local
    # contrast is near zero. This is deliberately coarse and used only as an
    # OR-addition (never removes anything local thresholding already found),
    # so it cannot make small/medium puncta detection any less sensitive.
    try:
        otsu_thresh = sk.filters.threshold_otsu(segmented_img)
        bright_mask = segmented_img > otsu_thresh
        segmented_mask = np.logical_or(segmented_mask, bright_mask)
    except ValueError:
        # threshold_otsu can raise on a degenerate (near-constant) image;
        # local_thresholding_func's result alone is used in that case.
        pass

    # ── Large-condensate rim bridging ───────────────────────────────────
    # The upstream ball_radius-scale enhancement (white top-hat / Gaussian
    # background division) is a band-pass operation: it suppresses the flat,
    # uniform interior of condensates that are large relative to ball_radius,
    # leaving only their curved rim. Local (Niblack/Sauvola) thresholding on
    # that rim-only signal often breaks it into a scatter of disconnected
    # fragments rather than one continuous ring -- a "necklace" of small
    # puncta instead of one solid object. The binary_fill_holes call below
    # only closes a hole that's already fully enclosed by a continuous ring;
    # it can't do anything for a ring that's broken into pieces. A
    # morphological closing first bridges those gaps into a continuous loop
    # so the fill (and the external-contour fill downstream) can recover the
    # object's full extent.
    #
    # IMPORTANT: rim_close_radius is intentionally NOT scaled with
    # ball_radius. The fragmentation gap size comes from small, fixed-scale
    # operations upstream (disk(1) erosion/dilation, Gabor filtering), not
    # from the condensate's own size. An earlier version of this fix set
    # close_radius = ball_radius, which at realistic ball_radius values
    # (e.g. 75) applies a ~150px-wide closing to the whole image and
    # erroneously fuses distinct, well-separated puncta/condensates
    # anywhere in the mask -- corrupting segmentation broadly, not just for
    # large condensates. Keeping this small and fixed avoids that.
    close_radius = max(1, int(rim_close_radius))
    closed = ndi.binary_closing(segmented_mask, structure=sk.morphology.disk(close_radius))
    filled_closed = ndi.binary_fill_holes(closed)

    # Only ACCEPT the closing/fill result where it produces a sufficiently
    # large object. For an isolated small punctum (or several genuinely
    # distinct, closely-spaced small puncta), the closing can subtly deform
    # or -- at larger rim_close_radius values -- bridge them into elongated
    # "worm" shapes instead of the clean, compact round dots local
    # thresholding originally found. Gating by the RESULTING component size
    # (rather than by rim_close_radius alone) means small puncta always keep
    # their original, un-closed shape, regardless of how large
    # rim_close_radius needs to be tuned to bridge a real large-condensate
    # rim. Only components that end up at/above rim_close_min_result_area
    # (i.e., plausibly a bridged large-condensate rim, not ordinary puncta)
    # get the closed/filled version; everything else reverts to the
    # pre-closing mask.
    lbl_closed = sk.measure.label(filled_closed)
    if lbl_closed.max() > 0:
        areas = ndi.sum(np.ones_like(lbl_closed), lbl_closed, range(1, lbl_closed.max() + 1))
        large_labels = np.where(areas >= rim_close_min_result_area)[0] + 1
        accept = np.isin(lbl_closed, large_labels)
    else:
        accept = np.zeros_like(filled_closed, dtype=bool)
    segmented_mask = np.where(accept, filled_closed, segmented_mask)

    # Determine the maximum area for objects based on the input cell mask.
    # This is intentionally permissive (previously a hard 25% cap): rejecting
    # objects purely for being large also throws away genuine large/coarsened
    # condensates. A more informed, shape-aware rejection of implausible
    # (e.g., erroneously merged) large objects happens later in
    # puncta_refinement_filtering_func, once solidity is available.
    max_area = (np.sum(mask.astype(bool)) * 0.9)

    # Detect external contours and fill them to ensure solid object representation
    contour_mask = opencv_contour_func(segmented_mask, max_area=max_area)

    # Explicitly fill any residual interior holes. Local (Niblack/Sauvola)
    # thresholding hollows out large bright flat cores into rings; the external
    # contour fill above closes most of these, but this guarantees fully solid
    # objects (e.g. when a ring didn't fully close) so bright condensates are not
    # left partially segmented.
    contour_mask = ndi.binary_fill_holes(contour_mask.astype(bool)).astype(contour_mask.dtype)

    # Combine with the eroded input mask to refine the final mask and reduce edge artifacts
    boolean_mask = (contour_mask * ndi.binary_erosion(mask, sk.morphology.disk(1))).astype(bool)

    # Dilate the mask to ensure objects are fully covered
    boolean_mask = binary_morph_operation(boolean_mask, iterations=1, element_size=1, element_shape='Disk', mode='Dilation')

    return boolean_mask
