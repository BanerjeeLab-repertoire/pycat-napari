"""
**Putting pixels into napari. The last step, and the one every loader shares.**

``load_into_viewer`` is what the 2-D loader, the mask loader and both stack loaders **all** call once
they have an array: normalise the dtype, add the layer, tag it, enable the scale bar.

── Why it comes out here ────────────────────────────────────────────────────────────────

It is a dependency of **five** other methods, and it depended on **two** — both of which had already
been extracted (``_enable_auto_scale_bar`` -> ``napari_adapter``, ``_tag_loaded_layer`` ->
``tagging``). *Taking it now unblocks the tier above it.*

``determine_file_format_and_process_data`` comes with it: a **ten-line legacy shim** that touched
``self`` for nothing at all.
"""

from __future__ import annotations

import numpy as np

from pycat.file_io.napari_adapter import _enable_auto_scale_bar
from pycat.file_io.tagging import _tag_loaded_layer
from pycat.utils.general_utils import debug_log, dtype_conversion_func

# `add_image_with_default_colormap` lives in `pycat.ui.ui_utils`, which imports napari at module
# scope. Importing it HERE at module scope would drag napari into every importer of `viewer_load`,
# including headless `core` tests (test_load_into_viewer_scale) that have no napari installed and
# monkeypatch this name to run without a viewer. So it is bound LAZILY inside `load_into_viewer`
# below. The module-level name is declared here so tests can
# `monkeypatch.setattr(viewer_load, 'add_image_with_default_colormap', ...)` and the function will
# pick up the patched value instead of importing the real (napari-touching) one.
add_image_with_default_colormap = None  # bound lazily in load_into_viewer; patchable in tests


def load_into_viewer(viewer, central_manager, data, name, is_mask=False):
    """
    Loads the given data into the Napari viewer, distinguishing between image and mask data, and applies appropriate 
    visual representations.

    Parameters
    ----------
    data : array-like
        The image or mask data to be loaded into the viewer.
    name : str
        The name to assign to the layer in the viewer.
    is_mask : bool, optional
        A flag indicating whether the data is a mask, defaults to False.

    Notes
    -----
    This method ensures that mask data is loaded as label layers and image data as image layers. It handles data type 
    conversions and scaling to optimize visualization within the Napari environment.
    """
    if is_mask:
        # If it's a mask, skip conversion to float and ensure it's int type
        if np.issubdtype(data.dtype, np.integer):
            data = data.astype(int) if not np.issubdtype(data.dtype, int) else data
        # Add the mask to the viewer
        viewer.add_labels(data, name=name)
        # Tag: this is a mask (role/provenance), 2D dimensionality.
        try:
            if len(viewer.layers):
                _mpp = None
                try:
                    _mps = central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
                    _mpp = (float(_mps) ** 0.5) if _mps else None
                except Exception:
                    _mpp = None
                _tag_loaded_layer(central_manager, 
                    viewer.layers[-1], role='mask', n_t=1, n_z=1,
                    microns_per_pixel=_mpp, provenance='segmentation')
        except Exception as _e:
            debug_log("file_io: 2D mask tagging failed", _e)
    else:
        # Handle as before for images
        if np.issubdtype(data.dtype, np.integer):
            if np.issubdtype(data.dtype, np.signedinteger):
                data = data.astype(np.uint16)
        elif np.issubdtype(data.dtype, np.floating):
            # A float IMAGE is by contract already DTYPE-MAX [0, 1] (`to_unit_float32` /
            # `dtype_conversion_func` in the loaders). PASS IT THROUGH — do NOT min-max
            # (`apply_rescale_intensity`), which is a per-frame, offset-injecting contrast stretch
            # that `utils/intensity_semantics` classifies DESTROYED: it forces every frame's brightest
            # pixel to exactly 1.0 (false-tripping the saturation ceilings in partition/qc), injects
            # the frame's dark-noise floor as an offset (corrupting partition-coefficient RATIOS —
            # `partition_coefficient_local` REFUSES a min-max layer), and is frame-dependent (breaking
            # any intensity time-course). The loaders now hand in dtype-max [0,1] (or integer, handled
            # above), so raw-counts floats no longer arrive here. A frame already in range must not be
            # rescaled a second time. See tests/test_loaders_agree_on_scale.py.
            data = data.astype(np.float32)
        data = dtype_conversion_func(data, 'float32')  # Ensure image data is correct float32 dtype
        # Add the image to the viewer
        # Lazy import (see module-level note): keeps napari out of headless importers; a test's
        # monkeypatched value on this module takes precedence over the real import.
        _add_image = add_image_with_default_colormap
        if _add_image is None:
            from pycat.ui.ui_utils import add_image_with_default_colormap as _add_image
        _add_image(data, viewer, name=name)
        # Stash the current file's metadata on the layer so a later
        # multi-image comparison can diff acquisition settings per-layer even
        # though data_repository['file_metadata'] is overwritten on each load.
        try:
            _md = central_manager.active_data_class.data_repository.get('file_metadata')
            if _md is not None and len(viewer.layers):
                viewer.layers[-1].metadata['pycat_file_metadata'] = _md
        except Exception:
            pass
        # Tag: this is a 2D image (role/dimensionality/scale/provenance);
        # channel identity from the layer name (metadata-driven naming already
        # applied it upstream).
        try:
            if len(viewer.layers):
                _mpp = None
                try:
                    _mps = central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
                    _mpp = (float(_mps) ** 0.5) if _mps else None
                except Exception:
                    _mpp = None
                _tag_loaded_layer(central_manager, 
                    viewer.layers[-1], role='image', n_t=1, n_z=1,
                    microns_per_pixel=_mpp,
                    channel=getattr(viewer.layers[-1], 'name', None),
                    provenance='raw')
        except Exception as _e:
            debug_log("file_io: 2D image tagging failed", _e)
        # Auto scale bar for the freshly-loaded 2D image.
        _enable_auto_scale_bar(viewer, central_manager)


def determine_file_format_and_process_data(viewer, central_manager, layer_type, data):
    """Legacy helper kept for compatibility; new code uses _save_layer."""
    if layer_type in ['Labels', 'Shapes']:
        return ".png", dtype_conversion_func(data, 'uint16')
    elif layer_type == 'Image':
        if data.ndim == 3:
            return ".png", dtype_conversion_func(data, 'uint8')
        else:
            return ".tiff", dtype_conversion_func(data, 'uint16')
    else:
        return ".dat", data
