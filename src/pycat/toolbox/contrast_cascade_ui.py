"""
Contrast Cascade UI — visualise and analyse images with large object-to-object
brightness swings (bright condensate body + dim fibers).
"""

from __future__ import annotations
import numpy as np
import napari
from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel, QPushButton,
    QSpinBox, QComboBox, QWidget, QSizePolicy,
)

_BAND_CMAPS = ['red', 'yellow', 'green', 'cyan', 'blue', 'magenta']


def _add_contrast_cascade(ui_instance, layout=None, separate_widget=False):
    viewer = ui_instance.viewer
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Contrast Cascade', bold=True)
    desc = QLabel(
        "<span style='color:#888;font-size:9pt;'>For images with huge brightness "
        "swings (a bright body plus dim fibers). Split the intensity range into "
        "bands to SEE everything, use brightness-invariant features to SEGMENT "
        "with the Random Forest, and diagnose WHY dim objects are dim.</span>")
    desc.setWordWrap(True); outer.addWidget(desc)

    # ── 1. Visualise: cascade bands ──────────────────────────────────────
    vg = QGroupBox("1 — Visualise: contrast cascade")
    vf = QFormLayout(vg); vf.setContentsMargins(4, 18, 4, 4); vf.setSpacing(5)
    img_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    vf.addRow("Image:", img_dd)
    n_bands = QSpinBox(); n_bands.setRange(2, 6); n_bands.setValue(4)
    n_bands.setToolTip("Number of intensity bands, from the brightest structures "
                       "down to the dimmest. Each is shown as its own coloured layer.")
    vf.addRow("Bands:", n_bands)
    method = QComboBox(); method.addItems(['percentile', 'multiotsu'])
    method.setToolTip("How to split the intensity range: 'percentile' gives "
                      "even-area bands; 'multiotsu' finds natural class breaks "
                      "(often better when background dominates).")
    vf.addRow("Band method:", method)
    band_btn = QPushButton("Show Cascade Bands")
    band_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    band_btn.setToolTip("Add one coloured layer per intensity band, each with its "
                        "own contrast so bright and dim structure are both visible.")
    tone_btn = QPushButton("Add Tone-Mapped View")
    tone_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    tone_btn.setToolTip("Add a single log/CLAHE-compressed image where the whole "
                        "dynamic range is visible at once.")
    vf.addRow(band_btn); vf.addRow(tone_btn)
    outer.addWidget(vg)

    # ── 2. Segment: cascade Random Forest ────────────────────────────────
    sg = QGroupBox("2 — Segment: cascade Random Forest")
    sf = QFormLayout(sg); sf.setContentsMargins(4, 18, 4, 4); sf.setSpacing(5)
    seg_img_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    sf.addRow("Image:", seg_img_dd)
    scribble_btn = QPushButton("Add / Select Scribble Layer")
    scribble_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    scribble_btn.setToolTip("Create a Labels layer to paint training scribbles: "
                            "use label 1 = body, 2 = fiber, 3 = background.")
    scribble_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    sf.addRow("Scribbles:", scribble_dd)
    obj_d = QSpinBox(); obj_d.setRange(3, 200); obj_d.setValue(20)
    obj_d.setToolTip("Approximate body diameter (px) — sets the scale of the "
                     "local-contrast and multi-scale features.")
    sf.addRow("Object diameter:", obj_d)
    seg_btn = QPushButton("▶  Segment (Cascade RF)")
    seg_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    seg_btn.setToolTip("Train a Random Forest on brightness-invariant features "
                       "(local contrast + ridge/tubeness) from your scribbles and "
                       "predict body / fiber / background across the image.")
    sf.addRow(seg_btn)
    outer.addWidget(sg)

    # ── 3. Diagnose: why are dim objects dim? ────────────────────────────
    dg = QGroupBox("3 — Diagnose: below-focus vs growth")
    dfm = QFormLayout(dg); dfm.setContentsMargins(4, 18, 4, 4); dfm.setSpacing(5)
    diag_img_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    dfm.addRow("Image:", diag_img_dd)
    diag_lbl_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    dfm.addRow("Object labels:", diag_lbl_dd)
    from PyQt5.QtWidgets import QDoubleSpinBox
    dim_thr = QDoubleSpinBox(); dim_thr.setRange(0.05, 0.95); dim_thr.setDecimals(2)
    dim_thr.setSingleStep(0.05); dim_thr.setValue(0.60)
    dim_thr.setToolTip("Objects dimmer than this fraction of the body's "
                       "brightness are treated as 'dim' and diagnosed. Higher = "
                       "more objects count as dim.")
    dfm.addRow("Dim threshold:", dim_thr)
    blur_thr = QDoubleSpinBox(); blur_thr.setRange(0.05, 0.95); blur_thr.setDecimals(2)
    blur_thr.setSingleStep(0.05); blur_thr.setValue(0.65)
    blur_thr.setToolTip("A dim object whose edge sharpness is below this fraction "
                        "of the body's is called below-focus (blurry); above it, "
                        "growth-phase (sharp). Lower = fewer objects called "
                        "below-focus. Calibrate against a known in/out-of-focus pair.")
    dfm.addRow("Blur threshold:", blur_thr)
    diag_btn = QPushButton("▶  Focus-vs-Growth Diagnostic")
    diag_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    diag_btn.setToolTip("For each object, compare brightness AND edge sharpness "
                        "to the body: dim+blurry ⇒ below focus; dim+sharp ⇒ "
                        "nucleation/growth. Shows a plot and a table.")
    dfm.addRow(diag_btn)
    outer.addWidget(dg)

    # ── handlers ─────────────────────────────────────────────────────────
    def _on_bands():
        name = img_dd.currentText()
        if name not in [l.name for l in viewer.layers]:
            napari_show_warning("Select an image."); return
        from pycat.toolbox.contrast_cascade_tools import contrast_cascade_bands
        data = np.asarray(viewer.layers[name].data)
        img2d = data.max(axis=0) if data.ndim == 3 else data
        bands = contrast_cascade_bands(img2d, n_bands=n_bands.value(),
                                       method=method.currentText())
        if not bands:
            napari_show_warning("Could not compute bands."); return
        for i, b in enumerate(bands):
            viewer.add_image(b['band_image'], name=f"Cascade {b['name']}",
                             colormap=_BAND_CMAPS[i % len(_BAND_CMAPS)],
                             blending='additive', opacity=0.9)
        napari_show_info(f"Added {len(bands)} cascade band layers "
                         "(brightest first). Toggle layers to compare.")

    def _on_tone():
        name = img_dd.currentText()
        if name not in [l.name for l in viewer.layers]:
            napari_show_warning("Select an image."); return
        from pycat.toolbox.contrast_cascade_tools import tone_map
        data = np.asarray(viewer.layers[name].data)
        img2d = data.max(axis=0) if data.ndim == 3 else data
        viewer.add_image(tone_map(img2d, method='log'),
                         name="Tone-mapped (log)", colormap='viridis')
        napari_show_info("Added a log tone-mapped view — full dynamic range at once.")

    def _on_scribble():
        nm = "Cascade Scribbles"
        if nm not in [l.name for l in viewer.layers]:
            iname = seg_img_dd.currentText()
            shape = (np.asarray(viewer.layers[iname].data).shape[-2:]
                     if iname in [l.name for l in viewer.layers] else (512, 512))
            lyr = viewer.add_labels(np.zeros(shape, dtype=np.uint8), name=nm)
        else:
            lyr = viewer.layers[nm]
        viewer.layers.selection.active = lyr
        lyr.mode = 'paint'
        napari_show_info("Paint: label 1 = body, 2 = fiber, 3 = background, then "
                         "run the cascade RF.")

    def _on_segment():
        iname = seg_img_dd.currentText(); sname = scribble_dd.currentText()
        if iname not in [l.name for l in viewer.layers]:
            napari_show_warning("Select an image."); return
        if sname not in [l.name for l in viewer.layers]:
            napari_show_warning("Add and paint a scribble layer first."); return
        from pycat.toolbox.contrast_cascade_tools import cascade_rf_segment
        data = np.asarray(viewer.layers[iname].data)
        img2d = data.max(axis=0) if data.ndim == 3 else data
        scr = np.asarray(viewer.layers[sname].data)
        if scr.ndim == 3:
            scr = scr.max(axis=0)
        try:
            pred = cascade_rf_segment(img2d, scr, object_diameter=obj_d.value())
        except Exception as e:
            napari_show_warning(f"Cascade RF failed: {e}"); return
        viewer.add_labels(pred.astype(np.int32), name="Cascade RF classes")
        napari_show_info(f"Cascade RF done — classes {sorted(np.unique(pred))} "
                         "(as painted: 1=body, 2=fiber, 3=background).")

    def _on_diag():
        iname = diag_img_dd.currentText(); lname = diag_lbl_dd.currentText()
        if iname not in [l.name for l in viewer.layers] or \
           lname not in [l.name for l in viewer.layers]:
            napari_show_warning("Select an image and an object-labels layer."); return
        from pycat.toolbox.contrast_cascade_tools import focus_vs_growth_diagnostic
        data = np.asarray(viewer.layers[iname].data)
        img2d = data.max(axis=0) if data.ndim == 3 else data
        labs = np.asarray(viewer.layers[lname].data)
        if labs.ndim == 3:
            labs = labs.max(axis=0)
        df = focus_vs_growth_diagnostic(img2d, labs,
                                        dim_ratio=dim_thr.value(),
                                        blur_ratio=blur_thr.value())
        if df.empty:
            napari_show_warning("No labelled objects found."); return
        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'contrast_cascade_diagnostic'] = df
        except Exception:
            pass
        try:
            from pycat.toolbox.analysis_plots import plot_focus_diagnostic
            plot_focus_diagnostic(df, blur_ratio=blur_thr.value(),
                                  dim_ratio=dim_thr.value(), interactive=True)
        except Exception as e:
            print(f"[PyCAT] focus diagnostic plot failed: {e}")
        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Focus vs Growth Diagnostic",
                                   [("Per-object", df.round(4))])
        except Exception:
            pass
        n_focus = int(df['interpretation'].str.contains('below focus').sum())
        n_growth = int(df['interpretation'].str.contains('growth').sum())
        napari_show_info(f"Diagnostic: {n_growth} sharp-dim (growth-like), "
                         f"{n_focus} blurry-dim (below-focus-like). "
                         "Confirm below-focus with a z-stack.")

    band_btn.clicked.connect(_on_bands)
    tone_btn.clicked.connect(_on_tone)
    scribble_btn.clicked.connect(_on_scribble)
    seg_btn.clicked.connect(_on_segment)
    diag_btn.clicked.connect(_on_diag)

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Contrast Cascade")
