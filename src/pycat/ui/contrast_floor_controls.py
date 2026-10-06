"""The minimum-contrast-floor control for condensate segmentation: a checkbox and a live slider.

Segmentation measures every object's local CNR once (`segmentation.contrast_floor`); moving the slider
only re-filters through a label -> keep lookup, so the overlay follows the slider in real time. Objects
above the floor stay in "Total Refined Puncta Mask"; those below move to a muted layer, still visible,
so the user sees WHICH shapes are gained or lost, not just a count.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from pycat.toolbox.segmentation.contrast_floor import CONTRAST_FLOOR_DEFAULT

_STEP = 0.05          # CNR per slider tick
_MAX_CNR = 8.0
REFINED_LAYER = "Total Refined Puncta Mask"


class ContrastFloorControls(QWidget):
    """``data_getter()`` returns the active data instance; ``on_change(on, floor)`` is told the settled
    value (used to keep the recorded batch parameters in step with the slider)."""

    def __init__(self, viewer, data_getter, on_change=None, parent=None):
        super().__init__(parent)
        self._viewer, self._data_getter, self._on_change = viewer, data_getter, on_change
        self.apply_cb = QCheckBox("Apply minimum contrast floor")
        self.apply_cb.setChecked(True)
        self.apply_cb.setToolTip(
            "Objects whose local contrast-to-noise ratio (contrast above the surrounding background, in "
            "units of its noise) is below the floor are moved to a red 'Below Contrast Floor' layer and "
            "flagged in the condensate table (below_contrast_floor, with their local_cnr) -- not deleted. "
            "Contrast only: no shape or size term. Default 1.75 (calibrated on Meet's diffuse cells; "
            "removes most of what he rejected and ~1 in 10 condensates both annotators traced).")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(round(_MAX_CNR / _STEP)))
        self.slider.setValue(int(round(CONTRAST_FLOOR_DEFAULT / _STEP)))
        self.value_lbl = QLabel()
        self.count_lbl = QLabel("Run segmentation to see kept / below-floor counts.")
        row = QHBoxLayout(); row.addWidget(self.slider); row.addWidget(self.value_lbl)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.apply_cb); lay.addLayout(row); lay.addWidget(self.count_lbl)
        # Debounce: a fast drag coalesces into one re-filter instead of queueing redraws.
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(30)
        self._timer.timeout.connect(self.refilter)
        self.slider.valueChanged.connect(self._changed)
        self.apply_cb.toggled.connect(self._changed)
        self._show_value()

    def floor(self):
        """The floor; the slider's minimum is NO floor, so it reproduces the unfiltered result exactly."""
        return -float('inf') if self.slider.value() == 0 else self.slider.value() * _STEP

    def on(self):
        return self.apply_cb.isChecked()

    def _show_value(self):
        self.value_lbl.setText("off" if self.slider.value() == 0 else f"CNR {self.floor():.2f}")
        self.slider.setEnabled(self.on())

    def _changed(self, *_):
        self._show_value()
        self._timer.start()

    def refilter(self):
        """Apply the current floor to the stored objects and update both layers and the count."""
        from pycat.toolbox.segmentation.contrast_floor import apply_floor, floor_counts
        from pycat.toolbox.segmentation.subcellular import show_below_floor
        data = self._data_getter()
        above, below = apply_floor(data, self.floor(), self.on()) if data is not None else (None, None)
        if above is None:
            return
        if REFINED_LAYER in self._viewer.layers:
            self._viewer.layers[REFINED_LAYER].data = above
        show_below_floor(self._viewer, below)
        n_above, n_below = floor_counts(data, self.floor(), self.on())
        self.count_lbl.setText(f"Kept {n_above}  ·  below floor {n_below}")
        if self._on_change is not None:
            self._on_change(self.on(), self.floor())
