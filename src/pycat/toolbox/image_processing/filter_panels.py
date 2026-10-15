"""The filters' viewer panels -- split out of filters.py (OperationService adoption).

Each ``run_*`` reads the active layer and the panel's inputs, runs the filter through ``OperationService`` (so the
panel and its batch replay compute the same thing) and adds the result as a layer. The pure filters stay in
``filters.py``. Moved VERBATIM apart from that.
"""
from __future__ import annotations

import math

from pycat.toolbox.image_processing._base import (
  _add_image,
  _napari,
  _safe_equalize_adapthist,
)
from pycat.utils.general_utils import dtype_conversion_func
from pycat.utils.notify import show_warning as napari_show_warning


def run_peak_and_edge_enhancement(data_instance, viewer):
  """
  Applies peak and edge enhancement techniques to the currently active image layer in a Napari viewer. The enhancement
  process includes Gabor filtering, morphological operations, Gaussian smoothing, and adaptive histogram equalization.

  Parameters
  ----------
  viewer : napari.Viewer
      The viewer containing the image layer to be enhanced.

  Raises
  ------
  Error
      If no active image layer is selected, preventing the function from proceeding.

  Notes
  -----
  The function retrieves the currently active image layer, applies the `peak_and_edge_enhancement_func`, and adds the
  enhanced image back as a new layer to the viewer.
  """

  ball_radius = math.ceil(data_instance.data_repository['ball_radius'])

  # Retrieve the currently active image layer from the viewer
  active_layer = viewer.layers.selection.active

  # Validate that an active layer is selected
  if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
      # Raise an error if no layer is currently active
      raise ValueError("No active image layer selected")

  # Retrieve the image data from the active layer
  image = active_layer.data

  # Apply the peak and edge enhancement function to the input image
  from pycat.kernel import OperationService
  enhanced_image = OperationService.produce('peak_edge', {'image': image}, {'ball_radius': ball_radius})

  # Add the enhanced image as a new layer to the viewer with a descriptive name
  _add_image(enhanced_image, viewer, name=f"Peak & Edge Enhanced {active_layer.name}", operation='log_enhance')


def run_apply_laplace_of_gauss_filter(sigma_input, viewer):
    """
    Applies the Laplacian of Gaussian (LoG) filter to the currently active image layer in a Napari viewer, 
    using a user-specified sigma value from UI input. This enhances the image by highlighting edges through LoG filtering.

    Parameters
    ----------
    sigma_input : UI Element
        A UI element that allows the user to input the sigma value for the LoG filter.
    viewer : napari.Viewer
        The Napari viewer instance where the image layer is displayed and processed.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer, prevent the application of the filter.
    """

    active_layer = viewer.layers.selection.active

    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        raise ValueError("No active image layer selected.")
    
    sigma = float(sigma_input.text()) if sigma_input.text() else 3

    # Apply the LoG filter to the input image
    from pycat.kernel import OperationService
    LoG_image = OperationService.produce('log', {'image': image}, {'sigma': sigma})

    # Add the LoG filtered image to the viewer
    _add_image(LoG_image, viewer, name=f"LoG of {active_layer.name}", operation='log_enhance')


def run_morphological_gaussian_filter(filter_size_input, viewer):
    """
    Applies morphological operations and Gaussian smoothing to the active image layer in the Napari viewer,
    enhancing structural features and reducing noise. The process involves morphological dilation and erosion
    followed by Gaussian smoothing, with the results added as a new layer to the viewer.

    Parameters
    ----------
    filter_size_input : text input
        A user interface element that allows the user to input the filter size, influencing the extent of the
        morphological operations and Gaussian smoothing.
    viewer : Viewer
        An image viewer object that contains image layers, such as in a Napari viewer.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer.

    Notes
    -----
    The filter size from the user input determines the size of the disk-shaped structural element used for
    morphological dilation and erosion, directly impacting the degree of feature enhancement and noise reduction.
    """

    # Retrieve the currently active image layer from the viewer
    active_layer = viewer.layers.selection.active
    
    # Validate that an active layer is selected
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        # Raise an error if no layer is currently active
        raise ValueError("No active image layer selected")
    
    # Determine the filter size from the user input or default to 2
    filter_size = int(filter_size_input.text()) if filter_size_input.text() else 2
    from pycat.kernel import OperationService
    image = OperationService.produce('morph_gaussian', {'image': active_layer.data}, {'filter_size': filter_size})

    # Add the processed image as a new layer to the viewer with a descriptive name
    _add_image(image, viewer, name=f"Filtered {active_layer.name}", operation='gaussian_blur')


def run_clahe(clip_input, k_size_input, viewer):
    """
    Applies Contrast Limited Adaptive Histogram Equalization (CLAHE) to the currently active image layer in the Napari
    viewer. This technique enhances the contrast of the image by dividing it into small blocks and applying histogram
    equalization to each block independently, limiting the amplification of noise common in standard methods.

    Parameters
    ----------
    clip_input : UI Element (Text Input)
        A UI element that allows the user to input the clip limit value for CLAHE.
    k_size_input : UI Element (Text Input)
        A UI element that allows the user to input the kernel size for CLAHE.
    viewer : napari.Viewer
        The Napari viewer instance where the image layer is displayed and processed.

    Raises
    ------
    Error
        If no active image layer is selected in the viewer.

    Notes
    -----
    The function processes the image by converting it to float32 for enhanced precision during CLAHE and then converts
    it back to its original data type. The clip limit and kernel size are adjustable, allowing for fine-tuning of the
    contrast enhancement based on specific image requirements.
    """
    
    # Retrieve the currently active image layer from the viewer
    active_layer = viewer.layers.selection.active
    
    # Validate that an active layer is selected
    if active_layer is None or not isinstance(active_layer, _napari().layers.Image):
        # Raise an error if no layer is currently active
        raise ValueError("No active image layer selected")
    
    input_dtype = str(active_layer.data.dtype)  # Store the input data type for conversion back at the end
    # Retrieve the image data from the active layer
    image = active_layer.data
    # Convert the image to float32 for processing
    img = dtype_conversion_func(image, output_bit_depth='float32')
    
    # Retrieve clip limit and kernel size from the UI input, falling back to defaults if necessary
    clip_val = float(clip_input.text()) if clip_input.text() else 0.0025
    k_size = int(k_size_input.text()) if k_size_input.text() else None

    # The number of bins is set dynamically based on the image height
    no_bins = image.shape[0]

    # Apply CLAHE to the image
    CLAHE_img = _safe_equalize_adapthist(img, kernel_size=k_size, clip_limit=clip_val, nbins=no_bins)

    # Convert the processed image back to its original data type
    CLAHE_img = dtype_conversion_func(CLAHE_img, output_bit_depth=input_dtype)

    # Add the CLAHE-enhanced image as a new layer to the Napari viewer
    _add_image(CLAHE_img, viewer, name=f"CLAHE Contrast EQed {active_layer.name}", operation='clahe')


def run_apply_bilateral_filter(radius_input, viewer):
    """
    Applies a bilateral filter to an active image layer in a Napari viewer to reduce noise while preserving 
    important details. The filter radius is retrieved from the user's input.

    Parameters
    ----------
    radius_input : QLineEdit
        The input field where users specify the filter radius.
    viewer : napari.Viewer
        The viewer instance where the processed image will be displayed.

    Raises
    ------
    Error
        If no active image layer is selected.

    Notes
    ----
    The function retrieves the radius from the input, applies the bilateral filter, and displays the result as a 
    new layer in the viewer, facilitating immediate visual feedback.
    """

    active_layer = viewer.layers.selection.active
    # Check if their is an active layer, and that it is a Napari image layer
    if active_layer is not None and isinstance(active_layer, _napari().layers.Image):
        image = active_layer.data
    else:
        napari_show_warning("No active image layer selected.")
        return

    # Get the radius value from the input field
    radius = float(radius_input.text()) if radius_input.text() else 2

    # Apply the bilateral filter to the image
    from pycat.kernel import OperationService
    filtered_image = OperationService.produce('bilateral', {'image': image}, {'radius': radius})

    # Add the filtered image as a new layer to the viewer
    _add_image(filtered_image, viewer, name=f"Bilateral Filtered {active_layer.name}")
