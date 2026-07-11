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

        # Sampling can be entered either as a rate (Hz) or, more naturally for
        # C-Trap force data, as the sampling PERIOD in microseconds (e.g. 12.8 µs
        # = 78125 Hz). The period field carries enough decimals for sub-µs values;
        # the two stay in sync. Leaving both at 0 auto-uses the .h5 value.
        self._sample_rate = QDoubleSpinBox()
        self._sample_rate.setRange(0.0, 1e8); self._sample_rate.setValue(0.0)
        self._sample_rate.setDecimals(2)
        self._sample_rate.setToolTip(
            "Force sampling rate (Hz). Leave 0 to auto-use the value read "
            "from the .h5. Kept in sync with the sampling period below.")
        form.addRow("Force sample rate (Hz):", self._sample_rate)

        self._sample_period_us = QDoubleSpinBox()
        self._sample_period_us.setRange(0.0, 1e6); self._sample_period_us.setValue(0.0)
        self._sample_period_us.setDecimals(4)   # sub-µs precision (e.g. 12.8000)
        self._sample_period_us.setSingleStep(0.1)
        self._sample_period_us.setToolTip(
            "Force sampling PERIOD in microseconds (the time between samples). "
            "For many C-Trap force channels this is fixed, e.g. 12.8 µs "
            "(= 78125 Hz). Entering it here fills the rate above automatically.")
        form.addRow("…or sample period (µs):", self._sample_period_us)

        # Keep the two in sync without recursing (guard flag).
        self._sr_sync = False
        def _rate_changed(v):
            if self._sr_sync:
                return
            self._sr_sync = True
            try:
                self._sample_period_us.setValue((1e6 / v) if v > 0 else 0.0)
            finally:
                self._sr_sync = False
        def _period_changed(v):
            if self._sr_sync:
                return
            self._sr_sync = True
            try:
                self._sample_rate.setValue((1e6 / v) if v > 0 else 0.0)
            finally:
                self._sr_sync = False
        self._sample_rate.valueChanged.connect(_rate_changed)
        self._sample_period_us.valueChanged.connect(_period_changed)

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
            from pycat.file_io.file_io import materialize_stack
            stack = materialize_stack(self.viewer.layers[name].data)
            # Image-mode fusion treats frames as TIME (aspect-ratio relaxation) —
            # warn once if the stack's axis was assumed at load.
            try:
                from pycat.file_io.file_io import warn_if_assumed_axis
                warn_if_assumed_axis(self._dr(),
                                     "Droplet fusion (treats frames as time)")
            except Exception:
                pass
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
        # Show the built signal immediately so the analysis isn't a black box —
        # the user sees the force/aspect-ratio profile they're about to fit and
        # can read off a sensible fit window.
        try:
            self._plot_fusion_signal(time, sig, src)
        except Exception as e:
            print(f"[PyCAT Fusion] signal plot failed: {e}")
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

        # Show the fit model + what each parameter means (mirrors the FRAP module),
        # so the fitted numbers aren't cryptic.
        eqn = QLabel(
            "<span style='font-size:9pt;'>"
            "<b>Model:</b> S(t) = a·e<sup>−t/τ</sup> + b·t + d<br>"
            "<b>τ</b> = fusion relaxation time (the result) · "
            "<b>a</b> = relaxation amplitude · "
            "<b>b</b> = slow linear drift · "
            "<b>d</b> = baseline offset</span>")
        eqn.setWordWrap(True)
        eqn.setStyleSheet("padding:4px; background:#232323; border-radius:4px;")
        form.addRow(eqn)

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

    def _plot_fusion_signal(self, time, sig, src):
        """Show the built fusion signal (force profile or aspect ratio) so the
        analysis isn't a black box — the user sees what's being fit and can pick
        the fit window INTERACTIVELY by dragging a span on the plot (which syncs
        to the Fit start/end fields), with a "Fit this range" button right on the
        plot so they can fit without leaving it."""
        import matplotlib.pyplot as plt
        from matplotlib.widgets import SpanSelector, Button
        import numpy as _np
        fig, ax = plt.subplots(figsize=(7.4, 5.0))
        fig.subplots_adjust(bottom=0.2)
        ax.plot(time, sig, '-', lw=0.9, color='#4c72b0')
        ax.set_xlabel("time (s)"); ax.set_ylabel(f"signal ({src})")
        ax.set_title("Fusion signal — drag to select the fit window",
                     fontweight='bold')
        ax.grid(True, alpha=0.15)
        ax.text(0.5, 0.97,
                "Drag across the fusion event to set the fit window "
                "(excludes flat baseline + post-fusion drift), then 'Fit this "
                "range' — or type the values in Step 3.",
                transform=ax.transAxes, ha='center', va='top', fontsize=8,
                color='#666')

        state = {'lo': None, 'hi': None}

        def _on_span(lo, hi):
            if hi <= lo:
                return
            state['lo'], state['hi'] = float(lo), float(hi)
            # Sync to the Step-3 spinboxes so the panel and plot agree.
            try:
                self._t_start.setValue(float(lo))
                self._t_end.setValue(float(hi))
            except Exception:
                pass

        # Hold a reference on self so the selector isn't garbage-collected (a
        # matplotlib gotcha: a SpanSelector with no surviving reference stops
        # responding immediately). Construct defensively: the 'props' kwarg was
        # 'rectprops' before mpl 3.5, and 'drag_from_anywhere'/'interactive' were
        # added later — fall back to a minimal selector if the modern kwargs are
        # rejected, so this works across matplotlib versions.
        self._fusion_span = None
        for _kwargs in (
            dict(useblit=True, props=dict(alpha=0.15, facecolor='#f0a500'),
                 interactive=True, drag_from_anywhere=True),
            dict(useblit=True, rectprops=dict(alpha=0.15, facecolor='#f0a500')),
            dict(useblit=True),
            dict(),
        ):
            try:
                self._fusion_span = SpanSelector(
                    ax, _on_span, 'horizontal', **_kwargs)
                break
            except TypeError:
                continue

        # "Fit this range" button on the plot → fits the current span directly.
        axbtn = fig.add_axes([0.78, 0.02, 0.2, 0.06])
        btn = Button(axbtn, 'Fit this range')
        def _fit_span(_evt):
            if state['lo'] is not None and state['hi'] is not None:
                self._t_start.setValue(state['lo'])
                self._t_end.setValue(state['hi'])
            self._on_fit()
        btn.on_clicked(_fit_span)
        self._fusion_fit_btn = btn  # keep ref alive

        fig.tight_layout(rect=[0, 0.1, 1, 1])
        try:
            plt.show(block=False)
        except Exception:
            pass

    def _plot_fusion_fit(self, time, sig, fit):
        """Plot the fusion signal with the fitted relaxation curve overlaid, the
        model equation, and the fitted parameters — so the user sees how well the
        fit matches and what the numbers mean (mirrors the FRAP fit plot)."""
        import matplotlib.pyplot as plt
        import numpy as _np
        fig, ax = plt.subplots(figsize=(7.0, 5.0))
        ax.plot(time, sig, '.', ms=2, color='#4c72b0', alpha=0.5,
                label="fusion signal")
        ft = fit.get('fit_time'); fc = fit.get('fit_curve')
        if ft is not None and fc is not None and len(ft):
            ax.plot(ft, fc, '-', color='#c44e52', lw=2.2, label="fit")
        # Shade the fit window.
        ts, te = fit.get('t_start'), fit.get('t_end')
        if ts == ts and te == te:
            ax.axvspan(ts, te, color='#f0a500', alpha=0.08)
        ax.set_xlabel("time (s)"); ax.set_ylabel("fusion signal")
        ax.set_title("Droplet fusion relaxation fit", fontweight='bold')
        ax.grid(True, alpha=0.15)
        # Parameter box with the equation + fitted values.
        tau = fit.get('tau_s'); a = fit.get('a'); b = fit.get('b')
        d = fit.get('d'); r2 = fit.get('r_squared')
        txt = ("S(t) = a·e^(−t/τ) + b·t + d\n"
               f"τ = {tau:.4g} s\n"
               f"a = {a:.4g}\n"
               f"b = {b:.4g} (drift)\n"
               f"d = {d:.4g} (offset)\n"
               f"R² = {r2:.4g}")
        ax.text(0.97, 0.03, txt, transform=ax.transAxes, ha='right', va='bottom',
                fontsize=9, family='monospace',
                bbox=dict(boxstyle='round', facecolor='#f7f7f7', alpha=0.9))
        ax.legend(fontsize=9, loc='upper right')
        fig.tight_layout()
        try:
            plt.show(block=False)
        except Exception:
            pass

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

        # Plot the fusion signal with the fitted curve overlaid, the equation, and
        # the fitted parameters labelled — a visual check of fit quality (the
        # numbers-only table is cryptic on its own).
        try:
            self._plot_fusion_fit(time, sig, fit)
        except Exception as e:
            print(f"[PyCAT Fusion] fit plot failed: {e}")

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
