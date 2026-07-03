"""
PyCAT Droplet Fusion UI (C-Trap)
=================================
Measure the fusion relaxation time τ of two optically-trapped droplets
brought into contact on a Lumicks C-Trap, by fitting

    S(t) = a·exp(−t/τ) + b·t + d

to either the trap force transient (Lumicks .h5) or the aspect-ratio decay
of the merging pair in a brightfield / fluorescence image stack.

Steps
-----
  Step 1 — Load data:
             • Lumicks C-Trap .h5 force traces, or
             • an image stack (T,H,W) already open in the viewer
  Step 2 — Build the fusion signal (force channel, or image aspect ratio)
  Step 3 — Fit S(t) over the fusion window → relaxation time τ
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar, QComboBox,
    QScrollArea, QSizePolicy, QRadioButton,
)
from PyQt5.QtCore import Qt


class DropletFusionUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type, name_hint=''):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type, name_hint=name_hint)

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('fusion')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Droplet Fusion (C-Trap)</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Two trapped droplets brought into contact; the coalescence "
            "relaxation is fit to S(t)=a·e<sup>−t/τ</sup>+b·t+d to extract the "
            "fusion relaxation time τ.</span>")
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        self._add_data_source(layout)
        self._add_signal_build(layout)
        self._add_fit(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="Droplet Fusion")

    # ── Step 1: data source ────────────────────────────────────────────
    def _add_data_source(self, layout):
        grp  = QGroupBox("Step 1 — Data Source")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        mode_row = QHBoxLayout()
        self._rb_force = QRadioButton("C-Trap force (.h5)")
        self._rb_image = QRadioButton("Image stack (aspect ratio)")
        self._rb_image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_force.setChecked(True)
        self._rb_force.setToolTip("Fit the trap force transient from a Lumicks .h5 file.")
        self._rb_image.setToolTip(
            "Fit the aspect-ratio relaxation of the merging droplet pair in a "
            "brightfield / fluorescence image stack.")
        mode_row.addWidget(self._rb_force); mode_row.addWidget(self._rb_image)
        mode_row.addStretch()
        mw = QWidget(); mw.setLayout(mode_row)
        form.addRow("Signal type:", mw)

        # Force source
        self._force_container = QWidget()
        fc = QFormLayout(self._force_container)
        fc.setContentsMargins(0, 0, 0, 0); fc.setSpacing(4)
        load_btn = QPushButton("Load Lumicks C-Trap .h5…")
        load_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        load_btn.setToolTip("Open a Lumicks .h5 and read Force 1x/1y/2x/2y traces.")
        load_btn.clicked.connect(self._on_load_forces)
        fc.addRow(load_btn)
        self._chan_dd = QComboBox()
        self._chan_dd.addItems(['F1x', 'F1y', 'F2x', 'F2y', 'F1', 'F2', 'sum'])
        self._chan_dd.setToolTip(
            "Which force signal to fit. F1x…F2y = single channel; F1/F2 = "
            "magnitude of bead 1/2; sum = combined magnitude of both beads.")
        fc.addRow("Force channel:", self._chan_dd)
        form.addRow(self._force_container)

        # Image source
        self._image_container = QWidget()
        ic = QFormLayout(self._image_container)
        ic.setContentsMargins(0, 0, 0, 0); ic.setSpacing(4)
        self._img_dd = self.create_layer_dropdown(napari.layers.Image)
        self._img_dd.setToolTip("Image stack (T,H,W) of the fusing droplet pair.")
        ic.addRow("Image stack:", self._img_dd)
        form.addRow(self._image_container)
        self._image_container.setVisible(False)

        def _on_mode():
            self._force_container.setVisible(self._rb_force.isChecked())
            self._image_container.setVisible(self._rb_image.isChecked())
        self._rb_force.toggled.connect(lambda _: _on_mode())
        self._rb_image.toggled.connect(lambda _: _on_mode())

        layout.addWidget(grp)

    def _on_load_forces(self):
        from PyQt5.QtWidgets import QFileDialog
        from pycat.file_io.frap_io import lumicks_available, load_lumicks_fusion
        if not lumicks_available():
            napari_show_warning("lumicks.pylake not installed. Run: pip install lumicks.pylake")
            return
        path, _ = QFileDialog.getOpenFileName(
            None, "Open Lumicks C-Trap fusion .h5", "", "Lumicks HDF5 (*.h5)")
        if not path:
            return
        try:
            data = load_lumicks_fusion(path)
        except Exception as e:
            napari_show_warning(f"Failed to load forces: {e}")
            import traceback; traceback.print_exc(); return
        self._dr()['fusion_forces'] = data['forces']
        self._dr()['fusion_sample_rate'] = data['sample_rate_hz']
        self._dr()['fusion_h5_path'] = path
        chans = ', '.join(sorted(data['forces'].keys()))
        sr = data['sample_rate_hz']
        napari_show_info(
            f"Loaded force channels: {chans} ({data['n_samples']} samples"
            + (f", {sr:.0f} Hz)." if sr else ")."))

    # ── Step 2: signal build ───────────────────────────────────────────
    def _add_signal_build(self, layout):
        grp  = QGroupBox("Step 2 — Build Fusion Signal")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._sample_rate = QDoubleSpinBox()
        self._sample_rate.setRange(0.0, 1e7); self._sample_rate.setValue(0.0)
        self._sample_rate.setDecimals(1)
        self._sample_rate.setToolTip(
            "Force sampling rate (Hz). Leave 0 to auto-use the value read "
            "from the .h5; the time axis is built from this.")
        form.addRow("Force sample rate (Hz):", self._sample_rate)

        self._frame_dt = QDoubleSpinBox()
        self._frame_dt.setRange(0.0001, 3600); self._frame_dt.setValue(1.0)
        self._frame_dt.setDecimals(4)
        self._frame_dt.setToolTip("Image mode only: time per frame (s).")
        form.addRow("Frame interval (s):", self._frame_dt)

        btn = QPushButton("▶  Build Signal")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_build_signal)
        form.addRow(btn)
        layout.addWidget(grp)

    def _on_build_signal(self):
        if self._rb_force.isChecked():
            from pycat.toolbox.fusion_tools import build_fusion_signal_from_forces
            forces = self._dr().get('fusion_forces')
            if not forces:
                napari_show_warning("No force traces loaded — use Step 1 to load a .h5."); return
            sr = self._sample_rate.value() or self._dr().get('fusion_sample_rate')
            try:
                time, sig = build_fusion_signal_from_forces(
                    forces, which=self._chan_dd.currentText(), sample_rate_hz=sr)
            except Exception as e:
                napari_show_warning(f"Could not build force signal: {e}"); return
            src = f"force {self._chan_dd.currentText()}"
        else:
            from pycat.toolbox.fusion_tools import aspect_ratio_signal
            name = self._img_dd.currentText()
            if name not in [l.name for l in self.viewer.layers]:
                napari_show_warning(f"Image stack '{name}' not found."); return
            stack = np.asarray(self.viewer.layers[name].data)
            try:
                time, sig = aspect_ratio_signal(
                    stack, frame_interval_s=self._frame_dt.value())
            except Exception as e:
                napari_show_warning(f"Could not build aspect-ratio signal: {e}"); return
            src = "image aspect ratio"

        self._dr()['fusion_time'] = time
        self._dr()['fusion_signal'] = sig
        # Pre-fill the fit window with the full range
        self._t_start.setValue(float(np.nanmin(time)))
        self._t_end.setValue(float(np.nanmax(time)))
        self._record('fusion_build_signal', {
            'source': src,
            'n_samples': int(len(sig))})
        napari_show_info(
            f"Built fusion signal from {src}: {len(sig)} samples, "
            f"t=[{np.nanmin(time):.4g}, {np.nanmax(time):.4g}] s. "
            "Set the fit window to isolate the fusion event, then fit.")

    # ── Step 3: fit ────────────────────────────────────────────────────
    def _add_fit(self, layout):
        grp  = QGroupBox("Step 3 — Fit Relaxation")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Set the fit window to bracket the fusion event (exclude the "
            "flat baseline before contact and any post-fusion drift).</span>")
        note.setWordWrap(True); form.addRow(note)

        self._t_start = QDoubleSpinBox()
        self._t_start.setRange(-1e9, 1e9); self._t_start.setDecimals(4)
        self._t_start.setToolTip("Fit window start (s).")
        form.addRow("Fit start (s):", self._t_start)

        self._t_end = QDoubleSpinBox()
        self._t_end.setRange(-1e9, 1e9); self._t_end.setDecimals(4)
        self._t_end.setToolTip("Fit window end (s).")
        form.addRow("Fit end (s):", self._t_end)

        btn = QPushButton("▶  Fit Fusion Relaxation")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_fit)
        form.addRow(btn)
        layout.addWidget(grp)

    def _on_fit(self):
        from pycat.toolbox.fusion_tools import fit_fusion_relaxation
        time = self._dr().get('fusion_time')
        sig  = self._dr().get('fusion_signal')
        if time is None or sig is None:
            napari_show_warning("No fusion signal built — run Step 2 first."); return

        t_start = self._t_start.value()
        t_end   = self._t_end.value()
        if t_end <= t_start:
            t_start, t_end = None, None  # fit whole trace

        fit = fit_fusion_relaxation(time, sig, t_start=t_start, t_end=t_end)
        self._dr()['fusion_fit'] = fit

        self._record('fusion_fit', {
            'tau_s': fit.get('tau_s'),
            'r_squared': fit.get('r_squared'),
            't_start': fit.get('t_start'),
            't_end': fit.get('t_end')})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summary = pd.DataFrame([{
                'τ (s)': round(fit['tau_s'], 5) if fit['tau_s']==fit['tau_s'] else None,
                'a':     round(fit['a'], 5) if fit['a']==fit['a'] else None,
                'b (drift)': round(fit['b'], 6) if fit['b']==fit['b'] else None,
                'd (offset)': round(fit['d'], 5) if fit['d']==fit['d'] else None,
                'R²':    round(fit['r_squared'], 4) if fit['r_squared']==fit['r_squared'] else None,
                'window (s)': f"[{fit['t_start']:.3g}, {fit['t_end']:.3g}]"
                              if fit['t_start']==fit['t_start'] else None,
            }])
            curve = pd.DataFrame({
                'time_s': fit['fit_time'],
                'fit': fit['fit_curve'],
            }) if len(fit['fit_time']) else pd.DataFrame()
            show_dataframes_dialog("Droplet Fusion Fit",
                                   [('Summary', summary), ('Fitted curve', curve.round(5))])
        except Exception:
            pass

        napari_show_info(
            f"Fusion fit: τ={fit['tau_s']:.4g}s, R²={fit['r_squared']:.3g} "
            f"(window [{fit['t_start']:.3g}, {fit['t_end']:.3g}]s)")
