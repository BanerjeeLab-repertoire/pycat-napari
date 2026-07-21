"""
Diagnostics & tuner widgets mixin for ToolboxFunctionsUI.

This mixin holds the self-contained diagnostic and calibration widgets — pipeline
SNR analysis, pipeline step diagnostics, the foreground-suppression tuner, the
segmentation speed comparison, the chromatin topology map, the nucleolus/void
estimator, and display diagnostics. It was split out of the (very large)
ui_modules.ToolboxFunctionsUI class to make that file navigable; the methods are
moved verbatim and inherited via the mixin, so behaviour is unchanged.

These methods rely on attributes/methods provided by BaseUIClass and
ToolboxFunctionsUI at runtime (self.viewer, self.central_manager,
self.add_text_label, self.create_layer_dropdown, self._add_widget_to_layout_or_dock,
etc.). The mixin is only ever combined into ToolboxFunctionsUI, which provides them.
"""

import math

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy)


class _DiagnosticsWidgetsMixin:
    """Diagnostic and tuner widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_pipeline_snr_analysis(self, layout=None, separate_widget=False):
        """Delegate to pipeline_snr_tools._add_pipeline_snr_analysis."""
        from pycat.toolbox.pipeline_snr_tools import _add_pipeline_snr_analysis
        _add_pipeline_snr_analysis(self, layout=layout, separate_widget=separate_widget)

    def _add_pipeline_diagnostics(self, layout=None, separate_widget=False):
        """Two diagnostic panels in one dock:
          (A) CURRENT pipeline — a layer for every step of pre_process_image
              and rb_gaussian_bg_removal_with_edge_enhancement.
          (B) v1.0.0 pipeline — identical input, identical output labelling,
              but following the original 1.0.0 code exactly (disk footprint,
              LoG not DoG, no /max normalisation).
        Run each panel independently to compare step-by-step.
        """
        from PyQt5.QtWidgets import QGroupBox, QFormLayout, QTabWidget, QWidget, QVBoxLayout
        import napari

        outer = QVBoxLayout()
        self.add_text_label(outer, 'Pipeline Step Diagnostics', bold=True)
        self.add_text_label(outer,
            'Runs every sub-step and adds a named layer for each. '
            'Compare current vs v1.0.0 to find where the pipelines diverge.')

        tabs = QTabWidget()

        def _make_panel(label, run_fn):
            from PyQt5.QtWidgets import QProgressBar as _QProgressBar
            grp_w = QWidget(); vb = QVBoxLayout(grp_w)
            form = QFormLayout()
            form.setContentsMargins(4, 8, 4, 4)
            img_dd = self.create_layer_dropdown(napari.layers.Image)
            form.addRow("Image layer:", img_dd)
            vb.addLayout(form)

            run_btn = QPushButton(f"▶  Run {label}")
            run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            prog = _QProgressBar(); prog.setRange(0, 0); prog.setVisible(False)
            vb.addWidget(run_btn); vb.addWidget(prog)

            def _run():
                import numpy as np

                name = img_dd.currentText()
                if not name or name.lower() in ('none', 'select', '--'):
                    from napari.utils.notifications import show_warning
                    show_warning("Select an image layer first."); return
                try:
                    layer = self.viewer.layers[name]
                    img = np.asarray(layer.data)
                except Exception as e:
                    from napari.utils.notifications import show_warning
                    show_warning(f"Could not read layer: {e}"); return

                dr = self.central_manager.active_data_class.data_repository
                ball_radius = int(dr.get('ball_radius', 50))
                window_size = int(dr.get('cell_diameter', 100)) // 2

                prog.setVisible(True); run_btn.setEnabled(False)
                try:
                    steps = run_fn(img, ball_radius, window_size)
                    for step_name, arr in steps:
                        from pycat.ui.ui_utils import add_image_with_default_colormap
                        add_image_with_default_colormap(
                            arr.astype(np.float32), self.viewer, name=step_name)
                    from napari.utils.notifications import show_info
                    show_info(f"{label}: {len(steps)} step layers added.")
                except Exception as e:
                    from napari.utils.notifications import show_warning
                    show_warning(f"{label} failed: {e}")
                    import traceback; traceback.print_exc()
                finally:
                    prog.setVisible(False); run_btn.setEnabled(True)

            run_btn.clicked.connect(_run)
            return grp_w

        # ── Tab A: Current full pipeline ──────────────────────────────────
        from pycat.toolbox.pipeline_diagnostic_tools import (
            preprocess_steps_current, bg_removal_steps_current,
            preprocess_steps_v100, bg_removal_steps_v100)

        def _run_current(img, br, ws):
            return preprocess_steps_current(img, br, ws) +                    bg_removal_steps_current(img, br)

        def _run_v100(img, br, ws):
            return preprocess_steps_v100(img, br, ws) +                    bg_removal_steps_v100(img, br)

        tab_curr = _make_panel("Current pipeline", _run_current)
        tab_v100 = _make_panel("v1.0.0 pipeline", _run_v100)

        tabs.addTab(tab_curr, "Current (1.5.x)")
        tabs.addTab(tab_v100, "v1.0.0 reference")

        # Description of known differences — shown as a note below tabs
        outer.addWidget(tabs)
        note = QLabel(
            "<b>Key differences (current vs v1.0.0):</b><br>"
            "① Current normalises to [0,1] (/actual max) before any processing; "
            "v1.0.0 passes the raw /65535 float (dim images arrive at ~0.046 max).<br>"
            "② Structuring element: current uses <b>square(2r+1)</b>; "
            "v1.0.0 uses <b>disk(r)</b> — same radius, much larger area.<br>"
            "③ Blob detection: current uses <b>DoG(σ=2.0, 3.2)</b> fixed sigmas; "
            "v1.0.0 uses <b>LoG(σ=3)</b> — DoG sigmas don't scale with ball_radius."
        )
        note.setWordWrap(True)

        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        note.setStyleSheet("font-size:10px; color:#aaa; padding:4px;")
        outer.addWidget(note)

        w = QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Pipeline Step Diagnostics")

    def _add_foreground_suppression_tuner(self, layout=None, separate_widget=False):
        """Live tuner for the four foreground-suppression parameters.

        Pick a preprocessed image layer, drag the sliders, and a 'Suppression
        Preview [name]' layer updates in place so you can dial in the
        keep-vs-attenuate boundary against your own ground truth. A button copies
        the current values into the session defaults so the next Pre-process /
        batch run uses them.
        """
        from PyQt5.QtWidgets import (QSlider, QLabel as _QLabel, QWidget as _QWidget,
                                     QFormLayout, QHBoxLayout as _QHBoxLayout)
        from PyQt5.QtCore import Qt
        import napari
        from pycat.toolbox.image_processing_tools import (
            FOREGROUND_SUPPRESSION_DEFAULTS, soft_foreground_suppression)

        outer = QVBoxLayout()
        self.add_text_label(outer, 'Foreground Suppression Tuner', bold=True)
        self.add_text_label(
            outer,
            'Attenuates noise-like foreground while preserving real puncta and '
            'the nucleoplasm baseline. Select a preprocessed layer, tune, then '
            'apply the values as the session default.')

        form = QFormLayout()
        img_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Preprocessed layer:", img_dd)
        outer.addLayout(form)

        d = FOREGROUND_SUPPRESSION_DEFAULTS
        # If the session already has overrides, start from those.
        dr0 = self.central_manager.active_data_class.data_repository
        sp0 = dr0.get('foreground_suppression_params', None) or {}
        init = {
            'strength': float(sp0.get('strength', d['strength'])),
            'log_p':    float(sp0.get('log_p', d['log_p'])),
            'con_p':    float(sp0.get('con_p', d['con_p'])),
            'min_area': int(sp0.get('min_area', d['min_area'])),
            'border_grow': int(sp0.get('border_grow', d['border_grow'])),
        }

        def _mk(minv, maxv, val, scale):
            s = QSlider(Qt.Horizontal)
            s.setMinimum(int(minv * scale)); s.setMaximum(int(maxv * scale))
            s.setValue(int(val * scale)); return s

        strength_sl = _mk(0.0, 1.0, init['strength'], 100)
        logp_sl     = _mk(0.0, 95.0, init['log_p'], 1)
        conp_sl     = _mk(0.0, 95.0, init['con_p'], 1)
        minarea_sl  = _mk(1, 30, init['min_area'], 1)
        border_sl   = _mk(0, 10, init['border_grow'], 1)

        strength_lbl = _QLabel(f"{init['strength']:.2f}")
        logp_lbl     = _QLabel(f"{int(init['log_p'])}")
        conp_lbl     = _QLabel(f"{int(init['con_p'])}")
        minarea_lbl  = _QLabel(f"{int(init['min_area'])}")
        border_lbl   = _QLabel(f"{int(init['border_grow'])}")

        sform = QFormLayout()
        def _row(text, slider, label):
            row = _QWidget(); rl = _QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(slider); rl.addWidget(label)
            sform.addRow(text, row)
        _row("strength", strength_sl, strength_lbl)
        _row("log_p (blob)", logp_sl, logp_lbl)
        _row("con_p (contrast)", conp_sl, conp_lbl)
        _row("min_area (px)", minarea_sl, minarea_lbl)
        _row("border_grow (px)", border_sl, border_lbl)
        outer.addLayout(sform)

        def _current():
            return {
                'strength': strength_sl.value() / 100.0,
                'log_p':    float(logp_sl.value()),
                'con_p':    float(conp_sl.value()),
                'min_area': int(minarea_sl.value()),
                'border_grow': int(border_sl.value()),
            }

        def _preview():
            import numpy as np
            strength_lbl.setText(f"{strength_sl.value()/100.0:.2f}")
            logp_lbl.setText(f"{logp_sl.value()}")
            conp_lbl.setText(f"{conp_sl.value()}")
            minarea_lbl.setText(f"{minarea_sl.value()}")
            border_lbl.setText(f"{border_sl.value()}")

            name = img_dd.currentText()
            if not name or name.lower() in ('none', 'select', '--'):
                return
            try:
                src = np.asarray(self.viewer.layers[name].data)
            except Exception:
                return
            dr = self.central_manager.active_data_class.data_repository
            ball_radius = int(dr.get('ball_radius', 50))
            p = _current()
            try:
                out = soft_foreground_suppression(
                    src, ball_radius, strength=p['strength'], log_p=p['log_p'],
                    con_p=p['con_p'], min_area=p['min_area'],
                    border_grow=p['border_grow'])
            except Exception as e:
                from napari.utils.notifications import show_warning
                show_warning(f"Suppression preview failed: {e}"); return
            pname = f"Suppression Preview {name}"
            if pname in self.viewer.layers:
                self.viewer.layers[pname].data = out
            else:
                from pycat.ui.ui_utils import add_image_with_default_colormap
                add_image_with_default_colormap(out, self.viewer, name=pname)

        for _s in (strength_sl, logp_sl, conp_sl, minarea_sl, border_sl):
            _s.valueChanged.connect(_preview)
        img_dd.currentTextChanged.connect(lambda *_: _preview())

        preview_btn = QPushButton("Preview")
        preview_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        preview_btn.clicked.connect(_preview)
        outer.addWidget(preview_btn)

        apply_btn = QPushButton("Apply as session default")
        apply_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _apply_default():
            dr = self.central_manager.active_data_class.data_repository
            dr['foreground_suppression_params'] = _current()
            dr['suppress_foreground'] = True
            from napari.utils.notifications import show_info
            p = _current()
            show_info(
                f"Suppression default set: strength={p['strength']:.2f} "
                f"log_p={int(p['log_p'])} con_p={int(p['con_p'])} "
                f"min_area={p['min_area']} border_grow={p['border_grow']}. "
                f"Applied on next Pre-process / batch.")
        apply_btn.clicked.connect(_apply_default)
        outer.addWidget(apply_btn)

        reset_btn = QPushButton("Reset to tuned defaults")
        reset_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _reset():
            strength_sl.setValue(int(d['strength'] * 100))
            logp_sl.setValue(int(d['log_p']))
            conp_sl.setValue(int(d['con_p']))
            minarea_sl.setValue(int(d['min_area']))
            border_sl.setValue(int(d['border_grow']))
            dr = self.central_manager.active_data_class.data_repository
            dr['foreground_suppression_params'] = None
        reset_btn.clicked.connect(_reset)
        outer.addWidget(reset_btn)

        w = QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Foreground Suppression Tuner")

    def _add_temporal_enhancement_optimizer(self, layout=None, separate_widget=False):
        """Compete temporally-aware enhancement strategies against a loaded
        time-series and pick the one that best preserves the true intensity
        trend across frames.

        Per-frame CLAHE/LoG normalisation makes a brightening focus appear to
        dim over time (and drops dim condensates once a bright one enters). This
        tool runs several temporal strategies (per-frame baseline, pooled-stats
        nn/nnn, windowed-mean, tri-planar), scores each by how well it preserves
        the raw intensity trend, and lets you apply the winner as the session
        default for time-series preprocessing.
        """
        from PyQt5.QtWidgets import (QFormLayout, QComboBox, QSpinBox,
                                     QCheckBox, QLabel as _QLabel)
        import napari

        outer = QVBoxLayout()
        self.add_text_label(outer, 'Temporal Enhancement Optimizer', bold=True)
        self.add_text_label(
            outer,
            'Per-frame CLAHE/LoG normalisation is per-frame adaptive, which is '
            'only consistent across XY, not across time. For a correlated '
            'time-series this makes a brightening focus look like it dims. This '
            'tool competes several temporally-aware strategies (nn/nnn pooled '
            'stats, windowed-mean, tri-planar) against your data and picks the '
            'one that best preserves the true intensity trend.')

        warn_lbl = _QLabel(
            "<span style='color:#f0a500;font-size:9pt;'>\u26a0 Temporal "
            "enhancement is valid only when neighbouring frames are correlated. "
            "Run the correlation check below to confirm for your data.</span>")
        warn_lbl.setWordWrap(True)
        outer.addWidget(warn_lbl)

        form = QFormLayout()
        stack_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Time-series stack:", stack_dd)

        override_cb = QCheckBox("Set window manually")
        override_cb.setChecked(False)
        form.addRow("", override_cb)
        window_spin = QSpinBox(); window_spin.setRange(1, 5); window_spin.setValue(2)
        window_spin.setToolTip("Temporal half-width: 1 = nearest neighbour, "
                               "2 = nn + next-nearest.")
        window_spin.setEnabled(False)
        form.addRow("Window (frames):", window_spin)
        override_cb.toggled.connect(window_spin.setEnabled)
        outer.addLayout(form)

        corr_lbl = _QLabel("")
        corr_lbl.setWordWrap(True)
        outer.addWidget(corr_lbl)

        check_btn = QPushButton("Check temporal correlation")
        check_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _check_corr():
            import numpy as np
            name = stack_dd.currentText()
            if not name or name.lower() in ('none', 'select', '--'):
                return
            try:
                data = np.asarray(self.viewer.layers[name].data)
            except Exception:
                return
            if data.ndim != 3:
                corr_lbl.setText("<span style='color:#d9534f;font-size:9pt;'>"
                                 "Need a 3D (T,H,W) stack.</span>")
                return
            from pycat.toolbox.timeseries_condensate_tools import estimate_temporal_correlation
            res = estimate_temporal_correlation(data)
            colors = {'oversampled': '#5cb85c', 'moderate': '#f0a500',
                      'undersampled': '#d9534f'}
            c = colors.get(res['regime'], '#aaa')
            mc = res.get('mean_correlation', float('nan'))
            corr_lbl.setText(
                "<span style='color:%s;font-size:9pt;'><b>%s</b> \u2014 mean r=%.2f. %s</span>"
                % (c, res['regime'].upper(), mc, res['recommendation']))
            warn_lbl.setVisible(res['regime'] not in ('oversampled', 'moderate'))
        check_btn.clicked.connect(_check_corr)
        outer.addWidget(check_btn)

        results_lbl = _QLabel("")
        results_lbl.setWordWrap(True)
        outer.addWidget(results_lbl)

        self._temporal_enh_winner = {}

        run_btn = QPushButton("\u25b6  Run competition")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        from pycat.ui.operation_gating import gate_run_button
        gate_run_button(run_btn, ('time_axis',), getattr(self, 'central_manager', None),
                        getattr(self, 'viewer', None),
                        base_tooltip="Load a time-series (frames over time) to enable this.")
        def _run():
            import numpy as np
            from napari.utils.notifications import show_info, show_warning
            name = stack_dd.currentText()
            if not name or name.lower() in ('none', 'select', '--'):
                show_warning("Select a time-series stack."); return
            try:
                data = np.asarray(self.viewer.layers[name].data).astype(np.float32)
            except Exception as e:
                show_warning("Could not read stack: %s" % e); return
            if data.ndim != 3 or data.shape[0] < 3:
                show_warning("Need a (T,H,W) stack with 3+ frames."); return
            g0, g1 = float(data.min()), float(data.max())
            if g1 > g0:
                data = (data - g0) / (g1 - g0)
            dr = self.central_manager.active_data_class.data_repository
            ball_radius = int(dr.get('ball_radius', 15))
            windows = [window_spin.value()] if override_cb.isChecked() else [1, 2]

            from pycat.toolbox.temporal_enhancement_tools import compete_methods
            run_btn.setEnabled(False); run_btn.setText("Running competition...")
            try:
                results = compete_methods(data, ball_radius, windows=windows)
            except Exception as e:
                import traceback; print(traceback.format_exc())
                show_warning("Competition failed: %s - see terminal." % e)
                run_btn.setEnabled(True); run_btn.setText("\u25b6  Run competition")
                return
            run_btn.setEnabled(True); run_btn.setText("\u25b6  Run competition")

            header = ("<b>Ranked by trend preservation (best first):</b>"
                      "<table cellpadding='3'>"
                      "<tr><td><b>#</b></td><td><b>Method</b></td><td><b>Win</b></td>"
                      "<td><b>Spearman</b></td><td><b>Monotonic</b></td>"
                      "<td><b>Score</b></td></tr>")
            body = []
            for i, r in enumerate(results, 1):
                if r['method'] == 'per_frame':
                    tag = "per_frame (baseline)"
                else:
                    tag = "%s (w%d)" % (r['method'], r['window'])
                win_mark = "BEST" if i == 1 else ""
                body.append(
                    "<tr><td>%d</td><td>%s</td><td>%s</td><td>%.3f</td><td>%.2f</td><td>%.3f</td></tr>"
                    % (i, tag, win_mark, r['spearman'], r['monotonic_match'], r['composite']))
            results_lbl.setText(header + "".join(body) + "</table>")

            best = results[0]
            self._temporal_enh_winner = {
                'method': best['method'], 'window': int(best['window'])}
            wname = "Temporal-Enhanced [%s] %s" % (best['method'], name)
            if wname in self.viewer.layers:
                self.viewer.layers[wname].data = best['enhanced']
            else:
                from pycat.ui.ui_utils import add_image_with_default_colormap
                add_image_with_default_colormap(best['enhanced'], self.viewer, name=wname)
            show_info("Winner: %s (w%d) - spearman %.3f, monotonic %.2f."
                      % (best['method'], best['window'], best['spearman'], best['monotonic_match']))
        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)

        apply_btn = QPushButton("Apply winner as session default")
        apply_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _apply():
            from napari.utils.notifications import show_info, show_warning
            w = getattr(self, '_temporal_enh_winner', {})
            if not w:
                show_warning("Run the competition first."); return
            dr = self.central_manager.active_data_class.data_repository
            dr['temporal_enhancement'] = dict(w)
            show_info("Time-series preprocessing will use temporal enhancement: %s (w%d)."
                      % (w['method'], w['window']))
        apply_btn.clicked.connect(_apply)
        outer.addWidget(apply_btn)

        wdt = QWidget(); wdt.setLayout(outer)
        self._add_widget_to_layout_or_dock(wdt, layout, separate_widget,
                                            "Temporal Enhancement Optimizer")

    def _add_segmentation_benchmark(self, layout=None, separate_widget=False):
        """General segmentation benchmarking harness.

        Compares segmentation candidates on the same image and reports metrics
        as a pasteable table plus in-app side-by-side mask layers. Candidates
        can be PyCAT's built-in methods AND masks uploaded/loaded from other
        tools (any Labels layer), so you can compare PyCAT vs an external tool
        on identical data. Any candidate can be marked as the ground truth, in
        which case the others are scored against it (pixel Dice/IoU AND
        matched-detection precision/recall/F1, shown side by side).
        """
        from PyQt5.QtWidgets import (QFormLayout, QCheckBox, QComboBox,
                                     QDoubleSpinBox, QLabel as _QLabel,
                                     QListWidget, QListWidgetItem, QGroupBox,
                                     QVBoxLayout as _QV)
        from PyQt5.QtCore import Qt
        import napari

        outer = QVBoxLayout()
        self.add_text_label(outer, 'Segmentation Benchmark', bold=True)
        self.add_text_label(
            outer,
            'Compare segmentation methods (and masks from other tools) on the '
            'same image. Reports pixel overlap (Dice/IoU) and matched-detection '
            '(precision/recall/F1) side by side, with a pasteable table and '
            'side-by-side mask layers. Mark one candidate as ground truth to '
            'score the others against it.')

        form = QFormLayout()
        img_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Image:", img_dd)
        outer.addLayout(form)

        # --- Built-in method candidates ---
        methods_box = QGroupBox("Built-in methods to run")
        mb = _QV()
        method_checks = {}
        for key, label in [('otsu', 'Otsu'), ('multiotsu', 'Multi-Otsu'),
                           ('sauvola', 'Sauvola'), ('felzenszwalb', 'Felzenszwalb'),
                           ('watershed', 'Watershed'), ('cellpose', 'Cellpose')]:
            cb = QCheckBox(label)
            cb.setChecked(key in ('otsu', 'multiotsu'))
            method_checks[key] = cb
            mb.addWidget(cb)
        methods_box.setLayout(mb)
        outer.addWidget(methods_box)

        # --- External / uploaded mask candidates (any Labels layers) ---
        ext_box = QGroupBox("External / uploaded masks to include (from other tools)")
        eb = _QV()
        eb.addWidget(_QLabel(
            "<span style='font-size:9pt;color:#888;'>Tick any Labels layers to "
            "include as candidates — e.g. a mask exported from another tool, "
            "or a manual annotation. Load one via File → Open, then it appears "
            "here.</span>"))
        ext_list = QListWidget()
        ext_list.setSelectionMode(QListWidget.NoSelection)
        ext_box.setLayout(eb)
        eb.addWidget(ext_list)
        outer.addWidget(ext_box)

        def _refresh_ext_list():
            ext_list.clear()
            for lyr in self.viewer.layers:
                if isinstance(lyr, napari.layers.Labels):
                    it = QListWidgetItem(lyr.name)
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Unchecked)
                    ext_list.addItem(it)
        _refresh_ext_list()
        refresh_btn = QPushButton("↻ Refresh mask list")
        refresh_btn.clicked.connect(_refresh_ext_list)
        outer.addWidget(refresh_btn)

        # --- Ground truth + tolerance controls ---
        form2 = QFormLayout()
        gt_dd = QComboBox()
        gt_dd.addItem("(none — method comparison)")
        form2.addRow("Ground truth:", gt_dd)

        def _refresh_gt():
            cur = gt_dd.currentText()
            gt_dd.clear()
            gt_dd.addItem("(none — method comparison)")
            for key, cb in method_checks.items():
                if cb.isChecked():
                    gt_dd.addItem(cb.text())
            for i in range(ext_list.count()):
                it = ext_list.item(i)
                if it.checkState() == Qt.Checked:
                    gt_dd.addItem(it.text())
        gt_refresh_btn = QPushButton("↻ Update ground-truth choices")
        gt_refresh_btn.clicked.connect(_refresh_gt)

        tol_mode = QComboBox()
        tol_mode.addItems(["Auto (fraction of spot radius)", "Fixed pixels"])
        form2.addRow("Match tolerance:", tol_mode)
        tol_frac = QDoubleSpinBox(); tol_frac.setRange(0.1, 3.0)
        tol_frac.setSingleStep(0.1); tol_frac.setValue(0.5)
        form2.addRow("  · auto fraction:", tol_frac)
        tol_px = QDoubleSpinBox(); tol_px.setRange(1.0, 100.0); tol_px.setValue(5.0)
        form2.addRow("  · fixed px:", tol_px)
        outer.addLayout(form2)
        outer.addWidget(gt_refresh_btn)

        results_lbl = _QLabel("")
        results_lbl.setWordWrap(True)
        results_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(results_lbl)

        self._benchmark_last_md = ""

        run_btn = QPushButton("▶  Run benchmark")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        def _run():
            import numpy as np
            from napari.utils.notifications import show_info, show_warning
            from pycat.toolbox import benchmark_tools as bt

            iname = img_dd.currentText()
            if not iname or iname.lower() in ('none', 'select', '--'):
                show_warning("Select an image."); return
            try:
                image = np.asarray(self.viewer.layers[iname].data)
            except Exception as e:
                show_warning("Could not read image: %s" % e); return
            if image.ndim != 2:
                show_warning("Benchmark runs on a single 2D image "
                             "(pick one frame/plane)."); return

            dr = self.central_manager.active_data_class.data_repository
            obj_d = int(dr.get('cell_diameter', dr.get('object_size', 30)) or 30)
            ball_r = int(dr.get('ball_radius', 15) or 15)

            # Build candidates: built-in methods + checked external masks.
            include = [k for k, cb in method_checks.items() if cb.isChecked()]
            cands = bt.builtin_method_candidates(object_diameter=obj_d,
                                                 ball_radius=ball_r,
                                                 include=include) if include else []
            # Watershed needs a binary input; wrap it specially if requested.
            if 'watershed' in include:
                from pycat.toolbox.segmentation_tools import (
                    opencv_watershed_func)
                from skimage.filters import threshold_otsu
                def _ws(img):
                    a = np.asarray(img, dtype=float)
                    try: t = threshold_otsu(a)
                    except Exception: t = a.mean()
                    return np.asarray(opencv_watershed_func((a > t).astype(np.uint8),
                                      original_image=a), dtype=np.int32)
                cands.append(bt.Candidate('Watershed', method_fn=_ws,
                                          params={'method': 'Otsu + watershed'}))
            for i in range(ext_list.count()):
                it = ext_list.item(i)
                if it.checkState() == Qt.Checked:
                    nm = it.text()
                    try:
                        m = np.asarray(self.viewer.layers[nm].data)
                        cands.append(bt.Candidate(nm, mask=m, external=True,
                                                  params={'source': 'uploaded/external'}))
                    except Exception:
                        pass
            if len(cands) < 1:
                show_warning("Select at least one method or external mask.")
                return

            gt_choice = gt_dd.currentText()
            gt_name = None if gt_choice.startswith("(none") else gt_choice
            mode = 'fixed' if tol_mode.currentIndex() == 1 else 'auto'
            mpx = float(dr.get('microns_per_pixel_sq', 0) or 0) ** 0.5 or None

            run_btn.setEnabled(False); run_btn.setText("Running benchmark…")
            try:
                res = bt.run_benchmark(
                    image, cands, ground_truth_name=gt_name,
                    tolerance_mode=mode, fixed_tolerance_px=tol_px.value(),
                    scale_fraction=tol_frac.value(), microns_per_px=mpx)
            except Exception as e:
                import traceback; print(traceback.format_exc())
                show_warning("Benchmark failed: %s — see terminal." % e)
                run_btn.setEnabled(True); run_btn.setText("▶  Run benchmark")
                return
            run_btn.setEnabled(True); run_btn.setText("▶  Run benchmark")

            # Add each candidate's mask as a side-by-side layer.
            for name, lab in res['labels'].items():
                lyr_name = "bench: %s" % name
                if lyr_name in self.viewer.layers:
                    self.viewer.layers[lyr_name].data = lab
                else:
                    self.viewer.add_labels(np.asarray(lab), name=lyr_name)

            md = bt.to_markdown_table(res)
            self._benchmark_last_md = md
            # Render as a simple HTML table for the panel.
            html = md.replace('&', '&amp;')
            results_lbl.setText("<pre style='font-size:9pt;'>" + html + "</pre>")
            show_info("Benchmark complete: %d candidates. Table is selectable "
                      "for copy; masks added as 'bench: ' layers." % len(cands))

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)

        copy_btn = QPushButton("Copy table (markdown)")
        def _copy():
            from napari.utils.notifications import show_info
            try:
                from qtpy.QtWidgets import QApplication
                QApplication.clipboard().setText(self._benchmark_last_md or "")
                show_info("Benchmark table copied to clipboard (markdown).")
            except Exception:
                pass
        copy_btn.clicked.connect(_copy)
        outer.addWidget(copy_btn)

        wdt = QWidget(); wdt.setLayout(outer)
        self._add_widget_to_layout_or_dock(wdt, layout, separate_widget,
                                            "Segmentation Benchmark")

    def _add_control_validation(self, layout=None, separate_widget=False):
        """Positive/negative control validation.

        Sweeps an intensity threshold across a matched positive control (known to
        contain the objects) and negative control (should contain none), and
        recommends the operating point that maximizes detection in the positive
        control while keeping the negative control near zero — or REFUSES, with a
        stated reason, when no setting separates them (an assay finding, not a
        software one). Produces the supplementary-figure artifact: detections vs
        threshold for both controls, the recommended point marked.
        """
        from PyQt5.QtWidgets import (QFormLayout, QComboBox, QSpinBox, QLabel as _QLabel)
        from PyQt5.QtCore import Qt
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        import napari

        outer = QVBoxLayout()
        self.add_text_label(outer, 'Control Validation', bold=True)
        self.add_text_label(
            outer,
            'Sweep a detection threshold across a matched positive/negative control '
            'pair. Recommends the setting that maximizes detection in the positive '
            'control while the negative control stays near zero — and refuses, with a '
            'reason, when no setting separates them. Counts are density-normalized '
            '(objects/µm²) when a pixel size is known.')

        form = QFormLayout()
        pos_dd = self.create_layer_dropdown(napari.layers.Image)
        neg_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Positive control:", pos_dd)
        form.addRow("Negative control:", neg_dd)
        n_steps = QSpinBox(); n_steps.setRange(3, 40); n_steps.setValue(12)
        form.addRow("Threshold steps:", n_steps)
        exp_neg = QSpinBox(); exp_neg.setRange(0, 100000); exp_neg.setValue(0)
        form.addRow("Expected negative count:", exp_neg)
        outer.addLayout(form)

        results_lbl = _QLabel(""); results_lbl.setWordWrap(True)
        results_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(results_lbl)

        fig = Figure(figsize=(6, 4)); canvas = FigureCanvasQTAgg(fig)
        outer.addWidget(canvas, 1)

        run_btn = QPushButton("▶  Validate against controls")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        def _run():
            import warnings
            import numpy as np
            from napari.utils.notifications import show_info, show_warning
            from pycat.toolbox.control_validation import (
                validate_against_controls, recommend_parameters, control_report_figure)

            pn, nn = pos_dd.currentText(), neg_dd.currentText()
            if not pn or not nn or pn == nn:
                show_warning("Pick two DIFFERENT image layers (positive and negative)."); return
            try:
                pos = np.asarray(self.viewer.layers[pn].data, dtype=float)
                neg = np.asarray(self.viewer.layers[nn].data, dtype=float)
            except Exception as e:  # broad-ok: reading a napari layer's .data can fail many ways; report and abort, don't crash the widget
                show_warning("Could not read the control images: %s" % e); return
            if pos.ndim != 2 or neg.ndim != 2:
                show_warning("Control validation runs on single 2D fields (pick one plane each)."); return

            lo = float(min(pos.min(), neg.min())); hi = float(max(pos.max(), neg.max()))
            if not (hi > lo):
                show_warning("The control images are flat — no threshold sweep is possible."); return
            grid = [{'threshold': float(t)}
                    for t in np.linspace(lo, hi, int(n_steps.value()) + 2)[1:-1]]

            def _thresh(image, threshold=0.5):
                return np.asarray(image) > threshold

            dr = self.central_manager.active_data_class.data_repository
            mpx = float(dr.get('microns_per_pixel_sq', 0) or 0) ** 0.5 or None

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                df = validate_against_controls(pos, neg, _thresh, grid, microns_per_px=mpx,
                                               expected_negative=int(exp_neg.value()))
                rec = recommend_parameters(df)
            refusal = next((str(w.message) for w in caught
                            if 'do not separate' in str(w.message)
                            or 'distinguishes your positive' in str(w.message)), None)

            fig.clear(); ax = fig.add_subplot(111)
            control_report_figure(df, recommended=rec, ax=ax)
            canvas.draw()

            if rec is None:
                results_lbl.setText(
                    "<b style='color:#c0392b;'>No usable operating point.</b><br>"
                    + (refusal or "The controls do not separate at any tested threshold.")
                    + "<br><i>This is a finding about the assay, not the software.</i>")
                show_warning("Control validation: the controls do not separate — see the panel.")
            else:
                dens = ("—" if rec.positive_density != rec.positive_density
                        else "%.4g /µm²" % rec.positive_density)
                results_lbl.setText(
                    "<b>Recommended:</b> %s &nbsp; <b>verdict:</b> %s<br>"
                    "positive: %d objects (%s) &nbsp; negative: %d &nbsp; "
                    "false-positive rate: %.0f%% &nbsp; separation: %.2f<br>"
                    "<i>%s</i>" % (rec.params, rec.verdict, rec.n_positive, dens, rec.n_negative,
                                   100 * rec.false_positive_rate, rec.separation, rec.reason))
                show_info("Control validation complete: %s (%s)." % (rec.params, rec.verdict))

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)

        wdt = QWidget(); wdt.setLayout(outer)
        self._add_widget_to_layout_or_dock(wdt, layout, separate_widget,
                                            "Control Validation")

    def _add_segmentation_speed_comparison(self, layout=None, separate_widget=False):
        """A/B widget: run condensate segmentation with the original vs the
        windowed (fast) refinement filter, timing each and verifying the refined
        masks are identical. Reports timings, speedup, and equivalence.
        """
        from PyQt5.QtWidgets import QFormLayout, QWidget as _QWidget, QVBoxLayout as _QVBoxLayout
        import napari

        outer = _QVBoxLayout()
        self.add_text_label(outer, 'Segmentation Speed Comparison', bold=True)
        self.add_text_label(
            outer,
            'Runs condensate segmentation twice — original vs fast refinement — '
            'and reports timing, speedup, and whether the masks are identical. '
            'Adds the fast result layers (and a DIFF layer if they differ).')

        form = QFormLayout()
        pp_dd = self.create_layer_dropdown(napari.layers.Image)
        orig_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Pre-processed image:", pp_dd)
        form.addRow("Original image:", orig_dd)
        outer.addLayout(form)

        run_btn = QPushButton("▶  Run comparison")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        result_lbl = QLabel("")
        result_lbl.setWordWrap(True)
        result_lbl.setStyleSheet("font-size:11px; padding:4px;")

        def _run():
            from napari.utils.notifications import show_warning
            pp_name = pp_dd.currentText(); orig_name = orig_dd.currentText()
            if (not pp_name or pp_name.lower() in ('none', 'select', '--') or
                    not orig_name or orig_name.lower() in ('none', 'select', '--')):
                show_warning("Select both a pre-processed and an original image layer.")
                return
            try:
                pp_layer = self.viewer.layers[pp_name]
                orig_layer = self.viewer.layers[orig_name]
            except Exception as e:
                show_warning(f"Could not read layers: {e}"); return

            dr = self.central_manager.active_data_class.data_repository
            from pycat.toolbox.segmentation_tools import compare_segmentation_speed
            run_btn.setEnabled(False); result_lbl.setText("Running… (this runs segmentation twice)")
            self.viewer.window._qt_window.repaint()
            try:
                res = compare_segmentation_speed(
                    pp_layer, orig_layer, self.central_manager.active_data_class, self.viewer)
                result_lbl.setText(
                    f"original: {res['t_slow']:.2f} s ({res['n_slow']} obj)\n"
                    f"fast: {res['t_fast']:.2f} s ({res['n_fast']} obj)\n"
                    f"speedup: {res['speedup']:.1f}×\n"
                    f"masks identical: {res['identical']}")
            except Exception as e:
                import traceback; traceback.print_exc()
                result_lbl.setText(f"Comparison failed: {e}")
            finally:
                run_btn.setEnabled(True)

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)
        outer.addWidget(result_lbl)

        w = _QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Segmentation Speed Comparison")

    def _add_chromatin_topology(self, layout=None, separate_widget=False):
        """Widget for the chromatin/nucleoplasm topology envelope. Computes the
        smoothed structural envelope (rolling-ball or gaussian) of a chosen
        channel, adds raw + normalised layers, and — if a Labeled Cell Mask
        exists — writes per-cell topology metrics into cell_df.
        """
        from PyQt5.QtWidgets import (QFormLayout, QComboBox, QDoubleSpinBox,
                                     QWidget as _QWidget, QVBoxLayout as _QVBoxLayout)
        import napari

        outer = _QVBoxLayout()
        self.add_text_label(outer, 'Chromatin Topology Map', bold=True)
        self.add_text_label(
            outer,
            'Smoothed structural envelope of a nuclear/other channel (the '
            'rolling-ball background, which traces chromatin topology). Adds raw '
            'and mask-normalised layers; writes per-cell metrics if a Labeled '
            'Cell Mask is present.')

        form = QFormLayout()
        img_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("Channel:", img_dd)

        mode_dd = QComboBox()
        mode_dd.addItems(["rolling_ball", "gaussian"])
        form.addRow("Envelope mode:", mode_dd)

        pct_sb = QDoubleSpinBox()
        pct_sb.setRange(1.0, 99.0); pct_sb.setValue(50.0); pct_sb.setSingleStep(5.0)
        form.addRow("Connectivity percentile:", pct_sb)
        outer.addLayout(form)

        run_btn = QPushButton("Compute topology map")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        def _run():
            from napari.utils.notifications import show_warning
            name = img_dd.currentText()
            if not name or name.lower() in ('none', 'select', '--'):
                show_warning("Select a channel to analyse."); return
            try:
                img_layer = self.viewer.layers[name]
            except Exception as e:
                show_warning(f"Could not read layer: {e}"); return
            from pycat.toolbox.topology_tools import run_chromatin_topology
            run_btn.setEnabled(False)
            try:
                run_chromatin_topology(
                    img_layer, self.central_manager.active_data_class, self.viewer,
                    mode=mode_dd.currentText(),
                    connectivity_percentile=float(pct_sb.value()))
                self._record('chromatin_topology', {
                    'channel': name, 'mode': mode_dd.currentText(),
                    'connectivity_percentile': float(pct_sb.value()),
                })
            except Exception as e:
                import traceback; traceback.print_exc()
                show_warning(f"Topology computation failed: {e}")
            finally:
                run_btn.setEnabled(True)

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)

        w = _QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Chromatin Topology Map")

    def _add_nucleolus_void_estimator(self, layout=None, separate_widget=False):
        """Live tuner for chromatin-void / nucleolus estimation. Detects rounded
        DNA-excluding voids in a DAPI channel from its chromatin-density envelope,
        classifies each as nucleolus-like vs irregular, and (with a condensate
        channel) infers partition vs exclusion. Calibrate the knobs against real
        data like the foreground-suppression tuner, then the defaults can be baked.
        """
        from PyQt5.QtWidgets import (QFormLayout, QComboBox, QSlider, QLabel as _QLabel,
                                     QWidget as _QWidget, QVBoxLayout as _QVBoxLayout,
                                     QHBoxLayout as _QHBoxLayout)
        from PyQt5.QtCore import Qt
        import napari
        from pycat.toolbox.topology_tools import VOID_DETECTION_DEFAULTS

        d = VOID_DETECTION_DEFAULTS
        outer = _QVBoxLayout()
        self.add_text_label(outer, 'Nucleolus / Void Estimator', bold=True)
        self.add_text_label(
            outer,
            'Finds rounded DNA-excluding voids (nucleolus-like) in DAPI from the '
            'chromatin-density envelope. Weak inference: round voids are *likely* '
            'nucleoli. With a condensate channel, infers partition vs exclusion.')

        form = QFormLayout()
        dapi_dd = self.create_layer_dropdown(napari.layers.Image)
        cond_dd = self.create_layer_dropdown(napari.layers.Image)
        form.addRow("DAPI channel:", dapi_dd)
        form.addRow("Condensate channel (optional):", cond_dd)
        outer.addLayout(form)

        sliders = {}
        def _slider(key, lo, hi, init, scale, label):
            row = _QHBoxLayout()
            s = QSlider(Qt.Horizontal)
            s.setMinimum(int(lo * scale)); s.setMaximum(int(hi * scale))
            s.setValue(int(init * scale))
            val = _QLabel(f"{init:g}")
            s.valueChanged.connect(lambda v, l=val, sc=scale: l.setText(f"{v/sc:g}"))
            row.addWidget(_QLabel(label)); row.addWidget(s); row.addWidget(val)
            outer.addLayout(row)
            sliders[key] = (s, scale)

        _slider('density_percentile', 10, 60, d['density_percentile'], 1, "density %ile")
        _slider('circularity_min', 0.3, 0.95, d['circularity_min'], 100, "circularity min")
        _slider('solidity_min', 0.5, 0.99, d['solidity_min'], 100, "solidity min")
        _slider('envelope_sigma_scale', 0.3, 1.5, d['envelope_sigma_scale'], 100, "envelope sigma×br")
        _slider('min_void_area', 10, 300, d['min_void_area'], 1, "min area (px)")

        run_btn = QPushButton("Detect voids")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        result_lbl = _QLabel(""); result_lbl.setWordWrap(True)
        result_lbl.setStyleSheet("font-size:11px; padding:4px;")

        def _run():
            from napari.utils.notifications import show_warning
            dn = dapi_dd.currentText()
            if not dn or dn.lower() in ('none', 'select', '--'):
                show_warning("Select a DAPI channel."); return
            try:
                dapi_layer = self.viewer.layers[dn]
            except Exception as e:
                show_warning(f"Could not read DAPI layer: {e}"); return
            cn = cond_dd.currentText()
            cond_layer = None
            if cn and cn.lower() not in ('none', 'select', '--') and cn != dn:
                try:
                    cond_layer = self.viewer.layers[cn]
                except Exception:
                    cond_layer = None
            params = {k: (s.value() / sc) for k, (s, sc) in sliders.items()}
            params['min_void_area'] = int(params['min_void_area'])
            # Remove any prior void layers so re-runs don't stack.
            for nm in list(self.viewer.layers):
                if 'Voids' in getattr(nm, 'name', ''):
                    try: self.viewer.layers.remove(nm)
                    except Exception: pass
            from pycat.toolbox.topology_tools import run_chromatin_void_detection
            run_btn.setEnabled(False)
            try:
                res = run_chromatin_void_detection(
                    dapi_layer, self.viewer, self.central_manager.active_data_class,
                    condensate_layer=cond_layer, params=params)
                n_nuc = sum(1 for v in res['voids'] if v['class'] == 'nucleolus-like')
                n_irr = len(res['voids']) - n_nuc
                lines = [f"{n_nuc} nucleolus-like, {n_irr} irregular voids"]
                for v in res['voids'][:8]:
                    part = f" {v.get('partition_call')}" if cond_layer else ""
                    lines.append(f"  #{v['id']} {v['class']} a={v['area']} "
                                 f"circ={v['circularity']}{part}")
                result_lbl.setText("\n".join(lines))
                self._record('chromatin_void_detection', {
                    'dapi': dn, 'condensate': cn if cond_layer else None, 'params': params})
            except Exception as e:
                import traceback; traceback.print_exc()
                result_lbl.setText(f"Detection failed: {e}")
            finally:
                run_btn.setEnabled(True)

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn); outer.addWidget(result_lbl)
        w = _QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Nucleolus / Void Estimator")

    def _add_display_diagnostics(self, layout=None, separate_widget=False):
        """Diagnostic for 'layer controls (contrast/gamma) do nothing'. Reports,
        for the active layer, the facts that distinguish the likely causes:
        layer type/dtype, data min/max, current contrast_limits and their range,
        colormap, RGB flag, visibility, and whether it is the top visible layer.
        Also does a live probe: nudges contrast_limits and checks the change
        actually registers on the layer object.
        """
        from PyQt5.QtWidgets import QWidget as _QWidget, QVBoxLayout as _QVBoxLayout, QTextEdit
        import numpy as _np

        outer = _QVBoxLayout()
        self.add_text_label(outer, 'Display Diagnostics', bold=True)
        self.add_text_label(
            outer,
            'Select an image layer, then click. Reports why layer controls may '
            'appear to do nothing (wrong layer on top, RGB layer, pinned range, '
            'napari version). Copy the output if reporting an issue.')

        report = QTextEdit(); report.setReadOnly(True)
        report.setStyleSheet("font-family:monospace; font-size:10px;")
        report.setMinimumHeight(220)

        run_btn = QPushButton("Inspect active layer")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        def _run():
            import napari
            lines = []
            try:
                import napari as _n
                lines.append(f"napari version: {_n.__version__}")
            except Exception:
                lines.append("napari version: <unknown>")

            active = self.viewer.layers.selection.active
            if active is None:
                lines.append("No active layer selected.")
                report.setPlainText("\n".join(lines)); return

            lines.append(f"active layer: {active.name!r}")
            lines.append(f"  type: {type(active).__name__}")
            lines.append(f"  visible: {getattr(active, 'visible', '?')}")
            lines.append(f"  opacity: {getattr(active, 'opacity', '?')}")
            lines.append(f"  blending: {getattr(active, 'blending', '?')}")

            # Is this the top visible image layer? Controls adjust the SELECTED
            # layer, but if a different layer is drawn opaque on top, you won't
            # see the change.
            imgs = [l for l in self.viewer.layers
                    if isinstance(l, napari.layers.Image)]
            top_visible = None
            for l in reversed(list(self.viewer.layers)):
                if getattr(l, 'visible', False):
                    top_visible = l; break
            _tv_name = repr(top_visible.name) if top_visible else None
            lines.append(f"  top visible layer: {_tv_name}")
            if top_visible is not None and top_visible is not active:
                lines.append("  ** NOTE: the selected layer is NOT the top visible "
                             "layer — contrast changes to it may be hidden behind "
                             f"{top_visible.name!r}.")

            if isinstance(active, napari.layers.Image):
                data = _np.asarray(active.data)
                lines.append(f"  dtype: {data.dtype}  shape: {data.shape}")
                try:
                    lines.append(f"  data min/max: {float(data.min()):.4g} / "
                                 f"{float(data.max()):.4g}")
                except Exception:
                    pass
                lines.append(f"  rgb: {getattr(active, 'rgb', '?')}")
                lines.append(f"  colormap: {getattr(active.colormap, 'name', active.colormap)}")
                try:
                    lines.append(f"  contrast_limits: {active.contrast_limits}")
                    lines.append(f"  contrast_limits_range: {active.contrast_limits_range}")
                except Exception as e:
                    lines.append(f"  contrast_limits: <error {e}>")

                # Live probe: change contrast_limits and confirm it took.
                try:
                    before = list(active.contrast_limits)
                    lo, hi = active.contrast_limits_range
                    mid = lo + 0.5 * (hi - lo)
                    active.contrast_limits = [lo, max(mid, lo + 1e-6)]
                    after = list(active.contrast_limits)
                    took = not _np.allclose(before, after)
                    lines.append(f"  probe: set CL to [{lo:.4g}, {mid:.4g}] -> "
                                 f"now {after}  (changed on object: {took})")
                    active.contrast_limits = before  # restore
                    if took:
                        lines.append("  => the layer OBJECT accepts contrast "
                                     "changes. If the canvas still doesn't update, "
                                     "the issue is rendering (GPU/OpenGL) or the "
                                     "top-visible-layer note above, not the data.")
                    else:
                        lines.append("  => the layer object REJECTED the change — "
                                     "likely an RGB layer or a napari-version issue.")
                except Exception as e:
                    lines.append(f"  probe failed: {e}")

            report.setPlainText("\n".join(lines))
            print("[PyCAT Display Diagnostics]\n" + "\n".join(lines))

        run_btn.clicked.connect(_run)
        outer.addWidget(run_btn)
        outer.addWidget(report)

        w = _QWidget(); w.setLayout(outer)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                            "Display Diagnostics")

