"""
Spatial Autocorrelation Function (SACF) Analysis Module for PyCAT
=================================================================

Performs spatial autocorrelation function (SACF) analysis on 2D images or
z-stack / timelapse data, yielding Gaussian sigma values (cluster size
estimates) per cell and per slice.

Three ROI modes
---------------
LIR — default and recommended
    The largest interior rectangle inscribed within each labeled cell mask
    is found using ``largestinteriorrectangle``.  Every pixel in the crop is
    real fluorescence data, avoiding the spectral leakage artifacts that
    occur when zeroed background pixels are fed into the FFT.

Drawn rectangle
    The user draws one or more rectangles on a napari Shapes layer and
    selects it in the widget.  Each rectangle in the layer defines one ROI;
    all slices of the stack are analysed within that fixed ROI.  Useful for
    in-vitro droplet data where there are no cells to segment.

Whole image
    No ROI at all — the entire image frame is passed to the FFT at each
    slice.  Equivalent to your original script when run without segmentation
    (``labelim = np.ones(...)``).

Algorithm (per ROI per slice)
-----------------------------
1. Crop to the ROI (LIR rectangle, drawn rectangle, or full image).
2. Compute the 2D SACF via FFT (Wiener-Khinchin theorem), normalised to
   [0, 1], zero-frequency centred.
3. Extract central x- and y-axis slices through the SACF peak.
4. Isolate the central lobe between its flanking local minima.
5. Fit a Gaussian; record sigma (2·sigma ≈ cluster diameter in pixels/µm).

Integration
-----------
1. Drop this file into  src/pycat/toolbox/
2. In ui_modules.py ToolboxFunctionsUI.__init__ add:
       from pycat.toolbox.spatial_acf_tools import _add_run_sacf_analysis
       self._add_run_sacf_analysis = lambda **kw: _add_run_sacf_analysis(self, **kw)
3. In MenuManager._add_toolbox_to_menu add a 'Spatial Metrology' submenu:
       spatial_metrology_submenu = self.toolbox_menu.addMenu('Spatial Metrology')
       spatial_metrology_actions = {
           'Spatial ACF Analysis': (
               self.central_manager.toolbox_functions_ui._add_run_sacf_analysis,
               {'separate_widget': True}
           )
       }
       self._add_actions_to_menu(spatial_metrology_actions, spatial_metrology_submenu)

Author
------
    Christian Neureuter / Gable Wadsworth, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

# Standard library imports
import warnings
import time

# Third-party imports
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import largestinteriorrectangle as lir
from scipy.optimize import curve_fit
from scipy.signal import argrelextrema
import napari
from napari.utils.notifications import (
    show_warning as napari_show_warning,
    show_info as napari_show_info,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QPushButton, QWidget, QComboBox, QButtonGroup,
    QRadioButton, QHBoxLayout,
)

# Local application imports
from pycat.toolbox.correlation_func_analysis_tools import calculate_autocorrelation
from pycat.ui.ui_utils import show_dataframes_dialog


# ---------------------------------------------------------------------------
# ROI extraction helpers
# ---------------------------------------------------------------------------

def _lir_crop(image, binary_mask):
    """
    Crop ``image`` to the largest interior rectangle inscribed in
    ``binary_mask``.

    Parameters
    ----------
    image : np.ndarray, shape (H, W)
    binary_mask : np.ndarray, shape (H, W), bool

    Returns
    -------
    roi : np.ndarray or None
        Cropped rectangular patch.  None if the rectangle is degenerate
        (< 4 px in either dimension).
    rect : tuple (x, y, w, h) in pixel coords, or None
    """
    rect = lir.lir(binary_mask.astype(bool))
    x, y, w, h = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
    print(f"[PyCAT SACF]     LIR result: x={x} y={y} w={w} h={h} (mask area={binary_mask.sum()}px)")
    if w < 2 or h < 2:
        print(f"[PyCAT SACF]     LIR rectangle degenerate — cell mask may be too thin or curved.")
        return None, None
    return image[y:y + h, x:x + w].copy().astype(float), (x, y, w, h)


def _rect_from_shape_vertices(vertices, image_shape):
    """
    Convert a napari rectangle's 4 corner vertices (row, col order) to an
    axis-aligned bounding box clipped to the image.

    napari stores each shape as an (N, 2) array of [row, col] coordinates.
    For a rectangle drawn with the rectangle tool N=4.  We take the min/max
    of each axis to get an axis-aligned crop even if the rectangle was drawn
    at a slight angle.

    Parameters
    ----------
    vertices : np.ndarray, shape (N, 2)
        [row, col] coordinates of the shape vertices.
    image_shape : tuple (H, W)

    Returns
    -------
    tuple (row_min, row_max, col_min, col_max) clipped to image bounds,
    or None if the resulting box is degenerate.
    """
    rows = vertices[:, 0]
    cols = vertices[:, 1]
    r0 = max(int(np.floor(rows.min())), 0)
    r1 = min(int(np.ceil(rows.max())), image_shape[0])
    c0 = max(int(np.floor(cols.min())), 0)
    c1 = min(int(np.ceil(cols.max())), image_shape[1])
    if (r1 - r0) < 4 or (c1 - c0) < 4:
        return None
    return r0, r1, c0, c1


def _crops_from_shapes_layer(image, shapes_layer):
    """
    Extract one crop per shape in ``shapes_layer``.

    Only rectangle shapes are used; other shape types are skipped with a
    warning.  Returns a list of (roi_array, label_str) tuples.

    Parameters
    ----------
    image : np.ndarray, shape (H, W)
    shapes_layer : napari.layers.Shapes

    Returns
    -------
    list of (np.ndarray, str)
        Each entry is (cropped_image, descriptive_label).
    """
    crops = []
    for i, (vertices, stype) in enumerate(
        zip(shapes_layer.data, shapes_layer.shape_type)
    ):
        if stype != 'rectangle':
            napari_show_warning(
                f"SACF: Shape {i} is '{stype}', not a rectangle — skipped. "
                "Only rectangle shapes are supported."
            )
            continue
        box = _rect_from_shape_vertices(np.array(vertices), image.shape)
        if box is None:
            napari_show_warning(f"SACF: Rectangle {i} is too small — skipped.")
            continue
        r0, r1, c0, c1 = box
        roi = image[r0:r1, c0:c1].copy().astype(float)
        crops.append((roi, f"rect_{i}_r{r0}-{r1}_c{c0}-{c1}"))
    return crops


# ---------------------------------------------------------------------------
# Core SACF math
# ---------------------------------------------------------------------------

def _gaussian(x, amplitude, mean, sigma):
    """Simple 1D Gaussian (no offset)."""
    return amplitude * np.exp(-((x - mean) ** 2) / (2.0 * sigma ** 2))


def _isolate_central_mode(y_axis, x_axis):
    """
    Trim a 1D SACF slice to the central lobe by bracketing the global
    maximum with its nearest flanking local minima (Gable's original logic).
    """
    tmax = int(np.argmax(y_axis))
    tmin_idx = argrelextrema(y_axis, np.less)[0]

    if len(tmin_idx) == 0:
        return y_axis, x_axis
    elif len(tmin_idx) == 1:
        if tmin_idx[0] > tmax:
            return y_axis[:tmin_idx[0]], x_axis[:tmin_idx[0]]
        else:
            return y_axis[tmin_idx[0]:], x_axis[tmin_idx[0]:]
    else:
        pos = int(np.searchsorted(tmin_idx, tmax))
        left  = tmin_idx[max(pos - 1, 0)]
        right = tmin_idx[min(pos, len(tmin_idx) - 1)]
        return y_axis[left:right], x_axis[left:right]


def _fit_sacf_1d(y_axis, x_axis):
    """Isolate central lobe and fit a Gaussian. Returns sigma or np.nan."""
    y_trim, x_trim = _isolate_central_mode(y_axis, x_axis)
    if len(y_trim) <= 3:
        return np.nan
    try:
        popt, _ = curve_fit(
            _gaussian, x_trim, y_trim,
            p0=[1.0, float(x_trim[int(np.argmax(y_trim))]), 1.0],
            maxfev=2000,
        )
        return abs(popt[2])
    except (RuntimeError, ValueError):
        return np.nan


def sacf_single_roi(roi_image):
    """
    Compute the 2D SACF of a pre-cropped rectangular ROI and return
    Gaussian sigma values for the central x- and y-axis slices.

    Parameters
    ----------
    roi_image : np.ndarray, shape (H, W)
        Rectangular image patch — all pixels must be real data (no zeroing).

    Returns
    -------
    sigma_x : float   (np.nan on failure)
    sigma_y : float   (np.nan on failure)
    sacf    : np.ndarray  full 2D SACF, normalised [0,1], zero-freq centred
    """
    sacf = calculate_autocorrelation(roi_image)
    centre = np.unravel_index(np.argmax(sacf), sacf.shape)

    x_coords = np.linspace(-sacf.shape[0] / 2, sacf.shape[0] / 2, sacf.shape[0])
    sigma_x = _fit_sacf_1d(sacf[:, centre[1]], x_coords)

    y_coords = np.linspace(-sacf.shape[1] / 2, sacf.shape[1] / 2, sacf.shape[1])
    sigma_y = _fit_sacf_1d(sacf[centre[0], :], y_coords)

    return sigma_x, sigma_y, sacf


# ---------------------------------------------------------------------------
# Per-mode analysis drivers
# ---------------------------------------------------------------------------

def _make_record(slice_idx, roi_label, sigma_x, sigma_y,
                 microns_per_pixel, extra=None):
    """Build a result record dict."""
    r = {
        'slice': slice_idx,
        'roi': roi_label,
        'sigma_x_px': sigma_x,
        'sigma_y_px': sigma_y,
        'sigma_x_um': sigma_x * microns_per_pixel if not np.isnan(sigma_x) else np.nan,
        'sigma_y_um': sigma_y * microns_per_pixel if not np.isnan(sigma_y) else np.nan,
        'diameter_x_um': 2 * sigma_x * microns_per_pixel if not np.isnan(sigma_x) else np.nan,
        'diameter_y_um': 2 * sigma_y * microns_per_pixel if not np.isnan(sigma_y) else np.nan,
    }
    if extra:
        r.update(extra)
    return r


def sacf_lir_mode(stack, labeled_cell_mask, microns_per_pixel=1.0):
    """
    LIR mode: largest interior rectangle per cell per slice.

    Parameters
    ----------
    stack : np.ndarray (N, H, W) or (H, W)
    labeled_cell_mask : np.ndarray (H, W), integer labels, 0 = background
    microns_per_pixel : float

    Returns
    -------
    pd.DataFrame  columns: slice, roi (= cell label), lir_x/y/w/h,
                           sigma_x/y_px, sigma_x/y_um, diameter_x/y_um
    """
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]
    cell_labels = np.unique(labeled_cell_mask)
    cell_labels = cell_labels[cell_labels != 0]
    records = []

    for slice_idx in range(stack.shape[0]):
        napari_show_info(f"[SACF] Slice {slice_idx + 1}/{stack.shape[0]} …")
        print(f"[PyCAT SACF] Slice {slice_idx + 1}/{stack.shape[0]}")
        slice_img = stack[slice_idx]

        for cell_label in cell_labels:
            binary_mask = labeled_cell_mask == cell_label
            print(f"[PyCAT SACF]   Cell {cell_label}: mask pixels={binary_mask.sum()}")
            roi, rect = _lir_crop(slice_img, binary_mask)
            extra = {
                'lir_x': rect[0] if rect else np.nan,
                'lir_y': rect[1] if rect else np.nan,
                'lir_w': rect[2] if rect else np.nan,
                'lir_h': rect[3] if rect else np.nan,
            }
            if roi is None:
                records.append(_make_record(
                    slice_idx, int(cell_label), np.nan, np.nan,
                    microns_per_pixel, extra))
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sx, sy, _ = sacf_single_roi(roi)
            records.append(_make_record(
                slice_idx, int(cell_label), sx, sy,
                microns_per_pixel, extra))

    return pd.DataFrame(records)


def sacf_drawn_rect_mode(stack, shapes_layer, microns_per_pixel=1.0):
    """
    Drawn-rectangle mode: one fixed ROI per rectangle shape per slice.

    Each rectangle in the Shapes layer is used as an ROI.  All slices of
    the stack are analysed within each rectangle.  Useful for in-vitro
    data where you want to analyse a specific field region across a z-stack
    or timelapse without cell segmentation.

    Parameters
    ----------
    stack : np.ndarray (N, H, W) or (H, W)
    shapes_layer : napari.layers.Shapes
    microns_per_pixel : float

    Returns
    -------
    pd.DataFrame  columns: slice, roi (= rect label), sigma_x/y_px,
                           sigma_x/y_um, diameter_x/y_um
    """
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]
    records = []

    # Pre-extract rectangle bounding boxes from the shapes layer
    rects = []
    for i, (vertices, stype) in enumerate(
        zip(shapes_layer.data, shapes_layer.shape_type)
    ):
        if stype != 'rectangle':
            napari_show_warning(
                f"SACF: Shape {i} is '{stype}' — only rectangles are used."
            )
            continue
        box = _rect_from_shape_vertices(np.array(vertices), stack.shape[1:])
        if box is None:
            napari_show_warning(f"SACF: Rectangle {i} is too small — skipped.")
            continue
        rects.append((f"rect_{i}", box))

    if not rects:
        napari_show_warning("SACF: No valid rectangles found in the Shapes layer.")
        return pd.DataFrame()

    for slice_idx in range(stack.shape[0]):
        napari_show_info(f"[SACF] Slice {slice_idx + 1}/{stack.shape[0]} …")
        print(f"[PyCAT SACF] Slice {slice_idx + 1}/{stack.shape[0]}")
        slice_img = stack[slice_idx]

        for rect_label, (r0, r1, c0, c1) in rects:
            roi = slice_img[r0:r1, c0:c1].copy().astype(float)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sx, sy, _ = sacf_single_roi(roi)
            records.append(_make_record(slice_idx, rect_label, sx, sy,
                                        microns_per_pixel))

    return pd.DataFrame(records)


def sacf_whole_image_mode(stack, microns_per_pixel=1.0):
    """
    Whole-image mode: run SACF on the full frame at each slice.

    Equivalent to Gable's original script with ``labelim = np.ones(...)``.

    Parameters
    ----------
    stack : np.ndarray (N, H, W) or (H, W)
    microns_per_pixel : float

    Returns
    -------
    pd.DataFrame  columns: slice, roi, sigma_x/y_px, sigma_x/y_um,
                           diameter_x/y_um
    """
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]
    records = []

    for slice_idx in range(stack.shape[0]):
        napari_show_info(f"[SACF] Slice {slice_idx + 1}/{stack.shape[0]} …")
        print(f"[PyCAT SACF] Slice {slice_idx + 1}/{stack.shape[0]}")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sx, sy, _ = sacf_single_roi(stack[slice_idx].astype(float))
        records.append(_make_record(slice_idx, 'whole_image', sx, sy,
                                    microns_per_pixel))

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_sigma_vs_slice(results_df):
    """
    Plot mean cluster diameter (µm) vs slice with ± 1 SD shading.
    When there is only one ROI per slice (drawn-rect or whole-image with a
    single rect), SD shading is omitted.
    """
    slices = sorted(results_df['slice'].unique())
    grouped = results_df.groupby('slice')

    mean_x = grouped['diameter_x_um'].mean().reindex(slices).values
    std_x  = grouped['diameter_x_um'].std().reindex(slices).fillna(0).values
    mean_y = grouped['diameter_y_um'].mean().reindex(slices).values
    std_y  = grouped['diameter_y_um'].std().reindex(slices).fillna(0).values

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(slices, mean_x, 'b-o', markersize=4, label='Diameter x (µm)')
    if std_x.max() > 0:
        ax.fill_between(slices,
                        np.nan_to_num(mean_x - std_x),
                        np.nan_to_num(mean_x + std_x),
                        alpha=0.2, color='blue')
    ax.plot(slices, mean_y, 'r-s', markersize=4, label='Diameter y (µm)')
    if std_y.max() > 0:
        ax.fill_between(slices,
                        np.nan_to_num(mean_y - std_y),
                        np.nan_to_num(mean_y + std_y),
                        alpha=0.2, color='red')
    ax.set_xlabel('Slice / Frame', fontsize=12)
    ax.set_ylabel('Cluster diameter (µm)', fontsize=12)
    ax.set_title('SACF cluster size vs slice', fontsize=13)
    ax.legend()
    ax.minorticks_on()
    plt.tight_layout()
    plt.show()
    return fig


# ---------------------------------------------------------------------------
# PyCAT run_ wrapper
# ---------------------------------------------------------------------------

# Mode constants
MODE_LIR   = 'lir'
MODE_RECT  = 'drawn_rectangle'
MODE_WHOLE = 'whole_image'


def run_sacf_analysis(image_layer, mode, data_instance, viewer,
                      labels_layer=None, shapes_layer=None,
                      plot_results=True):
    """
    PyCAT entry point for SACF analysis.

    Parameters
    ----------
    image_layer : napari.layers.Image
        2D or 3D (N, H, W) fluorescence image.
    mode : str
        One of 'lir', 'drawn_rectangle', 'whole_image'.
    data_instance : BaseDataClass
    viewer : napari.Viewer
    labels_layer : napari.layers.Labels or None
        Required for mode='lir'.
    shapes_layer : napari.layers.Shapes or None
        Required for mode='drawn_rectangle'.
    plot_results : bool
    """
    t0 = time.perf_counter()
    image = image_layer.data
    if image.ndim == 2:
        stack = image[np.newaxis, ...]
    elif image.ndim == 3:
        stack = image
    else:
        napari_show_warning("SACF: Image must be 2D or 3D.")
        return

    microns_per_pixel = np.sqrt(
        data_instance.data_repository.get('microns_per_pixel_sq', 1.0)
    )

    # ── LIR mode ──────────────────────────────────────────────────────────
    if mode == MODE_LIR:
        if labels_layer is None:
            napari_show_warning("SACF: LIR mode requires a labeled cell mask.")
            return
        labeled_mask = labels_layer.data
        if labeled_mask.ndim != 2:
            napari_show_warning("SACF: Labels layer must be a 2D mask.")
            return
        if stack.shape[1:] != labeled_mask.shape:
            napari_show_warning(
                f"SACF: Image spatial dims {stack.shape[1:]} do not match "
                f"mask shape {labeled_mask.shape}."
            )
            return
        napari_show_info("Running per-cell SACF (LIR crop) …")
        results_df = sacf_lir_mode(stack, labeled_mask, microns_per_pixel)

    # ── Drawn-rectangle mode ───────────────────────────────────────────────
    elif mode == MODE_RECT:
        if shapes_layer is None:
            napari_show_warning(
                "SACF: Drawn-rectangle mode requires a Shapes layer."
            )
            return
        napari_show_info("Running SACF on drawn rectangles …")
        results_df = sacf_drawn_rect_mode(stack, shapes_layer, microns_per_pixel)
        if results_df.empty:
            return

    # ── Whole-image mode ───────────────────────────────────────────────────
    elif mode == MODE_WHOLE:
        napari_show_info("Running whole-image SACF …")
        results_df = sacf_whole_image_mode(stack, microns_per_pixel)

    else:
        napari_show_warning(f"SACF: Unknown mode '{mode}'.")
        return

    data_instance.data_repository['sacf_results_df'] = results_df

    elapsed = time.perf_counter() - t0
    new_timing_row = pd.DataFrame([{
        'step': 'run_sacf_analysis',
        'elapsed_s': round(elapsed, 4),
        'image_shape': str(stack.shape),
    }])
    if 'timing_df' not in data_instance.data_repository:
        data_instance.data_repository['timing_df'] = new_timing_row
    else:
        data_instance.data_repository['timing_df'] = pd.concat(
            [data_instance.data_repository['timing_df'], new_timing_row],
            ignore_index=True
        )
    print(f"[PyCAT Timing] run_sacf_analysis: {elapsed:.3f}s")

    summary = results_df.groupby('slice').agg(
        n_rois=('roi', 'count'),
        mean_diameter_x_um=('diameter_x_um', 'mean'),
        std_diameter_x_um=('diameter_x_um', 'std'),
        mean_diameter_y_um=('diameter_y_um', 'mean'),
        std_diameter_y_um=('diameter_y_um', 'std'),
    ).round(4).reset_index()

    show_dataframes_dialog("Spatial ACF Analysis", [
        ("SACF Results", results_df.round(4)),
        ("SACF Summary (per slice)", summary),
    ])

    if plot_results and stack.shape[0] > 1:
        plot_sigma_vs_slice(results_df)

    napari_show_info("SACF analysis complete.")


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

def _add_run_sacf_analysis(ui_instance, layout=None, separate_widget=False):
    """
    Build the SACF widget.  Three radio buttons select the ROI mode; the
    relevant layer dropdowns show/hide accordingly.
    """
    sacf_layout = QVBoxLayout()
    ui_instance.add_text_label(sacf_layout, 'Spatial ACF Analysis', bold=True)

    # ── Mode selection ─────────────────────────────────────────────────────
    ui_instance.add_text_label(sacf_layout, 'ROI Mode:', font_size=9)

    rb_lir   = QRadioButton("LIR — largest interior rectangle per cell (default)")
    rb_rect  = QRadioButton("Drawn rectangle — select a Shapes layer")
    rb_whole = QRadioButton("Whole image — no ROI")
    rb_lir.setChecked(True)

    mode_group = QButtonGroup()
    mode_group.addButton(rb_lir,   0)
    mode_group.addButton(rb_rect,  1)
    mode_group.addButton(rb_whole, 2)

    sacf_layout.addWidget(rb_lir)
    sacf_layout.addWidget(rb_rect)
    sacf_layout.addWidget(rb_whole)

    # ── LIR inputs ─────────────────────────────────────────────────────────
    lir_widget = QWidget()
    lir_layout = QVBoxLayout(lir_widget)
    lir_layout.setContentsMargins(0, 0, 0, 0)
    ui_instance.add_text_label(lir_layout, 'Image layer:', font_size=9)
    image_dropdown_lir = ui_instance.create_layer_dropdown(napari.layers.Image)
    lir_layout.addWidget(image_dropdown_lir)
    ui_instance.add_text_label(lir_layout, 'Labeled cell mask:', font_size=9)
    labels_dropdown = ui_instance.create_layer_dropdown(napari.layers.Labels)
    lir_layout.addWidget(labels_dropdown)
    sacf_layout.addWidget(lir_widget)

    # ── Drawn-rect inputs ──────────────────────────────────────────────────
    rect_widget = QWidget()
    rect_layout = QVBoxLayout(rect_widget)
    rect_layout.setContentsMargins(0, 0, 0, 0)
    ui_instance.add_text_label(rect_layout, 'Image layer:', font_size=9)
    image_dropdown_rect = ui_instance.create_layer_dropdown(napari.layers.Image)
    rect_layout.addWidget(image_dropdown_rect)
    ui_instance.add_text_label(rect_layout, 'Shapes layer (rectangles):', font_size=9)
    shapes_dropdown = ui_instance.create_layer_dropdown(napari.layers.Shapes)
    rect_layout.addWidget(shapes_dropdown)
    rect_widget.setVisible(False)
    sacf_layout.addWidget(rect_widget)

    # ── Whole-image inputs ─────────────────────────────────────────────────
    whole_widget = QWidget()
    whole_layout = QVBoxLayout(whole_widget)
    whole_layout.setContentsMargins(0, 0, 0, 0)
    ui_instance.add_text_label(whole_layout, 'Image layer:', font_size=9)
    image_dropdown_whole = ui_instance.create_layer_dropdown(napari.layers.Image)
    whole_layout.addWidget(image_dropdown_whole)
    whole_widget.setVisible(False)
    sacf_layout.addWidget(whole_widget)

    # ── Show/hide on mode change ───────────────────────────────────────────
    def _on_mode_changed(btn_id):
        lir_widget.setVisible(btn_id == 0)
        rect_widget.setVisible(btn_id == 1)
        whole_widget.setVisible(btn_id == 2)

    mode_group.idClicked.connect(_on_mode_changed)

    # ── Run button ─────────────────────────────────────────────────────────
    run_button = QPushButton("Run SACF Analysis")
    run_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _on_run():
        btn_id = mode_group.checkedId()

        if btn_id == 0:   # LIR
            image_layer  = ui_instance.viewer.layers[image_dropdown_lir.currentText()]
            labels_layer = ui_instance.viewer.layers[labels_dropdown.currentText()]
            shapes_layer = None
            mode = MODE_LIR
            record_params = {
                'image_layer': image_dropdown_lir.currentText(),
                'labels_layer': labels_dropdown.currentText(),
            }
        elif btn_id == 1: # Drawn rect
            image_layer  = ui_instance.viewer.layers[image_dropdown_rect.currentText()]
            labels_layer = None
            shapes_layer = ui_instance.viewer.layers[shapes_dropdown.currentText()]
            mode = MODE_RECT
            record_params = {
                'image_layer': image_dropdown_rect.currentText(),
                'shapes_layer': shapes_dropdown.currentText(),
            }
        else:             # Whole image
            image_layer  = ui_instance.viewer.layers[image_dropdown_whole.currentText()]
            labels_layer = None
            shapes_layer = None
            mode = MODE_WHOLE
            record_params = {
                'image_layer': image_dropdown_whole.currentText(),
            }

        run_sacf_analysis(
            image_layer=image_layer,
            mode=mode,
            data_instance=ui_instance.central_manager.active_data_class,
            viewer=ui_instance.viewer,
            labels_layer=labels_layer,
            shapes_layer=shapes_layer,
            plot_results=True,
        )
        ui_instance._record('sacf_analysis', {'mode': mode, **record_params})

    run_button.clicked.connect(_on_run)
    sacf_layout.addWidget(run_button)

    sacf_widget = QWidget()
    sacf_widget.setLayout(sacf_layout)
    ui_instance._add_widget_to_layout_or_dock(
        sacf_widget, layout, separate_widget, dock_name='Spatial ACF Analysis'
    )
