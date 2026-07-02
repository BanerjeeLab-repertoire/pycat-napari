"""
PyCAT Client Partition / Enrichment
=====================================
Quantify how strongly a second channel (a "client" protein or RNA) is enriched
inside condensates defined by a first channel (the "scaffold").

The single-channel bimodal partition tool (fit_bimodal_intensity) answers
"how much brighter is the dense phase than the dilute phase in the SAME
channel". This module answers the complementary, very common question: "given
condensates segmented in channel A, how concentrated is channel B inside them
versus outside" — the client enrichment / partition coefficient.

Definitions
-----------
For a client channel B, a condensate (dense) mask, and a surrounding region
(the cell, or a dilute-phase mask):

    enrichment (partition coefficient)
        = mean(B in dense) / mean(B in dilute)

    where the dilute region is (cell mask AND NOT dense mask), i.e. the same
    cell's non-condensate area. A value >1 means B is recruited into the
    condensate; ~1 means no preference; <1 means exclusion.

Both a per-condensate table (one enrichment per object, using each object's
local dilute background) and a per-cell summary are produced.

Author
------
    Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import scipy.ndimage as ndi

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Core enrichment computation
# ---------------------------------------------------------------------------

def client_enrichment(
    client_image: np.ndarray,
    dense_mask: np.ndarray,
    cell_mask: Optional[np.ndarray] = None,
    dilute_dilation_px: int = 0,
    background: float = 0.0,
) -> dict:
    """
    Global client enrichment: mean(client in dense) / mean(client in dilute).

    Parameters
    ----------
    client_image : (H, W) intensity image of the client channel (B).
    dense_mask : (H, W) binary/int mask of the condensates (from channel A).
    cell_mask : optional (H, W) mask bounding the dilute region. If None, the
        dilute region is everything outside the dense mask.
    dilute_dilation_px : if >0, the dilute region is a shell of this thickness
        around each condensate (dense dilated minus dense), giving a LOCAL
        background rather than the whole cell. 0 = use the full cell/outside.
    background : constant to subtract from the client image before ratioing
        (e.g. camera offset). Values are floored at 0 after subtraction.

    Returns
    -------
    dict with dense_mean, dilute_mean, enrichment, n_dense_px, n_dilute_px.
    """
    img = np.asarray(client_image, dtype=float) - float(background)
    img = np.clip(img, 0, None)
    dense = np.asarray(dense_mask) > 0

    if dilute_dilation_px > 0:
        dilated = ndi.binary_dilation(dense, iterations=int(dilute_dilation_px))
        dilute = dilated & ~dense
        if cell_mask is not None:
            dilute &= (np.asarray(cell_mask) > 0)
    else:
        if cell_mask is not None:
            dilute = (np.asarray(cell_mask) > 0) & ~dense
        else:
            dilute = ~dense

    n_dense = int(dense.sum())
    n_dilute = int(dilute.sum())
    dense_mean = float(img[dense].mean()) if n_dense else np.nan
    dilute_mean = float(img[dilute].mean()) if n_dilute else np.nan
    enrichment = (dense_mean / dilute_mean) if (dilute_mean and dilute_mean > 0) else np.nan
    return dict(dense_mean=dense_mean, dilute_mean=dilute_mean,
                enrichment=enrichment, n_dense_px=n_dense, n_dilute_px=n_dilute)


def client_enrichment_per_condensate(
    client_image: np.ndarray,
    dense_labels: np.ndarray,
    cell_mask: Optional[np.ndarray] = None,
    shell_px: int = 5,
    background: float = 0.0,
) -> pd.DataFrame:
    """
    Per-condensate client enrichment, using each object's LOCAL dilute shell.

    For every labeled condensate, the dilute reference is a ring of thickness
    `shell_px` around that object (excluding all condensates), so enrichment is
    measured against the local background rather than a global one — more
    robust to intensity gradients across the field.

    Parameters
    ----------
    client_image : (H, W) client channel.
    dense_labels : (H, W) integer label image of condensates (one label each).
    cell_mask : optional bounding mask for the dilute shell.
    shell_px : thickness (px) of the local dilute ring around each object.
    background : constant subtracted from the client image.

    Returns
    -------
    DataFrame: label, area_px, client_mean_dense, client_mean_local_dilute,
               enrichment, integrated_client.
    """
    img = np.asarray(client_image, dtype=float) - float(background)
    img = np.clip(img, 0, None)
    labels = np.asarray(dense_labels)
    all_dense = labels > 0
    cm = (np.asarray(cell_mask) > 0) if cell_mask is not None else None

    rows = []
    for lbl in np.unique(labels):
        if lbl == 0:
            continue
        obj = labels == lbl
        area = int(obj.sum())
        # Local dilute shell = (obj dilated by shell_px) minus ALL condensates
        shell = ndi.binary_dilation(obj, iterations=int(shell_px)) & ~all_dense
        if cm is not None:
            shell &= cm
        dense_mean = float(img[obj].mean()) if area else np.nan
        dilute_mean = float(img[shell].mean()) if shell.any() else np.nan
        enr = (dense_mean / dilute_mean) if (dilute_mean and dilute_mean > 0) else np.nan
        rows.append({
            'label': int(lbl), 'area_px': area,
            'client_mean_dense': dense_mean,
            'client_mean_local_dilute': dilute_mean,
            'enrichment': enr,
            'integrated_client': float(img[obj].sum()),
        })
    return pd.DataFrame(rows)


def client_enrichment_per_cell(
    client_image: np.ndarray,
    dense_labels: np.ndarray,
    cell_labels: np.ndarray,
    shell_px: int = 5,
    background: float = 0.0,
) -> pd.DataFrame:
    """
    Per-cell client enrichment summary.

    For each labeled cell, computes the whole-cell enrichment (mean client in
    that cell's condensates / mean client in that cell's dilute phase) and the
    median of the per-condensate enrichments within the cell.

    Parameters
    ----------
    client_image : (H, W) client channel.
    dense_labels : (H, W) condensate label image.
    cell_labels : (H, W) cell label image.
    shell_px : local dilute-shell thickness for the per-condensate values.
    background : constant subtracted from the client image.

    Returns
    -------
    DataFrame: cell_label, n_condensates, whole_cell_enrichment,
               median_per_condensate_enrichment, dense_mean, dilute_mean.
    """
    img = np.asarray(client_image, dtype=float) - float(background)
    img = np.clip(img, 0, None)
    dense_labels = np.asarray(dense_labels)
    cell_labels = np.asarray(cell_labels)
    all_dense = dense_labels > 0

    rows = []
    for cl in np.unique(cell_labels):
        if cl == 0:
            continue
        cell = cell_labels == cl
        dense_in_cell = all_dense & cell
        dilute_in_cell = cell & ~all_dense
        dense_mean = float(img[dense_in_cell].mean()) if dense_in_cell.any() else np.nan
        dilute_mean = float(img[dilute_in_cell].mean()) if dilute_in_cell.any() else np.nan
        whole = (dense_mean / dilute_mean) if (dilute_mean and dilute_mean > 0) else np.nan

        # per-condensate enrichments within this cell
        obj_labels = np.unique(dense_labels[dense_in_cell])
        obj_labels = obj_labels[obj_labels > 0]
        per_obj = []
        for lbl in obj_labels:
            obj = dense_labels == lbl
            shell = ndi.binary_dilation(obj, iterations=int(shell_px)) & ~all_dense & cell
            if obj.any() and shell.any():
                dm = float(img[obj].mean()); lm = float(img[shell].mean())
                if lm > 0:
                    per_obj.append(dm / lm)
        rows.append({
            'cell_label': int(cl),
            'n_condensates': int(len(obj_labels)),
            'whole_cell_enrichment': whole,
            'median_per_condensate_enrichment': float(np.median(per_obj)) if per_obj else np.nan,
            'dense_mean': dense_mean, 'dilute_mean': dilute_mean,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# UI entry point (Toolbox)
# ---------------------------------------------------------------------------

def _add_client_enrichment(ui_instance, layout=None, separate_widget=False):
    """
    Widget: client/partner enrichment of a second channel inside condensates.

    Needs a client intensity image, a condensate label (or binary) mask, and
    optionally a cell label mask. Reports per-condensate and (if cells given)
    per-cell enrichment.
    """
    import napari
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QSpinBox, QDoubleSpinBox, QPushButton,
        QProgressBar)

    grp  = QGroupBox("Client Partition / Enrichment")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

    desc = QLabel(
        "Measures how enriched a client channel is inside condensates: "
        "mean(client in dense) / mean(client in local dilute shell). Needs a "
        "client image and a condensate mask; a cell mask enables per-cell "
        "summaries.")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    client_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    client_dd.setToolTip("Client channel (the protein/RNA whose enrichment you want).")
    form.addRow("Client channel:", client_dd)

    dense_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    dense_dd.setToolTip("Condensate mask (labels or binary) from the scaffold channel.")
    form.addRow("Condensate mask:", dense_dd)

    cell_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd.setToolTip("Optional cell labels — enables per-cell enrichment. 'None' to skip.")
    form.addRow("Cell mask (optional):", cell_dd)

    shell_spin = QSpinBox(); shell_spin.setRange(1, 100); shell_spin.setValue(5)
    shell_spin.setToolTip("Thickness (px) of the local dilute ring around each condensate.")
    form.addRow("Dilute shell (px):", shell_spin)

    bg_spin = QDoubleSpinBox(); bg_spin.setRange(0, 1e6); bg_spin.setValue(0.0)
    bg_spin.setDecimals(2)
    bg_spin.setToolTip("Constant background subtracted from the client image before ratioing.")
    form.addRow("Background subtract:", bg_spin)

    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton("▶  Compute Enrichment")
    form.addRow(prog); form.addRow(btn)

    def _on_run():
        from napari.utils.notifications import show_info as _info, show_warning as _warn
        import numpy as _np
        layers = [l.name for l in ui_instance.viewer.layers]
        iname, dname = client_dd.currentText(), dense_dd.currentText()
        if iname not in layers:
            _warn("Select a valid client image."); return
        if dname not in layers:
            _warn("Select a valid condensate mask."); return
        client = _np.asarray(ui_instance.viewer.layers[iname].data)
        dense = _np.asarray(ui_instance.viewer.layers[dname].data)
        if client.ndim != 2:
            _warn("Client enrichment currently expects a 2D image (or one slice)."); return

        # If the dense mask is binary, label it for per-condensate output
        if dense.max() <= 1:
            from scipy.ndimage import label as _label
            dense_labels, _n = _label(dense > 0)
        else:
            dense_labels = dense

        cname = cell_dd.currentText()
        cell_labels = None
        if cname != 'None' and cname in layers:
            cell_labels = _np.asarray(ui_instance.viewer.layers[cname].data)

        prog.setVisible(True); prog.setRange(0, 0)
        try:
            per_cond = client_enrichment_per_condensate(
                client, dense_labels,
                cell_mask=(cell_labels > 0) if cell_labels is not None else None,
                shell_px=shell_spin.value(), background=bg_spin.value())
            glob = client_enrichment(
                client, dense_labels,
                cell_mask=(cell_labels > 0) if cell_labels is not None else None,
                background=bg_spin.value())
            per_cell = None
            if cell_labels is not None:
                per_cell = client_enrichment_per_cell(
                    client, dense_labels, cell_labels,
                    shell_px=shell_spin.value(), background=bg_spin.value())
        except Exception as e:
            prog.setVisible(False)
            _warn(f"Enrichment failed: {e}")
            import traceback; traceback.print_exc(); return
        prog.setVisible(False)

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'client_enrichment_df'] = per_cond
        except Exception:
            pass
        rec = getattr(ui_instance, '_record', None)
        if callable(rec):
            rec('client_enrichment', {
                'client': iname, 'condensate_mask': dname,
                'cell_mask': cname if cell_labels is not None else None,
                'global_enrichment': glob['enrichment'],
                'n_condensates': int(len(per_cond))})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            overview = pd.DataFrame([{
                'global enrichment': round(glob['enrichment'], 3) if glob['enrichment']==glob['enrichment'] else None,
                'dense mean': round(glob['dense_mean'], 2) if glob['dense_mean']==glob['dense_mean'] else None,
                'dilute mean': round(glob['dilute_mean'], 2) if glob['dilute_mean']==glob['dilute_mean'] else None,
                'n condensates': len(per_cond),
                'median per-condensate enrichment': round(per_cond['enrichment'].median(), 3) if len(per_cond) else None,
            }])
            tables = [('Overview', overview), ('Per-condensate', per_cond.round(3))]
            if per_cell is not None and not per_cell.empty:
                tables.append(('Per-cell', per_cell.round(3)))
            show_dataframes_dialog("Client Enrichment", tables)
        except Exception:
            pass
        _info(f"Global client enrichment = {glob['enrichment']:.2f}× "
              f"across {len(per_cond)} condensates.")

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QSizePolicy
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Client Enrichment", area='right')
