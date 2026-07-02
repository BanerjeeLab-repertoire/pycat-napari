"""
PyCAT Temperature-Dependent Condensate UI
===========================================
Synchronise a MicroManager time-lapse with a temperature CSV, annotate the
temperature (and a scale bar) onto the movie, and detect the phase-separation
/ dissolution transitions from a focus-corrected entropy turbidity curve.

Steps
-----
  Step 1 — Open the OME-TIFF stack (via File menu) and point at the CSV, or
           let the tool locate it from a temperature-files folder by date.
  Step 2 — Sync temperatures to frames; add a temperature text layer.
  Step 3 — Turbidity analysis: entropy vs temperature, focus-corrected,
           with algorithmic T_phase / T_clear / hysteresis.
  Step 4 — Add a scale bar and export an annotated MP4.
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
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar, QLineEdit,
    QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt


class TemperatureDependentUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager
        self._tiff_path      = None

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type, name_hint=''):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type, name_hint=name_hint)

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('temperature')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8); layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Temperature-Dependent Condensate Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Sync a MicroManager time-lapse with a temperature log, annotate "
            "temperatures on the movie, and detect T_phase / T_clear from a "
            "focus-corrected entropy turbidity curve.</span>")
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        self._add_sync(layout)
        self._add_turbidity(layout)
        self._add_export(layout)
        self._add_batch(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setWidget(main_w); scroll.setMinimumWidth(320)
        self.viewer.window.add_dock_widget(scroll, name="Temperature-Dependent Condensate")

    # ── Step 2: sync ───────────────────────────────────────────────────
    def _add_sync(self, layout):
        grp  = QGroupBox("Step 2 — Sync Temperatures to Frames")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the loaded stack. Then either browse to the temperature CSV "
            "directly, or point at the temperature-files parent folder and let "
            "the tool find the CSV by the TIFF's date.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._stack_dd = self.create_layer_dropdown(napari.layers.Image)
        self._stack_dd.setToolTip("The MicroManager OME-TIFF stack (already opened).")
        form.addRow("Stack layer:", self._stack_dd)

        tiff_row = QHBoxLayout()
        self._tiff_edit = QLineEdit(); self._tiff_edit.setPlaceholderText("path to .ome.tif (for metadata)")
        self._tiff_edit.setToolTip("The TIFF file on disk — needed to read MicroManager timing metadata.")
        tiff_btn = QPushButton("Browse…"); tiff_btn.clicked.connect(self._browse_tiff)
        tiff_row.addWidget(self._tiff_edit); tiff_row.addWidget(tiff_btn)
        tw = QWidget(); tw.setLayout(tiff_row); form.addRow("TIFF file:", tw)

        csv_row = QHBoxLayout()
        self._csv_edit = QLineEdit(); self._csv_edit.setPlaceholderText("path to temperature CSV")
        csv_btn = QPushButton("Browse…"); csv_btn.clicked.connect(self._browse_csv)
        csv_row.addWidget(self._csv_edit); csv_row.addWidget(csv_btn)
        cw = QWidget(); cw.setLayout(csv_row); form.addRow("Temperature CSV:", cw)

        folder_row = QHBoxLayout()
        self._tempfolder_edit = QLineEdit()
        self._tempfolder_edit.setPlaceholderText("or: temperature-files parent folder (auto-find by date)")
        folder_btn = QPushButton("Browse…"); folder_btn.clicked.connect(self._browse_tempfolder)
        auto_btn = QPushButton("Find CSV by date"); auto_btn.clicked.connect(self._auto_find_csv)
        folder_row.addWidget(self._tempfolder_edit); folder_row.addWidget(folder_btn)
        fw = QWidget(); fw.setLayout(folder_row); form.addRow("Temp folder:", fw)
        form.addRow(auto_btn)

        self._temp_col = QLineEdit("AI0 (°C)")
        self._temp_col.setToolTip("CSV column holding the temperature.")
        form.addRow("Temp column:", self._temp_col)

        self._csv_header = QSpinBox(); self._csv_header.setRange(0, 100); self._csv_header.setValue(6)
        self._csv_header.setToolTip("Header row index for pd.read_csv (lab DAQ export uses 6).")
        form.addRow("CSV header row:", self._csv_header)

        self._add_text_layer = QCheckBox("Add temperature text layer over image")
        self._add_text_layer.setChecked(True)
        form.addRow(self._add_text_layer)

        self._sync_prog = QProgressBar(); self._sync_prog.setVisible(False)
        btn = QPushButton("▶  Sync Temperatures")
        btn.clicked.connect(self._on_sync)
        form.addRow(self._sync_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _browse_tiff(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(None, "Select OME-TIFF", "", "TIFF (*.tif *.tiff)")
        if p:
            self._tiff_edit.setText(p); self._tiff_path = p

    def _browse_csv(self):
        from PyQt5.QtWidgets import QFileDialog
        p, _ = QFileDialog.getOpenFileName(None, "Select temperature CSV", "", "CSV (*.csv)")
        if p:
            self._csv_edit.setText(p)

    def _browse_tempfolder(self):
        from PyQt5.QtWidgets import QFileDialog
        p = QFileDialog.getExistingDirectory(None, "Select temperature-files parent folder")
        if p:
            self._tempfolder_edit.setText(p)

    def _auto_find_csv(self):
        from pycat.toolbox.temperature_tools import locate_temperature_csv
        tiff = self._tiff_edit.text().strip()
        folder = self._tempfolder_edit.text().strip()
        if not tiff or not os.path.exists(tiff):
            napari_show_warning("Set a valid TIFF path first (its date locates the CSV)."); return
        if not folder or not os.path.isdir(folder):
            napari_show_warning("Set the temperature-files parent folder first."); return
        csv = locate_temperature_csv(tiff, folder)
        if csv:
            self._csv_edit.setText(csv)
            napari_show_info(f"Found temperature CSV: {os.path.basename(csv)}")
        else:
            napari_show_warning(
                "No matching temperature CSV found for the TIFF's date. "
                "Check the folder or browse to the CSV manually.")

    def _on_sync(self):
        from pycat.toolbox.temperature_tools import (
            read_micromanager_times, sync_temperatures, elapsed_to_seconds,
            build_temperature_labels)
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        tiff = self._tiff_edit.text().strip()
        csv  = self._csv_edit.text().strip()
        if not tiff or not os.path.exists(tiff):
            napari_show_warning("Set a valid TIFF path (needed for timing metadata)."); return
        if not csv or not os.path.exists(csv):
            napari_show_warning("Set a valid temperature CSV path."); return

        self._sync_prog.setVisible(True); self._sync_prog.setRange(0, 0)
        try:
            times = read_micromanager_times(tiff)
            temps = sync_temperatures(
                times['received'], csv,
                temp_column=self._temp_col.text().strip() or 'AI0 (°C)',
                csv_header=self._csv_header.value())
            elapsed_s = elapsed_to_seconds(times['elapsed_ms'])
        except Exception as e:
            self._sync_prog.setVisible(False)
            napari_show_warning(f"Sync failed: {e}")
            import traceback; traceback.print_exc(); return
        self._sync_prog.setVisible(False)

        matched = int(np.isfinite(temps).sum())
        self._dr()['temp_temperatures'] = temps
        self._dr()['temp_elapsed_s'] = elapsed_s
        self._tiff_path = tiff

        if self._add_text_layer.isChecked():
            self._make_text_layer(sname, temps, elapsed_s)

        self._record('temperature_sync', {
            'tiff': os.path.basename(tiff), 'csv': os.path.basename(csv),
            'n_frames': times['n_frames'], 'n_matched': matched})
        napari_show_info(
            f"Synced {matched}/{times['n_frames']} frames to temperatures "
            f"({np.nanmin(temps):.1f}–{np.nanmax(temps):.1f} °C).")

    def _make_text_layer(self, stack_name, temps, elapsed_s):
        from pycat.toolbox.temperature_tools import build_temperature_labels
        stack = np.asarray(self.viewer.layers[stack_name].data)
        n = stack.shape[0]
        labels = build_temperature_labels(temps, elapsed_s)
        # One text point per frame, anchored top-left; napari shows the text
        # for the current T slice via the 'frame' coordinate.
        H = stack.shape[1]
        coords = np.array([[i, 0.06 * H, 0.05 * stack.shape[2]] for i in range(n)])
        props = {'label': labels}
        if "Temperature Annotation" in self.viewer.layers:
            self.viewer.layers.remove("Temperature Annotation")
        self.viewer.add_points(
            coords, name="Temperature Annotation", size=0,
            properties=props, text={'string': '{label}', 'size': 12,
                                    'color': 'yellow', 'anchor': 'upper_left'})

    # ── Step 3: turbidity ──────────────────────────────────────────────
    def _add_turbidity(self, layout):
        grp  = QGroupBox("Step 3 — Entropy Turbidity & Transitions")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        self._subtract_first = QCheckBox("Subtract first frame (remove static pattern)")
        self._subtract_first.setChecked(True)
        self._subtract_first.setToolTip(
            "Subtract the (assumed clear) first frame to remove the static "
            "illumination pattern before computing entropy.")
        form.addRow(self._subtract_first)

        self._correct_focus = QCheckBox("Correct focal drift (recommended)")
        self._correct_focus.setChecked(True)
        self._correct_focus.setToolTip(
            "Regress the per-frame focus score out of the entropy signal. "
            "Defocus broadens the histogram like turbidity does, so this "
            "isolates the genuine phase-separation signal.")
        form.addRow(self._correct_focus)

        self._entropy_bins = QSpinBox(); self._entropy_bins.setRange(16, 1024); self._entropy_bins.setValue(256)
        self._entropy_bins.setToolTip("Histogram bins for the entropy calculation.")
        form.addRow("Entropy bins:", self._entropy_bins)

        self._turb_prog = QProgressBar(); self._turb_prog.setVisible(False)
        btn = QPushButton("▶  Compute Turbidity & Detect Transitions")
        btn.clicked.connect(self._on_turbidity)
        form.addRow(self._turb_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _on_turbidity(self):
        from pycat.toolbox.temperature_tools import (
            entropy_turbidity_curve, detect_transitions)
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        temps = self._dr().get('temp_temperatures')
        if temps is None:
            napari_show_warning("Run Step 2 (Sync Temperatures) first."); return
        stack = np.asarray(self.viewer.layers[sname].data)
        if stack.ndim != 3:
            napari_show_warning("Temperature analysis needs a (T, H, W) stack."); return

        self._turb_prog.setVisible(True); self._turb_prog.setRange(0, 0)
        try:
            df = entropy_turbidity_curve(
                stack, temps,
                subtract_first_frame=self._subtract_first.isChecked(),
                correct_focal_drift=self._correct_focus.isChecked(),
                bins=self._entropy_bins.value())
            sig_col = 'entropy_corrected' if self._correct_focus.isChecked() else 'entropy'
            trans = detect_transitions(df, sig_col)
        except Exception as e:
            self._turb_prog.setVisible(False)
            napari_show_warning(f"Turbidity analysis failed: {e}")
            import traceback; traceback.print_exc(); return
        self._turb_prog.setVisible(False)

        self._dr()['temp_turbidity_df'] = df
        self._dr()['temp_transitions'] = trans
        self._record('temperature_turbidity', {
            'subtract_first': self._subtract_first.isChecked(),
            'correct_focal_drift': self._correct_focus.isChecked(),
            'T_phase_C': trans['T_phase_C'], 'T_clear_C': trans['T_clear_C'],
            'hysteresis_C': trans['hysteresis_C']})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summary = pd.DataFrame([{
                'T_phase (°C, cloud)':  round(trans['T_phase_C'], 2) if trans['T_phase_C']==trans['T_phase_C'] else None,
                'T_clear (°C)':         round(trans['T_clear_C'], 2) if trans['T_clear_C']==trans['T_clear_C'] else None,
                'hysteresis (°C)':      round(trans['hysteresis_C'], 2) if trans['hysteresis_C']==trans['hysteresis_C'] else None,
                'branch split frame':   trans['loc'],
                'focus corrected':      self._correct_focus.isChecked(),
            }])
            show_dataframes_dialog("Temperature Turbidity",
                                   [('Transitions', summary),
                                    ('Turbidity curve', df.round(4))])
        except Exception:
            pass

        tp = trans['T_phase_C']; tc = trans['T_clear_C']
        napari_show_info(
            f"Transitions: T_phase={tp:.2f}°C, T_clear={tc:.2f}°C, "
            f"hysteresis={trans['hysteresis_C']:.2f}°C"
            if np.isfinite(tp) and np.isfinite(tc) else
            "Turbidity computed — see the results table (transition estimate "
            "may be unreliable; check the entropy curve).")

    # ── Step 4: scale bar + export ─────────────────────────────────────
    def _add_export(self, layout):
        grp  = QGroupBox("Step 4 — Scale Bar & Annotated Export")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        self._px_to_um = QDoubleSpinBox()
        self._px_to_um.setRange(0.0, 1000); self._px_to_um.setValue(0.0)
        self._px_to_um.setDecimals(4); self._px_to_um.setSingleStep(0.01)
        self._px_to_um.setToolTip(
            "Pixel size (µm/px) for the scale bar. Leave 0 to use the value "
            "already stored from image loading, if any.")
        form.addRow("Pixel size (µm/px):", self._px_to_um)

        self._scalebar_um = QDoubleSpinBox()
        self._scalebar_um.setRange(0.1, 10000); self._scalebar_um.setValue(10.0)
        self._scalebar_um.setDecimals(1)
        self._scalebar_um.setToolTip("Length of the scale bar in µm.")
        form.addRow("Scale bar length (µm):", self._scalebar_um)

        sb_btn = QPushButton("Add / Update Scale Bar")
        sb_btn.clicked.connect(self._on_scalebar)
        form.addRow(sb_btn)

        self._fps = QSpinBox(); self._fps.setRange(1, 60); self._fps.setValue(10)
        self._fps.setToolTip("Playback frame rate of the exported MP4.")
        form.addRow("Video FPS:", self._fps)

        self._export_prog = QProgressBar(); self._export_prog.setVisible(False)
        exp_btn = QPushButton("▶  Export Annotated MP4")
        exp_btn.clicked.connect(self._on_export)
        form.addRow(self._export_prog); form.addRow(exp_btn)
        layout.addWidget(grp)

    def _pixel_size(self):
        px = self._px_to_um.value()
        if px > 0:
            return px
        stored = self._dr().get('microns_per_pixel_sq')
        return float(stored) ** 0.5 if stored else 1.0

    def _on_scalebar(self):
        # napari has a built-in scale bar; set the layer scale + enable it.
        px = self._pixel_size()
        try:
            self.viewer.scale_bar.visible = True
            self.viewer.scale_bar.unit = "um"
            sname = self._stack_dd.currentText()
            if sname in [l.name for l in self.viewer.layers]:
                lyr = self.viewer.layers[sname]
                # scale is (…, y, x); set the last two dims to the pixel size
                sc = list(lyr.scale)
                sc[-1] = px; sc[-2] = px
                lyr.scale = sc
            napari_show_info(
                f"Scale bar enabled at {px:.4g} µm/px "
                f"({self._scalebar_um.value():.0f} µm reference).")
        except Exception as e:
            napari_show_warning(f"Could not enable scale bar: {e}")

    def _on_export(self):
        from PyQt5.QtWidgets import QFileDialog
        from pathlib import Path
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        temps = self._dr().get('temp_temperatures')
        elapsed_s = self._dr().get('temp_elapsed_s')
        if temps is None:
            napari_show_warning("Run Step 2 (Sync Temperatures) first."); return

        out, _ = QFileDialog.getSaveFileName(
            None, "Save annotated MP4", "temperature_annotated.mp4", "MP4 (*.mp4)")
        if not out:
            return

        stack = np.asarray(self.viewer.layers[sname].data)
        self._export_prog.setVisible(True); self._export_prog.setRange(0, stack.shape[0])
        try:
            self._render_annotated_mp4(
                stack, temps, elapsed_s, Path(out),
                fps=self._fps.value(), pixel_um=self._pixel_size(),
                scalebar_um=self._scalebar_um.value())
        except Exception as e:
            self._export_prog.setVisible(False)
            napari_show_warning(f"Export failed: {e}")
            import traceback; traceback.print_exc(); return
        self._export_prog.setVisible(False)
        self._record('temperature_export_video', {
            'output': os.path.basename(out), 'fps': self._fps.value(),
            'scalebar_um': self._scalebar_um.value()})
        napari_show_info(f"Exported annotated movie to {out}")

    # ── Step 5: batch ──────────────────────────────────────────────────
    def _add_batch(self, layout):
        grp  = QGroupBox("Step 5 — Batch (folder of TIFFs)")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 4, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Process every TIFF under a folder (including nested subfolders), "
            "auto-matching each to its temperature CSV by date, and tabulate "
            "T_phase / T_clear for all of them.</span>")
        note.setWordWrap(True); form.addRow(note)

        row1 = QHBoxLayout()
        self._batch_tiff_root = QLineEdit(); self._batch_tiff_root.setPlaceholderText("folder containing TIFFs")
        b1 = QPushButton("Browse…")
        b1.clicked.connect(lambda: self._browse_into(self._batch_tiff_root))
        row1.addWidget(self._batch_tiff_root); row1.addWidget(b1)
        w1 = QWidget(); w1.setLayout(row1); form.addRow("TIFF folder:", w1)

        row2 = QHBoxLayout()
        self._batch_temp_root = QLineEdit(); self._batch_temp_root.setPlaceholderText("temperature-files parent folder")
        b2 = QPushButton("Browse…")
        b2.clicked.connect(lambda: self._browse_into(self._batch_temp_root))
        row2.addWidget(self._batch_temp_root); row2.addWidget(b2)
        w2 = QWidget(); w2.setLayout(row2); form.addRow("Temp folder:", w2)

        self._batch_prog = QProgressBar(); self._batch_prog.setVisible(False)
        btn = QPushButton("▶  Run Batch Turbidity Analysis")
        btn.clicked.connect(self._on_batch)
        form.addRow(self._batch_prog); form.addRow(btn)
        layout.addWidget(grp)

    def _browse_into(self, line_edit):
        from PyQt5.QtWidgets import QFileDialog
        p = QFileDialog.getExistingDirectory(None, "Select folder")
        if p:
            line_edit.setText(p)

    def _on_batch(self):
        from pycat.toolbox.temperature_tools import run_temperature_batch
        tiff_root = self._batch_tiff_root.text().strip()
        temp_root = self._batch_temp_root.text().strip()
        if not tiff_root or not os.path.isdir(tiff_root):
            napari_show_warning("Set a valid TIFF folder."); return
        if not temp_root or not os.path.isdir(temp_root):
            napari_show_warning("Set a valid temperature-files parent folder."); return

        self._batch_prog.setVisible(True); self._batch_prog.setRange(0, 0)
        try:
            df = run_temperature_batch(
                tiff_root, temp_root,
                subtract_first_frame=self._subtract_first.isChecked(),
                correct_focal_drift=self._correct_focus.isChecked(),
                temp_column=self._temp_col.text().strip() or 'AI0 (°C)',
                csv_header=self._csv_header.value(),
                progress_callback=lambda i, n: (
                    self._batch_prog.setRange(0, n), self._batch_prog.setValue(i)))
        except Exception as e:
            self._batch_prog.setVisible(False)
            napari_show_warning(f"Batch failed: {e}")
            import traceback; traceback.print_exc(); return
        self._batch_prog.setVisible(False)

        self._dr()['temp_batch_df'] = df
        n_ok = int((df['status'] == 'ok').sum())
        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Temperature Batch Results",
                                   [('Per-file transitions', df.round(3))])
        except Exception:
            pass
        napari_show_info(f"Batch complete: {n_ok}/{len(df)} TIFFs processed successfully.")

    def _render_annotated_mp4(self, stack, temps, elapsed_s, out_path,
                              fps, pixel_um, scalebar_um):
        """Render frames with temperature text + scale bar burned in via matplotlib."""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import imageio.v3 as iio
        from datetime import timedelta

        n = stack.shape[0]
        vmin, vmax = float(np.percentile(stack, 1)), float(np.percentile(stack, 99))
        bar_px = scalebar_um / pixel_um if pixel_um > 0 else 0

        frames = []
        for i in range(n):
            fig, ax = plt.subplots(figsize=(5, 5), dpi=100)
            ax.imshow(stack[i], cmap='gray', vmin=vmin, vmax=vmax)
            ax.axis('off')
            T = temps[i]
            secs = int(elapsed_s[i]) if (elapsed_s is not None and np.isfinite(elapsed_s[i])) else 0
            tc = f"{T:.2f} °C" if np.isfinite(T) else "-- °C"
            ax.set_title(f"{tc}   |   {timedelta(seconds=secs)} (h:m:s)", fontsize=12)
            # Scale bar in lower-right
            if bar_px > 0:
                H, W = stack.shape[1], stack.shape[2]
                x0 = W * 0.95 - bar_px; y0 = H * 0.92
                ax.plot([x0, x0 + bar_px], [y0, y0], '-', color='white', lw=3)
                ax.text(x0 + bar_px / 2, y0 - H * 0.02, f"{scalebar_um:.0f} µm",
                        color='white', ha='center', va='bottom', fontsize=10)
            fig.canvas.draw()
            buf = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8)
            buf = buf.reshape(fig.canvas.get_width_height()[::-1] + (4,))[:, :, :3]
            frames.append(buf.copy())
            plt.close(fig)
            self._export_prog.setValue(i + 1)

        iio.imwrite(str(out_path), np.stack(frames), fps=fps)
