"""
PyCAT Intensity Profile Tools
===============================
Line-scan and radial intensity profiles across condensates — for interface
(edge) sharpness, core-vs-rim gradients, and cross-sectional figure panels.

Two profile types:
  1. Line profile: sample intensity along a straight line drawn in a napari
     Shapes layer (or given as endpoints). Optional averaging over a line
     width. Good for a cross-section through a condensate / interface.
  2. Radial profile: mean intensity in concentric rings outward from a centre
     (a condensate centroid, or a clicked point). Good for core→rim decay and
     an objective interface-width estimate (distance over which intensity
     falls from high to background).

Author
------
    Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Line profile
# ---------------------------------------------------------------------------

def line_profile(image: np.ndarray, start_yx, end_yx,
                 linewidth: int = 1, microns_per_pixel: float = 1.0) -> pd.DataFrame:
    """
    Intensity along a line from start_yx to end_yx.

    Parameters
    ----------
    image : 2D intensity image.
    start_yx, end_yx : (y, x) endpoints in pixels.
    linewidth : width (px) to average over perpendicular to the line.
    microns_per_pixel : for a physical distance axis.

    Returns
    -------
    DataFrame: distance_px, distance_um, intensity.
    """
    from skimage.measure import profile_line
    img = np.asarray(image, dtype=float)
    prof = profile_line(img, tuple(start_yx), tuple(end_yx),
                        linewidth=max(1, int(linewidth)), mode='reflect')
    dist_px = np.arange(len(prof), dtype=float)
    return pd.DataFrame({
        'distance_px': dist_px,
        'distance_um': dist_px * microns_per_pixel,
        'intensity': prof,
    })


def line_profiles_from_shapes(image: np.ndarray, shapes_layer,
                              linewidth: int = 1,
                              microns_per_pixel: float = 1.0) -> list:
    """
    Build a line profile for every line shape in a napari Shapes layer.

    Returns a list of (index, DataFrame) tuples, one per line. Non-line
    shapes (rectangles, ellipses) are treated as their first→last vertex
    diagonal so any drawn shape yields a usable cross-section.
    """
    img = np.asarray(image, dtype=float)
    out = []
    for i, (verts, stype) in enumerate(zip(shapes_layer.data, shapes_layer.shape_type)):
        verts = np.asarray(verts)
        if verts.shape[0] < 2:
            continue
        # Use last two dims (y, x); handle possible leading frame axis
        start = verts[0][-2:]
        end = verts[-1][-2:]
        df = line_profile(img, start, end, linewidth=linewidth,
                          microns_per_pixel=microns_per_pixel)
        df.insert(0, 'line_index', i)
        out.append((i, df))
    return out


# ---------------------------------------------------------------------------
# Radial profile
# ---------------------------------------------------------------------------

def radial_profile(image: np.ndarray, center_yx,
                   max_radius_px: Optional[int] = None,
                   n_bins: Optional[int] = None,
                   microns_per_pixel: float = 1.0) -> pd.DataFrame:
    """
    Mean intensity in concentric rings outward from a centre point.

    Parameters
    ----------
    image : 2D intensity image.
    center_yx : (y, x) centre in pixels (e.g. a condensate centroid).
    max_radius_px : outer radius to profile to. Defaults to a value that stays
        inside the image bounds from the centre.
    n_bins : number of radial bins. Defaults to int(max_radius_px).
    microns_per_pixel : for a physical radius axis.

    Returns
    -------
    DataFrame: radius_px, radius_um, mean_intensity, std_intensity, n_pixels.
    """
    img = np.asarray(image, dtype=float)
    cy, cx = float(center_yx[0]), float(center_yx[1])
    H, W = img.shape
    if max_radius_px is None:
        max_radius_px = int(min(cy, cx, H - 1 - cy, W - 1 - cx))
    max_radius_px = max(1, int(max_radius_px))
    if n_bins is None:
        n_bins = max_radius_px

    y, x = np.indices(img.shape)
    r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2)
    edges = np.linspace(0, max_radius_px, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])

    means, stds, counts = [], [], []
    for b in range(n_bins):
        m = (r >= edges[b]) & (r < edges[b + 1])
        vals = img[m]
        if vals.size:
            means.append(float(vals.mean())); stds.append(float(vals.std()))
            counts.append(int(vals.size))
        else:
            means.append(np.nan); stds.append(np.nan); counts.append(0)

    return pd.DataFrame({
        'radius_px': centers,
        'radius_um': centers * microns_per_pixel,
        'mean_intensity': means,
        'std_intensity': stds,
        'n_pixels': counts,
    })


def interface_width_from_radial(radial_df: pd.DataFrame,
                                signal_col: str = 'mean_intensity') -> dict:
    """
    Estimate the interface (edge) width from a radial profile as the distance
    over which intensity falls from 80% to 20% of its span (10-90 is common
    too; 20-80 is more robust to noise). Also returns the half-max radius (a
    proxy for condensate radius).

    Returns
    -------
    dict: interface_width_px, interface_width_um, half_max_radius_px,
          half_max_radius_um.
    """
    df = radial_df.dropna(subset=[signal_col])
    if len(df) < 3:
        return dict(interface_width_px=np.nan, interface_width_um=np.nan,
                    half_max_radius_px=np.nan, half_max_radius_um=np.nan)
    r = df['radius_px'].values
    s = df[signal_col].values
    r_um = df['radius_um'].values
    hi, lo = np.nanmax(s), np.nanmin(s)
    span = hi - lo
    if span <= 0:
        return dict(interface_width_px=np.nan, interface_width_um=np.nan,
                    half_max_radius_px=np.nan, half_max_radius_um=np.nan)
    lvl80 = lo + 0.8 * span
    lvl50 = lo + 0.5 * span
    lvl20 = lo + 0.2 * span

    def _first_cross(level):
        # first radius where the (descending) profile drops below `level`
        below = np.where(s <= level)[0]
        return r[below[0]] if below.size else np.nan

    def _first_cross_um(level):
        below = np.where(s <= level)[0]
        return r_um[below[0]] if below.size else np.nan

    r80, r20 = _first_cross(lvl80), _first_cross(lvl20)
    r80u, r20u = _first_cross_um(lvl80), _first_cross_um(lvl20)
    width_px = (r20 - r80) if (np.isfinite(r80) and np.isfinite(r20)) else np.nan
    width_um = (r20u - r80u) if (np.isfinite(r80u) and np.isfinite(r20u)) else np.nan
    return dict(interface_width_px=float(width_px) if np.isfinite(width_px) else np.nan,
                interface_width_um=float(width_um) if np.isfinite(width_um) else np.nan,
                half_max_radius_px=float(_first_cross(lvl50)),
                half_max_radius_um=float(_first_cross_um(lvl50)))


# ---------------------------------------------------------------------------
# UI entry point (Toolbox)
# ---------------------------------------------------------------------------

def _add_intensity_profile(ui_instance, layout=None, separate_widget=False):
    """
    Widget: line-scan and radial intensity profiles.

    Line mode uses a Shapes layer of drawn lines. Radial mode uses either a
    Points layer (centres) or the centroids of a condensate labels layer.
    """
    import napari
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QSpinBox, QPushButton, QRadioButton,
        QHBoxLayout, QWidget, QComboBox)

    grp  = QGroupBox("Intensity Profiles (line / radial)")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    desc = QLabel(
        "Line-scan across a drawn line, or a radial profile outward from "
        "condensate centroids / clicked points. Radial mode also estimates "
        "interface (edge) width and half-max radius.")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    image_dd.setToolTip("Intensity image to profile.")
    form.addRow("Image:", image_dd)

    mode_row = QHBoxLayout()
    rb_line = QRadioButton("Line (Shapes)")
    rb_radial = QRadioButton("Radial (centroids/points)")
    rb_line.setChecked(True)
    mode_row.addWidget(rb_line); mode_row.addWidget(rb_radial); mode_row.addStretch()
    mw = QWidget(); mw.setLayout(mode_row)
    form.addRow("Mode:", mw)

    # Line source
    line_dd = ui_instance.create_layer_dropdown(napari.layers.Shapes)
    line_dd.setToolTip("Shapes layer containing the drawn line(s).")
    form.addRow("Line shapes:", line_dd)

    lw_spin = QSpinBox(); lw_spin.setRange(1, 51); lw_spin.setValue(1)
    lw_spin.setToolTip("Averaging width (px) perpendicular to the line.")
    form.addRow("Line width (px):", lw_spin)

    # Radial source
    src_dd = QComboBox(); src_dd.addItems(['Labels centroids', 'Points'])
    src_dd.setToolTip("Radial centres: condensate label centroids, or a Points layer.")
    form.addRow("Radial centres from:", src_dd)

    labels_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    labels_dd.setToolTip("Condensate labels — centroids used as radial centres.")
    form.addRow("Condensate labels:", labels_dd)

    points_dd = ui_instance.create_layer_dropdown(napari.layers.Points)
    points_dd.setToolTip("Points layer of radial centres (if using Points).")
    form.addRow("Points:", points_dd)

    rad_spin = QSpinBox(); rad_spin.setRange(3, 500); rad_spin.setValue(30)
    rad_spin.setToolTip("Maximum radius (px) for the radial profile.")
    form.addRow("Max radius (px):", rad_spin)

    btn = QPushButton("▶  Compute Profiles")
    form.addRow(btn)

    def _mpx():
        try:
            v = ui_instance.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
            return float(v) ** 0.5 if v else 1.0
        except Exception:
            return 1.0

    def _on_run():
        from napari.utils.notifications import show_info as _info, show_warning as _warn
        import numpy as _np
        layers = [l.name for l in ui_instance.viewer.layers]
        iname = image_dd.currentText()
        if iname not in layers:
            _warn("Select a valid image."); return
        img = _np.asarray(ui_instance.viewer.layers[iname].data)
        if img.ndim != 2:
            _warn("Profiles expect a 2D image (or one slice)."); return
        mpp = _mpx()

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
        except Exception:
            show_dataframes_dialog = None

        if rb_line.isChecked():
            lname = line_dd.currentText()
            if lname not in layers:
                _warn("Select a Shapes layer with a drawn line."); return
            shapes = ui_instance.viewer.layers[lname]
            profs = line_profiles_from_shapes(img, shapes,
                                              linewidth=lw_spin.value(),
                                              microns_per_pixel=mpp)
            if not profs:
                _warn("No usable line shapes found."); return
            combined = pd.concat([df for _i, df in profs], ignore_index=True)
            ui_instance.central_manager.active_data_class.data_repository['line_profile_df'] = combined
            rec = getattr(ui_instance, '_record', None)
            if callable(rec):
                rec('intensity_profile', {'mode': 'line', 'image': iname,
                                          'n_lines': len(profs)})
            if show_dataframes_dialog:
                show_dataframes_dialog("Line Profiles", [('Profiles', combined.round(3))])
            _info(f"Computed {len(profs)} line profile(s).")
        else:
            centers = []
            if src_dd.currentText() == 'Points':
                pname = points_dd.currentText()
                if pname not in layers:
                    _warn("Select a Points layer."); return
                pts = _np.asarray(ui_instance.viewer.layers[pname].data)
                centers = [(p[-2], p[-1]) for p in pts]
            else:
                lname = labels_dd.currentText()
                if lname not in layers:
                    _warn("Select a condensate labels layer."); return
                labs = _np.asarray(ui_instance.viewer.layers[lname].data)
                import scipy.ndimage as _ndi
                lbl_ids = [l for l in _np.unique(labs) if l != 0]
                coms = _ndi.center_of_mass(_np.ones_like(labs), labs, lbl_ids)
                centers = [(c[0], c[1]) for c in coms]
            if not centers:
                _warn("No radial centres found."); return

            all_rows, iface_rows = [], []
            for k, ctr in enumerate(centers):
                rp = radial_profile(img, ctr, max_radius_px=rad_spin.value(),
                                    microns_per_pixel=mpp)
                rp.insert(0, 'center_index', k)
                all_rows.append(rp)
                iw = interface_width_from_radial(rp)
                iface_rows.append({'center_index': k,
                                   'center_y': ctr[0], 'center_x': ctr[1],
                                   **iw})
            combined = pd.concat(all_rows, ignore_index=True)
            iface_df = pd.DataFrame(iface_rows)
            dr = ui_instance.central_manager.active_data_class.data_repository
            dr['radial_profile_df'] = combined
            dr['interface_width_df'] = iface_df
            rec = getattr(ui_instance, '_record', None)
            if callable(rec):
                rec('intensity_profile', {'mode': 'radial', 'image': iname,
                                          'n_centers': len(centers)})
            if show_dataframes_dialog:
                show_dataframes_dialog("Radial Profiles",
                                       [('Interface widths', iface_df.round(3)),
                                        ('Radial profiles', combined.round(3))])
            med_iw = iface_df['interface_width_um'].median()
            _info(f"Computed {len(centers)} radial profile(s); "
                  f"median interface width ≈ {med_iw:.3f} µm.")

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QScrollArea, QSizePolicy
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Intensity Profiles", area='right')
