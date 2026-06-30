"""
PyCAT Workflow Checklist
=========================
A persistent dock widget that shows the ordered steps for the active
analysis pipeline, auto-checks each step when it is recorded to the
batch processor, and highlights the next pending step so users always
know where they are in the workflow.

Design
------
- One checklist definition per pipeline (Condensate Analysis, Time-Series,
  Colocalization, etc.).  The active pipeline registers its checklist when
  setup_ui() runs.
- Steps auto-check by listening to BatchProcessor.record() via a Qt signal
  that central_manager emits after every bp.record() call.
- Users can also manually check/uncheck any step.
- A "Reset" button unchecks all steps to start a new file in the same
  session without switching analysis mode.
- The checklist is displayed in a collapsible dock so it doesn't consume
  screen space when not needed.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations
from typing import Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QColor


# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------
# Each pipeline is a list of (step_key, display_label) tuples.
# step_key matches the string passed to bp.record(), or '' for manual-only steps.

CONDENSATE_PIPELINE = [
    ('open_image',              '1.  Open image(s)'),
    ('measure_line',            '2.  Measure lines (cell & object diameter)'),
    ('upscaling',               '3.  Upscale image(s)  [optional]'),
    ('preprocessing',           '4.  Pre-process image'),
    ('background_removal',      '5.  Background removal'),
    ('cellpose_segmentation',   '6.  Cellpose cell segmentation'),
    ('cell_analysis',           '7.  Cell Analyzer'),
    ('condensate_segmentation', '8.  Condensate segmentation'),
    ('condensate_analysis',     '9.  Condensate analysis'),
    ('save_and_clear',          '10. Save & Clear'),
]

TIMESERIES_PIPELINE = [
    ('open_stack',              '1.  Open IMS / image stack'),
    ('',                        '2.  Extract reference frame'),
    ('measure_line',            '3.  Measure lines (cell & object diameter)'),
    ('lazy_preprocess_stack',   '4.  Preprocess stack (zarr-backed)'),
    ('ts_cellpose_keyframe',    '5.  Keyframe Cellpose segmentation'),
    ('cell_analysis',           '6.  Cell Analyzer'),
    ('timeseries_condensate',   '7.  Time-Series Condensate Analysis'),
    ('export_timeseries_video', '8.  Export video  [optional]'),
    ('save_and_clear',          '9.  Save & Clear'),
]

COLOC_PIPELINE = [
    ('open_image',              '1.  Open multi-channel image(s)'),
    ('measure_line',            '2.  Measure lines'),
    ('upscaling',               '3.  Upscale  [optional]'),
    ('preprocessing',           '4.  Pre-process'),
    ('background_removal',      '5.  Background removal'),
    ('cellpose_segmentation',   '6.  Cellpose cell segmentation'),
    ('cell_analysis',           '7.  Cell Analyzer'),
    ('condensate_segmentation', '8.  Condensate segmentation (Ch 1)'),
    ('condensate_analysis',     '9.  Condensate analysis (Ch 1)'),
    ('two_channel_condensate_coloc', '10. Two-Channel Colocalization'),
    ('save_and_clear',          '11. Save & Clear'),
]

PIPELINE_DEFS = {
    'condensate':   CONDENSATE_PIPELINE,
    'timeseries':   TIMESERIES_PIPELINE,
    'coloc':        COLOC_PIPELINE,
}


# ---------------------------------------------------------------------------
# Checklist widget
# ---------------------------------------------------------------------------

class WorkflowChecklist(QWidget):
    """
    Persistent dock widget displaying the active pipeline's steps as
    checkboxes.  Auto-checks steps when bp.record() fires.
    """

    def __init__(self, pipeline_name: str, parent=None):
        super().__init__(parent)
        self._pipeline_name = pipeline_name
        self._steps         = PIPELINE_DEFS.get(pipeline_name, [])
        self._checkboxes: list[QCheckBox] = []
        self._step_map: dict[str, QCheckBox] = {}   # key → checkbox
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # Header
        header = QLabel(f"Workflow: {self._pipeline_name.title()}")
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        header.setFont(font)
        root.addWidget(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # Steps
        for key, label in self._steps:
            cb = QCheckBox(label)
            cb.setStyleSheet(self._style_unchecked())
            cb.stateChanged.connect(lambda _, c=cb: self._on_manual_toggle(c))
            self._checkboxes.append(cb)
            if key:
                # Multiple steps can share a key (e.g. both 'open_image'
                # and 'open_stack' map to step 1 depending on pipeline)
                if key not in self._step_map:
                    self._step_map[key] = cb
            root.addWidget(cb)

        root.addStretch()

        # Buttons row
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("↺  Reset")
        reset_btn.setToolTip("Uncheck all steps to start a new file")
        reset_btn.clicked.connect(self.reset)
        reset_btn.setMaximumWidth(90)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._highlight_next()

    # ── Styling ────────────────────────────────────────────────────────

    @staticmethod
    def _style_unchecked() -> str:
        return "QCheckBox { color: palette(text); } "

    @staticmethod
    def _style_checked() -> str:
        return "QCheckBox { color: #5cb85c; text-decoration: line-through; }"

    @staticmethod
    def _style_next() -> str:
        return ("QCheckBox { color: #f0a500; font-weight: bold; } "
                "QCheckBox::indicator { border: 2px solid #f0a500; border-radius: 3px; }")

    # ── Step management ────────────────────────────────────────────────

    def mark_step(self, step_key: str):
        """Called when bp.record() fires with step_key."""
        cb = self._step_map.get(step_key)
        if cb and not cb.isChecked():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
            cb.setStyleSheet(self._style_checked())
            self._highlight_next()

    def _on_manual_toggle(self, cb: QCheckBox):
        if cb.isChecked():
            cb.setStyleSheet(self._style_checked())
        else:
            cb.setStyleSheet(self._style_unchecked())
        self._highlight_next()

    def _highlight_next(self):
        """Highlight the first unchecked step in amber."""
        found_next = False
        for cb in self._checkboxes:
            if not cb.isChecked():
                if not found_next:
                    cb.setStyleSheet(self._style_next())
                    found_next = True
                else:
                    # Only reset style if it's currently the 'next' style
                    if cb.styleSheet() == self._style_next():
                        cb.setStyleSheet(self._style_unchecked())
            # Checked steps keep their style

    def reset(self):
        """Uncheck all steps."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
            cb.setStyleSheet(self._style_unchecked())
        self._highlight_next()


# ---------------------------------------------------------------------------
# Manager — one instance lives on CentralManager
# ---------------------------------------------------------------------------

class WorkflowChecklistManager(QObject):
    """
    Manages the lifecycle of WorkflowChecklist widgets.
    CentralManager holds one instance and calls .activate(pipeline_name)
    when the user switches analysis mode, and .on_step_recorded(key) from
    the bp.record() hook.
    """

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self._viewer   = viewer
        self._widget:  Optional[WorkflowChecklist] = None
        self._dock     = None

    def activate(self, pipeline_name: str):
        """
        Show (or replace) the checklist for the given pipeline.
        Called from CondensateAnalysisUI.setup_ui(), TimeSeriesCondensateUI.setup_ui(), etc.
        """
        if pipeline_name not in PIPELINE_DEFS:
            return

        # Remove existing dock if present
        if self._dock is not None:
            try:
                self._viewer.window.remove_dock_widget(self._dock)
            except Exception:
                pass
            self._dock   = None
            self._widget = None

        self._widget = WorkflowChecklist(pipeline_name)
        self._widget.setSizePolicy(
            QWidget.sizeHint(self._widget).width(),
            QWidget.sizeHint(self._widget).height(),
        )

        self._dock = self._viewer.window.add_dock_widget(
            self._widget,
            name=f"Workflow Checklist",
            area='right',
        )

    def on_step_recorded(self, step_key: str):
        """Hook called after every bp.record() — auto-checks the matching step."""
        if self._widget is not None:
            self._widget.mark_step(step_key)

    def reset(self):
        if self._widget is not None:
            self._widget.reset()
