"""
Data Quality Control dashboard UI.

A teaching-oriented panel: pick an image or stack, optionally supply the optics
(pixel size, NA, wavelength) and timing, and get a colour-coded report with a
diagnostic plot for each metric plus plain-language notes on how each is measured
and what good data looks like.
"""

from __future__ import annotations
import numpy as np

from pycat.utils.general_utils import debug_log
import napari
from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning
from qtpy.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QComboBox, QWidget, QSizePolicy, QProgressBar,
)

#: (label, axis passed to run_full_qc). A 3-D stack's axis is STATED, never assumed (Completion Spec G6).
_AXIS_CHOICES = (("Not specified — axis-dependent checks are refused", None),
                 ("Time series (frames)", 't'),
                 ("Z-stack (through-focus)", 'z'),
                 ("Channels", 'c'))


def _suggested_axis(ui_instance, layer_data):
    """'t' when the loaded image's recorded frame count matches this stack's first axis, else None (unstated).
    A suggestion only — the user's choice in the dropdown is what runs."""
    try:
        repo = ui_instance.central_manager.active_data_class.data_repository
        n_t = int(repo.get('n_t') or 0)
        shape = getattr(layer_data, 'shape', ())
        return 't' if len(shape) == 3 and n_t > 1 and int(shape[0]) == n_t else None
    except Exception as exc:  # broad-ok: optional_probe — no suggestion means the axis stays unstated (refused)
        debug_log('QC: could not suggest the stack axis', exc)
        return None


def _prefill_pixel_size(ui_instance, spinbox):
    """Pre-fill the pixel size ONLY from a real calibration (G4): the loaders write the 1-µm/px placeholder when
    a file has no scale, and pre-filling it fabricated a Nyquist ratio and an absolute focus verdict for every
    uncalibrated image. Routed through the single pixel-size gate."""
    try:
        from pycat.utils.pixel_size import has_real_pixel_size, pixel_size_um
        repo = ui_instance.central_manager.active_data_class.data_repository
        if has_real_pixel_size(repo):
            value = pixel_size_um(repo, context='data QC')
            if value == value and value > 0:
                spinbox.setValue(float(value))
    except Exception as exc:  # broad-ok: optional_probe — an unreadable repository leaves the field at 0 = unknown
        debug_log('QC: could not pre-fill the pixel size', exc)


def _sibling_channels(ui_instance, active_name, active_data):
    """The other image layers of the same shape — i.e. the other colour channels.

    Chromatic aberration is a comparison BETWEEN channels, so the check needs them. A
    multi-colour acquisition loads as several same-shaped image layers, and that is what this
    collects. Returns None if there is nothing to compare against, which the check reports
    honestly as "single channel — cannot assess".
    """
    try:
        import numpy as _np
        shape = _np.shape(active_data)
        out = [_np.asarray(active_data)]
        for layer in ui_instance.viewer.layers:
            if layer.name == active_name:
                continue
            arr = getattr(layer, 'data', None)
            if arr is None or getattr(arr, 'ndim', 0) != len(shape):
                continue
            if tuple(_np.shape(arr)) != tuple(shape):
                continue
            out.append(_np.asarray(arr))
        return out if len(out) > 1 else None
    except Exception as exc:
        debug_log('QC: could not collect sibling channels', exc)
        return None


def _qc_object_table(ui_instance):
    """The segmented-object table to run object-level biological QC on, or None.

    Prefers the cell table, then puncta — the tables PyCAT's pipelines populate in the data repository.
    Table-based flags (size / shape / intensity outliers) need only the table, so this alone lets the
    object-QC section appear; the mask-based flags (edge, containment) are surfaced by the batch /
    upstream path where the label image lives, and are not guessed at here (a wrong table→mask pairing
    would fabricate an edge flag). Best-effort: any failure simply means no object section.
    """
    try:
        repo = ui_instance.central_manager.active_data_class.data_repository
    except Exception as exc:      # broad-ok: ui_cleanup — object QC is optional — no repository simply means no section
        debug_log('QC: no data repository for object-level QC', exc)
        return None
    for key in ('cell_df', 'puncta_df'):
        df = repo.get(key)
        if df is not None and getattr(df, 'empty', True) is False:
            return df
    return None


def _axis_dropdown(ui_instance, image_dd):
    """The "Stack axis" choice (G6), re-suggested from the recorded frame count when the image changes."""
    axis_dd = QComboBox()
    for _label, _axis in _AXIS_CHOICES:
        axis_dd.addItem(_label, _axis)
    axis_dd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    axis_dd.setToolTip(
        "What the planes of a 3-D stack ARE. Drift, vibration, photobleaching, focus and ghosting mean "
        "different things — or nothing — depending on it: treated as frames, a z-stack with zero drift reads "
        "'Drift: bad, 101 px'. Left unspecified, those checks are refused rather than guessed. Ignored for a "
        "2-D image.")

    def _suggest_axis(_name=None):
        name = image_dd.currentText()
        names = [l.name for l in ui_instance.viewer.layers]
        if name in names:
            suggestion = _suggested_axis(ui_instance, ui_instance.viewer.layers[name].data)
            axis_dd.setCurrentIndex(next(i for i, (_l, a) in enumerate(_AXIS_CHOICES) if a == suggestion))
    image_dd.currentTextChanged.connect(_suggest_axis)
    _suggest_axis()
    return axis_dd


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

    axis_dd = _axis_dropdown(ui_instance, image_dd)
    form.addRow("Stack axis:", axis_dd)

    # --- optics (for Nyquist / chromatic) ---
    opt = QGroupBox("Optics (for Nyquist sampling)")
    of = QFormLayout(opt); of.setContentsMargins(4, 20, 4, 4); of.setSpacing(5)
    px = QDoubleSpinBox(); px.setRange(0.0, 100.0); px.setDecimals(4)
    px.setSingleStep(0.01); px.setSuffix(" µm")
    px.setToolTip("Physical pixel size in the sample plane. 0 = unknown.")
    _prefill_pixel_size(ui_instance, px)
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

    # QC on a stack decodes every frame. Without a bar the panel just stops — and QC is exactly the
    # step a user runs on their BIGGEST acquisition, to find out whether it is worth analysing.
    run_prog = QProgressBar(); run_prog.setVisible(False)

    # holds the latest results + figure so they can be saved
    _state = {'results': None, 'fig': None, 'name': None}

    def _on_run():
        name = image_dd.currentText()
        if name not in [l.name for l in ui_instance.viewer.layers]:
            napari_show_warning("Select an image layer first."); return
        # ── np.asarray() on a lazy stack returns FRAME 0 ONLY ───────────────────
        #
        # This is the 1.5.273 bug, still live in the QC UI. A lazy `_TiffPageStack` implements
        # `__array__` as a deliberately-truncated single frame, so `np.asarray(layer.data)`
        # silently collapses a 1000-frame movie to one image.
        #
        # **The consequence here is that QC lies about what it checked.** Drift, vibration and
        # photobleaching all need a time series; given one frame they report "n/a — needs a time
        # series", and the user — who is looking at a movie — reads that as "PyCAT looked and
        # found nothing to report". It did not look.
        #
        # Every other stack-consuming UI already uses `materialize_stack`. This one did not.
        from pycat.utils.qt_worker import materialize_off_thread
        from pycat.toolbox.data_qc_tools import run_full_qc, plot_qc_report, QC_MAX_FRAMES
        _layer_data = ui_instance.viewer.layers[name].data
        # Read the dimensionality from SHAPE, not a `.ndim` attribute: the IMS readers advertise a
        # (T, Y, X) shape but no `ndim`, so `getattr(..., 'ndim', 2)` used to read them as 2-D and fall
        # to `np.asarray(wrapper)` — which the lazy guard REFUSES (the crash the user hit).
        _shape = getattr(_layer_data, 'shape', None)
        _nd = getattr(_layer_data, 'ndim', None)
        if _nd is None:
            _nd = len(_shape) if _shape is not None else 2
        _n_source = None
        if _nd == 3:
            # A long movie is capped to QC_MAX_FRAMES evenly-spaced frames — read OFF the Qt thread and
            # only those frames off disk (a full 600×2048² read is ~18 GiB and OOM'd QC). The report
            # notes it assessed N of M frames.
            _n_source = int(_shape[0]) if _shape is not None else None
            data = materialize_off_thread(_layer_data, viewer=ui_instance.viewer,
                                          max_frames=QC_MAX_FRAMES)
        else:
            data = np.asarray(_layer_data)      # already 2-D: instant, a bar would only flash
        if data.ndim not in (2, 3):
            napari_show_warning("QC needs a 2-D image or a 3-D (T/Z, H, W) stack."); return
        try:
            results = run_full_qc(
                data,
                pixel_um=(px.value() or None), na=(na.value() or None),
                wavelength_nm=(wl.value() or None),
                frame_interval_s=(dt.value() or None),
                process_timescale_s=(tau.value() or None),
                n_channels=nch.value(), axis=axis_dd.currentData(),
                n_source_frames=_n_source,
                # ── A correct check that never receives its data never runs ──────
                #
                # `qc_chromatic` MEASURES correctly when handed the channel images (0.00 px on
                # registered channels, 3.61 px on a true 3.6 px shift — 1.5.471). The UI passed
                # only the channel COUNT, so it could never do anything but report "info — pass
                # the channel images", and a **working check sat idle in every session.**
                #
                # The channels are the other 2-D image layers of the same shape in the viewer —
                # which is exactly what a multi-colour acquisition looks like once loaded.
                channels=_sibling_channels(ui_instance, name, data),
                # ── Object-level biological QC (second layer): "can I trust this OBJECT?" ────────
                # Appended only when the pipeline has produced a segmented-object table. Table-based
                # flags (size/shape/intensity) surface here; edge/containment ride the batch path where
                # the label mask lives. Flags are REPORTED, never used to drop an object.
                object_table=_qc_object_table(ui_instance))
        except Exception as e:
            napari_show_warning(f"QC failed: {e}")
            import traceback; traceback.print_exc(); return
        _state['results'] = results; _state['name'] = name
        # store for reuse / saving
        try:
            ui_instance.central_manager.active_data_class.data_repository['data_qc_results'] = results
        except Exception:
            pass
        # ── Write the QC verdict ONTO the assessed layer (audit A6) ──────────────────────────
        # QC used to leave its judgement in a disconnected result table; nothing marked the layer it
        # judged, so a later step could not ask for "a layer that passed QC". Attach an overall
        # quality_status tag (fail if any metric is bad, warn if any warns, else pass) so the verdict
        # travels with the data and a resolver can honour it. source='pipeline' — this is a known
        # operation's output, not an inference.
        try:
            from pycat.utils.layer_tags import tag_layer as _tag_layer
            _assessed = ui_instance.viewer.layers[name]
            _statuses = {r.get('status') for r in results}
            _verdict = 'fail' if 'bad' in _statuses else ('warn' if 'warn' in _statuses else 'pass')
            _tag_layer(_assessed, 'quality_status', _verdict, source='pipeline')
        except Exception as _qe:
            # Tagging is best-effort — a failure here must never block showing the QC report.
            print(f"[PyCAT QC] could not tag quality_status onto layer: {_qe}")
        # concise in-app summary
        bad = [r['name'] for r in results if r['status'] == 'bad']
        warn = [r['name'] for r in results if r['status'] == 'warn']
        if bad:
            napari_show_warning("QC — POOR: " + ", ".join(bad) +
                                (("; CHECK: " + ", ".join(warn)) if warn else ""))
        elif warn:
            napari_show_info("QC — CHECK: " + ", ".join(warn))
        else:
            # ── "All assessed metrics look good" is the coverage trap ────────────
            #
            # Fixed in `plot_qc_report` in 1.5.469 — and the UI carried its own hardcoded copy
            # of the same sentence, so the fix never reached the message the user actually sees.
            # **A correction that lands in one of two copies has not landed.**
            _ran = sum(1 for r in results if r['status'] in ('good', 'warn', 'bad'))
            _skipped = len(results) - _ran
            napari_show_info(
                f"QC — all {_ran} checks that ran look good."
                + (f"  BUT {_skipped} could NOT run (missing metadata, or the wrong kind of "
                   f"data) — this is not a clean bill of health. See the report."
                   if _skipped else ""))
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
        from qtpy.QtWidgets import QFileDialog
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
            # The versioned, machine-readable report (schema_version + every result, diag included) — the
            # form that ships as a supplementary file (G10).
            from pycat.toolbox.data_qc_tools import results_to_json
            with open(base + "_metrics.json", "w", encoding="utf-8") as fh:
                fh.write(results_to_json(results, indent=1))
            fig = _state.get('fig')
            if fig is not None:
                fig.savefig(base + ".png", dpi=150, bbox_inches='tight')
                napari_show_info(f"Saved {os.path.basename(base)}.png, _metrics.csv and _metrics.json.")
            else:
                napari_show_info(f"Saved {os.path.basename(base)}_metrics.csv and .json "
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
    # ── The gallery must be REACHABLE, or it is not a teaching tool ─────────────
    #
    # The exemplar gallery (1.5.466) shows a clean image beside one carrying a known defect,
    # with the verdict PyCAT gives each — the *Image* half of Image → Assessment →
    # Interpretation → Recommendation. **It was built and never wired to anything.**
    #
    # A user reading "Focus: bad" on their own data has no reference for what "bad" looks like
    # unless they can open it from here, next to the report that just told them.
    gallery_btn = QPushButton("What does a quality problem look like?")
    gallery_btn.setToolTip(
        "Open a side-by-side gallery: a clean image next to one carrying each defect, and "
        "what PyCAT says about both. The exemplars are simulated, and each one names the "
        "exact parameter that produced it.")

    def _open_gallery():
        try:
            from pycat.toolbox.qc_gallery_ui import make_qc_gallery_widget
            ui_instance.viewer.window.add_dock_widget(
                make_qc_gallery_widget(), name="QC — what a problem looks like", area="right")
        except Exception as exc:
            debug_log('QC: could not open the exemplar gallery', exc)
            napari_show_warning(f"Could not open the gallery: {exc}")

    gallery_btn.clicked.connect(_open_gallery)

    outer.addWidget(run_prog)
    outer.addWidget(run_btn)
    outer.addWidget(save_btn)
    outer.addWidget(gallery_btn)

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Data Quality Control")
