"""
PyCAT Two-Channel Condensate Colocalization
=============================================
Convenience workflow that segments condensates independently in two
fluorescence channels (e.g. GFP-labeled and mCherry-labeled condensates)
and runs object-based colocalization analysis (Jaccard, Dice, Manders,
distance) between them on a per-cell basis.

This is a thin orchestration layer over two functions that already exist
in PyCAT:
    segment_subcellular_objects()            — per-cell condensate segmentation
    object_based_colocalization_analysis()   — Jaccard/Dice/Manders/distance

Workflow
--------
1. User selects two pre-processed images (one per channel) and a labeled
   cell mask (from Cell Analyzer, shared between both channels since cells
   are the same physical structures regardless of which fluorophore is
   imaged).
2. Condensates are segmented independently in each channel, per cell, using
   the same refinement parameters as the standard condensate segmentation
   widget (kurtosis, SNR, intensity scale, area fraction, min spot radius —
   each channel gets its own parameter set since different fluorophores
   often need different thresholds).
3. For each cell, object_based_colocalization_analysis() runs on the two
   resulting refined puncta masks restricted to that cell's ROI.
4. Results are aggregated into a per-cell DataFrame with columns:
     cell_label, n_objects_ch1, n_objects_ch2, jaccard_index,
     dice_coefficient, manders_m1, manders_m2, mean_distance_um,
     percent_noncoincident

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import skimage as sk
import napari
from napari.utils.notifications import (
    show_info as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QSizePolicy,
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QDoubleSpinBox, QCheckBox, QProgressBar,
)
from PyQt5.QtCore import QThread, pyqtSignal

from pycat.toolbox.segmentation_tools import segment_subcellular_objects
from pycat.toolbox.obj_based_coloc_analysis_tools import object_based_colocalization_analysis
from pycat.ui.ui_utils import show_dataframes_dialog


# ---------------------------------------------------------------------------
# Pure analysis function
# ---------------------------------------------------------------------------

def run_two_channel_condensate_colocalization(
    image_ch1: np.ndarray,
    preprocessed_ch1: np.ndarray,
    image_ch2: np.ndarray,
    preprocessed_ch2: np.ndarray,
    labeled_cell_mask: np.ndarray,
    ball_radius: float,
    microns_per_pixel_sq: float,
    refinement_params_ch1: dict,
    refinement_params_ch2: dict,
    coloc_methods: list[str],
    cell_df: pd.DataFrame = None,
    progress_callback=None,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """
    Segment condensates independently in two channels and run object-based
    colocalization analysis per cell.

    Parameters
    ----------
    image_ch1, image_ch2 : np.ndarray
        Raw fluorescence images for each channel (e.g. GFP, mCherry).
    preprocessed_ch1, preprocessed_ch2 : np.ndarray
        Pre-processed versions of each channel image.
    labeled_cell_mask : np.ndarray
        Integer-labeled cell mask, shared between both channels.
    ball_radius : float
    microns_per_pixel_sq : float
    refinement_params_ch1, refinement_params_ch2 : dict
        Each containing: kurtosis_threshold, local_snr_threshold,
        global_snr_threshold, intensity_hwhm_scale, max_area_fraction,
        min_spot_radius — applied independently per channel since
        different fluorophores often need different thresholds.
    coloc_methods : list of str
        Subset of ["Mander's M1 value", "Mander's M2 value", "Jaccard Index",
        "Sorensen-Dice Coefficient", "Calculate Distance Between Objects"]
    cell_df : pd.DataFrame, optional
        Cell analysis dataframe (for skip-empty-cell optimization).
    progress_callback : callable(cell_idx, total_cells) or None

    Returns
    -------
    results_df : pd.DataFrame — one row per cell with colocalization metrics
    puncta_mask_ch1 : np.ndarray bool — full-frame refined puncta mask, channel 1
    puncta_mask_ch2 : np.ndarray bool — full-frame refined puncta mask, channel 2
    """
    cell_labels = np.unique(labeled_cell_mask)
    cell_labels = cell_labels[cell_labels != 0]
    n_cells = len(cell_labels)

    H, W = labeled_cell_mask.shape
    puncta_mask_ch1 = np.zeros((H, W), dtype=bool)
    puncta_mask_ch2 = np.zeros((H, W), dtype=bool)

    records = []

    # Build a fake data_instance-like object for object_based_colocalization_analysis
    class _MiniDataInstance:
        def __init__(self, mpx_sq):
            self.data_repository = {'microns_per_pixel_sq': mpx_sq}

    mini_data_instance = _MiniDataInstance(microns_per_pixel_sq)

    for i, cell_label in enumerate(cell_labels):
        cell_mask = (labeled_cell_mask == cell_label).astype(bool)

        refined1, _ = segment_subcellular_objects(
            image_ch1, preprocessed_ch1, cell_mask, int(cell_label),
            ball_radius, cell_df,
            **refinement_params_ch1,
        )
        refined2, _ = segment_subcellular_objects(
            image_ch2, preprocessed_ch2, cell_mask, int(cell_label),
            ball_radius, cell_df,
            **refinement_params_ch2,
        )

        puncta_mask_ch1 |= refined1
        puncta_mask_ch2 |= refined2

        labeled1 = sk.measure.label(refined1)
        labeled2 = sk.measure.label(refined2)
        n_obj1 = int(labeled1.max())
        n_obj2 = int(labeled2.max())

        row = {
            'cell_label': int(cell_label),
            'n_objects_ch1': n_obj1,
            'n_objects_ch2': n_obj2,
        }

        if n_obj1 == 0 or n_obj2 == 0:
            # No objects in one or both channels — colocalization metrics
            # are undefined; record zeros/NaN rather than calling the
            # analysis functions on empty masks.
            for method in coloc_methods:
                key = method.lower().replace(" ", "_").replace("'", "")
                row[key] = np.nan
        else:
            table1, table2 = object_based_colocalization_analysis(
                labeled1, labeled2, cell_mask, coloc_methods, mini_data_instance
            )
            if table1 is not None:
                for _, r in table1.iterrows():
                    key = str(r.get('Metric', r.iloc[0])).lower().replace(" ", "_").replace("'", "")
                    val_col = 'Value' if 'Value' in table1.columns else table1.columns[-1]
                    row[key] = r.get(val_col, r.iloc[-1])
            if table2 is not None:
                for _, r in table2.iterrows():
                    key = str(r['Metric']).lower().replace(" ", "_").replace("(", "").replace(")", "")
                    row[key] = r['Value']

        records.append(row)

        if progress_callback is not None:
            progress_callback(i + 1, n_cells)

    results_df = pd.DataFrame(records)
    return results_df, puncta_mask_ch1, puncta_mask_ch2


def condensate_coloc_time_trace(
    stack_ch1, stack_proc_ch1, stack_ch2, stack_proc_ch2, cell_mask_stack,
    ball_radius, microns_per_pixel_sq, refinement_params_ch1,
    refinement_params_ch2, coloc_methods, frame_interval_s=1.0,
    per_cell=True, progress_callback=None, cancel_check=None):
    """Run object-based two-channel condensate colocalization frame-by-frame and
    return a time trace — per cell (default) or aggregated per frame.

    Cell identity across frames comes from the LABELED cell mask, exactly as the
    time-series condensate pipeline does it: a labeled mask carries stable cell
    IDs, so the same label = the same cell frame-to-frame. Supply either a single
    (H, W) labeled mask (propagated to every frame — the cell doesn't move) or a
    (T, H, W) labeled stack (each frame's own segmentation; consistent labels
    track moving cells). No separate tracking step is needed — the segmentation's
    labels ARE the identity.

    per_cell : if True (default) keep one row per (frame, cell_label) so each
        cell's colocalization can be followed over time; if False, average across
        cells for a single per-frame trace (the old behaviour).
    cancel_check : optional callable() -> bool; when it returns True the run stops
        early and returns what's been computed so far.

    Returns a tidy DataFrame:
      per_cell=True  → frame, time_s, cell_label, <each coloc metric column>
      per_cell=False → frame, time_s, n_cells, mean_<metric> …
    Inputs may be lazy stacks; frames are streamed one at a time.
    """
    from pycat.file_io.file_io import iter_frames, layer_is_stack, extract_2d_plane

    shp = getattr(stack_ch1, 'shape', None)
    n_t = int(shp[0]) if (shp is not None and len(shp) == 3) else 1
    mask_is_stack = layer_is_stack(cell_mask_stack)
    static_mask = None
    if not mask_is_stack:
        static_mask = extract_2d_plane(cell_mask_stack, frame_index=0, dtype=None)

    rows = []
    it1 = iter_frames(stack_ch1);  it1p = iter_frames(stack_proc_ch1)
    it2 = iter_frames(stack_ch2);  it2p = iter_frames(stack_proc_ch2)
    for (t, f1), (_, f1p), (_, f2), (_, f2p) in zip(it1, it1p, it2, it2p):
        if cancel_check is not None:
            try:
                if cancel_check():
                    break
            except Exception:
                pass
        cmask = (np.asarray(cell_mask_stack[t]) if mask_is_stack else static_mask)
        try:
            per_cell_df, _m1, _m2 = run_two_channel_condensate_colocalization(
                image_ch1=f1, preprocessed_ch1=f1p,
                image_ch2=f2, preprocessed_ch2=f2p,
                labeled_cell_mask=cmask, ball_radius=ball_radius,
                microns_per_pixel_sq=microns_per_pixel_sq,
                refinement_params_ch1=refinement_params_ch1,
                refinement_params_ch2=refinement_params_ch2,
                coloc_methods=coloc_methods, cell_df=None)
        except Exception:
            per_cell_df = pd.DataFrame()

        time_s = float(t) * float(frame_interval_s)
        if per_cell:
            # One row per cell, preserving cell_label so each cell can be tracked
            # through time. (Empty frames contribute no rows.)
            for _, r in per_cell_df.iterrows():
                row = {'frame': int(t), 'time_s': time_s}
                # keep the cell identity column under a stable name
                cl = r.get('cell_label', r.get('cell_id', None))
                row['cell_label'] = int(cl) if cl is not None and cl == cl else -1
                for col in per_cell_df.columns:
                    if col in ('cell_label', 'cell_id'):
                        continue
                    v = pd.to_numeric(pd.Series([r[col]]), errors='coerce').iloc[0]
                    row[col] = v
                rows.append(row)
        else:
            row = {'frame': int(t), 'time_s': time_s,
                   'n_cells': int(len(per_cell_df))}
            if len(per_cell_df):
                for col in per_cell_df.columns:
                    if col in ('cell_label', 'cell_id'):
                        continue
                    vals = pd.to_numeric(per_cell_df[col], errors='coerce')
                    if vals.notna().any():
                        row[f'mean_{col}'] = float(vals.mean())
            rows.append(row)

        if progress_callback is not None:
            try:
                progress_callback(t + 1, n_t)
            except Exception:
                pass

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class TwoChannelColocWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object, object, object)
    error    = pyqtSignal(str)

    def __init__(self, kwargs: dict, parent=None):
        super().__init__(parent)
        self._kwargs = kwargs

    def run(self):
        try:
            def _cb(i, total):
                self.progress.emit(i, total)
            results_df, mask1, mask2 = run_two_channel_condensate_colocalization(
                progress_callback=_cb, **self._kwargs
            )
            self.finished.emit(results_df, mask1, mask2)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


class CondensateColocTimeWorker(QThread):
    """Runs condensate_coloc_time_trace off the UI thread (frame-by-frame over a
    stack can be slow), reporting per-frame progress and supporting cancellation."""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)      # the trace DataFrame
    error    = pyqtSignal(str)

    def __init__(self, kwargs: dict, parent=None):
        super().__init__(parent)
        self._kwargs = kwargs
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            def _cb(done, total):
                self.progress.emit(done, total)
            trace = condensate_coloc_time_trace(
                progress_callback=_cb,
                cancel_check=lambda: self._cancel,
                **self._kwargs)
            self.finished.emit(trace)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

def _add_run_two_channel_coloc(ui_instance, layout=None, separate_widget=False):
    """
    Build the Two-Channel Condensate Colocalization widget.

    Intended for use in the Object Based Colocalization Analysis pipeline,
    as an alternative entry point to manual mask selection — this widget
    handles segmentation of both channels automatically before running OBCA.
    """
    main_layout = QVBoxLayout()
    ui_instance.add_text_label(main_layout, 'Two-Channel Condensate Colocalization', bold=True)
    ui_instance.add_text_label(
        main_layout,
        'Segments condensates independently in two channels (e.g. GFP and '
        'mCherry) and computes per-cell colocalization metrics.',
        font_size=9
    )

    # ── Channel 1 inputs ─────────────────────────────────────────────────
    ch1_group = QGroupBox("Channel 1 (e.g. GFP)")
    ch1_form = QFormLayout(ch1_group)
    ch1_form.setContentsMargins(9, 20, 9, 6)
    ch1_raw_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ch1_proc_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ch1_form.addRow("Raw image:", ch1_raw_dropdown)
    ch1_form.addRow("Pre-processed image:", ch1_proc_dropdown)
    main_layout.addWidget(ch1_group)

    # ── Channel 2 inputs ─────────────────────────────────────────────────
    ch2_group = QGroupBox("Channel 2 (e.g. mCherry)")
    ch2_form = QFormLayout(ch2_group)
    ch2_form.setContentsMargins(9, 20, 9, 6)
    ch2_raw_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ch2_proc_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    ch2_form.addRow("Raw image:", ch2_raw_dropdown)
    ch2_form.addRow("Pre-processed image:", ch2_proc_dropdown)
    main_layout.addWidget(ch2_group)

    # ── Shared cell mask ─────────────────────────────────────────────────
    mask_group = QGroupBox("Shared Cell Mask")
    mask_form = QFormLayout(mask_group)
    mask_form.setContentsMargins(9, 20, 9, 6)
    cell_mask_dropdown = ui_instance.create_layer_dropdown(napari.layers.Labels)
    mask_form.addRow("Labeled cell mask:", cell_mask_dropdown)
    main_layout.addWidget(mask_group)

    # ── Per-channel refinement parameters ───────────────────────────────
    def _build_refinement_group(title):
        grp = QGroupBox(title)
        form = QFormLayout(grp)
        form.setContentsMargins(9, 20, 9, 6)

        def _dspin(lo, hi, val, step):
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi); sb.setValue(val); sb.setSingleStep(step); sb.setDecimals(2)
            return sb

        spins = {
            'min_spot_radius':     _dspin(1, 20, 2, 0.5),
            'kurtosis_threshold':  _dspin(-10, 0, -3.0, 0.5),
            'local_snr_threshold': _dspin(0, 5, 1.0, 0.1),
            'global_snr_threshold':_dspin(0, 5, 1.0, 0.1),
            'intensity_hwhm_scale':_dspin(0, 5, 1.17, 0.1),
            'max_area_fraction':   _dspin(0.01, 1.0, 0.25, 0.05),
        }
        form.addRow("Min spot radius (px):", spins['min_spot_radius'])
        form.addRow("Kurtosis threshold:", spins['kurtosis_threshold'])
        form.addRow("Local SNR threshold:", spins['local_snr_threshold'])
        form.addRow("Global SNR threshold:", spins['global_snr_threshold'])
        form.addRow("Intensity scale (×SD):", spins['intensity_hwhm_scale'])
        form.addRow("Max area (frac of cell):", spins['max_area_fraction'])
        return grp, spins

    ch1_ref_group, ch1_spins = _build_refinement_group("Channel 1 Refinement Parameters")
    ch2_ref_group, ch2_spins = _build_refinement_group("Channel 2 Refinement Parameters")
    main_layout.addWidget(ch1_ref_group)
    main_layout.addWidget(ch2_ref_group)

    # ── Colocalization methods ──────────────────────────────────────────
    methods_group = QGroupBox("Colocalization Metrics")
    methods_layout = QVBoxLayout(methods_group)
    methods_layout.setContentsMargins(9, 20, 9, 6)
    method_checks = {
        "Jaccard Index": QCheckBox("Jaccard Index"),
        "Sorensen-Dice Coefficient": QCheckBox("Sorensen-Dice Coefficient"),
        "Mander's M1 value": QCheckBox("Mander's M1 (ch1→ch2 overlap fraction)"),
        "Mander's M2 value": QCheckBox("Mander's M2 (ch2→ch1 overlap fraction)"),
        "Calculate Distance Between Objects": QCheckBox("Distance Between Objects"),
    }
    for cb in method_checks.values():
        cb.setChecked(True)
        methods_layout.addWidget(cb)
    main_layout.addWidget(methods_group)

    # ── Progress & run ───────────────────────────────────────────────────
    progress_bar = QProgressBar()
    progress_bar.setVisible(False)
    main_layout.addWidget(progress_bar)

    run_btn = QPushButton("▶  Run Two-Channel Colocalization")
    run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    main_layout.addWidget(run_btn)

    time_btn = QPushButton("▶  Coloc Over Time (all frames)")
    time_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    time_btn.setToolTip(
        "Run object-based condensate colocalization on every frame of the "
        "selected stacks and plot the per-frame mean coloc metrics vs time. Use "
        "when the channels are a time-series. Cells are not tracked across frames, "
        "so the trace is the per-frame average across cells.")
    main_layout.addWidget(time_btn)

    def _on_run():
        try:
            ch1_raw_layer  = ui_instance.viewer.layers[ch1_raw_dropdown.currentText()]
            ch1_proc_layer = ui_instance.viewer.layers[ch1_proc_dropdown.currentText()]
            ch2_raw_layer  = ui_instance.viewer.layers[ch2_raw_dropdown.currentText()]
            ch2_proc_layer = ui_instance.viewer.layers[ch2_proc_dropdown.currentText()]
            cell_mask_layer = ui_instance.viewer.layers[cell_mask_dropdown.currentText()]
        except KeyError as e:
            napari_show_warning(f"Two-Channel Coloc: layer not found — {e}")
            return

        # Colocalization is a 2D operation (two channels of ONE field). If any
        # input is actually a lazy time-series / z-stack, reading it with
        # np.asarray would silently grab frame 0 (the _TiffPageStack trap). Detect
        # that, and use the CURRENT VIEWER FRAME instead — warning the user which
        # frame was used. (Sequential per-frame colocalization over time is a
        # planned follow-on; for now this measures the frame you're looking at.)
        from pycat.file_io.file_io import layer_is_stack, extract_2d_plane
        any_stack = any(layer_is_stack(l.data) for l in
                        (ch1_raw_layer, ch1_proc_layer, ch2_raw_layer,
                         ch2_proc_layer, cell_mask_layer))
        cur_frame = 0
        if any_stack:
            try:
                cur_frame = int(ui_instance.viewer.dims.current_step[0])
            except Exception:
                cur_frame = 0
            napari_show_warning(
                f"Two-Channel Coloc: one or more inputs is a stack — measuring "
                f"the current frame (t={cur_frame}). Per-frame colocalization over "
                f"time is coming; for now, step to the frame you want and re-run.")

        def _plane(layer, floaty=True):
            arr = extract_2d_plane(layer.data, frame_index=cur_frame,
                                   dtype=(np.float32 if floaty else None))
            return arr

        ch1_raw  = _plane(ch1_raw_layer)
        ch1_proc = _plane(ch1_proc_layer)
        ch2_raw  = _plane(ch2_raw_layer)
        ch2_proc = _plane(ch2_proc_layer)
        # Mask must keep its integer labels — do not float it.
        cell_mask = _plane(cell_mask_layer, floaty=False)

        if ch1_raw.shape != ch2_raw.shape:
            napari_show_warning("Two-Channel Coloc: channel images must have the same shape.")
            return

        selected_methods = [name for name, cb in method_checks.items() if cb.isChecked()]
        if not selected_methods:
            napari_show_warning("Select at least one colocalization metric.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        ball_radius = float(data_instance.data_repository.get('ball_radius', 50))
        mpx_sq = float(data_instance.data_repository.get('microns_per_pixel_sq', 1.0))
        cell_df = data_instance.data_repository.get('cell_df', None)

        refinement_ch1 = {k: s.value() for k, s in ch1_spins.items()}
        refinement_ch2 = {k: s.value() for k, s in ch2_spins.items()}

        n_cells = len(np.unique(cell_mask)) - 1
        progress_bar.setMaximum(max(1, n_cells))
        progress_bar.setValue(0)
        progress_bar.setVisible(True)
        run_btn.setEnabled(False)

        kwargs = dict(
            image_ch1=ch1_raw,
            preprocessed_ch1=ch1_proc,
            image_ch2=ch2_raw,
            preprocessed_ch2=ch2_proc,
            labeled_cell_mask=cell_mask,
            ball_radius=ball_radius,
            microns_per_pixel_sq=mpx_sq,
            refinement_params_ch1=refinement_ch1,
            refinement_params_ch2=refinement_ch2,
            coloc_methods=selected_methods,
            cell_df=cell_df,
        )

        worker = TwoChannelColocWorker(kwargs)
        ui_instance._two_channel_worker = worker  # keep alive

        worker.progress.connect(lambda i, t: progress_bar.setValue(i))
        worker.finished.connect(lambda df, m1, m2: _on_finished(df, m1, m2))
        worker.error.connect(_on_error)
        worker.start()

        ui_instance._record('two_channel_condensate_coloc', {
            'ch1_raw_layer': ch1_raw_dropdown.currentText(),
            'ch1_proc_layer': ch1_proc_dropdown.currentText(),
            'ch2_raw_layer': ch2_raw_dropdown.currentText(),
            'ch2_proc_layer': ch2_proc_dropdown.currentText(),
            'cell_mask_layer': cell_mask_dropdown.currentText(),
            'refinement_params_ch1': refinement_ch1,
            'refinement_params_ch2': refinement_ch2,
            'coloc_methods': selected_methods,
        })

    def _on_finished(results_df, mask1, mask2):
        progress_bar.setVisible(False)
        run_btn.setEnabled(True)

        if results_df.empty:
            napari_show_info("No cells found in the labeled mask.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        data_instance.data_repository['two_channel_coloc_df'] = results_df

        ui_instance.viewer.add_labels(
            mask1.astype(int), name="Channel 1 Refined Puncta Mask"
        )
        ui_instance.viewer.add_labels(
            mask2.astype(int), name="Channel 2 Refined Puncta Mask"
        )

        show_dataframes_dialog(
            "Two-Channel Condensate Colocalization",
            [("Per-Cell Results", results_df.round(4))]
        )

        napari_show_info(
            f"Colocalization complete: {len(results_df)} cells analyzed."
        )

    def _on_error(msg):
        progress_bar.setVisible(False)
        run_btn.setEnabled(True)
        napari_show_warning("Two-Channel Coloc error — see terminal for details.")
        print(f"[PyCAT TwoChannelColoc] ERROR:\n{msg}")

    run_btn.clicked.connect(_on_run)

    def _on_run_time():
        # Condensate coloc frame-by-frame over the selected stacks → per-frame
        # trace. Reuses the same dropdowns and refinement settings as _on_run.
        try:
            l_ch1_raw  = ui_instance.viewer.layers[ch1_raw_dropdown.currentText()]
            l_ch1_proc = ui_instance.viewer.layers[ch1_proc_dropdown.currentText()]
            l_ch2_raw  = ui_instance.viewer.layers[ch2_raw_dropdown.currentText()]
            l_ch2_proc = ui_instance.viewer.layers[ch2_proc_dropdown.currentText()]
            l_cell     = ui_instance.viewer.layers[cell_mask_dropdown.currentText()]
        except KeyError as e:
            napari_show_warning(f"Coloc over time: layer not found — {e}"); return

        from pycat.file_io.file_io import layer_is_stack
        if not (layer_is_stack(l_ch1_raw.data) or layer_is_stack(l_ch2_raw.data)):
            napari_show_warning(
                "Coloc over time needs a stack (time-series) — the selected "
                "channels look 2-D. Use the single-frame run button instead.")
            return

        data_instance = ui_instance.central_manager.active_data_class
        ball_radius = float(data_instance.data_repository.get('ball_radius', 50))
        mpx_sq = float(data_instance.data_repository.get('microns_per_pixel_sq', 1.0))
        refine1 = {k: s.value() for k, s in ch1_spins.items()}
        refine2 = {k: s.value() for k, s in ch2_spins.items()}
        methods = [name for name, cb in method_checks.items() if cb.isChecked()]
        if not methods:
            napari_show_warning("Select at least one colocalization metric."); return
        try:
            fi = ((data_instance.data_repository.get('file_metadata') or {}
                   ).get('common') or {}).get('frame_interval_s') or 1.0
        except Exception:
            fi = 1.0

        from pycat.toolbox.two_channel_coloc_tools import CondensateColocTimeWorker
        progress_bar.setVisible(True); progress_bar.setValue(0)
        shp = getattr(l_ch1_raw.data, 'shape', None)
        n_t = int(shp[0]) if (shp and len(shp) == 3) else 1
        progress_bar.setMaximum(max(1, n_t))
        run_btn.setEnabled(False); time_btn.setEnabled(False)

        worker = CondensateColocTimeWorker(dict(
            stack_ch1=l_ch1_raw.data, stack_proc_ch1=l_ch1_proc.data,
            stack_ch2=l_ch2_raw.data, stack_proc_ch2=l_ch2_proc.data,
            cell_mask_stack=l_cell.data, ball_radius=ball_radius,
            microns_per_pixel_sq=mpx_sq, refinement_params_ch1=refine1,
            refinement_params_ch2=refine2, coloc_methods=methods,
            frame_interval_s=float(fi), per_cell=True))
        # keep a reference so the thread isn't GC'd mid-run
        ui_instance._condensate_coloc_time_worker = worker

        def _on_prog(done, total):
            progress_bar.setValue(done)
        def _on_done(trace):
            progress_bar.setVisible(False)
            run_btn.setEnabled(True); time_btn.setEnabled(True)
            if trace is None or trace.empty:
                napari_show_info("Coloc over time: no frames produced a result.")
                return
            data_instance.data_repository['condensate_coloc_time_trace_df'] = trace
            try:
                ui_instance._record('condensate_coloc_time_trace', {
                    'methods': methods, 'n_frames': int(trace['frame'].nunique()),
                    'n_cells': int(trace['cell_label'].nunique()),
                    'frame_interval_s': float(fi), 'per_cell': True})
            except Exception:
                pass
            from pycat.toolbox.pixel_wise_corr_analysis_tools import (
                plot_per_cell_coloc_time_trace)
            def _jump(fr):
                try:
                    step = list(ui_instance.viewer.dims.current_step)
                    step[0] = int(fr)
                    ui_instance.viewer.dims.current_step = tuple(step)
                except Exception:
                    pass
            plot_per_cell_coloc_time_trace(
                trace, title="Per-cell condensate colocalization over time",
                on_pick_frame=_jump)
            try:
                show_dataframes_dialog("Per-Cell Condensate Coloc Over Time",
                                       [("Per (frame, cell)", trace.round(4))])
            except Exception:
                pass
            napari_show_info(
                f"Per-cell coloc over time: {trace['frame'].nunique()} frames × "
                f"{trace['cell_label'].nunique()} cells.")
        def _on_err(msg):
            progress_bar.setVisible(False)
            run_btn.setEnabled(True); time_btn.setEnabled(True)
            napari_show_warning("Coloc over time error — see terminal.")
            print(f"[PyCAT CondensateColocTime] ERROR:\n{msg}")

        worker.progress.connect(_on_prog)
        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        worker.start()

    time_btn.clicked.connect(_on_run_time)

    widget = QWidget()
    widget.setLayout(main_layout)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Two-Channel Condensate Colocalization"
    )
