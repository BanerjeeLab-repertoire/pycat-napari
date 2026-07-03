"""
PyCAT Force-Distance Curve UI — DNA Tethering (C-Trap)
=======================================================
Load a Lumicks C-Trap .h5, read the paired Force/Distance channels, and unfold
the time-wise trace into overlapping stretch/relax FD loops (rips and unzips),
with an optional worm-like chain reference overlay.

Steps
-----
  Step 1 — Load a Lumicks .h5 and pick the Force / Distance channels.
  Step 2 — Segment into stretch/relax half-cycles (unfold into FD loops).
  Step 3 — Plot the overlaid loops, optionally with a WLC reference, and
           export per-cycle tables.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QComboBox, QScrollArea,
    QSizePolicy,
)
from PyQt5.QtCore import Qt


class FDCurveUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('fd_curve')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8); layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Force-Distance Curve (DNA Tethering)</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Load a C-Trap .h5, unfold the force trace into overlapping "
            "stretch/relax FD loops to reveal rips and unzips, with an "
            "optional worm-like chain reference.</span>")
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        self._add_load(layout)
        self._add_segment(layout)
        self._add_plot(layout)
        self._add_rips(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="Force-Distance Curve")

    # ── Step 1: load ───────────────────────────────────────────────────
    def _add_load(self, layout):
        grp  = QGroupBox("Step 1 — Load Lumicks .h5")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        load_btn = QPushButton("Load Lumicks C-Trap .h5…")
        load_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        load_btn.setToolTip("Open a Lumicks .h5 and read its Distance and Force channels.")
        load_btn.clicked.connect(self._on_load)
        form.addRow(load_btn)

        self._force_dd = QComboBox()
        self._force_dd.setToolTip("Force channel to use (from Force LF, or HF if LF absent).")
        form.addRow("Force channel:", self._force_dd)

        self._dist_dd = QComboBox()
        self._dist_dd.setToolTip("Distance channel to use.")
        form.addRow("Distance channel:", self._dist_dd)

        rebuild_btn = QPushButton("Use selected channels")
        rebuild_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rebuild_btn.setToolTip("Re-read the trace using the channels chosen above.")
        rebuild_btn.clicked.connect(self._on_rechannel)
        form.addRow(rebuild_btn)

        self._load_status = QLabel("<span style='color:#888;'>No file loaded.</span>")
        self._load_status.setWordWrap(True)
        form.addRow(self._load_status)
        layout.addWidget(grp)

    def _on_load(self):
        from PyQt5.QtWidgets import QFileDialog
        from pycat.file_io.frap_io import lumicks_available, load_lumicks_fd
        if not lumicks_available():
            napari_show_warning("lumicks.pylake not installed. Run: pip install lumicks.pylake")
            return
        path, _ = QFileDialog.getOpenFileName(
            None, "Open Lumicks C-Trap .h5", "", "Lumicks HDF5 (*.h5)")
        if not path:
            return
        try:
            data = load_lumicks_fd(path)
        except Exception as e:
            napari_show_warning(f"Failed to load FD data: {e}")
            import traceback; traceback.print_exc(); return

        self._dr()['fd_path'] = path
        self._dr()['fd_force'] = data['force']
        self._dr()['fd_distance'] = data['distance']
        self._dr()['fd_time'] = data['time_s']

        # Populate channel dropdowns
        self._force_dd.clear(); self._force_dd.addItems(data['available_force'])
        self._dist_dd.clear(); self._dist_dd.addItems(data['available_distance'])
        idx = self._force_dd.findText(data['force_channel'])
        if idx >= 0: self._force_dd.setCurrentIndex(idx)
        idx = self._dist_dd.findText(data['distance_channel'])
        if idx >= 0: self._dist_dd.setCurrentIndex(idx)

        self._record('fd_load', {
            'file': os.path.basename(path),
            'force_channel': data['force_channel'],
            'distance_channel': data['distance_channel'],
            'n_samples': int(len(data['force']))})
        self._load_status.setText(
            f"<span style='color:#8f8;'>Loaded {os.path.basename(path)}: "
            f"{len(data['force'])} samples. Force '{data['force_channel']}', "
            f"distance '{data['distance_channel']}'.</span>")
        napari_show_info(
            f"Loaded FD trace: {len(data['force'])} samples "
            f"(F={data['force_channel']}, d={data['distance_channel']}).")

    def _on_rechannel(self):
        from pycat.file_io.frap_io import load_lumicks_fd
        path = self._dr().get('fd_path')
        if not path:
            napari_show_warning("Load a file first."); return
        try:
            data = load_lumicks_fd(path,
                                   force_channel=self._force_dd.currentText(),
                                   distance_channel=self._dist_dd.currentText())
        except Exception as e:
            napari_show_warning(f"Failed to re-read channels: {e}"); return
        self._dr()['fd_force'] = data['force']
        self._dr()['fd_distance'] = data['distance']
        self._dr()['fd_time'] = data['time_s']
        napari_show_info(
            f"Now using F='{data['force_channel']}', d='{data['distance_channel']}'.")

    # ── Step 2: segment ────────────────────────────────────────────────
    def _add_segment(self, layout):
        grp  = QGroupBox("Step 2 — Unfold into Stretch/Relax Cycles")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._smooth = QCheckBox("Savitzky-Golay smooth force")
        self._smooth.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._smooth.setChecked(False)
        self._smooth.setToolTip("Lightly smooth the force channel before segmentation.")
        form.addRow(self._smooth)

        self._smooth_win = QSpinBox(); self._smooth_win.setRange(3, 201); self._smooth_win.setValue(11)
        self._smooth_win.setSingleStep(2)
        self._smooth_win.setToolTip("Savitzky-Golay window length (forced odd).")
        form.addRow("Smooth window:", self._smooth_win)

        self._min_seg = QSpinBox(); self._min_seg.setRange(2, 100000); self._min_seg.setValue(20)
        self._min_seg.setToolTip(
            "Minimum half-cycle length (samples). Turning points closer than "
            "this are merged out to suppress jitter at reversals.")
        form.addRow("Min segment (samples):", self._min_seg)

        self._dist_smooth = QSpinBox(); self._dist_smooth.setRange(3, 501); self._dist_smooth.setValue(11)
        self._dist_smooth.setSingleStep(2)
        self._dist_smooth.setToolTip(
            "Smoothing window for the distance signal when detecting turning "
            "points (stabilises the derivative sign).")
        form.addRow("Distance smooth window:", self._dist_smooth)

        btn = QPushButton("▶  Segment Cycles")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_segment)
        form.addRow(btn)
        layout.addWidget(grp)

    def _on_segment(self):
        from pycat.toolbox.fd_curve_tools import segment_fd_cycles, smooth_force, summarise_cycles
        force = self._dr().get('fd_force')
        dist = self._dr().get('fd_distance')
        if force is None or dist is None:
            napari_show_warning("Load a file first (Step 1)."); return

        f = np.asarray(force, dtype=float)
        if self._smooth.isChecked():
            f = smooth_force(f, window_length=self._smooth_win.value(), polyorder=3)

        seg = segment_fd_cycles(f, dist,
                                min_segment=self._min_seg.value(),
                                smooth_window=self._dist_smooth.value())
        self._dr()['fd_segments'] = seg
        self._dr()['fd_force_used'] = f

        self._record('fd_segment', {
            'smoothed': self._smooth.isChecked(),
            'min_segment': self._min_seg.value(),
            'n_stretches': len(seg['stretches']),
            'n_relaxes': len(seg['relaxes'])})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summ = summarise_cycles(seg)
            show_dataframes_dialog("FD Cycles", [('Half-cycle summary', summ.round(3))])
        except Exception:
            pass
        napari_show_info(
            f"Unfolded into {len(seg['stretches'])} stretch and "
            f"{len(seg['relaxes'])} relax half-cycles.")

    # ── Step 3: plot + export ──────────────────────────────────────────
    def _add_plot(self, layout):
        grp  = QGroupBox("Step 3 — Plot Loops & Export")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._show_wlc = QCheckBox("Overlay worm-like chain (dsDNA) reference")
        self._show_wlc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._show_wlc.setChecked(True)
        form.addRow(self._show_wlc)

        self._wlc_L0 = QDoubleSpinBox(); self._wlc_L0.setRange(0.01, 1000); self._wlc_L0.setValue(16.49)
        self._wlc_L0.setDecimals(3)
        self._wlc_L0.setToolTip("Contour length L0 (µm).")
        form.addRow("WLC contour length (µm):", self._wlc_L0)

        self._wlc_Lp = QDoubleSpinBox(); self._wlc_Lp.setRange(0.1, 1000); self._wlc_Lp.setValue(50.0)
        self._wlc_Lp.setDecimals(1)
        self._wlc_Lp.setToolTip("Persistence length Lp (nm). dsDNA ≈ 50 nm.")
        form.addRow("WLC persistence length (nm):", self._wlc_Lp)

        self._wlc_K0 = QDoubleSpinBox(); self._wlc_K0.setRange(1, 100000); self._wlc_K0.setValue(1500.0)
        self._wlc_K0.setDecimals(0)
        self._wlc_K0.setToolTip("Stretch modulus K0 (pN). dsDNA ≈ 1500 pN.")
        form.addRow("WLC stretch modulus (pN):", self._wlc_K0)

        plot_btn = QPushButton("▶  Plot FD Loops")
        plot_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        plot_btn.clicked.connect(self._on_plot)
        form.addRow(plot_btn)

        export_btn = QPushButton("Export Per-Cycle Tables (CSV)")
        export_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        export_btn.clicked.connect(self._on_export)
        form.addRow(export_btn)
        layout.addWidget(grp)

    def _on_plot(self):
        from pycat.toolbox.fd_curve_tools import wlc_extensible
        seg = self._dr().get('fd_segments')
        if seg is None:
            napari_show_warning("Segment the cycles first (Step 2)."); return
        try:
            import matplotlib
            import matplotlib.pyplot as plt
        except Exception as e:
            napari_show_warning(f"matplotlib unavailable: {e}"); return

        fig, ax = plt.subplots(figsize=(7, 6))
        ns = len(seg['stretches']); nr = len(seg['relaxes'])
        for i, (d_seg, f_seg) in enumerate(seg['stretches']):
            ax.plot(d_seg, f_seg, color='tab:red', alpha=0.4 + 0.5 * (i / max(ns, 1)),
                    lw=1, label='stretch' if i == 0 else None)
        for i, (d_seg, f_seg) in enumerate(seg['relaxes']):
            ax.plot(d_seg, f_seg, color='tab:green', alpha=0.4 + 0.5 * (i / max(nr, 1)),
                    lw=1, label='relax' if i == 0 else None)

        if self._show_wlc.isChecked():
            f_ref = np.linspace(0.5, 60, 500)
            d_ref = wlc_extensible(f_ref, self._wlc_L0.value(),
                                   self._wlc_Lp.value(), self._wlc_K0.value())
            ax.plot(d_ref, f_ref, 'k--', lw=1.5, label='WLC (dsDNA)')

        ax.set_xlabel('Distance (µm)'); ax.set_ylabel('Force (pN)')
        ax.set_title(os.path.basename(self._dr().get('fd_path', 'FD curve')))
        ax.legend(frameon=False)
        fig.tight_layout()

        self._record('fd_plot', {
            'wlc_overlay': self._show_wlc.isChecked(),
            'n_stretches': ns, 'n_relaxes': nr})
        try:
            self.viewer.window.add_dock_widget(
                self._mpl_canvas(fig), name="FD Loops", area='right')
        except Exception:
            plt.show()
        napari_show_info(f"Plotted {ns} stretch and {nr} relax loops.")

    def _mpl_canvas(self, fig):
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        return FigureCanvasQTAgg(fig)

    def _add_rips(self, layout):
        grp  = QGroupBox("Step 4 — Detect Rips / Unzips")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Finds sudden force drops in each stretch (structure unfolding) and "
            "estimates the rupture force and contour-length released (ΔLc → "
            "nucleotides) using a single-stranded DNA/RNA model.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._na_type = QComboBox(); self._na_type.addItems(['ssDNA', 'ssRNA'])
        self._na_type.setToolTip("Strand type of the unfolding segment (sets the rise per nucleotide).")
        form.addRow("Strand type:", self._na_type)

        self._min_drop = QDoubleSpinBox(); self._min_drop.setRange(0.1, 200); self._min_drop.setValue(3.0)
        self._min_drop.setDecimals(1)
        self._min_drop.setToolTip("Minimum force drop (pN) counted as a rip (peak prominence).")
        form.addRow("Min force drop (pN):", self._min_drop)

        self._kuhn = QDoubleSpinBox(); self._kuhn.setRange(0.1, 20); self._kuhn.setValue(1.5)
        self._kuhn.setDecimals(2)
        self._kuhn.setToolTip("Kuhn length (nm) = 2·persistence length. ssNA ≈ 1.5 nm.")
        form.addRow("Kuhn length (nm):", self._kuhn)

        self._ss_mod = QDoubleSpinBox(); self._ss_mod.setRange(1, 100000); self._ss_mod.setValue(800.0)
        self._ss_mod.setDecimals(0)
        self._ss_mod.setToolTip("ssNA stretch modulus S (pN). ssNA ≈ 800 pN.")
        form.addRow("Stretch modulus (pN):", self._ss_mod)

        self._rise = QDoubleSpinBox(); self._rise.setRange(0.1, 2.0); self._rise.setValue(0.59)
        self._rise.setDecimals(3)
        self._rise.setToolTip("Contour rise per nucleotide (nm). ssDNA/ssRNA ≈ 0.59 nm/nt.")
        form.addRow("Rise per nt (nm):", self._rise)

        btn = QPushButton("▶  Detect Rips (all stretches)")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_rips)
        form.addRow(btn)
        layout.addWidget(grp)

    def _on_rips(self):
        from pycat.toolbox.fd_curve_tools import detect_all_rips, TERRA_REPEAT_NT
        seg = self._dr().get('fd_segments')
        if seg is None:
            napari_show_warning("Segment the cycles first (Step 2)."); return
        rips = detect_all_rips(
            seg, which='stretches',
            min_force_drop_pN=self._min_drop.value(),
            kuhn_length_nm=self._kuhn.value(),
            stretch_modulus_pN=self._ss_mod.value(),
            rise_nm_per_nt=self._rise.value())
        self._dr()['fd_rips'] = rips

        self._record('fd_rips', {
            'na_type': self._na_type.currentText(),
            'min_force_drop_pN': self._min_drop.value(),
            'n_rips': int(len(rips))})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            if len(rips):
                med_force = rips['rupture_force_pN'].median()
                med_nt = rips['n_nucleotides'].median()
                overview = pd.DataFrame([{
                    'n_rips': len(rips),
                    'median rupture force (pN)': round(med_force, 2),
                    'median ΔLc (nm)': round(rips['delta_Lc_um'].median() * 1000, 2),
                    'median nucleotides released': round(med_nt, 1),
                    'TERRA (UUAGGG)x10 full contour (nt)': TERRA_REPEAT_NT * 10,
                }])
                show_dataframes_dialog("FD Rip Analysis",
                                       [('Overview', overview),
                                        ('Per-rip', rips.round(4))])
            else:
                show_dataframes_dialog("FD Rip Analysis",
                                       [('Result', pd.DataFrame([{'message': 'No rips detected above threshold.'}]))])
        except Exception:
            pass
        napari_show_info(
            f"Detected {len(rips)} rips across all stretches."
            + (f" Median rupture force {rips['rupture_force_pN'].median():.1f} pN."
               if len(rips) else " Try lowering the min force drop."))

    def _on_export(self):
        from PyQt5.QtWidgets import QFileDialog
        from pycat.toolbox.fd_curve_tools import cycles_to_dataframe
        seg = self._dr().get('fd_segments')
        if seg is None:
            napari_show_warning("Segment the cycles first (Step 2)."); return
        folder = QFileDialog.getExistingDirectory(None, "Select export folder")
        if not folder:
            return
        base = os.path.splitext(os.path.basename(self._dr().get('fd_path', 'fd')))[0]
        stretch_df = cycles_to_dataframe(seg['stretches'], 'stretch')
        relax_df = cycles_to_dataframe(seg['relaxes'], 'relax')
        try:
            sp = os.path.join(folder, f"{base}_stretch.csv")
            rp = os.path.join(folder, f"{base}_relax.csv")
            stretch_df.to_csv(sp, index=False)
            relax_df.to_csv(rp, index=False)
        except Exception as e:
            napari_show_warning(f"Export failed: {e}"); return
        self._record('fd_export', {'folder': folder, 'base': base})
        napari_show_info(f"Exported stretch/relax cycle tables to {folder}.")
