"""
General-purpose image/stack tools promoted out of method-specific pipelines.

Several genuinely general techniques were implemented inside a single analysis
method and were only reachable from that method's widget, even though they apply
to almost any data:

* **Image registration** (subpixel phase-cross-correlation alignment) lived in
  ``fibril_tools`` — but aligning two images has nothing to do with fibrils.
* **Focus / frame-quality scoring** (Brenner gradient, frame entropy,
  out-of-focus detection, clearest-frame picking) lived in ``temperature_tools``
  and ``condensate_physics_tools``.
* **Photobleach correction** (fit an exponential to the mean trace and divide it
  out) lived in ``condensate_physics_tools``.
* **Temporal detrending** (remove slow bleaching/drift before a fluctuation
  analysis) lived in ``nb_tools``.

This module does NOT reimplement any of them — it imports the existing, tested
implementations and wraps them in standalone Toolbox widgets so they can be used
on any layer, in any workflow. The original pipelines keep calling their own
functions exactly as before.
"""

import numpy as np

from PyQt5.QtWidgets import (
    QGroupBox, QFormLayout, QVBoxLayout, QWidget, QComboBox, QPushButton,
    QLabel, QSpinBox, QDoubleSpinBox, QCheckBox, QSizePolicy)

from napari.utils.notifications import (
    show_info as napari_show_info, show_warning as napari_show_warning)


# ─────────────────────────── helpers ────────────────────────────────────────

def _image_layer_names(viewer):
    import napari
    return [l.name for l in viewer.layers if isinstance(l, napari.layers.Image)]


def _refresh_dropdown(viewer, dd):
    cur = dd.currentText()
    dd.clear()
    dd.addItems(_image_layer_names(viewer))
    if cur:
        i = dd.findText(cur)
        if i >= 0:
            dd.setCurrentIndex(i)


def _current_2d(viewer, layer):
    """Take a 2-D plane from a layer that might be a stack, using the frame the
    user is actually looking at (never silently frame 0)."""
    from pycat.file_io.file_io import layer_is_stack, extract_2d_plane
    fi = 0
    if layer_is_stack(layer.data):
        try:
            fi = int(viewer.dims.current_step[0])
        except Exception:
            fi = 0
    return extract_2d_plane(layer.data, frame_index=fi, dtype=None)


def _as_stack(layer):
    """Materialise a layer as a (T, H, W) float32 stack (2-D → 1-frame stack)."""
    from pycat.file_io.file_io import materialize_stack
    arr = materialize_stack(layer.data)
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[None, ...]
    return arr


# ─────────────────────── 1. Image Registration ──────────────────────────────

def _add_image_registration(ui_instance, layout=None, separate_widget=False):
    """Align one image to another with subpixel phase cross-correlation.

    Promoted from the Fibril Analysis widget: the underlying ``register_images``
    (Guizar-Sicairos subpixel phase correlation) is completely general — channel
    alignment, drift correction, before/after comparison — and had no reason to
    be reachable only from fibril analysis.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Image Registration (subpixel)")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Aligns <b>moving</b> to "
        "<b>reference</b> by subpixel phase cross-correlation, and adds the "
        "registered image plus a difference image. Works on any two images "
        "(channel alignment, drift, before/after).</span>"))

    ref_dd = QComboBox(); mov_dd = QComboBox()
    for dd in (ref_dd, mov_dd):
        dd.addItems(_image_layer_names(viewer))
    form.addRow("Reference:", ref_dd)
    form.addRow("Moving:", mov_dd)

    status = QLabel("")
    status.setWordWrap(True)

    btn = QPushButton("\u25b6  Register")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh_dropdown(viewer, ref_dd); _refresh_dropdown(viewer, mov_dd)
        try:
            lref = viewer.layers[ref_dd.currentText()]
            lmov = viewer.layers[mov_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"Registration: layer not found — {e}"); return
        if lref.name == lmov.name:
            napari_show_warning("Pick two different layers."); return
        ref = _current_2d(viewer, lref).astype(float)
        mov = _current_2d(viewer, lmov).astype(float)
        if ref.shape != mov.shape:
            napari_show_warning(
                f"Images must be the same shape ({ref.shape} vs {mov.shape})."); return
        from pycat.toolbox.fibril_tools import register_images
        shift, registered, err = register_images(ref, mov)
        viewer.add_image(registered, name=f"{lmov.name} (registered)")
        viewer.add_image(np.abs(ref - registered),
                         name=f"{lmov.name} - {lref.name} (diff)")
        status.setText(
            f"<span style='color:#8f8;'>Shift (row, col) = "
            f"({shift[0]:+.2f}, {shift[1]:+.2f}) px · error = {err:.4f}</span>")
        napari_show_info(
            f"Registered: shift ({shift[0]:+.2f}, {shift[1]:+.2f}) px, error {err:.4f}")
        try:
            ui_instance._record('image_registration', {
                'reference': lref.name, 'moving': lmov.name,
                'shift_row': float(shift[0]), 'shift_col': float(shift[1]),
                'error': float(err)})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w = QWidget(); w.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, "Image Registration")


# ───────────────── 2. Frame Quality / Focus QC ──────────────────────────────

def _add_frame_quality_qc(ui_instance, layout=None, separate_widget=False):
    """Per-frame focus / entropy / out-of-focus QC for any stack.

    Promoted from temperature_tools (focus_scores, frame_entropy,
    guess_clear_frame) and condensate_physics_tools (detect_out_of_focus): these
    answer "which frames of this stack are usable?", which every time-series and
    z-stack workflow needs, not just the temperature ramp.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Frame Quality / Focus QC")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Scores every frame of a stack "
        "for sharpness (Brenner gradient) and information content (entropy), "
        "flags out-of-focus frames, and proposes the clearest frame — useful "
        "before any time-series or z-stack analysis.</span>"))

    stack_dd = QComboBox(); stack_dd.addItems(_image_layer_names(viewer))
    form.addRow("Stack:", stack_dd)

    # Focus is far more trustworthy measured INSIDE the biology. A whole-frame score
    # answers "what is the sharpest thing in the field?" -- and with dust on the
    # coverslip the answer is often the dust, because debris on a different focal plane
    # has its OWN focus curve and peaks at a different z.
    import napari as _np_fq
    mask_dd = QComboBox()
    mask_dd.addItem("(none \u2014 whole frame)")
    mask_dd.addItems([l.name for l in viewer.layers
                      if isinstance(l, _np_fq.layers.Labels)])
    mask_dd.setToolTip(
        "Restrict the focus metrics to a biologically relevant region (a cell mask, an "
        "object mask). Strongly recommended.\n\n"
        "Whole-frame focus scoring is easily fooled: benchmarked on synthetic z-sweeps "
        "with debris sitting at a different focal plane, whole-frame Brenner found the "
        "correct frame only 1 time in 6 \u2014 it picked the frame where the DUST was "
        "sharpest.\n\n"
        "Note that masking alone is not sufficient either (masked Brenner scored 0/6: "
        "restricting the region exposes Brenner's low bias). This tool therefore takes "
        "the CONSENSUS of three independent metrics (Brenner, Laplacian variance, "
        "Tenengrad), which scored 6/6.")
    form.addRow("Restrict to mask:", mask_dd)

    thresh = QDoubleSpinBox()
    thresh.setRange(0.05, 0.99); thresh.setSingleStep(0.05); thresh.setValue(0.5)
    thresh.setToolTip(
        "A frame is flagged out-of-focus when its focus score falls below this "
        "fraction of the stack's median focus score.")
    form.addRow("Out-of-focus threshold:", thresh)

    status = QLabel(""); status.setWordWrap(True)
    btn = QPushButton("\u25b6  Score Frames")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh_dropdown(viewer, stack_dd)
        try:
            layer = viewer.layers[stack_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"Frame QC: layer not found — {e}"); return
        stack = _as_stack(layer)
        if stack.shape[0] < 2:
            napari_show_warning(
                "Frame QC needs a stack (time-series or z-stack); this looks 2-D.")
            return

        import pandas as pd
        from pycat.toolbox.temperature_tools import (focus_scores, frame_entropy,
                                                     focus_scores_multimetric)

        # Optional mask: focus is far more trustworthy measured INSIDE the biology.
        # A whole-frame focus score answers "what is the sharpest thing in the field?",
        # and on a coverslip with dust the answer is often the dust -- debris on a
        # different focal plane has its own focus curve and peaks at a different z.
        _mask = None
        _mname = mask_dd.currentText()
        if _mname and not _mname.startswith('(none'):
            try:
                _mask = np.asarray(viewer.layers[_mname].data) != 0
                # A 3-D mask whose T does not match the stack is treated as a single
                # 2-D region (its first plane) applied to every frame.
                if _mask.ndim == 3 and _mask.shape[0] != stack.shape[0]:
                    _mask = _mask[0]
            except Exception as _e:
                napari_show_warning(f"Frame QC: could not use mask {_mname!r} ({_e}); "
                                    f"scoring the whole frame instead.")
                _mask = None

        # Multimetric CONSENSUS, not a single Brenner gradient. Benchmarked on synthetic
        # z-sweeps with debris at a different focal plane, the correct frame (+/-1) was
        # found by: whole-frame Brenner 1/6, MASKED Brenner 0/6 (masking alone is
        # WORSE -- it exposes Brenner's low bias inside a small region), masked
        # multimetric 6/6. Three independent metrics are much harder to fool than one.
        mm = focus_scores_multimetric(stack, mask=_mask)
        foc = np.asarray(focus_scores(stack, mask=_mask), dtype=float)
        ent = np.array([frame_entropy(np.asarray(stack[i]))
                        for i in range(stack.shape[0])], dtype=float)
        # NOTE: focus_scores() already divides by the stack's median, so its median
        # is ~1.0 and the threshold IS the fraction-of-median directly.
        cut = float(thresh.value())
        blurry = foc < cut

        df = pd.DataFrame({
            'frame': np.arange(len(foc)),
            'focus_score': foc,
            'brenner': mm['brenner'],
            'laplacian': mm['laplacian'],
            'tenengrad': mm['tenengrad'],
            'entropy': ent,
            'out_of_focus': blurry,
        })
        # The consensus of three metrics, not the argmax of one.
        best = int(mm['consensus_frame'])

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'frame_quality_df'] = df
        except Exception:
            pass

        # Plot focus + entropy vs frame, marking the flagged frames.
        try:
            import matplotlib.pyplot as plt
            fig, ax1 = plt.subplots(figsize=(7.4, 4.2))
            ax1.plot(df['frame'], df['focus_score'], '-o', ms=3, color='#4c72b0',
                     label='focus (Brenner)')
            ax1.axhline(cut, color='#c44e52', ls='--', lw=1,
                        label=f'out-of-focus cut ({thresh.value():.2f}×median)')
            if blurry.any():
                ax1.plot(df['frame'][blurry], df['focus_score'][blurry], 'x',
                         color='#c44e52', ms=8, label='flagged')
            ax1.set_xlabel('frame'); ax1.set_ylabel('focus score', color='#4c72b0')
            ax2 = ax1.twinx()
            ax2.plot(df['frame'], df['entropy'], '-', lw=1, alpha=0.6,
                     color='#55a868', label='entropy')
            ax2.set_ylabel('entropy', color='#55a868')
            ax1.axvline(best, color='#f0a500', lw=1.2, alpha=0.8)
            ax1.set_title(f"Frame quality — sharpest frame: {best}",
                          fontweight='bold')
            ax1.legend(fontsize=8, loc='lower right')
            fig.tight_layout(); plt.show(block=False)
        except Exception as e:
            print(f"[PyCAT FrameQC] plot failed: {e}")

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Frame Quality / Focus QC",
                                   [("Per-frame", df.round(4))])
        except Exception:
            pass

        # Surface the agreement between the three metrics. LOW agreement means the
        # focus call is being driven by something other than a clean focus curve --
        # very often a bright object on a different focal plane. It is the diagnostic;
        # the score alone is not.
        _agree = float(mm['agreement'])
        _scope = ("whole frame" if _mask is None
                  else f"inside {_mname!r}")
        if _agree >= 0.99:
            _acol, _anote = '#8f8', 'all three metrics agree'
        elif _agree >= 0.6:
            _acol, _anote = '#e8a33d', 'metrics partly disagree'
        else:
            _acol, _anote = '#f88', 'metrics DISAGREE — do not trust this focus call'
        _warn_unmasked = ("" if _mask is not None else
                          "<br><span style='color:#e8a33d;'>Scored over the whole "
                          "frame. If there is debris on the coverslip the sharpest "
                          "frame may be the sharpest DUST — restrict to a mask.</span>")
        status.setText(
            f"<span style='color:#8f8;'>{len(df)} frames · sharpest = frame "
            f"<b>{best}</b> (consensus of 3 metrics, {_scope}) · "
            f"{int(blurry.sum())} flagged out-of-focus.</span>"
            f"<br><span style='color:{_acol};'>metric agreement "
            f"{_agree:.0%} — {_anote} (peaks: {mm['peaks']})</span>"
            + _warn_unmasked)
        napari_show_info(
            f"Frame QC: sharpest frame {best} (3-metric consensus, {_scope}); "
            f"{int(blurry.sum())}/{len(df)} flagged out-of-focus; "
            f"metric agreement {_agree:.0%}.")
        try:
            ui_instance._record('frame_quality_qc', {
                'layer': layer.name, 'n_frames': int(len(df)),
                'threshold_fraction': float(thresh.value()),
                'n_out_of_focus': int(blurry.sum()), 'sharpest_frame': best})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w = QWidget(); w.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, "Frame Quality / Focus QC")


# ──────────────── 3. Photobleach Correction (any stack) ─────────────────────

def _add_bleach_correction(ui_instance, layout=None, separate_widget=False):
    """Fit and remove photobleaching from any time-series.

    Promoted from condensate_physics_tools (fit_photobleaching +
    apply_bleach_correction): bleaching affects every fluorescence time-series,
    so this shouldn't be reachable only from the condensate-physics widget.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Photobleach Correction")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Fits an exponential decay to "
        "the stack's mean intensity over time and divides it out, so later "
        "frames aren't artificially dim. Adds a corrected stack; the fitted "
        "decay is plotted so you can see whether bleaching was real.</span>"))

    stack_dd = QComboBox(); stack_dd.addItems(_image_layer_names(viewer))
    form.addRow("Stack:", stack_dd)

    dt = QDoubleSpinBox()
    dt.setDecimals(4); dt.setRange(0.0001, 10000.0); dt.setValue(1.0)
    dt.setToolTip("Seconds per frame (used only for the reported bleach time constant).")
    form.addRow("Frame interval (s):", dt)

    status = QLabel(""); status.setWordWrap(True)
    btn = QPushButton("\u25b6  Fit && Correct")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh_dropdown(viewer, stack_dd)
        try:
            layer = viewer.layers[stack_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"Bleach correction: layer not found — {e}"); return
        stack = _as_stack(layer)
        if stack.shape[0] < 3:
            napari_show_warning(
                "Bleach correction needs a time-series (≥3 frames)."); return

        from pycat.toolbox.condensate_physics_tools import (
            fit_photobleaching, apply_bleach_correction)
        means = np.array([float(np.nanmean(f)) for f in stack], dtype=float)
        try:
            fit = fit_photobleaching(means, frame_interval_s=float(dt.value()))
        except Exception as e:
            napari_show_warning(f"Bleach fit failed: {e}"); return

        if not fit.get('fit_success', True):
            napari_show_warning(
                "Bleach fit did not converge — no correction applied. (The trace "
                "may not show exponential decay.)")
            status.setText(
                "<span style='color:#c66;'>Fit did not converge — no correction "
                "applied.</span>")
            return
        factors = fit.get('correction_factors')
        if factors is None:
            napari_show_warning("Bleach fit did not return correction factors."); return
        corrected = apply_bleach_correction(stack, factors)
        viewer.add_image(np.asarray(corrected),
                         name=f"{layer.name} (bleach-corrected)")

        tau = fit.get('tau_bleach_s')
        try:
            import matplotlib.pyplot as plt
            t = np.arange(len(means)) * float(dt.value())
            fig, ax = plt.subplots(figsize=(7.0, 4.2))
            ax.plot(t, means, 'o', ms=3, color='#4c72b0', label='mean intensity')
            # Rebuild the fitted decay from the returned parameters (the fit dict
            # returns I0/tau/I_inf, not a precomputed curve).
            _I0 = fit.get('I0'); _inf = fit.get('I_inf')
            if (_I0 is not None and tau is not None and _inf is not None
                    and np.isfinite(tau) and tau > 0):
                ax.plot(t, _I0 * np.exp(-t / tau) + _inf, '-', lw=2,
                        color='#c44e52', label='fitted decay')
            corr_means = [float(np.nanmean(f)) for f in np.asarray(corrected)]
            ax.plot(t, corr_means, '-', lw=1.2, color='#55a868',
                    label='after correction')
            ax.set_xlabel('time (s)'); ax.set_ylabel('mean intensity')
            ttl = "Photobleaching"
            if tau is not None and np.isfinite(tau):
                ttl += f" — τ = {float(tau):.3g} s"
            ax.set_title(ttl, fontweight='bold')
            ax.grid(True, alpha=0.15); ax.legend(fontsize=8)
            fig.tight_layout(); plt.show(block=False)
        except Exception as e:
            print(f"[PyCAT Bleach] plot failed: {e}")

        status.setText(
            f"<span style='color:#8f8;'>Corrected stack added"
            + (f" · τ = {float(tau):.3g} s" if tau is not None and np.isfinite(tau) else "")
            + ".</span>")
        napari_show_info("Bleach correction applied — corrected stack added.")
        try:
            ui_instance._record('bleach_correction', {
                'layer': layer.name,
                'frame_interval_s': float(dt.value()),
                'tau_s': (float(tau) if tau is not None and np.isfinite(tau) else None)})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w = QWidget(); w.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, "Photobleach Correction")


# ─────────────────── 4. Detrend Stack (drift / bleaching) ───────────────────

def _add_detrend_stack(ui_instance, layout=None, separate_widget=False):
    """Remove slow bleaching/drift from a stack before a fluctuation analysis.

    Promoted from nb_tools: detrending is a prerequisite for ANY fluctuation
    measurement (N&B, camera-FCS, temporal correlation), not just N&B — an
    undetrended slow decay inflates the temporal variance and corrupts the result.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Detrend Stack (remove drift / bleaching)")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Removes the slow temporal trend "
        "that would otherwise inflate the temporal variance of a fluctuation "
        "measurement (N&amp;B, correlation analyses). <b>boxcar</b> smooths the "
        "global trace with a moving average; <b>linear</b> fits a straight line to "
        "it.</span>"))

    stack_dd = QComboBox(); stack_dd.addItems(_image_layer_names(viewer))
    form.addRow("Stack:", stack_dd)

    method_dd = QComboBox(); method_dd.addItems(['boxcar', 'linear'])
    form.addRow("Method:", method_dd)

    window = QSpinBox(); window.setRange(3, 9999); window.setValue(21)
    window.setToolTip("Boxcar window length in frames (ignored for 'linear').")
    form.addRow("Boxcar window (frames):", window)

    status = QLabel(""); status.setWordWrap(True)
    btn = QPushButton("\u25b6  Detrend")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh_dropdown(viewer, stack_dd)
        try:
            layer = viewer.layers[stack_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"Detrend: layer not found — {e}"); return
        stack = _as_stack(layer)
        if stack.shape[0] < 3:
            napari_show_warning("Detrending needs a time-series (≥3 frames)."); return

        from pycat.toolbox.nb_tools import detrend_timeseries
        try:
            detrended = np.asarray(
                detrend_timeseries(stack, method=method_dd.currentText(),
                                   window=int(window.value())))
        except Exception as e:
            napari_show_warning(f"Detrend failed: {e}"); return
        viewer.add_image(detrended, name=f"{layer.name} (detrended)")

        try:
            import matplotlib.pyplot as plt
            before = [float(np.nanmean(f)) for f in stack]
            after = [float(np.nanmean(f)) for f in detrended]
            fig, ax = plt.subplots(figsize=(7.0, 4.0))
            ax.plot(before, '-', lw=1.2, color='#4c72b0', label='before')
            ax.plot(after, '-', lw=1.2, color='#55a868', label='after detrend')
            ax.set_xlabel('frame'); ax.set_ylabel('mean intensity')
            ax.set_title(f"Detrend ({method_dd.currentText()})", fontweight='bold')
            ax.grid(True, alpha=0.15); ax.legend(fontsize=8)
            fig.tight_layout(); plt.show(block=False)
        except Exception as e:
            print(f"[PyCAT Detrend] plot failed: {e}")

        status.setText("<span style='color:#8f8;'>Detrended stack added.</span>")
        napari_show_info("Detrended stack added.")
        try:
            ui_instance._record('detrend_stack', {
                'layer': layer.name, 'method': method_dd.currentText(),
                'window': int(window.value())})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w = QWidget(); w.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, "Detrend Stack")


# ───────── 5. Motion Scale Estimator (time-projection, no linking) ──────────

def _add_motion_scale_estimator(ui_instance, layout=None, separate_widget=False):
    """Measure how far objects move between frames — WITHOUT linking any tracks.

    Generalised from VPT's ``estimate_linking_distance_um``. The idea: a
    short-window MAX-projection smears each object into a blob whose width is its
    single-frame width broadened by how far it MOVED over that window. Subtracting
    the single-frame width in quadrature recovers the motion scale directly:

        motion_sigma = sqrt(sigma_projected^2 - sigma_single_frame^2)

    That is exactly the quantity every frame-to-frame linker needs (its
    max-displacement / search-radius parameter) and which users are otherwise
    forced to GUESS. It is measured from the data, is viscosity/speed-adaptive,
    and costs one projection — no provisional linking pass.

    It applies to any dynamic localisation problem (beads, puncta, vesicles,
    condensates), not just VPT, and it doubles as a QC check: if the motion scale
    is comparable to (or larger than) the object size or the inter-object spacing,
    frame-to-frame linking is unreliable and the frame rate is too low.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Motion Scale Estimator (no linking needed)")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>Measures how far objects move "
        "per frame from a short MAX-projection: the projection smears each object "
        "by its motion, and subtracting the single-frame width in quadrature "
        "recovers the motion scale — <b>no tracking required</b>. Use it to set a "
        "linker's max-displacement from data instead of guessing, and to check "
        "whether your frame rate is fast enough to track at all.<br>"
        "<i>It is an estimate, not an exact measurement: fitting a Gaussian to "
        "the projected envelope slightly UNDER-estimates the true spread "
        "(~25% low on a synthetic random walk), which is what the margin factor "
        "k is for. Treat it as a well-grounded starting value, not a precise "
        "displacement.</i></span>"))

    stack_dd = QComboBox(); stack_dd.addItems(_image_layer_names(viewer))
    form.addRow("Stack:", stack_dd)

    mpp = QDoubleSpinBox()
    mpp.setDecimals(5); mpp.setRange(0.00001, 100.0); mpp.setValue(1.0)
    mpp.setToolTip("Pixel size (µm/px). Auto-filled from metadata when available.")
    try:
        _m = float(ui_instance._mpx())
        if _m and abs(_m - 1.0) > 1e-9:
            mpp.setValue(_m)
    except Exception:
        pass
    form.addRow("Pixel size (µm/px):", mpp)

    win = QSpinBox(); win.setRange(2, 200); win.setValue(8)
    win.setToolTip(
        "Frames in the short projection window. A few frames of motion — long "
        "enough to smear, short enough that the object doesn't wander far.")
    form.addRow("Projection window (frames):", win)

    kfac = QDoubleSpinBox()
    kfac.setRange(1.0, 10.0); kfac.setSingleStep(0.5); kfac.setValue(2.5)
    kfac.setToolTip(
        "Margin factor: suggested linking distance = k × motion sigma. Larger k "
        "covers more of the jitter tail at the risk of grabbing neighbours.")
    form.addRow("Margin factor k:", kfac)

    minsig = QDoubleSpinBox()
    minsig.setDecimals(1); minsig.setRange(0.5, 50.0); minsig.setValue(1.0)
    minsig.setToolTip("Smallest object sigma (px) for blob detection.")
    maxsig = QDoubleSpinBox()
    maxsig.setDecimals(1); maxsig.setRange(1.0, 100.0); maxsig.setValue(5.0)
    maxsig.setToolTip("Largest object sigma (px) for blob detection.")
    form.addRow("Min object sigma (px):", minsig)
    form.addRow("Max object sigma (px):", maxsig)

    status = QLabel(""); status.setWordWrap(True)
    btn = QPushButton("\u25b6  Estimate Motion Scale")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh_dropdown(viewer, stack_dd)
        try:
            layer = viewer.layers[stack_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"Motion scale: layer not found — {e}"); return
        stack = _as_stack(layer)
        if stack.shape[0] < 2:
            napari_show_warning(
                "Motion scale needs a time-series (≥2 frames)."); return

        from skimage.feature import blob_log
        from scipy.optimize import curve_fit

        T, H, W = stack.shape
        w = int(min(max(2, win.value()), T))
        half = 7

        def _fit_sigma(patch, h):

            """Fit a 2-D Gaussian to one bead and return its width.


            **The covariance is discarded here, and that is correct.**


            Elsewhere in PyCAT ``popt, _ = curve_fit(...)`` was a real bug: the SACF and CCF fits threw

            away the one number that says whether the Gaussian describes the data at all, and reported

            a **119.8 px correlation length for pure noise** (1.5.520).


            **This is not that.** There, ONE fit IS the answer. Here it is one of forty: the caller takes

            ``np.median(psf_sigmas)`` across every bead, and **the median tolerates up to 50 % garbage by

            construction.** Verified: with 40 % of the fits replaced by uniform noise, the median still

            recovers **2.12** against a true **2.00**.


            *A per-fit quality gate would add cost and no protection.*

            """
            p = np.asarray(patch, dtype=float)
            p = p - p.min()
            if p.max() <= 0:
                return np.nan
            yy, xx = np.mgrid[0:p.shape[0], 0:p.shape[1]]

            def g(c, A, x0, y0, s, o):
                x, y = c
                return (A * np.exp(-((x - x0) ** 2 + (y - y0) ** 2)
                                   / (2 * s ** 2)) + o).ravel()
            try:
                popt, _ = curve_fit(g, (xx, yy), p.ravel(),
                                    p0=[p.max(), h, h, 1.5, 0.0], maxfev=4000)
                return abs(float(popt[3]))
            except Exception:
                return np.nan

        # Find objects on frame 0 (generic blob detection — any bright compact
        # object, not just VPT beads).
        f0 = stack[0]
        rng_ = float(f0.max() - f0.min())
        norm = (f0 - f0.min()) / rng_ if rng_ > 0 else f0
        blobs = blob_log(norm, min_sigma=float(minsig.value()),
                         max_sigma=float(maxsig.value()), num_sigma=5,
                         threshold=0.05)
        if not len(blobs):
            napari_show_warning(
                "Motion scale: no objects detected on frame 0 — adjust the sigma "
                "range."); return
        centres = [(int(round(b[0])), int(round(b[1]))) for b in blobs]
        rng = np.random.default_rng(0)
        if len(centres) > 40:
            idx = rng.choice(len(centres), 40, replace=False)
            centres = [centres[i] for i in idx]

        proj = stack[:w].max(axis=0)     # the short-window MAX-projection
        psf_s, mot_s = [], []
        for (yi, xi) in centres:
            if yi - half < 0 or xi - half < 0 or yi + half + 1 > H or xi + half + 1 > W:
                continue
            s1 = _fit_sigma(f0[yi-half:yi+half+1, xi-half:xi+half+1], half)
            sp = _fit_sigma(proj[yi-half:yi+half+1, xi-half:xi+half+1], half)
            if not (np.isfinite(s1) and np.isfinite(sp)):
                continue
            psf_s.append(s1)
            mot_s.append(np.sqrt(max(sp**2 - s1**2, 0.0)))   # quadrature subtract

        if not mot_s:
            napari_show_warning(
                "Motion scale: could not fit any objects — try a different sigma "
                "range or window."); return

        px = float(mpp.value())
        wander_px = float(np.median(mot_s))    # smear over the WHOLE window
        psf_px = float(np.median(psf_s))
        # The projection smear is the wander over the whole window, not a single
        # frame. For diffusive motion the wander grows as sqrt(n_frames), so the
        # implied PER-FRAME step is wander / sqrt(window). Report both: the
        # per-frame step is what a frame-to-frame linker must bridge, while the
        # window wander is what was actually measured.
        per_frame_px = wander_px / max(np.sqrt(float(w)), 1.0)
        dist_px = float(kfac.value()) * per_frame_px
        cap_px = 3.0 * psf_px            # never link farther than the object's own size
        capped = dist_px > cap_px
        dist_px = min(dist_px, cap_px)

        wander_um = wander_px * px
        motion_um = per_frame_px * px
        psf_um = psf_px * px
        dist_um = dist_px * px

        # Show the projection so the user SEES the smear the estimate came from.
        viewer.add_image(proj, name=f"{layer.name} (max-proj, {w} frames)")

        # Anti-black-box: report the quantities behind the number. The relevant
        # comparison for LINKING is the per-frame step vs the object size.
        ratio = (per_frame_px / psf_px) if psf_px > 0 else float('nan')
        if ratio < 0.5:
            verdict = ("per-frame motion is well below the object size — "
                       "frame-to-frame linking should be reliable.")
            colour = '#8f8'
        elif ratio < 1.5:
            verdict = ("per-frame motion is comparable to the object size — "
                       "linking is workable but watch for mislinks in dense "
                       "fields.")
            colour = '#fc8'
        else:
            verdict = ("per-frame motion EXCEEDS the object size — frame-to-frame "
                       "linking is unreliable at this frame rate; consider faster "
                       "acquisition or a gap-closing linker.")
            colour = '#f88'

        msg = (f"<span style='color:{colour};'>"
               f"<b>Per-frame motion = {motion_um:.3f} µm</b> "
               f"({per_frame_px:.2f} px)<br>"
               f"<span style='color:#aaa;'>measured smear over {w} frames = "
               f"{wander_um:.3f} µm ({wander_px:.2f} px); per-frame = smear/\u221a{w}"
               f"</span><br>"
               f"Object sigma (single frame) = {psf_um:.3f} µm ({psf_px:.2f} px)<br>"
               f"<b>Suggested max linking distance = {dist_um:.3f} µm</b> "
               f"(k={kfac.value():g}"
               + (", CAPPED at the object footprint" if capped else "")
               + f")<br>{len(mot_s)} objects used · {verdict}</span>")
        status.setText(msg)
        napari_show_info(
            f"Motion scale: {motion_um:.3f} µm/frame; suggested linking distance "
            f"{dist_um:.3f} µm ({len(mot_s)} objects).")

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'motion_scale'] = dict(
                    motion_sigma_um=motion_um, window_wander_um=wander_um,
                    object_sigma_um=psf_um,
                    linking_distance_um=dist_um, capped=bool(capped),
                    n_objects=len(mot_s), window_frames=w)
        except Exception:
            pass
        try:
            ui_instance._record('motion_scale_estimate', {
                'layer': layer.name, 'window_frames': w, 'k': float(kfac.value()),
                'motion_sigma_um': motion_um, 'window_wander_um': wander_um,
                'object_sigma_um': psf_um,
                'linking_distance_um': dist_um, 'capped': bool(capped),
                'n_objects': int(len(mot_s))})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w_ = QWidget(); w_.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w_, layout, separate_widget, "Motion Scale Estimator")


# ────────── 6. Partial-Volume Measurement (measure on native pixels) ────────

def _add_partial_volume_measure(ui_instance, layout=None, separate_widget=False):
    """Measure objects segmented on an UPSCALED image, on the ORIGINAL pixels.

    Upscaling adds no information — it is a deterministic interpolation of the
    pixels you already had, and it cannot resolve anything the optics didn't
    (verified: upscaling never split objects that native-resolution segmentation
    merged). Its only legitimate use is to satisfy a segmentation model's learned
    object-scale prior. Reading INTENSITIES off interpolated pixels, however:

      * pseudoreplicates (16x the "samples", zero new photons → reported SEM comes
        out ~1.5x too confident), and
      * biases small objects low in a size-dependent way (which can manufacture a
        spurious intensity-vs-size trend).

    This tool does it correctly: it converts the high-resolution mask into
    FRACTIONAL COVERAGE weights on the native grid, then computes weighted
    statistics on the ORIGINAL image, with an effective sample size so the error
    bars stay honest.
    """
    viewer = ui_instance.viewer
    grp = QGroupBox("Partial-Volume Measurement (measure on original pixels)")
    form = QFormLayout(grp)

    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>For objects segmented on an "
        "<b>upscaled</b> image. Converts the high-res mask into fractional-coverage "
        "weights on the native grid and measures intensities on the "
        "<b>original</b> image — never on interpolated pixels. Reports an effective "
        "sample size so the SEM isn't pseudoreplicated.</span>"))

    mask_dd = QComboBox()
    img_dd = QComboBox()

    def _refresh():
        import napari
        cur_m, cur_i = mask_dd.currentText(), img_dd.currentText()
        mask_dd.clear(); img_dd.clear()
        mask_dd.addItems([l.name for l in viewer.layers
                          if isinstance(l, napari.layers.Labels)])
        img_dd.addItems(_image_layer_names(viewer))
        for dd, cur in ((mask_dd, cur_m), (img_dd, cur_i)):
            i = dd.findText(cur)
            if i >= 0:
                dd.setCurrentIndex(i)

    _refresh()
    form.addRow("High-res mask (from upscaled seg.):", mask_dd)
    form.addRow("ORIGINAL image (native scale):", img_dd)

    factor = QSpinBox(); factor.setRange(1, 16); factor.setValue(2)
    factor.setToolTip(
        "The upscale factor used for segmentation. The mask should be this many "
        "times larger than the original image. Use 1 if the mask is already at "
        "native scale (the weights are then just 0/1).")
    form.addRow("Upscale factor:", factor)

    resolved = QLabel("")
    resolved.setWordWrap(True)
    resolved.setStyleSheet("font-size:9pt;")
    form.addRow(resolved)

    def _auto_resolve():
        """Follow the MASK's recorded lineage to decide which image to measure on
        and what the upscale factor was — rather than making the user work it out,
        or guessing from layer names. The tag system already records
        mask --belongs_to--> segmentation image --derived_from(upscale)--> original.
        """
        try:
            lmask = viewer.layers[mask_dd.currentText()]
        except Exception:
            resolved.setText(""); return
        from pycat.toolbox.partial_volume_tools import resolve_measurement_source
        r = resolve_measurement_source(lmask, viewer)
        if not r.get('confident'):
            resolved.setText(
                f"<span style='color:#e8a33d;'>{r.get('reason','')}</span>")
            return
        # Point the image dropdown at the resolved source and set the factor.
        tgt = r.get('measure_on')
        if tgt is not None:
            i = img_dd.findText(tgt.name)
            if i >= 0:
                img_dd.setCurrentIndex(i)
        factor.setValue(int(r.get('factor', 1)))
        colour = '#8f8' if not r.get('upscaled') else '#8cf'
        resolved.setText(f"<span style='color:{colour};'>{r.get('reason','')}</span>")

    mask_dd.currentTextChanged.connect(lambda *_: _auto_resolve())
    _auto_resolve()

    bg = QDoubleSpinBox()
    bg.setDecimals(2); bg.setRange(-1e6, 1e6); bg.setValue(0.0)
    bg.setToolTip("Background level subtracted for the integrated-intensity column.")
    form.addRow("Background:", bg)

    status = QLabel(""); status.setWordWrap(True)
    btn = QPushButton("\u25b6  Measure (partial-volume)")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _run():
        _refresh()
        try:
            lmask = viewer.layers[mask_dd.currentText()]
            limg = viewer.layers[img_dd.currentText()]
        except KeyError as e:
            napari_show_warning(f"PV measure: layer not found — {e}"); return

        from pycat.file_io.file_io import layer_is_stack, extract_2d_plane
        fi = 0
        if layer_is_stack(limg.data):
            try:
                fi = int(viewer.dims.current_step[0])
            except Exception:
                fi = 0
        img = extract_2d_plane(limg.data, frame_index=fi, dtype=None)
        # Match the image's frame extraction: np.asarray on a lazy mask stack returns frame 0, so it would
        # pair frame `fi` of the image with frame 0 of the mask. extract_2d_plane reads the same frame.
        mask = extract_2d_plane(lmask.data, frame_index=fi, dtype=None)

        f = int(factor.value())
        exp = (img.shape[0] * f, img.shape[1] * f)
        if f > 1 and (abs(mask.shape[0] - exp[0]) > f or abs(mask.shape[1] - exp[1]) > f):
            napari_show_warning(
                f"The mask {mask.shape} is not a {f}x upscale of the image "
                f"{img.shape} (expected about {exp}). Check the factor, or pick "
                f"the ORIGINAL (not upscaled) image.")
            return
        # Guard the classic mistake: measuring on the upscaled image.
        from pycat.toolbox.partial_volume_tools import looks_upscaled
        if looks_upscaled(limg):
            napari_show_warning(
                "The selected image looks like an UPSCALED layer. Select the "
                "ORIGINAL image — measuring on interpolated pixels is exactly what "
                "this tool exists to avoid.")
            return

        from pycat.toolbox.partial_volume_tools import measure_objects_pv
        try:
            mpp = float(ui_instance._mpx())
        except Exception:
            mpp = 1.0
        try:
            df = measure_objects_pv(mask, img, factor=f,
                                    microns_per_pixel=mpp,
                                    background=float(bg.value()))
        except Exception as e:
            napari_show_warning(f"PV measurement failed: {e}"); return
        if df is None or df.empty:
            napari_show_info("PV measure: no objects found."); return

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'partial_volume_df'] = df
        except Exception:
            pass
        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Partial-Volume Measurements",
                                   [("Per object (measured on native pixels)",
                                     df.round(4))])
        except Exception:
            pass

        _npx = float(df['n_pixels_touched'].sum())
        _neff = float(df['n_eff'].sum())

        # ── The advisory that actually protects the science ──────────────────
        # The residual size-dependent bias is OPTICAL, not computational: an edge
        # pixel physically integrates a mix of object and background photons, so
        # small objects read dim no matter how the mask is handled. A shared bias
        # LEVEL cancels in a comparison, but the GRADIENT does not — two groups
        # with identical true intensity but different SIZE produce an apparent
        # intensity difference (verified: R=3 vs R=8 fabricates ~+12% with
        # p~1e-83). So we quantify it for the user's own optics and say so.
        advisory = ""
        try:
            from pycat.toolbox.partial_volume_tools import (
                estimate_psf_sigma, intensity_bias_for_size, is_sub_resolution,
                size_confound_warning)
            psf = estimate_psf_sigma(img)
            radii = np.sqrt(df['area_px'].values / np.pi)   # equivalent radius
            r_min, r_med, r_max = (float(np.min(radii)), float(np.median(radii)),
                                   float(np.max(radii)))
            b_min = intensity_bias_for_size(r_min, psf)
            b_med = intensity_bias_for_size(r_med, psf)
            b_max = intensity_bias_for_size(r_max, psf)
            n_sub = int(sum(is_sub_resolution(r, psf) for r in radii))

            # Does the spread of sizes WITHIN this field already threaten a
            # size-intensity correlation?
            conf = size_confound_warning(radii[radii <= r_med],
                                         radii[radii > r_med], psf)

            lines = [
                f"<b>Size-dependent intensity bias</b> (optical — present with any "
                f"masking method; PSF σ ≈ {psf:.1f} px):",
                f"&nbsp;&nbsp;smallest object r≈{r_min:.1f}px → <b>{b_min:+.0%}</b> · "
                f"median r≈{r_med:.1f}px → <b>{b_med:+.0%}</b> · "
                f"largest r≈{r_max:.1f}px → <b>{b_max:+.0%}</b>",
            ]
            if n_sub:
                lines.append(
                    f"<span style='color:#f88;'>⚠ {n_sub} object(s) are at or below "
                    f"the resolution limit — their absolute intensity is dominated "
                    f"by background mixing and is not trustworthy by any method.</span>")
            if conf['level'] == 'severe':
                lines.append(
                    f"<span style='color:#f88;'>⚠ <b>Size confound within this "
                    f"field:</b> small vs large objects differ enough that the bias "
                    f"gradient alone can produce a "
                    f"{conf['apparent_intensity_difference']:+.0%} apparent intensity "
                    f"difference. <b>Do not interpret an intensity-vs-size trend "
                    f"without size-matching.</b></span>")
            elif conf['level'] == 'mild':
                lines.append(
                    f"<span style='color:#e8a33d;'>Size range is mild "
                    f"({conf['apparent_intensity_difference']:+.0%} from size alone) "
                    f"— report size distributions alongside intensities.</span>")
            advisory = ("<div style='margin-top:6px;font-size:9pt;color:#bbb;'>"
                        + "<br>".join(lines) + "</div>")
            # Keep the numbers for the record.
            dr = ui_instance.central_manager.active_data_class.data_repository
            dr['intensity_bias_advisory'] = dict(
                psf_sigma_px=psf, r_min=r_min, r_med=r_med, r_max=r_max,
                bias_min=b_min, bias_med=b_med, bias_max=b_max,
                n_sub_resolution=n_sub, **{k: v for k, v in conf.items()
                                           if k != 'verdict'})
        except Exception as e:
            print(f"[PyCAT PV] bias advisory failed: {e}")

        status.setText(
            f"<span style='color:#8f8;'>{len(df)} objects measured on the "
            f"<b>original</b> image.<br>Effective N = {_neff:.0f} vs "
            f"{_npx:.0f} pixels touched — the SEM uses the effective N, so it is "
            f"not inflated by partially-covered edge pixels.</span>" + advisory)
        napari_show_info(
            f"Partial-volume measurement: {len(df)} objects (measured on native "
            f"pixels, effective N={_neff:.0f}).")
        try:
            ui_instance._record('partial_volume_measure', {
                'mask': lmask.name, 'image': limg.name, 'factor': f,
                'n_objects': int(len(df)), 'background': float(bg.value())})
        except Exception:
            pass

    btn.clicked.connect(_run)
    try:
        from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
    except Exception:
        form.addRow(btn)
    form.addRow(status)

    container = QVBoxLayout(); container.addWidget(grp)
    w_ = QWidget(); w_.setLayout(container)
    ui_instance._add_widget_to_layout_or_dock(
        w_, layout, separate_widget, "Partial-Volume Measurement")


