"""
PyCAT FRAP UI
=============
Self-contained Fluorescence Recovery After Photobleaching pipeline.

Steps
-----
  Step 1 — Open recovery time-series (File menu, or Lumicks .h5 loader)
  Step 2 — Define bleach + reference ROIs (draw, auto-circle, or multi-ROI
           for Mosaic / MicroPoint multi-spot photostimulation)
  Step 3 — Analyze recovery (intensity, photofading correction, Taylor or
           pre-bleach normalization, recovery-model fit)
"""
from __future__ import annotations
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t
import numpy as np

from pycat.utils.pixel_size import pixel_size_um_or_default
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton,
)
from PyQt5.QtCore import Qt


class FRAPUI:
    def __init__(self, viewer, central_manager):
        self.viewer          = viewer
        self.central_manager = central_manager

    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return pixel_size_um_or_default(self._dr(), context='frap_ui')

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type, name_hint=''):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type, name_hint=name_hint)

    def setup_ui(self):
        try:
            self.central_manager.workflow_checklist.activate('frap')
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp:
                for step in bp.config.get('steps', []):
                    self.central_manager.workflow_checklist.on_step_recorded(step['step'])
        except Exception:
            pass

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 20, 4, 4)

        header = QLabel(
            "<b>FRAP Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Fluorescence Recovery After Photobleaching — measures molecular "
            "mobility and mobile fraction inside condensates. Uses Taylor "
            "(Brangwynne lab) normalization.</span>")
        header.setWordWrap(True)

        header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        # ── Step 1: load (status marker + load instruction) ────────────────
        try:
            from pycat.ui.field_status import add_step1_file_io, add_pixel_size_gate
            add_step1_file_io(
                self.viewer, layout,
                instruction_html=(
                    "Open a recovery time-series via <b>Open/Save File(s)</b> "
                    "(or the Lumicks .h5 loader)."))
            self._pixel_gate_refresh = add_pixel_size_gate(
                layout,
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception as _gate_exc:
            # **The pixel-size gate is not optional.** It is the check that catches an image
            # with no physical scale — and it was installed inside `except Exception: pass`,
            # in SEVEN panels. If it threw, `_pixel_gate_refresh` was never set, the reset
            # hook found `None` and did nothing, and **the panel built perfectly.** The image
            # then loaded at 1.0 µm/px and *every length, area and diffusion coefficient was
            # silently in pixels while the column header said microns.*
            #
            # *That is the pixel-size gate regression that cost a night to find. It was
            # unfindable by construction.* See `utils.general_utils.guarantee`.
            from pycat.utils.general_utils import report_guarantee_failure
            report_guarantee_failure("frap_ui: pixel-size gate", _gate_exc)

        self._add_roi_definition(layout)
        self._add_analysis(layout)

        main_w = QWidget(); main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        from pycat.ui.ui_modules import _apply_scroll_guard
        _apply_scroll_guard(main_w)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_w.setMinimumWidth(0)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(scroll, name="FRAP Analysis")

    # ── Step 2: ROI definition ─────────────────────────────────────────
    def _add_roi_definition(self, layout):
        grp  = QGroupBox("Step 2 — Define Bleach & Reference ROIs")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Draw the ROIs, or auto-place circles. When drawing, the FIRST "
            "shape is the bleached region and the SECOND is the reference "
            "(unbleached) region used for photofading correction.</span>")
        note.setWordWrap(True); form.addRow(note)

        # Lumicks .h5 direct load
        lumicks_btn = QPushButton("Load Lumicks C-Trap .h5…")
        lumicks_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        lumicks_btn.setToolTip(
            "Open a Lumicks C-Trap FRAP .h5 directly via pylake — extracts the "
            "recovery scan, frame interval, and bleach→recovery lag automatically.")
        lumicks_btn.clicked.connect(self._on_load_lumicks)
        form.addRow(lumicks_btn)
        # Proactive hint: if pylake isn't installed, say so here (before the user
        # clicks and hits a failure), with the one-line install command.
        try:
            from pycat.file_io.frap_io import lumicks_available
            if not lumicks_available():
                _pk = QLabel("⚠ Lumicks .h5 needs 'lumicks.pylake' "
                             "(not installed): pip install lumicks.pylake")
                _pk.setWordWrap(True)
                _pk.setStyleSheet("color: #b06000; font-size: 9pt;")
                form.addRow(_pk)
        except Exception:
            pass

        self._rec_dd = self.create_layer_dropdown(napari.layers.Image)
        self._rec_dd.setToolTip("Recovery time-series (T, H, W) to analyze.")
        form.addRow("Recovery stack:", self._rec_dd)

        self._multi_roi = QCheckBox("Multiple bleach ROIs (Mosaic / MicroPoint)")
        self._multi_roi.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._multi_roi.setChecked(False)
        self._multi_roi.setToolTip(
            "For Andor Fusion Mosaic / MicroPoint acquisitions with several "
            "photostimulation spots. When on, every drawn shape except the "
            "last is treated as an independent bleach ROI; the last shape is "
            "the shared reference region.")
        form.addRow(self._multi_roi)

        # Method: draw vs auto-circle
        method_row = QHBoxLayout()
        self._rb_draw   = QRadioButton("Draw ROIs")
        self._rb_circle = QRadioButton("Auto-circle")
        self._rb_draw.setChecked(True)
        self._rb_draw.setToolTip("Manually draw bleach (1st) and reference (2nd) shapes.")
        self._rb_circle.setToolTip("Place circular bleach + reference ROIs by coordinates.")
        method_row.addWidget(self._rb_draw); method_row.addWidget(self._rb_circle)
        method_row.addStretch()
        mw = QWidget(); mw.setLayout(method_row)
        form.addRow("ROI mode:", mw)

        add_shapes_btn = QPushButton("＋  Add ROI Drawing Layer")
        add_shapes_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        add_shapes_btn.setToolTip(
            "Create a Shapes layer for drawing ROIs. Draw the bleached region "
            "first, then the reference region. Ellipse or rectangle both work.")
        add_shapes_btn.clicked.connect(self._on_add_shapes)
        form.addRow(add_shapes_btn)

        self._shapes_dd = self.create_layer_dropdown(napari.layers.Shapes, name_hint='FRAP ROI')
        self._shapes_dd.setToolTip("Shapes layer holding the drawn bleach + reference ROIs.")
        form.addRow("ROI shapes:", self._shapes_dd)

        # Auto-circle params (hidden until Auto-circle selected)
        self._circle_container = QWidget()
        cc = QFormLayout(self._circle_container)
        cc.setContentsMargins(0, 20, 0, 0); cc.setSpacing(4)
        self._bl_cx = QSpinBox(); self._bl_cx.setRange(0, 10000); self._bl_cx.setValue(100)
        self._bl_cy = QSpinBox(); self._bl_cy.setRange(0, 10000); self._bl_cy.setValue(100)
        self._ref_cx = QSpinBox(); self._ref_cx.setRange(0, 10000); self._ref_cx.setValue(50)
        self._ref_cy = QSpinBox(); self._ref_cy.setRange(0, 10000); self._ref_cy.setValue(50)
        self._radius = QSpinBox(); self._radius.setRange(1, 5000); self._radius.setValue(20)
        self._bl_cx.setToolTip("Bleach ROI center X (px).")
        self._bl_cy.setToolTip("Bleach ROI center Y (px).")
        self._ref_cx.setToolTip("Reference ROI center X (px).")
        self._ref_cy.setToolTip("Reference ROI center Y (px).")
        self._radius.setToolTip("ROI radius (px), applied to both circles.")
        cc.addRow("Bleach center X:", self._bl_cx)
        cc.addRow("Bleach center Y:", self._bl_cy)
        cc.addRow("Reference center X:", self._ref_cx)
        cc.addRow("Reference center Y:", self._ref_cy)
        cc.addRow("ROI radius (px):", self._radius)
        self._circle_container.setVisible(False)
        form.addRow(self._circle_container)

        def _on_mode():
            draw = self._rb_draw.isChecked()
            self._shapes_dd.setVisible(draw)
            add_shapes_btn.setVisible(draw)
            self._circle_container.setVisible(not draw)
        self._rb_draw.toggled.connect(lambda _: _on_mode())
        self._rb_circle.toggled.connect(lambda _: _on_mode())

        layout.addWidget(grp)

    def _on_add_shapes(self):
        roi_name = "FRAP ROIs"
        if roi_name not in [l.name for l in self.viewer.layers]:
            roi_layer = self.viewer.add_shapes(
                name=roi_name, shape_type='ellipse',
                face_color='transparent', edge_color='#ff4040', edge_width=2)
        else:
            roi_layer = self.viewer.layers[roi_name]
        self.viewer.layers.selection.active = roi_layer
        try:
            roi_layer.mode = 'add_ellipse'
        except Exception:
            pass
        self.central_manager.toolbox_functions_ui.update_dropdown_items(
            self._shapes_dd, napari.layers.Shapes)
        idx = self._shapes_dd.findText(roi_name)
        if idx != -1:
            self._shapes_dd.setCurrentIndex(idx)
        napari_show_info(
            "Draw the BLEACHED region first, then the REFERENCE region.")

    def _on_load_lumicks(self):
        from PyQt5.QtWidgets import QFileDialog
        from pycat.file_io.frap_io import (
            lumicks_available, load_lumicks_frap, compute_lumicks_timelag)
        if not lumicks_available():
            napari_show_warning(
                "Lumicks .h5 files need the optional 'lumicks.pylake' package, "
                "which isn't installed. Install it, then restart PyCAT:\n"
                "    pip install lumicks.pylake\n"
                "(Only C-Trap .h5 loading needs it; the rest of PyCAT works "
                "without it.)")
            return
        path, _ = QFileDialog.getOpenFileName(
            None, "Open Lumicks C-Trap FRAP .h5", "", "Lumicks HDF5 (*.h5)")
        if not path:
            return
        try:
            data = load_lumicks_frap(path, channel='green', recovery_scan_index=0)
            lag = compute_lumicks_timelag(path)
        except Exception as e:
            napari_show_warning(f"Failed to load Lumicks .h5: {e}")
            import traceback; traceback.print_exc()
            return

        import os
        layer_name = os.path.basename(path).replace('.h5', '') + " (recovery)"
        self.viewer.add_image(data['stack'], name=layer_name)
        self.central_manager.toolbox_functions_ui.update_dropdown_items(
            self._rec_dd, napari.layers.Image)
        idx = self._rec_dd.findText(layer_name)
        if idx != -1:
            self._rec_dd.setCurrentIndex(idx)
        # Auto-fill timing from the file
        self._frame_dt.setValue(data['frame_interval_s'])
        if lag > 0:
            self._time_lag.setValue(lag)
        napari_show_info(
            f"Loaded Lumicks recovery scan: {data['n_frames']} frames, "
            f"frame interval {data['frame_interval_s']:.4g}s"
            + (f", bleach lag {lag:.3g}s." if lag > 0 else "."))

    def _build_masks(self, image_shape):
        from pycat.toolbox.frap_tools import circular_mask, masks_from_shapes
        if self._rb_draw.isChecked():
            name = self._shapes_dd.currentText()
            if name not in [l.name for l in self.viewer.layers]:
                napari_show_warning(f"Shapes layer '{name}' not found — draw ROIs first.")
                return None, None
            return masks_from_shapes(self.viewer.layers[name], image_shape)
        else:
            r = self._radius.value()
            bl = circular_mask(image_shape, (self._bl_cy.value(), self._bl_cx.value()), r)
            ref = circular_mask(image_shape, (self._ref_cy.value(), self._ref_cx.value()), r)
            return bl, ref

    # ── Step 3: analysis ───────────────────────────────────────────────
    def _add_analysis(self, layout):
        grp  = QGroupBox("Step 3 — Analyze Recovery")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._frame_dt = QDoubleSpinBox()
        self._frame_dt.setRange(0.0001, 3600); self._frame_dt.setValue(1.0)
        self._frame_dt.setDecimals(4); self._frame_dt.setSingleStep(0.01)
        self._frame_dt.setToolTip("Time between recovery frames (seconds).")
        form.addRow("Frame interval (s):", self._frame_dt)

        self._time_lag = QDoubleSpinBox()
        self._time_lag.setRange(0.0, 3600); self._time_lag.setValue(0.0)
        self._time_lag.setDecimals(3); self._time_lag.setSingleStep(0.1)
        self._time_lag.setToolTip(
            "Delay between the bleach event and the first recovery frame (s).")
        form.addRow("Bleach→recovery lag (s):", self._time_lag)

        self._photofade = QCheckBox("Photofading correction (reference ROI)")
        self._photofade.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._photofade.setChecked(True)
        self._photofade.setToolTip(
            "Correct the bleach curve for acquisition photobleaching using the "
            "reference ROI: cf = ref[0]/ref(t). Requires a reference ROI.")
        form.addRow(self._photofade)

        self._taylor = QCheckBox("Taylor et al. normalization")
        self._taylor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._taylor.setChecked(True)
        self._taylor.setToolTip(
            "ON (Taylor / Brangwynne): I_norm = (I−I_0)/(I_pre−I_0), rescaling "
            "the curve to [0,1] so the mobile fraction is read off the plateau.\n"
            "OFF (pre-bleach): I_norm = I/I_pre, which preserves the bleach "
            "depth and reports recovery relative to the pre-bleach level.")
        form.addRow(self._taylor)

        self._spline_pts = QSpinBox()
        self._spline_pts.setRange(3, 200); self._spline_pts.setValue(30)
        self._spline_pts.setToolTip(
            "Number of early recovery points used to spline-extrapolate the "
            "immediate post-bleach intensity (bleach depth) at t=0.")
        form.addRow("Spline points (t=0 est.):", self._spline_pts)

        # Optional separate pre-bleach stack — the mean over the bleach ROI
        # of these frames gives the true pre-bleach reference (more rigorous
        # than using the max of the corrected recovery curve).
        self._prebleach_dd = self.create_layer_dropdown(napari.layers.Image)
        self._prebleach_dd.setToolTip(
            "Optional pre-bleach image stack. Its mean intensity in the bleach "
            "ROI defines the pre-bleach reference (I_pre). 'None' falls back to "
            "the maximum of the corrected recovery curve.")
        form.addRow("Pre-bleach stack (opt):", self._prebleach_dd)

        self._build_fit_model(form)

        self._prog = QProgressBar(); self._prog.setVisible(False)
        btn = QPushButton("▶  Run FRAP Analysis")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_analyze)
        form.addRow(self._prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
        layout.addWidget(grp)

    def _build_fit_model(self, form):
        # Fit model selector
        model_grp = QGroupBox("Fit model")
        model_grp.setFlat(True)
        ml = QVBoxLayout(model_grp)
        ml.setContentsMargins(4, 20, 4, 2); ml.setSpacing(2)
        self._rb_empirical = QRadioButton("Empirical  I(t)=(a+b·t/τ½)/(1+t/τ½)  → τ½, mobile frac")
        self._rb_empirical.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_rd        = QRadioButton("Reaction-diffusion (rectangular)  → D, k_off, mobile/bound frac")
        self._rb_rd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_circ      = QRadioButton("Circular Soumpasis  → D (µm²/s), mobile frac")
        self._rb_circ.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_empirical.setChecked(True)
        self._rb_empirical.setToolTip("Empirical recovery model — reports a recovery half-time τ½.")
        self._rb_rd.setToolTip(
            "Rectangular-ROI reaction-diffusion model — reports a physical "
            "diffusion coefficient D, off-rate k_off, and mobile/bound "
            "fractions with uncertainties. Needs the pixel size and a "
            "rectangular bleach ROI.")
        self._rb_circ.setToolTip(
            "Soumpasis (1983) circular-spot model — the correct closed form "
            "for a uniform circular bleach ROI under pure diffusion. Reports "
            "a physical diffusion coefficient D = w²/(4·τ_D) and mobile "
            "fraction. Needs the bleach radius in µm.")
        ml.addWidget(self._rb_empirical); ml.addWidget(self._rb_rd); ml.addWidget(self._rb_circ)
        form.addRow(model_grp)

        self._fit_koff = QCheckBox("Fit k_off (reaction-diffusion; off = pure diffusion)")
        self._fit_koff.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._fit_koff.setChecked(True)
        self._fit_koff.setToolTip(
            "Reaction-diffusion only. On: fit binding off-rate k_off. "
            "Off: pure-diffusion 3-parameter fit (k_off = 0).")
        self._fit_koff.setVisible(False)
        form.addRow(self._fit_koff)

        self._pixel_um = QDoubleSpinBox()
        self._pixel_um.setRange(0.001, 100); self._pixel_um.setValue(0.1)
        self._pixel_um.setDecimals(4); self._pixel_um.setSingleStep(0.01)
        self._pixel_um.setToolTip(
            "Pixel size (µm/px) — used to convert the bleach ROI box into "
            "physical dimensions for the reaction-diffusion fit.")
        self._pixel_um.setVisible(False)
        self._pixel_um_label = QLabel("Pixel size (µm/px):")
        self._pixel_um_label.setWordWrap(True)
        form.addRow(self._pixel_um_label, self._pixel_um)

        self._bleach_radius = QDoubleSpinBox()
        self._bleach_radius.setRange(0.001, 1000); self._bleach_radius.setValue(1.0)
        self._bleach_radius.setDecimals(3); self._bleach_radius.setSingleStep(0.1)
        self._bleach_radius.setToolTip(
            "Circular fit only. Bleach spot radius in µm (w). D = w²/(4·τ_D). "
            "Leave 'auto' by drawing a circular ROI — the radius is estimated "
            "from the ROI area if this is left at its default and a mask exists.")
        self._bleach_radius.setVisible(False)
        self._bleach_radius_label = QLabel("Bleach radius (µm):")
        self._bleach_radius_label.setWordWrap(True)
        form.addRow(self._bleach_radius_label, self._bleach_radius)

        def _on_model():
            rd = self._rb_rd.isChecked()
            circ = self._rb_circ.isChecked()
            self._fit_koff.setVisible(rd)
            self._pixel_um.setVisible(rd)
            self._pixel_um_label.setVisible(rd)
            self._bleach_radius.setVisible(circ)
            self._bleach_radius_label.setVisible(circ)
        self._rb_empirical.toggled.connect(lambda _: _on_model())
        self._rb_rd.toggled.connect(lambda _: _on_model())
        self._rb_circ.toggled.connect(lambda _: _on_model())

    def _offer_stack_2d_images(self, current_name, current_stack):
        """When the chosen recovery layer is 2D (the 'loaded as individual images'
        case), collect all same-shaped 2D Image layers, and — if there are ≥2 —
        offer to stack them into a (T, H, W) array (ordered by layer name, which
        typically carries the frame index). Returns the stacked array, or None if
        the user declines or there aren't enough matching layers."""
        try:
            import numpy as _np
            import napari
            from pycat.file_io.file_io import materialize_stack
            H, W = current_stack.shape[-2:]
            candidates = []
            for l in self.viewer.layers:
                if isinstance(l, napari.layers.Image):
                    # ── Ask the SHAPE before decoding the pixels ──────────────────────────
                    #
                    # This materialised every Image layer in the viewer and then threw away the
                    # ones that turned out not to be 2-D — i.e. it decoded every big lazy stack
                    # open, in full, to answer a question the array already knew. A progress bar
                    # here would only report work that should not happen: a lazy wrapper knows its
                    # own `ndim`/`shape` without reading a single frame.
                    if getattr(l.data, 'ndim', None) != 2:
                        continue
                    if tuple(getattr(l.data, 'shape', ()) or ()) != (H, W):
                        continue
                    try:
                        a = materialize_stack(l.data)
                    except Exception:
                        continue
                    if a.ndim == 2 and a.shape == (H, W):
                        candidates.append((l.name, a))
            if len(candidates) < 2:
                return None
            # Order by name (frame indices usually embedded), current layer first
            # is not required — natural/name order is the safest default.
            candidates.sort(key=lambda t: t[0])
            from PyQt5.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setWindowTitle("Stack 2D images into a recovery series?")
            box.setIcon(QMessageBox.Question)
            box.setText(
                f"The recovery layer is 2D, but {len(candidates)} same-sized 2D "
                "image layers are open. Stack them into a single (T, H, W) "
                "recovery time-series (ordered by layer name)?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            if box.exec_() != QMessageBox.Yes:
                return None
            arr = _np.stack([a for _, a in candidates], axis=0)
            # Add the stacked series as a new layer and select it in the dropdown.
            new_name = "FRAP recovery (stacked)"
            self.viewer.add_image(arr, name=new_name)
            try:
                self.central_manager.toolbox_functions_ui.update_dropdown_items(
                    self._rec_dd, napari.layers.Image)
                idx = self._rec_dd.findText(new_name)
                if idx != -1:
                    self._rec_dd.setCurrentIndex(idx)
            except Exception:
                pass
            return arr
        except Exception as _e:
            print(f"[PyCAT FRAP] stack-2D-images failed: {_e}")
            return None

    def _on_analyze(self):
        from pycat.toolbox.frap_tools import (
            run_frap_analysis, run_frap_analysis_multi, masks_from_shapes_multi)
        name = self._rec_dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            napari_show_warning(f"Recovery stack '{name}' not found."); return
        from pycat.file_io.file_io import materialize_stack
        from pycat.ui.ui_utils import PhasedProgress
        # The bar moves even though this blocks the thread: QProgressBar.setValue calls repaint(),
        # which is synchronous. (A status LABEL would not — setText only schedules an update that
        # the blocked event loop never runs.) This makes the wait VISIBLE; it does not remove it.
        _pp = PhasedProgress(self._prog, phases=[("Materializing frames", 1.0)])
        stack = materialize_stack(self.viewer.layers[name].data,
                                  progress_callback=_pp.callback)
        _pp.hide()
        # FRAP treats frames as TIME (recovery curve) — warn once if the axis was
        # assumed at load.
        try:
            from pycat.file_io.file_io import warn_if_assumed_axis
            warn_if_assumed_axis(self._dr(),
                                 "FRAP recovery (treats frames as time)")
        except Exception as _axis_exc:
            # NOT cosmetic: this is the T-vs-Z check. If this stack is really a Z-series,
            # 'time' is depth and the dynamics being reported are not dynamics at all.
            # It was swallowed in COMPLETE SILENCE.
            from pycat.utils.general_utils import report_guarantee_failure
            report_guarantee_failure('frap_ui: warn_if_assumed_axis', _axis_exc)
        if stack.ndim != 3:
            # Safety net for the "recovery loaded as individual 2D images" case
            # (a multipage TIFF that wasn't recognised as a stack). If several
            # same-sized 2D Image layers exist, offer to stack them into a
            # (T, H, W) series so the user isn't stuck.
            stacked = self._offer_stack_2d_images(name, stack)
            if stacked is None:
                napari_show_warning(
                    "Recovery stack must be a 3D (T, H, W) time series. This "
                    "layer is 2D — if your recovery loaded as separate images, "
                    "reopen the file and choose 'Time-series' at the prompt, or "
                    "use the offer to stack the open 2D images.")
                return
            stack = stacked

        norm_mode = 'taylor' if self._taylor.isChecked() else 'prebleach'
        dt   = self._frame_dt.value()
        lag  = self._time_lag.value()
        spts = self._spline_pts.value()

        # ── Multi-ROI (Mosaic / MicroPoint) branch ───────────────────────
        if self._multi_roi.isChecked():
            if not self._rb_draw.isChecked():
                napari_show_warning(
                    "Multiple bleach ROIs requires Draw mode — draw each bleach "
                    "spot, then the reference region last."); return
            sname = self._shapes_dd.currentText()
            if sname not in [l.name for l in self.viewer.layers]:
                napari_show_warning(f"Shapes layer '{sname}' not found — draw ROIs first."); return
            n_ref = 1 if self._photofade.isChecked() else 0
            bleach_masks, ref_mask = masks_from_shapes_multi(
                self.viewer.layers[sname], stack.shape[1:], n_reference=n_ref)
            if not bleach_masks:
                napari_show_warning("No bleach ROIs found — draw at least one bleach spot."); return
            if self._photofade.isChecked() and ref_mask is None:
                napari_show_warning(
                    "Photofading is on but no reference ROI was found. Draw a "
                    "reference shape last, or turn off photofading."); return

            self._record('frap_define_roi', {
                'roi_mode': 'draw_multi', 'n_bleach': len(bleach_masks),
                'has_reference': ref_mask is not None})
            try:
                multi = run_frap_analysis_multi(
                    stack, bleach_masks, reference_mask=ref_mask,
                    frame_interval_s=dt, time_lag_s=lag,
                    n_spline_points=spts, normalization=norm_mode)
            except Exception as e:
                napari_show_warning(f"Multi-ROI FRAP failed: {e}")
                import traceback; traceback.print_exc(); return

            self._dr()['frap_multi_result'] = multi
            self._record('frap_analysis', {
                'recovery_stack': name, 'frame_interval_s': dt, 'time_lag_s': lag,
                'photofading': self._photofade.isChecked(),
                'normalization': norm_mode, 'roi_mode': 'draw_multi',
                'n_bleach_roi': len(bleach_masks)})
            try:
                from pycat.ui.ui_utils import show_dataframes_dialog
                show_dataframes_dialog(
                    "FRAP Results (multi-ROI)",
                    [('Per-ROI summary', multi['summary_df'].round(4))])
            except Exception:
                pass
            napari_show_info(
                f"FRAP complete for {len(bleach_masks)} bleach ROIs "
                f"({norm_mode} normalization).")
            return

        # ── Single-ROI branch ─────────────────────────────────────────────
        bleach_mask, ref_mask = self._build_masks(stack.shape[1:])
        if bleach_mask is None:
            return
        if bleach_mask.sum() == 0:
            napari_show_warning("Bleach ROI is empty — check the drawn shape or circle coordinates."); return
        self._record('frap_define_roi', {
            'roi_mode': 'draw' if self._rb_draw.isChecked() else 'circle',
            'has_reference': ref_mask is not None})
        if not self._photofade.isChecked():
            ref_mask = None
        elif ref_mask is None:
            napari_show_warning(
                "Photofading correction is on but no reference ROI was found. "
                "Draw a second shape (reference) or turn off photofading.")
            return

        # Optional pre-bleach stack (mean over bleach ROI = true I_pre)
        prebleach_stack = None
        pbname = self._prebleach_dd.currentText()
        if pbname != "None" and pbname in [l.name for l in self.viewer.layers]:
            from pycat.file_io.file_io import materialize_stack
            from pycat.ui.ui_utils import PhasedProgress
            _pp = PhasedProgress(self._prog, phases=[("Materializing pre-bleach frames", 1.0)])
            prebleach_stack = materialize_stack(self.viewer.layers[pbname].data,
                                                progress_callback=_pp.callback)
            _pp.hide()

        # Fit model + ROI geometry
        use_rd   = self._rb_rd.isChecked()
        use_circ = self._rb_circ.isChecked()
        if use_rd:
            fit_model = 'reaction_diffusion'
        elif use_circ:
            fit_model = 'circular'
        else:
            fit_model = 'empirical'
        roi_dims_um = None
        bleach_radius_um = None
        if use_rd:
            ys, xs = np.where(bleach_mask)
            px = self._pixel_um.value()
            d_y = (ys.max() - ys.min() + 1) * px
            d_x = (xs.max() - xs.min() + 1) * px
            roi_dims_um = (d_x, d_y)
        elif use_circ:
            # Use the entered radius, but if left at default (1.0) and a mask
            # exists, estimate the equivalent radius from the ROI area:
            # area = π·r²  →  r = sqrt(area/π), converted to µm by pixel size.
            px = self._pixel_um.value() if self._pixel_um.value() > 0 else 1.0
            entered = self._bleach_radius.value()
            area_px = float(bleach_mask.sum())
            r_from_area = (area_px / np.pi) ** 0.5 * px
            bleach_radius_um = entered if abs(entered - 1.0) > 1e-6 else r_from_area

        try:
            result = run_frap_analysis(
                stack, bleach_mask, reference_mask=ref_mask,
                frame_interval_s=dt, time_lag_s=lag,
                prebleach_stack=prebleach_stack,
                n_spline_points=spts, normalization=norm_mode,
                fit_model=fit_model, roi_dims_um=roi_dims_um,
                fit_koff=self._fit_koff.isChecked(),
                bleach_radius_um=bleach_radius_um)
        except Exception as e:
            napari_show_warning(f"FRAP analysis failed: {e}")
            import traceback; traceback.print_exc()
            return

        fit = result['fit']
        rd = result.get('rd_fit')
        circ = result.get('circ_fit')
        self._dr()['frap_result'] = result
        rec_params = {
            'recovery_stack': name, 'frame_interval_s': dt, 'time_lag_s': lag,
            'photofading': self._photofade.isChecked(),
            'normalization': norm_mode, 'fit_model': fit_model,
            'prebleach_stack': pbname if prebleach_stack is not None else None,
            'roi_mode': 'draw' if self._rb_draw.isChecked() else 'circle',
            'tau_half_s': fit.get('half_time_s'),
            'mobile_fraction': fit.get('mobile_fraction')}
        if rd is not None:
            rec_params.update({'D_um2_per_s': rd.get('D_um2_per_s'),
                               'k_off_per_s': rd.get('k_off_per_s')})
        if circ is not None:
            rec_params.update({'D_um2_per_s': circ.get('D_um2_per_s'),
                               'bleach_radius_um': bleach_radius_um})
        self._record('frap_analysis', rec_params)

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            summary = pd.DataFrame([{
                'τ½ (s)':            round(fit['half_time_s'], 4) if fit['half_time_s']==fit['half_time_s'] else None,
                'mobile fraction':  round(fit['mobile_fraction'], 4) if fit['mobile_fraction']==fit['mobile_fraction'] else None,
                'immobile fraction':round(fit['immobile_fraction'], 4) if fit['immobile_fraction']==fit['immobile_fraction'] else None,
                'R²':               round(fit['r_squared'], 4) if fit['r_squared']==fit['r_squared'] else None,
                'bleach depth I₀':  round(result['intensity_0'], 2),
                'prebleach':        round(result['prebleach'], 2),
                'normalization':    norm_mode,
            }])
            tables = [('Empirical fit', summary)]
            if rd is not None:
                rd_df = pd.DataFrame([{
                    'D (µm²/s)':        f"{rd['D_um2_per_s']:.4g} ± {rd['D_err']:.2g}",
                    'k_off (1/s)':      f"{rd['k_off_per_s']:.4g} ± {rd['k_off_err']:.2g}",
                    'mobile frac f_f':  f"{rd['f_f']:.3f} ± {rd['f_f_err']:.2g}",
                    'bound frac f_b':   f"{rd['f_b']:.3f} ± {rd['f_b_err']:.2g}",
                    'R²':               round(rd['r_squared'], 4) if rd['r_squared']==rd['r_squared'] else None,
                    'ROI (µm)':         f"{roi_dims_um[0]:.2f} × {roi_dims_um[1]:.2f}",
                }])
                tables.insert(0, ('Reaction-diffusion fit', rd_df))
            if circ is not None:
                circ_df = pd.DataFrame([{
                    'D (µm²/s)':        f"{circ['D_um2_per_s']:.4g} ± {circ['D_err']:.2g}",
                    'mobile frac f_f':  f"{circ['f_f']:.3f} ± {circ['f_f_err']:.2g}",
                    'τ_D (s)':          f"{circ['tau_D_s']:.4g} ± {circ['tau_D_err']:.2g}",
                    'τ½ (s)':           round(circ['half_time_s'], 4) if circ['half_time_s']==circ['half_time_s'] else None,
                    'R²':               round(circ['r_squared'], 4) if circ['r_squared']==circ['r_squared'] else None,
                    'bleach radius (µm)': round(bleach_radius_um, 3) if bleach_radius_um else None,
                }])
                tables.insert(0, ('Circular Soumpasis fit', circ_df))
            tables.append(('Recovery curve', result['results_df'].round(4)))
            # Graph: the recovery curve with the fitted model — the core FRAP plot.
            try:
                from pycat.toolbox.analysis_plots import plot_frap_recovery
                rdf = result['results_df']
                plot_frap_recovery(rdf['time_s'].values, rdf['normalized'].values,
                                   fit, title="FRAP recovery", interactive=True)
            except Exception as e:
                print(f"[PyCAT] FRAP plot failed: {e}")
            show_dataframes_dialog("FRAP Results", tables)
        except Exception:
            pass

        if circ is not None and circ['D_um2_per_s'] == circ['D_um2_per_s']:
            napari_show_info(
                f"FRAP complete: D={circ['D_um2_per_s']:.3g} µm²/s, "
                f"mobile frac={circ['f_f']:.3g}, τ½={circ['half_time_s']:.3g}s, "
                f"R²={circ['r_squared']:.3g} (circular Soumpasis)")
        elif rd is not None and rd['D_um2_per_s'] == rd['D_um2_per_s']:
            napari_show_info(
                f"FRAP complete: D={rd['D_um2_per_s']:.3g} µm²/s, "
                f"mobile frac={rd['f_f']:.3g}, k_off={rd['k_off_per_s']:.3g}/s, "
                f"R²={rd['r_squared']:.3g} (reaction-diffusion)")
        else:
            napari_show_info(
                f"FRAP complete: τ½={fit['half_time_s']:.3g}s, "
                f"mobile fraction={fit['mobile_fraction']:.3g}, R²={fit['r_squared']:.3g} "
                f"({norm_mode} normalization)")
