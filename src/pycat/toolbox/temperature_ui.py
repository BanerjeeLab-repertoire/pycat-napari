"""
PyCAT Temperature-Dependent Microscopy Annotation & Processing UI
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
    QScrollArea, QSizePolicy, QComboBox,
)
from PyQt5.QtCore import Qt
from pycat.ui.field_status import (
    status_row, add_reset_buttons, REQUIRED, OPTIONAL, AUTO, EXPERT)


class TemperatureDependentUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager
        self._tiff_path      = None

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _get_stack(self, sname, progress_bar=None):
        """Materialize the named stack ONCE and cache it, so the several
        temperature analyses (clear-frame guess, turbidity, per-temperature,
        transitions) don't each re-decode the whole lazy stack from disk.

        The cache is keyed on the layer name AND the identity of the layer's
        underlying data object, so it invalidates automatically when the user
        picks a different stack or the layer data is replaced.
        """
        try:
            layer = self.viewer.layers[sname]
        except Exception:
            return None
        data = layer.data
        cache = getattr(self, '_stack_cache', None)
        if cache is not None and cache[0] == sname and cache[1] is data:
            return cache[2]
        # ── The cache is why this looked harmless ────────────────────────────────────────
        #
        # Only the FIRST call decodes; every later one is instant. So the freeze happened once, on
        # whichever section the user clicked first — the kind of "it only hangs sometimes" nobody pins
        # down. The decode now runs OFF the Qt thread behind a modal dialog, so even that first call no
        # longer freezes the window. `progress_bar` is retained for the five callers that pass their
        # section's bar, but the worker owns its own dialog, so it is no longer read.
        from pycat.utils.qt_worker import materialize_off_thread
        arr = materialize_off_thread(data, viewer=self.viewer)
        self._stack_cache = (sname, data, arr)
        return arr

    def _on_pixel_size_set(self, v):
        """When the user enters a pixel size in the gate, refresh the field
        circles AND update the on-screen scale bar to microns."""
        self._registry.refresh()
        try:
            self.central_manager.file_io._enable_auto_scale_bar()
        except Exception:
            pass

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
            "<b>Temperature-Dependent Microscopy Annotation &amp; Processing</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Sync a time-lapse to a temperature log and detect T_phase / T_clear.</span>")
        header.setWordWrap(True)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        # Field-status framework: a status circle in front of each interactive
        # input, a Step 1 file-I/O block, and a pixel-size gate that appears only
        # when the metadata provided no scale.
        from pycat.ui.field_status import (
            FieldRegistry, add_step1_file_io, add_pixel_size_gate)
        self._registry = FieldRegistry()
        add_step1_file_io(self.viewer, layout, self._registry,
                          on_change=self._registry.refresh)
        self._pixel_gate_refresh = add_pixel_size_gate(
            layout, self._dr, on_set=self._on_pixel_size_set,
            central_manager=self.central_manager)

        self._add_sync(layout)
        self._add_turbidity(layout)
        self._add_export(layout)
        self._add_batch(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="Temperature-Dependent Microscopy")

    # ── Step 2: sync ───────────────────────────────────────────────────
    def _add_sync(self, layout):
        grp  = QGroupBox("Step 2 — Sync Temperatures to Frames")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Pick the stack, then a temperature CSV file or its folder.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._stack_dd = self.create_layer_dropdown(napari.layers.Image)
        self._stack_dd.setToolTip("The MicroManager OME-TIFF stack (already opened).")
        status_row(form, self._registry, "Stack layer:", self._stack_dd, REQUIRED, step="step2")

        tiff_row = QHBoxLayout()
        self._tiff_edit = QLineEdit(); self._tiff_edit.setPlaceholderText("path to .ome.tif (for metadata)")
        self._tiff_edit.setMinimumWidth(40); self._tiff_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._tiff_edit.setToolTip("The TIFF file on disk — needed to read MicroManager timing metadata.")
        tiff_btn = QPushButton("Browse…"); tiff_btn.clicked.connect(self._browse_tiff)
        tiff_row.addWidget(self._tiff_edit); tiff_row.addWidget(tiff_btn)
        tw = QWidget(); tw.setLayout(tiff_row); form.addRow("TIFF file:", tw)
        # Auto-fill the TIFF path from where the selected stack was loaded.
        def _autofill_tiff(*_):
            if not self._tiff_edit.text().strip():
                p = getattr(self.central_manager.file_io, "filePath", "")
                if p:
                    self._tiff_edit.setText(str(p))
        self._stack_dd.currentIndexChanged.connect(_autofill_tiff)
        _autofill_tiff()

        # One flexible field: accepts either a temperature CSV file OR a folder
        # (in which case the matching CSV is auto-located by the TIFF's date).
        temp_row = QHBoxLayout()
        self._temp_input = QLineEdit()
        self._temp_input.setMinimumWidth(40); self._temp_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._temp_input.setPlaceholderText("temperature CSV file  —or—  its parent folder (auto-find by date)")
        self._temp_input.setToolTip(
            "Path to the temperature CSV, or to the folder containing it. "
            "If you give a folder, the CSV is matched to the TIFF's date.")
        file_btn = QPushButton("File…"); file_btn.clicked.connect(self._browse_csv)
        fold_btn = QPushButton("Folder…"); fold_btn.clicked.connect(self._browse_tempfolder)
        temp_row.addWidget(self._temp_input)
        temp_row.addWidget(file_btn); temp_row.addWidget(fold_btn)
        tcw = QWidget(); tcw.setLayout(temp_row); form.addRow("Temp CSV / folder:", tcw)
        # Back-compat aliases so the rest of the code keeps working.
        self._csv_edit = self._temp_input
        self._tempfolder_edit = self._temp_input

        self._temp_col = QLineEdit("AI0 (°C)")
        self._temp_col.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._temp_col.setToolTip("CSV column holding the temperature.")
        status_row(form, self._registry, "Temp column:", self._temp_col, OPTIONAL, default="AI0 (°C)", step="step2")

        self._csv_header = QSpinBox(); self._csv_header.setRange(0, 100); self._csv_header.setValue(6)
        self._csv_header.setToolTip("Header row index for pd.read_csv (lab DAQ export uses 6).")
        status_row(form, self._registry, "CSV header row:", self._csv_header, OPTIONAL, default=6, step="step2")

        self._add_text_layer = QCheckBox("Add temperature text layer over image")
        self._add_text_layer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._add_text_layer.setChecked(True)
        form.addRow(self._add_text_layer)

        self._sync_prog = QProgressBar(); self._sync_prog.setVisible(False)
        btn = QPushButton("▶  Sync Temperatures")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
        temp = self._temp_input.text().strip()
        if not tiff or not os.path.exists(tiff):
            napari_show_warning("Set a valid TIFF path (needed for timing metadata)."); return
        if not temp or not os.path.exists(temp):
            napari_show_warning("Set a valid temperature CSV file or its parent folder."); return
        # Flexible input: a folder auto-locates the CSV by the TIFF's date;
        # a file is used directly.
        if os.path.isdir(temp):
            from pycat.toolbox.temperature_tools import locate_temperature_csv
            csv = locate_temperature_csv(tiff, temp)
            if not csv:
                napari_show_warning(
                    "No matching temperature CSV found in that folder for the "
                    "TIFF's date. Point to the CSV file directly, or check the folder.")
                return
            napari_show_info(f"Found temperature CSV: {os.path.basename(csv)}")
        else:
            csv = temp

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

        # Auto-fill Step 5 (batch) from Step 2 paths when empty: the TIFF's
        # parent folder, and the temperature file's folder.
        try:
            if hasattr(self, '_batch_tiff_root') and not self._batch_tiff_root.text().strip():
                self._batch_tiff_root.setText(os.path.dirname(tiff))
            if hasattr(self, '_batch_temp_root') and not self._batch_temp_root.text().strip():
                temp_folder = temp if os.path.isdir(temp) else os.path.dirname(csv)
                self._batch_temp_root.setText(temp_folder)
        except Exception:
            pass

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
        labels = build_temperature_labels(temps, elapsed_s)
        n = len(labels)

        # Remove any legacy Points-layer annotation from older versions (that
        # one lived in data space and moved/scaled when zooming).
        if "Temperature Annotation" in self.viewer.layers:
            self.viewer.layers.remove("Temperature Annotation")

        # Use napari's canvas-fixed text overlay so the readout stays put while
        # zooming/panning, and update it as the T slider moves.
        ov = self.viewer.text_overlay
        ov.visible = True
        try:
            ov.color = 'yellow'
        except Exception:
            pass
        try:
            ov.font_size = 12
        except Exception:
            pass
        try:
            ov.position = 'top_left'
        except Exception:
            pass

        def _update(event=None):
            try:
                idx = int(self.viewer.dims.current_step[0])
            except Exception:
                idx = 0
            idx = max(0, min(idx, n - 1))
            try:
                self.viewer.text_overlay.text = labels[idx]
            except Exception:
                pass

        # Replace any previous callback so re-running Step 2 doesn't stack them.
        prev = getattr(self, '_temp_overlay_cb', None)
        if prev is not None:
            try:
                self.viewer.dims.events.current_step.disconnect(prev)
            except Exception:
                pass
        self._temp_overlay_cb = _update
        self.viewer.dims.events.current_step.connect(_update)
        _update()

    # ── Step 3: turbidity ──────────────────────────────────────────────
    def _add_turbidity(self, layout):
        grp  = QGroupBox("Step 3 — Entropy Turbidity & Transitions")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._subtract_first = QCheckBox("Subtract first frame (remove static pattern)")
        self._subtract_first.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._subtract_first.setChecked(False)
        self._subtract_first.setToolTip(
            "Subtract the (assumed clear) reference frame to remove the static "
            "illumination pattern before computing entropy.")
        status_row(form, self._registry, None, self._subtract_first, OPTIONAL, default=False, step="step3")

        # Reference frame to subtract (default 0) + an entropy-based guesser.
        ref_row = QHBoxLayout()
        self._ref_frame = QSpinBox(); self._ref_frame.setRange(0, 100000)
        self._ref_frame.setValue(0)
        self._ref_frame.setToolTip(
            "Which frame is the clear reference. For UCST/LCST samples the clear "
            "frame may be at the end, not the start.")
        guess_btn = QPushButton("Guess clear frame")
        guess_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        guess_btn.setToolTip(
            "Pick the lowest-entropy frame and check it is genuinely flat. "
            "Warns if no clear frame exists (condensates throughout).")
        guess_btn.clicked.connect(self._on_guess_clear_frame)
        ref_row.addWidget(self._ref_frame); ref_row.addWidget(guess_btn)
        rw = QWidget(); rw.setLayout(ref_row); form.addRow("Reference frame:", rw)

        # Let the user SEE the subtracted result as its own layer (previously the
        # subtraction was only applied internally to the entropy computation and
        # the export, with no visible layer). This applies the same reference
        # subtraction and adds the corrected stack to the viewer.
        preview_sub_btn = QPushButton("Preview subtracted stack \u2192 new layer")
        preview_sub_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        preview_sub_btn.setToolTip(
            "Apply the reference-frame subtraction (brightfield: subtract pattern, "
            "keep gray baseline) and add the result as a new layer so you can see "
            "it. Does not change the analysis \u2014 it just materialises what the "
            "subtraction produces.")
        preview_sub_btn.clicked.connect(self._on_preview_subtracted)
        form.addRow("", preview_sub_btn)

        self._correct_focus = QCheckBox("Correct focal drift (off by default)")
        self._correct_focus.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._correct_focus.setChecked(False)
        self._correct_focus.setToolTip(
            "Regress the per-frame focus score out of the entropy signal. Use "
            "ONLY if real focal drift is corrupting the curve. Caution: when "
            "condensates themselves sharpen the focus metric, this subtracts real "
            "turbidity signal (corrected entropy dips below baseline) — leave off "
            "unless you know defocus is the problem.")
        status_row(form, self._registry, None, self._correct_focus, OPTIONAL, default=False, step="step3")

        self._entropy_bins = QSpinBox(); self._entropy_bins.setRange(16, 1024); self._entropy_bins.setValue(256)
        self._entropy_bins.setToolTip("Histogram bins for the entropy calculation.")
        status_row(form, self._registry, "Entropy bins:", self._entropy_bins, EXPERT, default=256, step="step3")

        # Transition-temperature definition — different conventions suit different
        # samples, so let the user pick.
        from PyQt5.QtWidgets import QRadioButton, QButtonGroup
        self._method_baseline = QRadioButton("Baseline departure / return")
        self._method_baseline.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._method_baseline.setChecked(True)
        self._method_baseline.setToolTip(
            "Report where the signal first leaves the baseline (cloud) and "
            "returns to it (clear) — the onset/offset of the transition.")
        self._method_midpoint = QRadioButton("Steepest point (midpoint)")
        self._method_midpoint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._method_midpoint.setToolTip(
            "Report the steepest point of the transition (temperature of maximum "
            "|dS/dT|) — the transition midpoint.")
        self._method_group = QButtonGroup(self._method_baseline.parent())
        self._method_group.addButton(self._method_baseline)
        self._method_group.addButton(self._method_midpoint)
        form.addRow("Transition point:", self._method_baseline)
        form.addRow("", self._method_midpoint)

        # Onset threshold: how far above baseline (as a % of the baseline→peak
        # amplitude) counts as the departure/return point. Only used by the
        # baseline method.
        self._onset_frac = QDoubleSpinBox()
        self._onset_frac.setRange(1.0, 40.0); self._onset_frac.setValue(12.0)
        self._onset_frac.setSuffix(" %"); self._onset_frac.setSingleStep(1.0)
        self._onset_frac.setToolTip(
            "Baseline method: the signal must rise this fraction of the "
            "baseline-to-peak amplitude above baseline to count as the "
            "departure (cloud) or return (clear) point. Lower = earlier onset.")
        status_row(form, self._registry, "Onset threshold:", self._onset_frac, OPTIONAL, default=12.0, step="step3")

        self._turb_prog = QProgressBar(); self._turb_prog.setVisible(False)
        btn = QPushButton("▶  Compute Turbidity & Detect Transitions")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_turbidity)
        form.addRow(self._turb_prog); form.addRow(btn)

        # Save / Clear results.
        sc_row = QHBoxLayout()
        save_btn = QPushButton("Save Results (CSV)")
        save_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        save_btn.setToolTip("Save the transition summary (T_cloud, T_clear, "
                            "hysteresis) and the full turbidity curve to CSV.")
        save_btn.clicked.connect(self._on_save_turbidity)
        clear_btn = QPushButton("Clear Results")
        clear_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        clear_btn.setToolTip("Discard the stored turbidity results.")
        clear_btn.clicked.connect(self._on_clear_turbidity)
        sc_row.addWidget(save_btn); sc_row.addWidget(clear_btn)
        form.addRow(sc_row)
        # track the method choice + reference frame, and add reset buttons
        self._registry.register(self._method_baseline, OPTIONAL, default=True, step="step3")
        self._registry.register(self._ref_frame, OPTIONAL, default=0, step="step3")
        add_reset_buttons(form, self._registry, "step3")
        layout.addWidget(grp)

    def _on_guess_clear_frame(self):
        from pycat.toolbox.temperature_tools import guess_clear_frame
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        stack = self._get_stack(sname, progress_bar=self._sync_prog)
        if stack.ndim != 3:
            napari_show_warning("Reference-frame guessing needs a (T, H, W) stack."); return
        res = guess_clear_frame(stack)
        self._ref_frame.setValue(res['index'])
        if res['is_clear']:
            self._subtract_first.setChecked(True)
            napari_show_info(
                f"Clear frame ≈ {res['index']} (flat field, CoV={res['cov']:.3f}). "
                "Enabled subtraction.")
        else:
            napari_show_warning(
                f"Frame {res['index']} is the least-turbid frame but is NOT flat "
                f"(CoV={res['cov']:.3f} > {res['threshold']:.2f}) — this stack may "
                "have no clear reference. Subtraction left off; enable it only if "
                "you trust this frame.")

    def _on_preview_subtracted(self):
        """Apply the reference-frame subtraction and add the corrected stack as a
        visible layer, so the user can see what the subtraction produces. Uses
        the shared reference_subtraction (brightfield mode = the temperature use
        case: subtract the static pattern, keep the gray baseline; the reference
        frame is rebuilt from neighbours)."""
        from pycat.toolbox.temperature_tools import reference_subtraction
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        stack = self._get_stack(sname, progress_bar=self._sync_prog)
        if stack is None or stack.ndim != 3:
            napari_show_warning("Need a (T, H, W) stack to subtract."); return
        ref_idx = int(self._ref_frame.value())
        ref_idx = max(0, min(ref_idx, stack.shape[0] - 1))
        try:
            corrected, info = reference_subtraction(
                stack, stack[ref_idx], mode='brightfield',
                rebuild_reference_index=ref_idx)
        except Exception as e:
            napari_show_warning(f"Subtraction failed: {e}")
            import traceback; traceback.print_exc(); return
        name = f"{sname} (ref-subtracted f{ref_idx})"
        try:
            self.viewer.add_image(corrected, name=name)
            napari_show_info(f"Added '{name}'. Static pattern from frame "
                             f"{ref_idx} subtracted; gray baseline preserved.")
        except Exception as e:
            napari_show_warning(f"Could not add layer: {e}")

    def _on_turbidity(self):
        from pycat.toolbox.temperature_tools import (
            entropy_turbidity_curve, detect_transitions)
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        temps = self._dr().get('temp_temperatures')
        if temps is None:
            napari_show_warning("Run Step 2 (Sync Temperatures) first."); return
        stack = self._get_stack(sname, progress_bar=self._turb_prog)
        if stack.ndim != 3:
            napari_show_warning("Temperature analysis needs a (T, H, W) stack."); return

        self._turb_prog.setVisible(True); self._turb_prog.setRange(0, 0)
        try:
            df = entropy_turbidity_curve(
                stack, temps,
                subtract_first_frame=self._subtract_first.isChecked(),
                correct_focal_drift=self._correct_focus.isChecked(),
                bins=self._entropy_bins.value(),
                reference_frame_index=self._ref_frame.value())
            sig_col = 'entropy_corrected' if self._correct_focus.isChecked() else 'entropy'
            method = 'baseline' if self._method_baseline.isChecked() else 'midpoint'
            trans = detect_transitions(df, sig_col, method=method,
                                       frac=self._onset_frac.value() / 100.0)
        except Exception as e:
            self._turb_prog.setVisible(False)
            napari_show_warning(f"Turbidity analysis failed: {e}")
            import traceback; traceback.print_exc(); return
        self._turb_prog.setVisible(False)

        # Store the curve for the Plotting Widget without the focus_score column
        # (it is collinear with turbidity and misleads if plotted/regressed).
        self._dr()['temp_turbidity_df'] = df.drop(columns=['focus_score'], errors='ignore')
        self._dr()['temp_transitions'] = trans
        summary = pd.DataFrame([{
            'T_cloud_C':    round(trans['T_phase_C'], 3) if np.isfinite(trans['T_phase_C']) else None,
            'T_clear_C':    round(trans['T_clear_C'], 3) if np.isfinite(trans['T_clear_C']) else None,
            'hysteresis_C': round(trans['hysteresis_C'], 3) if np.isfinite(trans['hysteresis_C']) else None,
            'cloud_branch': trans.get('cloud_branch'),
            'clear_branch': trans.get('clear_branch'),
            'method':       method,
            'signal':       sig_col,
        }])
        self._dr()['temp_turbidity_summary'] = summary
        self._record('temperature_turbidity', {
            'subtract_first': self._subtract_first.isChecked(),
            'correct_focal_drift': self._correct_focus.isChecked(),
            'T_phase_C': trans['T_phase_C'], 'T_clear_C': trans['T_clear_C'],
            'hysteresis_C': trans['hysteresis_C']})

        try:
            from pycat.toolbox.temperature_tools import plot_turbidity_transitions
            plot_turbidity_transitions(df, trans, signal_column=sig_col,
                                       interactive=True)
        except Exception as e:
            print(f"[PyCAT] turbidity plot failed: {e}")

        tp = trans['T_phase_C']; tc = trans['T_clear_C']
        napari_show_info(
            f"Transitions: T_phase={tp:.2f}°C, T_clear={tc:.2f}°C, "
            f"hysteresis={trans['hysteresis_C']:.2f}°C"
            if np.isfinite(tp) and np.isfinite(tc) else
            "Turbidity computed — see the results table (transition estimate "
            "may be unreliable; check the entropy curve).")

    def _on_save_turbidity(self):
        from PyQt5.QtWidgets import QFileDialog
        import os
        summary = self._dr().get('temp_turbidity_summary')
        curve = self._dr().get('temp_turbidity_df')
        if summary is None or curve is None:
            napari_show_warning("No turbidity results to save. Run Step 3 first."); return
        path, _ = QFileDialog.getSaveFileName(
            None, "Save turbidity results (base name)",
            "temperature_turbidity.csv", "CSV (*.csv)")
        if not path:
            return
        base = path[:-4] if path.lower().endswith('.csv') else path
        try:
            summary.to_csv(base + "_transitions.csv", index=False)
            curve.to_csv(base + "_curve.csv", index=False)
            napari_show_info(
                f"Saved {os.path.basename(base)}_transitions.csv and _curve.csv.")
        except Exception as e:
            napari_show_warning(f"Save failed: {e}")

    def _on_clear_turbidity(self):
        for k in ('temp_turbidity_df', 'temp_transitions', 'temp_turbidity_summary'):
            self._dr().pop(k, None)
        napari_show_info("Cleared turbidity results.")

    # ── Step 4: scale bar + export ─────────────────────────────────────
    def _add_export(self, layout):
        grp  = QGroupBox("Step 4 — Scale Bar & Annotated Export")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._use_scalebar = QCheckBox("Burn a scale bar into the exported video")
        self._use_scalebar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._use_scalebar.setChecked(True)
        self._use_scalebar.setToolTip(
            "Draw a scale bar on the MP4 using the image's pixel size "
            "(set automatically on load, or via the Set Scale tool).")
        form.addRow(self._use_scalebar)

        # On-screen scale bar (PyCAT's own Shapes-layer bar; length in microns).
        self._scalebar_um = QDoubleSpinBox()
        self._scalebar_um.setRange(0.1, 100000.0); self._scalebar_um.setDecimals(1)
        self._scalebar_um.setValue(10.0); self._scalebar_um.setSuffix(" µm")
        self._scalebar_um.setToolTip("Length of the scale bar drawn on the image.")
        form.addRow("Scale bar length:", self._scalebar_um)
        sb_btn = QPushButton("Draw Scale Bar on Image")
        sb_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        sb_btn.setToolTip("Draw PyCAT's own scale bar as a layer (immune to the "
                          "napari units black-canvas issue).")
        sb_btn.clicked.connect(self._on_scalebar)
        form.addRow(sb_btn)

        self._fps = QSpinBox(); self._fps.setRange(1, 60); self._fps.setValue(30)
        self._fps.setToolTip("Playback frame rate of the exported MP4.")
        form.addRow("Video FPS:", self._fps)

        # Pattern-corrected (dust/scratch removed) stack option.
        self._export_corrected = QCheckBox("Use pattern-corrected stack (dust / scratch removed)")
        self._export_corrected.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._export_corrected.setChecked(False)
        self._export_corrected.setToolTip(
            "Remove the static brightfield pattern using the Step 3 reference "
            "frame, preserving the gray baseline. The annotated MP4 is rendered "
            "from the corrected stack when this is on.")
        form.addRow(self._export_corrected)
        corr_btn = QPushButton("Add Pattern-Corrected Stack as Layer")
        corr_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        corr_btn.clicked.connect(self._on_add_corrected_layer)
        form.addRow(corr_btn)

        self._export_prog = QProgressBar(); self._export_prog.setVisible(False)
        exp_btn = QPushButton("▶  Export Annotated MP4")
        exp_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        exp_btn.clicked.connect(self._on_export)
        form.addRow(self._export_prog); form.addRow(exp_btn)
        layout.addWidget(grp)

    def _corrected_stack(self):
        """Return the pattern-corrected stack for the selected layer + reference
        frame, or None if unavailable."""
        from pycat.toolbox.temperature_tools import apply_static_pattern_correction
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return None
        stack = self._get_stack(sname, progress_bar=self._export_prog)
        if stack.ndim != 3:
            napari_show_warning("Pattern correction needs a (T, H, W) stack."); return None
        return apply_static_pattern_correction(stack, self._ref_frame.value())

    def _on_add_corrected_layer(self):
        corrected = self._corrected_stack()
        if corrected is None:
            return
        self.viewer.add_image(
            corrected, name=f"Pattern-Corrected (ref {self._ref_frame.value()})")
        napari_show_info(
            f"Added pattern-corrected stack (reference frame {self._ref_frame.value()}).")

    def _pixel_size(self):
        stored = self._dr().get('microns_per_pixel_sq')
        return float(stored) ** 0.5 if stored else 1.0

    def _on_scalebar(self):
        # Draw PyCAT's OWN scale bar (a Shapes-layer rectangle in data pixels)
        # rather than napari's scale_bar. This needs no Layer.units / scale_bar.unit
        # (the "inconsistent units" black-canvas trigger on this build) and zooms
        # correctly. The bar length in microns is set by the export field.
        from pycat.ui.ui_utils import draw_custom_scale_bar
        px = self._pixel_size()
        sname = self._stack_dd.currentText()
        if sname not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Stack layer '{sname}' not found."); return
        bar_um = self._scalebar_um.value() if hasattr(self, '_scalebar_um') else 10.0
        try:
            sl = draw_custom_scale_bar(
                self.viewer, self.viewer.layers[sname], px, scalebar_um=bar_um)
            if sl is None:
                napari_show_warning(
                    "Could not draw scale bar — check the pixel size and that the "
                    f"bar ({bar_um:g} µm) is shorter than the field of view.")
            else:
                napari_show_info(
                    f"PyCAT scale bar: {bar_um:g} µm at {px:.4g} µm/px.")
        except Exception as e:
            napari_show_warning(f"Could not draw scale bar: {e}")

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

        stack = self._get_stack(sname, progress_bar=self._export_prog)
        if self._export_corrected.isChecked():
            from pycat.toolbox.temperature_tools import apply_static_pattern_correction
            stack = apply_static_pattern_correction(stack, self._ref_frame.value())
        self._export_prog.setVisible(True); self._export_prog.setRange(0, stack.shape[0])
        try:
            self._render_annotated_mp4(
                stack, temps, elapsed_s, Path(out),
                fps=self._fps.value(), pixel_um=self._pixel_size(),
                scalebar_um=(10.0 if self._use_scalebar.isChecked() else 0.0))
        except Exception as e:
            self._export_prog.setVisible(False)
            napari_show_warning(f"Export failed: {e}")
            import traceback; traceback.print_exc(); return
        self._export_prog.setVisible(False)
        self._record('temperature_export_video', {
            'output': os.path.basename(out), 'fps': self._fps.value(),
            'scalebar_um': (10.0 if self._use_scalebar.isChecked() else 0.0)})
        napari_show_info(f"Exported annotated movie to {out}")

    # ── Step 5: batch ──────────────────────────────────────────────────
    def _add_batch(self, layout):
        grp  = QGroupBox("Step 5 — Batch (folder of TIFFs)")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Process every TIFF in a folder; auto-matches each temperature CSV by date.</span>")
        note.setWordWrap(True); form.addRow(note)

        row1 = QHBoxLayout()
        self._batch_tiff_root = QLineEdit(); self._batch_tiff_root.setPlaceholderText("folder containing TIFFs")
        self._batch_tiff_root.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        b1 = QPushButton("Browse…")
        b1.clicked.connect(lambda: self._browse_into(self._batch_tiff_root))
        row1.addWidget(self._batch_tiff_root); row1.addWidget(b1)
        w1 = QWidget(); w1.setLayout(row1); form.addRow("TIFF folder:", w1)

        row2 = QHBoxLayout()
        self._batch_temp_root = QLineEdit(); self._batch_temp_root.setPlaceholderText("temperature-files parent folder")
        self._batch_temp_root.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        b2 = QPushButton("Browse…")
        b2.clicked.connect(lambda: self._browse_into(self._batch_temp_root))
        row2.addWidget(self._batch_temp_root); row2.addWidget(b2)
        w2 = QWidget(); w2.setLayout(row2); form.addRow("Temp folder:", w2)

        self._batch_export_mp4 = QCheckBox("Also export annotated MP4 for each TIFF (saved beside it)")
        self._batch_export_mp4.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._batch_export_mp4.setChecked(True)
        self._batch_export_mp4.setToolTip(
            "Render a temperature/time-annotated MP4 per TIFF, named from the "
            "source file and written next to it.")
        form.addRow(self._batch_export_mp4)

        self._batch_export_corrected = QCheckBox("Also save pattern-corrected stack for each TIFF")
        self._batch_export_corrected.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._batch_export_corrected.setChecked(False)
        self._batch_export_corrected.setToolTip(
            "Save a dust/scratch-corrected TIFF (gray-preserving) per input, "
            "named <file>_corrected.tif beside the source.")
        form.addRow(self._batch_export_corrected)

        self._batch_phase_diagram = QCheckBox("Build a phase diagram from the batch")
        self._batch_phase_diagram.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._batch_phase_diagram.setChecked(True)
        self._batch_phase_diagram.setToolTip(
            "After the batch, parse the filenames for the swept variable and "
            "replicates and plot T_cloud vs that variable, shading the 2-phase "
            "region. Warns if the filenames can't be parsed unambiguously.")
        form.addRow(self._batch_phase_diagram)
        self._phase_side = QComboBox()
        self._phase_side.addItems(['2-phase above boundary (LCST)',
                                   '2-phase below boundary (UCST)'])
        self._phase_side.setToolTip(
            "Which side of the cloud-point boundary is the two-phase region: "
            "above for LCST-type (phase-separates on heating), below for UCST.")
        form.addRow("Phase region:", self._phase_side)

        self._batch_prog = QProgressBar(); self._batch_prog.setVisible(False)
        btn = QPushButton("▶  Run Batch Turbidity Analysis")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
                export_mp4=self._batch_export_mp4.isChecked(),
                export_corrected=self._batch_export_corrected.isChecked(),
                fps=self._fps.value(),
                pixel_um=self._pixel_size(),
                scalebar_um=(10.0 if self._use_scalebar.isChecked() else 0.0),
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
        self._record('temperature_batch', {
            'tiff_root': tiff_root, 'temp_root': temp_root,
            'subtract_first_frame': self._subtract_first.isChecked(),
            'correct_focal_drift': self._correct_focus.isChecked(),
            'temp_column': self._temp_col.text().strip() or 'AI0 (°C)',
            'csv_header': self._csv_header.value(),
            'export_mp4': self._batch_export_mp4.isChecked(),
            'export_corrected': self._batch_export_corrected.isChecked(),
            'fps': self._fps.value(),
            'pixel_um': self._pixel_size(),
            'use_scalebar': self._use_scalebar.isChecked(),
            'build_phase_diagram': (getattr(self, '_batch_phase_diagram', None) is not None
                                     and self._batch_phase_diagram.isChecked()),
            'phase_side': (self._phase_side.currentIndex()
                            if getattr(self, '_phase_side', None) is not None else None),
            'n_files': len(df), 'n_ok': n_ok,
        })

        # Phase diagram from the batch (parse filenames for the swept variable).
        if getattr(self, '_batch_phase_diagram', None) and self._batch_phase_diagram.isChecked():
            self._build_phase_diagram(df)

    def _build_phase_diagram(self, df):
        from pycat.toolbox.temperature_tools import parse_batch_filenames
        ok_df = df[df['status'] == 'ok'] if 'status' in df else df
        files = list(ok_df['file']) if 'file' in ok_df else []
        if len(files) < 2:
            napari_show_warning("Need at least two successfully-processed TIFFs "
                                "to build a phase diagram."); return
        parsed = parse_batch_filenames(files)
        if not parsed['ok']:
            napari_show_warning("Phase diagram: " + parsed['reason'] +
                                (f" Detected tokens: {parsed['candidates']}."
                                 if parsed.get('candidates') else ""))
            return
        # merge x_value onto the results by filename
        xmap = {d['file']: d['x_value'] for d in parsed['per_file']}
        pdf = ok_df.copy()
        pdf['x_value'] = pdf['file'].map(lambda f: xmap.get(str(f)))
        pdf = pdf.rename(columns={'T_phase_C': 'T_cloud'}).dropna(subset=['x_value', 'T_cloud'])
        if pdf['x_value'].nunique() < 2:
            napari_show_warning("Phase diagram: the swept variable takes only one "
                                "value among successful files."); return
        self._dr()['temp_phase_diagram_df'] = pdf
        side = 'below' if self._phase_side.currentIndex() == 1 else 'above'
        try:
            from pycat.toolbox.analysis_plots import plot_phase_diagram
            plot_phase_diagram(pdf, x_name=parsed['x_name'], two_phase=side,
                               title=f"Phase diagram ({parsed['x_name']})",
                               interactive=True)
            napari_show_info(f"Phase diagram: {pdf['x_value'].nunique()} "
                             f"{parsed['x_name']} values, swept variable auto-detected.")
        except Exception as e:
            napari_show_warning(f"Phase diagram plot failed: {e}")

    def _render_annotated_mp4(self, stack, temps, elapsed_s, out_path,
                              fps, pixel_um, scalebar_um):
        """Render annotated MP4 via the shared headless renderer; drive the UI
        progress bar through its callback."""
        from pycat.toolbox.temperature_tools import render_annotated_mp4
        def _prog(i, n):
            try:
                self._export_prog.setValue(i)
            except Exception:
                pass
        return render_annotated_mp4(
            stack, temps, elapsed_s, out_path, fps=fps, pixel_um=pixel_um,
            scalebar_um=scalebar_um, progress_callback=_prog)
