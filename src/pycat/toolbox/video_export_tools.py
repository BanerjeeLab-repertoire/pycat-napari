"""
PyCAT Time-Series Video Export
================================
Exports a (T, H, W) image stack layer as an MP4 video, with a selectable
LUT/colormap applied for presentation-quality output.

Designed for the Time-Series Condensate Analysis pipeline, where a raw or
processed stack (potentially lazy/dask/zarr-backed) needs to become a
shareable video for talks, papers, and lab meetings.

Key design points
------------------
- Per-frame normalization uses global stack min/max (computed once) rather
  than per-frame auto-contrast, so intensity changes across time remain
  visually meaningful rather than being normalized away frame-by-frame.
- LUT application uses matplotlib's colormap registry, which has a much
  larger and more standard set of perceptually-aware colormaps than
  hand-rolling our own, and accepts the same names users see in napari's
  colormap dropdown (viridis, plasma, inferno, magma, gray, hot, etc.).
- Frames are processed and written one at a time (never holding the full
  RGB stack in memory at once), so this works safely even for stacks that
  were loaded lazily and are still backed by a dask/zarr array on disk.
- Uses imageio with the ffmpeg plugin, the same library family already
  used elsewhere in scientific Python tooling, avoiding a heavier
  dependency like OpenCV purely for video writing.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

from pathlib import Path
import numpy as np
import napari
from napari.utils.notifications import (
    show_info as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox, QFileDialog,
    QProgressBar, QCheckBox,
)
from PyQt5.QtCore import QThread, pyqtSignal


# ---------------------------------------------------------------------------
# Available colormaps — curated subset matching napari's common LUTs,
# grouped roughly by use case for the dropdown.
# ---------------------------------------------------------------------------

AVAILABLE_COLORMAPS = [
    "gray", "viridis", "plasma", "inferno", "magma", "cividis",
    "green", "red", "blue", "magenta", "cyan", "yellow",
    "hot", "cool", "turbo", "jet",
]


# ---------------------------------------------------------------------------
# Pure export function
# ---------------------------------------------------------------------------

def export_stack_as_mp4(
    stack,
    output_path: Path,
    colormap: str = "viridis",
    fps: int = 10,
    contrast_limits: tuple = None,
    quality: int = 7,
    progress_callback=None,
):
    """
    Export a (T, H, W) image stack as an MP4 video with a LUT applied.

    Parameters
    ----------
    stack : array-like, shape (T, H, W)
        Can be a numpy array or any lazy array supporting integer indexing
        that returns a 2D (H, W) frame (e.g. the _ZarrTYX lazy IMS loader,
        a dask array, or a plain ndarray) — only one frame is ever held in
        memory at a time during export.
    output_path : Path
        Destination .mp4 file path.
    colormap : str
        Name of a matplotlib colormap (see AVAILABLE_COLORMAPS for the
        curated list shown in the UI, though any valid matplotlib name works).
    fps : int
        Playback frame rate of the output video.
    contrast_limits : tuple (min, max), optional
        Intensity range used to normalize all frames. If None, computed
        from the first and last frames as a fast approximation of global
        min/max (full-stack min/max would require reading every frame
        twice for lazy arrays, which is expensive for large stacks).
    quality : int
        imageio-ffmpeg quality parameter, 0 (worst) to 10 (best).
    progress_callback : callable(frame_idx, total_frames) or None

    Returns
    -------
    Path — the output file path, for confirmation.
    """
    import imageio.v3 as iio
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors

    n_frames = stack.shape[0]

    # Determine contrast limits if not provided — sample a few frames
    # rather than the whole stack to keep this fast for large lazy stacks.
    if contrast_limits is None:
        sample_indices = sorted(set([0, n_frames // 4, n_frames // 2,
                                      3 * n_frames // 4, n_frames - 1]))
        sample_indices = [i for i in sample_indices if 0 <= i < n_frames]
        samples = [np.asarray(stack[i]) for i in sample_indices]
        global_min = float(min(s.min() for s in samples))
        global_max = float(max(s.max() for s in samples))
    else:
        global_min, global_max = contrast_limits

    if global_max <= global_min:
        global_max = global_min + 1.0  # avoid divide-by-zero on flat stacks

    cmap = cm.get_cmap(colormap)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with iio.imopen(str(output_path), "w", plugin="pyav") as writer:
        writer.init_video_stream("libx264", fps=fps)

        for t in range(n_frames):
            frame = np.asarray(stack[t]).astype(np.float32)
            normalized = np.clip((frame - global_min) / (global_max - global_min), 0, 1)
            rgba = cmap(normalized)  # (H, W, 4) float in [0,1]
            rgb = (rgba[..., :3] * 255).astype(np.uint8)
            writer.write_frame(rgb)

            if progress_callback is not None:
                progress_callback(t + 1, n_frames)

    return output_path


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class VideoExportWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, kwargs: dict, parent=None):
        super().__init__(parent)
        self._kwargs = kwargs

    def run(self):
        try:
            def _cb(i, total):
                self.progress.emit(i, total)
            result_path = export_stack_as_mp4(progress_callback=_cb, **self._kwargs)
            self.finished.emit(result_path)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

def _add_export_timeseries_video(ui_instance, layout=None, separate_widget=False):
    """
    Widget for applying a LUT to a time-series stack layer and exporting it
    as an MP4 video — for presentations, talks, and lab meetings.

    Sits in the Time-Series Condensate Analysis pipeline alongside the
    other stack tools. Works directly on whatever stack layer is selected,
    whether it's the raw stack, a lazy-preprocessed stack, or the
    TimeSeries Condensate Masks output layer.
    """
    main_layout = QVBoxLayout()
    ui_instance.add_text_label(main_layout, 'Export Time-Series Video', bold=True)
    ui_instance.add_text_label(
        main_layout,
        'Apply a colormap (LUT) and export the stack as an MP4 video for presentations.',
        font_size=9
    )

    stack_dropdown = ui_instance.create_layer_dropdown(napari.layers.Image)
    main_layout.addWidget(stack_dropdown)

    settings_group = QGroupBox("Export Settings")
    form = QFormLayout(settings_group)
    form.setContentsMargins(9, 20, 9, 6)

    colormap_dropdown = QComboBox()
    colormap_dropdown.addItems(AVAILABLE_COLORMAPS)
    colormap_dropdown.setCurrentText("viridis")
    form.addRow("Colormap (LUT):", colormap_dropdown)

    fps_spin = QSpinBox()
    fps_spin.setRange(1, 60)
    fps_spin.setValue(10)
    fps_spin.setToolTip("Playback speed of the output video, in frames per second.")
    form.addRow("Frame rate (fps):", fps_spin)

    quality_spin = QSpinBox()
    quality_spin.setRange(0, 10)
    quality_spin.setValue(7)
    quality_spin.setToolTip("Video encoding quality, 0 (smallest file) to 10 (best quality).")
    form.addRow("Quality (0-10):", quality_spin)

    auto_contrast_check = QCheckBox("Auto contrast (sample frames for min/max)")
    auto_contrast_check.setChecked(True)
    form.addRow("", auto_contrast_check)

    contrast_min_spin = QDoubleSpinBox()
    contrast_min_spin.setRange(-1e9, 1e9)
    contrast_min_spin.setDecimals(4)
    contrast_min_spin.setEnabled(False)
    contrast_max_spin = QDoubleSpinBox()
    contrast_max_spin.setRange(-1e9, 1e9)
    contrast_max_spin.setDecimals(4)
    contrast_max_spin.setValue(1.0)
    contrast_max_spin.setEnabled(False)
    form.addRow("Manual min:", contrast_min_spin)
    form.addRow("Manual max:", contrast_max_spin)

    def _on_auto_toggle(checked):
        contrast_min_spin.setEnabled(not checked)
        contrast_max_spin.setEnabled(not checked)
    auto_contrast_check.stateChanged.connect(lambda s: _on_auto_toggle(bool(s)))

    main_layout.addWidget(settings_group)

    progress_bar = QProgressBar()
    progress_bar.setVisible(False)
    main_layout.addWidget(progress_bar)

    export_btn = QPushButton("🎬  Export as MP4")
    main_layout.addWidget(export_btn)

    def _on_export():
        layer_name = stack_dropdown.currentText()
        try:
            layer = ui_instance.viewer.layers[layer_name]
        except KeyError:
            napari_show_warning(f"Layer '{layer_name}' not found.")
            return

        stack_data = layer.data
        if stack_data.ndim != 3:
            napari_show_warning("Video export requires a 3D (T, H, W) stack layer.")
            return

        default_name = f"{layer_name.replace(' ', '_')}.mp4"
        save_path, _ = QFileDialog.getSaveFileName(
            None, "Export Video As", default_name, "MP4 Video (*.mp4)"
        )
        if not save_path:
            return
        if not save_path.lower().endswith(".mp4"):
            save_path += ".mp4"

        contrast_limits = None
        if not auto_contrast_check.isChecked():
            contrast_limits = (contrast_min_spin.value(), contrast_max_spin.value())

        kwargs = dict(
            stack=stack_data,
            output_path=Path(save_path),
            colormap=colormap_dropdown.currentText(),
            fps=fps_spin.value(),
            contrast_limits=contrast_limits,
            quality=quality_spin.value(),
        )

        n_frames = stack_data.shape[0]
        progress_bar.setMaximum(n_frames)
        progress_bar.setValue(0)
        progress_bar.setVisible(True)
        export_btn.setEnabled(False)

        worker = VideoExportWorker(kwargs)
        ui_instance._video_export_worker = worker  # keep alive

        worker.progress.connect(lambda i, t: progress_bar.setValue(i))
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.start()

        ui_instance._record('export_timeseries_video', {
            'stack_layer': layer_name,
            'colormap': colormap_dropdown.currentText(),
            'fps': fps_spin.value(),
            'quality': quality_spin.value(),
            'auto_contrast': auto_contrast_check.isChecked(),
            'contrast_limits': contrast_limits,
            'output_path': save_path,
        })

    def _on_finished(result_path):
        progress_bar.setVisible(False)
        export_btn.setEnabled(True)
        napari_show_info(f"Video exported: {result_path}")

    def _on_error(msg):
        progress_bar.setVisible(False)
        export_btn.setEnabled(True)
        napari_show_warning("Video export error — see terminal for details.")
        print(f"[PyCAT VideoExport] ERROR:\n{msg}")

    export_btn.clicked.connect(_on_export)

    widget = QWidget()
    widget.setLayout(main_layout)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Export Time-Series Video"
    )
