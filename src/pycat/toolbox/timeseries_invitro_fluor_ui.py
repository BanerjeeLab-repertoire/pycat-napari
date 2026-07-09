"""
Time-Series In Vitro Fluorescence Analysis — stepped UI (2D+t).

The temporal counterpart of the 2D in vitro fluorescence pipeline
(``invitro_fluor_ui``). It segments every frame, LINKS droplets across frames
into per-condensate temporal objects (fusion-aware), and produces both
per-object and whole-field time-series. All heavy logic lives in
``timeseries_invitro_tools``; this file is UI + threading only.

Steps:
  1. Load  (requires a real time series; shows a pixel-size gate)
  2. Preprocess (optional, per frame)
  3. Segment stack  (per-frame Multi-Otsu/threshold+watershed; opt-in keyframing)
  4. Link condensates  (fusion-aware; builds tracked-label overlay)
  5. Per-object trajectories  (size/intensity/shape vs time, per tracked droplet)
  6. Field trajectories  (Φ, partition, C_sat vs time)

Later specialised analyses (interior bubbling, catalysis kinetics, internal
flow, fiber growth, contrast cascade) attach to the per-condensate object
records built here.
"""

import numpy as np
import pandas as pd
import napari

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QLabel, QScrollArea, QSizePolicy, QComboBox, QDoubleSpinBox, QSpinBox,
    QVBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout, QCheckBox,
    QProgressBar,
)
from napari.utils.notifications import (
    show_info as napari_show_info, show_warning as napari_show_warning)


class _TSIVFWorker(QThread):
    """Generic background worker running a callable that takes a progress cb."""
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            res = self._fn(lambda d, t: self.progress.emit(int(d), int(t)))
            self.finished_ok.emit(res)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class TimeSeriesInVitroFluorUI:
    def __init__(self, viewer, central_manager):
        self.viewer = viewer
        self.central_manager = central_manager
        self._workers = []

    # ── shared accessors ──────────────────────────────────────────────────
    def _dr(self):
        return self.central_manager.active_data_class.data_repository

    def _mpx(self):
        return float(self._dr().get('microns_per_pixel_sq', 1.0)) ** 0.5

    def _record(self, step, params):
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step, params)

    def create_layer_dropdown(self, layer_type):
        return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
            layer_type)

    def _frame_interval_s(self):
        """Frame interval (s) from captured metadata, else 1.0."""
        try:
            md = self._dr().get('file_metadata') or {}
            fi = (md.get('common') or {}).get('frame_interval_s')
            if fi and fi > 0:
                return float(fi)
        except Exception:
            pass
        return 1.0

    def _stack_layer(self, dd):
        """Return the (possibly lazy) stack data for the selected Image layer."""
        name = dd.currentText()
        if name not in [l.name for l in self.viewer.layers]:
            raise KeyError(f"Layer '{name}' not found.")
        return self.viewer.layers[name].data

    # ── UI construction ───────────────────────────────────────────────────
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(4, 20, 4, 4)
        header = QLabel(
            "<b>Time Series In Vitro Fluorescence Analysis</b><br>"
            "<span style='color:#888;font-size:9pt;'>"
            "Temporal analysis of protein/RNA LLPS droplets — segments every "
            "frame, links droplets across time into per-condensate objects "
            "(fusion-aware), and reports size/intensity/field dynamics.</span>")
        header.setWordWrap(True)
        header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        header.setStyleSheet("padding:6px; background:#2a2a2a; border-radius:4px;")
        layout.addWidget(header)

        # Step 1: load + pixel gate
        try:
            from pycat.ui.field_status import add_step1_file_io, add_pixel_size_gate
            add_step1_file_io(
                self.viewer, layout,
                instruction_html=(
                    "Load a fluorescence <b>time-series</b> (2D+t) via "
                    "<b>Open/Save File(s)</b>, or drag one onto the canvas."))
            self._pixel_gate_refresh = add_pixel_size_gate(
                layout,
                lambda: self.central_manager.active_data_class.data_repository,
                central_manager=self.central_manager)
        except Exception:
            pass

        _tsivf_preprocessing(self, layout)
        _tsivf_segmentation(self, layout)
        _tsivf_linking(self, layout)
        _tsivf_object_trajectories(self, layout)
        _tsivf_field_trajectories(self, layout)

        main_w = QWidget()
        main_w.setLayout(layout)
        main_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _relax_min_widths, _apply_scroll_guard
            _relax_min_widths(main_w)
            _apply_scroll_guard(main_w)
        except Exception:
            pass
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_w.setMinimumWidth(0)
        scroll.setWidget(main_w)
        self.viewer.window.add_dock_widget(
            scroll, name="Time Series In Vitro Fluorescence Analysis")


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run_row(form, label="▶  Run"):
    """A progress bar + run button pair added to a form."""
    prog = QProgressBar()
    prog.setVisible(False)
    prog.setTextVisible(True)
    run = QPushButton(label)
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog)
    form.addRow(run)
    return prog, run


def _show(title, tables):
    """Show result tables in PyCAT's dataframe dialog (best-effort)."""
    try:
        from pycat.ui.ui_utils import show_dataframes_dialog
        show_dataframes_dialog(title, [
            (k, v.round(4) if hasattr(v, 'round') else v) for k, v in tables])
    except Exception:
        for name, df in tables:
            try:
                print(f"[{title}] {name}\n{df.head().to_string()}")
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Preprocess (optional, per frame)
# ─────────────────────────────────────────────────────────────────────────────

def _tsivf_preprocessing(ui, layout):
    grp = QGroupBox("Step 2 — Preprocess (optional, per frame)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Optional per-frame preprocessing before segmentation. If skipped, the "
        "raw stack is segmented directly. (Preprocessing here mirrors the 2D "
        "in vitro path applied frame-by-frame.)</span>"))
    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Time-series image:", img_dd)
    ui._tsivf_pre_img_dd = img_dd
    layout.addWidget(grp)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Segment stack (per frame)
# ─────────────────────────────────────────────────────────────────────────────

def _tsivf_segmentation(ui, layout):
    grp = QGroupBox("Step 3 — Segment Droplets (every frame)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)

    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Time-series image:", img_dd)

    method_dd = QComboBox()
    method_dd.addItems(["Multi-Otsu", "Otsu", "Threshold + watershed"])
    method_dd.setToolTip(
        "Per-frame droplet segmentation. Multi-Otsu is the validated default "
        "for fluorescence droplets and is cheap enough to run every frame.")
    form.addRow("Method:", method_dd)

    classes_sp = QSpinBox()
    classes_sp.setRange(2, 5)
    classes_sp.setValue(3)
    form.addRow("Multi-Otsu classes:", classes_sp)

    min_area_sp = QSpinBox()
    min_area_sp.setRange(0, 100000)
    min_area_sp.setValue(20)
    min_area_sp.setToolTip("Remove segmented objects smaller than this (px²).")
    form.addRow("Min area (px²):", min_area_sp)

    # Opt-in keyframing for very long stacks.
    keyframe_cb = QCheckBox("Keyframe segmentation (long stacks only)")
    keyframe_cb.setToolTip(
        "OPT-IN speed trade for very long movies. When on, only every Nth frame "
        "is segmented and the previous keyframe's mask is COPIED to the frames "
        "between it — so growth, motion, and fusion WITHIN a keyframe gap are "
        "NOT resolved on those frames. Multi-Otsu is cheap, so leave this OFF "
        "unless the stack is so long that per-frame segmentation is impractical.")
    form.addRow(keyframe_cb)
    keyframe_every = QSpinBox()
    keyframe_every.setRange(2, 100)
    keyframe_every.setValue(5)
    keyframe_every.setEnabled(False)
    keyframe_cb.toggled.connect(keyframe_every.setEnabled)
    form.addRow("Keyframe every N:", keyframe_every)

    prog, run = _run_row(form, "▶  Segment Stack")

    def _segment_frame_fn_factory():
        method = method_dd.currentText()
        nclass = classes_sp.value()
        min_area = min_area_sp.value()

        def _seg(frame, t):
            from skimage import filters, morphology, measure
            f = np.asarray(frame, dtype=np.float32)
            mn, mx = float(f.min()), float(f.max())
            fn = (f - mn) / (mx - mn + 1e-8) if mx > mn else f
            if method == "Multi-Otsu":
                try:
                    ths = filters.threshold_multiotsu(fn, classes=nclass)
                    binary = fn > ths[-1]      # brightest class = droplets
                except Exception:
                    binary = fn > filters.threshold_otsu(fn)
            elif method == "Otsu":
                binary = fn > filters.threshold_otsu(fn)
            else:  # Threshold + watershed
                th = filters.threshold_otsu(fn)
                binary = fn > th
                from scipy import ndimage as ndi
                dist = ndi.distance_transform_edt(binary)
                from skimage.feature import peak_local_max
                coords = peak_local_max(dist, min_distance=5, labels=binary)
                markers = np.zeros(dist.shape, dtype=np.int32)
                for i, (yy, xx) in enumerate(coords, 1):
                    markers[yy, xx] = i
                from skimage.segmentation import watershed
                lab = watershed(-dist, markers, mask=binary)
                if min_area > 0:
                    lab = _drop_small(lab, min_area)
                return lab
            b = np.asarray(binary) > 0
            if min_area > 0:
                b = morphology.remove_small_objects(b, int(min_area))
            return measure.label(b).astype(np.int32)
        return _seg

    def _drop_small(lab, min_area):
        from skimage import measure, morphology
        keep = morphology.remove_small_objects(lab > 0, int(min_area))
        return measure.label(keep).astype(np.int32)

    def _on_run():
        from pycat.toolbox.timeseries_invitro_tools import segment_stack_per_frame
        try:
            stack = ui._stack_layer(img_dd)
        except KeyError as e:
            napari_show_warning(str(e))
            return
        seg_fn = _segment_frame_fn_factory()
        kf = keyframe_every.value() if keyframe_cb.isChecked() else 1
        img_name = img_dd.currentText()

        prog.setRange(0, 100)
        prog.setValue(0)
        prog.setVisible(True)
        run.setEnabled(False)

        def _task(progress):
            return segment_stack_per_frame(
                stack, seg_fn, keyframe_every=kf, progress_callback=progress)

        def _prog(d, t):
            prog.setRange(0, t)
            prog.setValue(d)

        def _done(label_stack):
            prog.setVisible(False)
            run.setEnabled(True)
            ui._dr()['tsivf_label_stack'] = label_stack
            nm = "TSIVF Droplet Labels (per-frame)"
            if nm in [l.name for l in ui.viewer.layers]:
                ui.viewer.layers[nm].data = label_stack
            else:
                ui.viewer.add_labels(label_stack, name=nm)
            n_obj = int(label_stack.max())
            ui._record('tsivf_segment_stack', {
                'image_layer': img_name, 'method': method_dd.currentText(),
                'multiotsu_classes': classes_sp.value(),
                'min_area_px': min_area_sp.value(),
                'keyframe_every': kf})
            napari_show_info(
                f"Segmented {label_stack.shape[0]} frames "
                f"(max {n_obj} objects/frame). Now link them in Step 4.")

        def _err(msg):
            prog.setVisible(False)
            run.setEnabled(True)
            napari_show_warning(f"Segmentation failed: {msg.splitlines()[0]}")

        w = _TSIVFWorker(_task)
        w.progress.connect(_prog)
        w.finished_ok.connect(_done)
        w.error.connect(_err)
        ui._workers.append(w)
        w.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Link condensates across frames (fusion-aware)
# ─────────────────────────────────────────────────────────────────────────────

def _tsivf_linking(ui, layout):
    grp = QGroupBox("Step 4 — Link Condensates (fusion-aware)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Links per-frame droplets into temporal tracks. Unlike bead tracking, "
        "the search radius scales with droplet size, area consistency is "
        "up-weighted, and droplet FUSION events (two tracks merging) are "
        "detected and flagged rather than mis-linked.</span>"))

    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Intensity image:", img_dd)

    radius_scale = QDoubleSpinBox()
    radius_scale.setRange(0.2, 20.0)
    radius_scale.setValue(2.0)
    radius_scale.setSingleStep(0.5)
    radius_scale.setToolTip(
        "Search radius = median droplet radius × this. A droplet moves at most a "
        "fraction of its own size per frame in a viscous sample.")
    form.addRow("Search radius × size:", radius_scale)

    gap_sp = QSpinBox()
    gap_sp.setRange(0, 20)
    gap_sp.setValue(2)
    gap_sp.setToolTip("Frames a droplet may vanish and still be reconnected.")
    form.addRow("Max frame gap:", gap_sp)

    area_w = QDoubleSpinBox()
    area_w.setRange(0.0, 2.0)
    area_w.setValue(0.6)
    area_w.setSingleStep(0.1)
    area_w.setToolTip(
        "Weight of area consistency in the link cost. Higher = size differences "
        "penalised more (helps disambiguate neighbouring droplets).")
    form.addRow("Area weight:", area_w)

    fusion_cb = QCheckBox("Detect fusion events")
    fusion_cb.setChecked(True)
    form.addRow(fusion_cb)

    minlen_sp = QSpinBox()
    minlen_sp.setRange(1, 1000)
    minlen_sp.setValue(3)
    minlen_sp.setToolTip("Discard tracks shorter than this many frames.")
    form.addRow("Min track length:", minlen_sp)

    prog, run = _run_row(form, "▶  Link Condensates")

    def _on_run():
        from pycat.toolbox.timeseries_invitro_tools import (
            stack_frame_properties, link_condensates, relabel_stack_by_track)
        label_stack = ui._dr().get('tsivf_label_stack')
        if label_stack is None:
            napari_show_warning("Run Step 3 (Segment Stack) first.")
            return
        try:
            intensity = ui._stack_layer(img_dd)
        except KeyError as e:
            napari_show_warning(str(e))
            return
        mpx = ui._mpx()
        rscale = radius_scale.value()
        gap = gap_sp.value()
        aw = area_w.value()
        do_fusion = fusion_cb.isChecked()
        minlen = minlen_sp.value()
        img_name = img_dd.currentText()

        prog.setRange(0, 0)
        prog.setVisible(True)
        run.setEnabled(False)

        def _task(progress):
            props = stack_frame_properties(label_stack, intensity, mpx)
            linked, fusions = link_condensates(
                props, search_radius_scale=rscale, max_gap_frames=gap,
                area_weight=aw, detect_fusion=do_fusion,
                progress_callback=progress)
            # Drop short tracks.
            if minlen > 1 and 'track_id' in linked.columns:
                counts = linked.groupby('track_id')['frame'].transform('size')
                linked = linked[counts >= minlen].reset_index(drop=True)
            tracked = relabel_stack_by_track(label_stack, linked)
            return linked, fusions, tracked

        def _prog(d, t):
            if t > 0:
                prog.setRange(0, t)
                prog.setValue(d)

        def _done(result):
            linked, fusions, tracked = result
            prog.setVisible(False)
            run.setEnabled(True)
            ui._dr()['tsivf_linked'] = linked
            ui._dr()['tsivf_fusions'] = fusions
            nm = "TSIVF Tracked Droplets"
            if nm in [l.name for l in ui.viewer.layers]:
                ui.viewer.layers[nm].data = tracked
            else:
                ui.viewer.add_labels(tracked, name=nm)
            n_tracks = linked['track_id'].nunique() if 'track_id' in linked else 0
            ui._record('tsivf_link_condensates', {
                'intensity_layer': img_name,
                'search_radius_scale': rscale, 'max_frame_gap': gap,
                'area_weight': aw, 'detect_fusion': do_fusion,
                'min_track_length': minlen})
            msg = f"Linked {n_tracks} condensate tracks"
            if do_fusion:
                msg += f"; {len(fusions)} fusion event(s)"
            napari_show_info(msg + ". Now build trajectories in Step 5.")
            tables = [("Linked detections", linked.head(200))]
            if len(fusions):
                tables.append(("Fusion events", fusions))
            _show("TSIVF Linking", tables)

        def _err(msg):
            prog.setVisible(False)
            run.setEnabled(True)
            napari_show_warning(f"Linking failed: {msg.splitlines()[0]}")

        w = _TSIVFWorker(_task)
        w.progress.connect(_prog)
        w.finished_ok.connect(_done)
        w.error.connect(_err)
        ui._workers.append(w)
        w.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Per-object temporal trajectories
# ─────────────────────────────────────────────────────────────────────────────

def _tsivf_object_trajectories(ui, layout):
    grp = QGroupBox("Step 5 — Per-Condensate Trajectories")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Builds a temporal object record per tracked droplet: size, intensity, "
        "and shape vs time, plus a linear area-growth rate. These records are "
        "the foundation later analyses (bubbling, catalysis, internal flow, "
        "fiber growth) attach to.</span>"))
    run = QPushButton("▶  Build Object Trajectories")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(run)

    def _on_run():
        from pycat.toolbox.timeseries_invitro_tools import (
            build_object_records, object_records_to_df)
        linked = ui._dr().get('tsivf_linked')
        if linked is None or 'track_id' not in getattr(linked, 'columns', []):
            napari_show_warning("Run Step 4 (Link Condensates) first.")
            return
        dt = ui._frame_interval_s()
        fusions = ui._dr().get('tsivf_fusions')
        records = build_object_records(linked, frame_interval_s=dt, fusion_df=fusions)
        ui._dr()['tsivf_object_records'] = records
        summary = object_records_to_df(records)
        ui._dr()['tsivf_object_summary'] = summary
        ui._record('tsivf_object_trajectories', {'frame_interval_s': dt})
        _show("TSIVF Per-Condensate Trajectories",
              [("Per-track summary", summary)])
        n_grow = int((summary['area_growth_rate_um2_per_s'] > 0).sum()) \
            if len(summary) else 0
        napari_show_info(
            f"Built {len(records)} object records "
            f"(dt={dt:.4g}s; {n_grow} growing).")

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Field-level trajectories
# ─────────────────────────────────────────────────────────────────────────────

def _tsivf_field_trajectories(ui, layout):
    grp = QGroupBox("Step 6 — Field Trajectories (Φ, partition, C_sat vs time)")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    img_dd = ui.create_layer_dropdown(napari.layers.Image)
    form.addRow("Intensity image:", img_dd)
    prog, run = _run_row(form, "▶  Compute Field Trajectories")

    def _on_run():
        from pycat.toolbox.timeseries_invitro_tools import field_trajectories
        label_stack = ui._dr().get('tsivf_label_stack')
        if label_stack is None:
            napari_show_warning("Run Step 3 (Segment Stack) first.")
            return
        try:
            intensity = ui._stack_layer(img_dd)
        except KeyError as e:
            napari_show_warning(str(e))
            return
        mpx = ui._mpx()
        dt = ui._frame_interval_s()
        img_name = img_dd.currentText()

        prog.setRange(0, 0)
        prog.setVisible(True)
        run.setEnabled(False)

        def _task(progress):
            return field_trajectories(
                label_stack, intensity, microns_per_pixel=mpx,
                frame_interval_s=dt, progress_callback=progress)

        def _prog(d, t):
            if t > 0:
                prog.setRange(0, t)
                prog.setValue(d)

        def _done(field_df):
            prog.setVisible(False)
            run.setEnabled(True)
            ui._dr()['tsivf_field_trajectories'] = field_df
            ui._record('tsivf_field_trajectories', {
                'intensity_layer': img_name, 'frame_interval_s': dt})
            _show("TSIVF Field Trajectories", [("Per-frame field summary", field_df)])
            try:
                phi0, phi1 = field_df['volume_fraction'].iloc[0], field_df['volume_fraction'].iloc[-1]
                napari_show_info(
                    f"Field trajectories over {len(field_df)} frames "
                    f"(Φ {phi0:.3f}→{phi1:.3f}).")
            except Exception:
                napari_show_info(f"Field trajectories over {len(field_df)} frames.")

        def _err(msg):
            prog.setVisible(False)
            run.setEnabled(True)
            napari_show_warning(f"Field trajectories failed: {msg.splitlines()[0]}")

        w = _TSIVFWorker(_task)
        w.progress.connect(_prog)
        w.finished_ok.connect(_done)
        w.error.connect(_err)
        ui._workers.append(w)
        w.start()

    run.clicked.connect(_on_run)
    layout.addWidget(grp)
