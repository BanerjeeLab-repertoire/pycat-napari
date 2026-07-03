"""
Data Quality Control dashboard UI.

A teaching-oriented panel: pick an image or stack, optionally supply the optics
(pixel size, NA, wavelength) and timing, and get a colour-coded report with a
diagnostic plot for each metric plus plain-language notes on how each is measured
and what good data looks like.
"""

from __future__ import annotations
import numpy as np
import napari
from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QCheckBox, QWidget, QSizePolicy,
)


def _add_data_qc(ui_instance, layout=None, separate_widget=False):
    """Build the Data QC dashboard widget."""
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Data Quality Control', bold=True)
    desc = QLabel(
        "<span style='color:#888;font-size:9pt;'>Assess acquisition quality and "
        "learn what good data looks like. CORE metrics use absolute thresholds; "
        "ADVISORY metrics are heuristics or need the optics/timing below.</span>")
    desc.setWordWrap(True)
    outer.addWidget(desc)

    grp = QGroupBox("Input")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    form.addRow("Image / stack:", image_dd)

    zstack_cb = QCheckBox("This stack is a z-stack (through-focus)")
    zstack_cb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    zstack_cb.setToolTip(
        "Tick only for a focus series. Spherical-aberration assessment measures "
        "the through-focus asymmetry, which is meaningless for a time-series.")
    form.addRow(zstack_cb)

    # --- optics (for Nyquist / chromatic) ---
    opt = QGroupBox("Optics (for Nyquist sampling)")
    of = QFormLayout(opt); of.setContentsMargins(4, 20, 4, 4); of.setSpacing(5)
    px = QDoubleSpinBox(); px.setRange(0.0, 100.0); px.setDecimals(4)
    px.setSingleStep(0.01); px.setSuffix(" µm")
    px.setToolTip("Physical pixel size in the sample plane. 0 = unknown.")
    # auto-fill from the data repository if a pixel size is known
    try:
        stored = ui_instance.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
        if stored:
            px.setValue(float(stored) ** 0.5)
    except Exception:
        pass
    of.addRow("Pixel size:", px)
    na = QDoubleSpinBox(); na.setRange(0.0, 1.6); na.setDecimals(2); na.setSingleStep(0.05)
    na.setToolTip("Objective numerical aperture. 0 = unknown.")
    of.addRow("Objective NA:", na)
    wl = QDoubleSpinBox(); wl.setRange(0.0, 1200.0); wl.setDecimals(0); wl.setSuffix(" nm")
    wl.setToolTip("Emission wavelength. 0 = unknown.")
    of.addRow("Wavelength:", wl)
    nch = QSpinBox(); nch.setRange(1, 8); nch.setValue(1)
    nch.setToolTip("Number of co-imaged channels (for chromatic-aberration check).")
    of.addRow("Channels:", nch)

    # --- timing (for temporal sampling) ---
    tim = QGroupBox("Timing (for time sampling)")
    tf = QFormLayout(tim); tf.setContentsMargins(4, 20, 4, 4); tf.setSpacing(5)
    dt = QDoubleSpinBox(); dt.setRange(0.0, 100000.0); dt.setDecimals(3); dt.setSuffix(" s")
    dt.setToolTip("Interval between frames. 0 = unknown.")
    tf.addRow("Frame interval:", dt)
    tau = QDoubleSpinBox(); tau.setRange(0.0, 100000.0); tau.setDecimals(3); tau.setSuffix(" s")
    tau.setToolTip("Timescale of the fastest process you want to capture. 0 = unknown.")
    tf.addRow("Process timescale:", tau)

    run_btn = QPushButton("▶  Run Quality Report")
    run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    # holds the latest results + figure so they can be saved
    _state = {'results': None, 'fig': None, 'name': None}

    def _on_run():
        name = image_dd.currentText()
        if name not in [l.name for l in ui_instance.viewer.layers]:
            napari_show_warning("Select an image layer first."); return
        data = np.asarray(ui_instance.viewer.layers[name].data)
        if data.ndim not in (2, 3):
            napari_show_warning("QC needs a 2-D image or a 3-D (T/Z, H, W) stack."); return
        from pycat.toolbox.data_qc_tools import run_full_qc, plot_qc_report
        try:
            results = run_full_qc(
                data,
                pixel_um=(px.value() or None), na=(na.value() or None),
                wavelength_nm=(wl.value() or None),
                frame_interval_s=(dt.value() or None),
                process_timescale_s=(tau.value() or None),
                n_channels=nch.value(), is_zstack=zstack_cb.isChecked())
        except Exception as e:
            napari_show_warning(f"QC failed: {e}")
            import traceback; traceback.print_exc(); return
        _state['results'] = results; _state['name'] = name
        # store for reuse / saving
        try:
            ui_instance.central_manager.active_data_class.data_repository['data_qc_results'] = results
        except Exception:
            pass
        # concise in-app summary
        bad = [r['name'] for r in results if r['status'] == 'bad']
        warn = [r['name'] for r in results if r['status'] == 'warn']
        if bad:
            napari_show_warning("QC — POOR: " + ", ".join(bad) +
                                (("; CHECK: " + ", ".join(warn)) if warn else ""))
        elif warn:
            napari_show_info("QC — CHECK: " + ", ".join(warn))
        else:
            napari_show_info("QC — all assessed metrics look good.")
        try:
            _state['fig'] = plot_qc_report(
                results, title=f"Data Quality Report — {name}", interactive=True)
        except Exception as e:
            _state['fig'] = None
            print(f"[PyCAT] QC report plot failed: {e}")

    run_btn.clicked.connect(_on_run)

    def _on_save():
        import os
        results = _state['results']
        if not results:
            napari_show_warning("Run the quality report first."); return
        from PyQt5.QtWidgets import QFileDialog
        import pandas as pd
        path, _ = QFileDialog.getSaveFileName(
            None, "Save QC report (base name)",
            f"qc_report_{_state.get('name') or 'image'}.png",
            "PNG (*.png)")
        if not path:
            return
        base = path[:-4] if path.lower().endswith('.png') else path
        try:
            table = pd.DataFrame([{
                'metric': r['name'], 'tier': r['tier'], 'status': r['status'],
                'value': r.get('value'), 'unit': r.get('unit'),
                'result': r['headline'], 'how_measured': r.get('how', ''),
                'good_data': r.get('good', ''),
            } for r in results])
            table.to_csv(base + "_metrics.csv", index=False)
            fig = _state.get('fig')
            if fig is not None:
                fig.savefig(base + ".png", dpi=150, bbox_inches='tight')
                napari_show_info(f"Saved {os.path.basename(base)}.png and _metrics.csv.")
            else:
                napari_show_info(f"Saved {os.path.basename(base)}_metrics.csv "
                                 "(figure unavailable — re-run the report).")
        except Exception as e:
            napari_show_warning(f"Save failed: {e}")

    save_btn = QPushButton("Save Report (PNG + CSV)")
    save_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    save_btn.setToolTip("Save the report figure (PNG) and the full metric table "
                        "(CSV: value, status, how measured, what good looks like).")
    save_btn.clicked.connect(_on_save)

    outer.addWidget(grp)
    outer.addWidget(opt)
    outer.addWidget(tim)
    outer.addWidget(run_btn)
    outer.addWidget(save_btn)

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Data Quality Control")
