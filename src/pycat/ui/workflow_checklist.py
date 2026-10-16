"""
PyCAT Workflow Checklist
=========================
A persistent bottom-bar dock that shows the ordered steps for the active
analysis pipeline as a compact horizontal row of numbered step indicators.

Each step is a small numbered circle:
  ✓  green   = completed
  →  amber   = current / next to do  (bold ring)
  ○  grey    = not yet reached

The bar lives in napari's bottom dock area so it is always visible
without competing with the right-side analysis docks. Hovering over any
step circle shows the full step name in a tooltip. A ▾ button expands
a floating step list for pipelines with many steps.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations
import re
from typing import Optional

from qtpy.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QScrollArea, QToolButton, QCheckBox,
)
from qtpy.QtCore import Qt, Signal, QObject, QPoint
from qtpy.QtGui import QFont


# The pipeline step tables are plain data and live in pipeline_steps (importable without Qt).
from pycat.ui.pipeline_steps import (  # noqa: F401  (re-exported)
    CONDENSATE_PIPELINE,
    TIMESERIES_PIPELINE,
    COLOC_PIPELINE,
    CELLULAR_BF_PIPELINE,
    INVITRO_FLUOR_PIPELINE,
    TIMESERIES_INVITRO_FLUOR_PIPELINE,
    INVITRO_BF_PIPELINE,
    FIBRIL_PIPELINE,
    ZSTACK_PIPELINE,
    VPT_PIPELINE,
    FRAP_PIPELINE,
    FUSION_PIPELINE,
    TEMPERATURE_PIPELINE,
    FD_CURVE_PIPELINE,
    PIPELINE_DEFS,
    PIPELINE_DISPLAY_NAMES,
)


# ---------------------------------------------------------------------------
# Step pill widget
# ---------------------------------------------------------------------------

class _StepPill(QPushButton):
    """
    A small circular button showing a step number.
    Visual state changes between unchecked, current, and checked.
    Hovering shows the full step label as a tooltip.
    """
    _BASE = (
        "QPushButton {"
        "  border-radius: 11px;"
        "  min-width: 22px; max-width: 22px;"
        "  min-height: 22px; max-height: 22px;"
        "  font-size: 8pt; font-weight: bold;"
        "  padding: 0px; border: 2px solid;"
        "}"
    )
    _DONE     = _BASE + "QPushButton { background:#2d6a2d; color:#b8f0b8; border-color:#5cb85c; }"
    _CURRENT  = _BASE + "QPushButton { background:#7a4800; color:#ffe0a0; border-color:#f0a500; }"
    _FUTURE   = _BASE + "QPushButton { background:#3a3a3a; color:#888; border-color:#555; }"
    # Required-but-not-yet-done and available (previous step complete): red,
    # matching the red/yellow/green/blue status logic of the workflow boxes.
    _REQUIRED = _BASE + "QPushButton { background:#6a2d2d; color:#f0b8b8; border-color:#b85c5c; }"
    # Optional step that has been done/used: blue.
    _OPTIONAL = _BASE + "QPushButton { background:#2d4a6a; color:#b8d8f0; border-color:#5c8cb8; }"

    def __init__(self, number: str, label: str, parent=None):
        super().__init__(str(number), parent)
        self._number = number
        self._label  = label
        # A step is optional if its label is tagged [opt] / [optional].
        self._optional = ('[opt]' in label.lower())
        self.setToolTip(label)
        self.setFlat(True)
        self.setCursor(Qt.ArrowCursor)
        self.set_future()

    def set_done(self):
        # Optional steps that have been used turn blue; required ones green.
        self.setStyleSheet(self._OPTIONAL if self._optional else self._DONE)

    def set_current(self):
        self.setStyleSheet(self._CURRENT)

    def set_required(self):
        self.setStyleSheet(self._REQUIRED)

    def set_future(self):
        self.setStyleSheet(self._FUTURE)


# ---------------------------------------------------------------------------
# Checklist widget — compact horizontal bottom bar
# ---------------------------------------------------------------------------

class WorkflowChecklist(QWidget):
    """
    Horizontal step-indicator bar. Lives in napari's bottom dock so it never
    overlaps the right-side analysis panels.

    Layout:
      [Pipeline name]  [●1] [●2] [→3] [○4] [○5]…  [↺]  [▾]
    """

    def __init__(self, pipeline_name: str, parent=None):
        super().__init__(parent)
        self._pipeline_name = pipeline_name
        self._steps         = PIPELINE_DEFS.get(pipeline_name, [])
        self._pills: list[_StepPill] = []
        self._checkboxes: list[QCheckBox] = []   # kept for compat with mark_step
        self._step_map: dict[str, int] = {}       # key → step index
        self._done: list[bool] = [False] * len(self._steps)
        self._detail_visible  = False
        self._detail_widget   = None
        self._setup_ui()

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 2, 6, 2)
        root.setSpacing(4)

        # Pipeline label
        display_name = PIPELINE_DISPLAY_NAMES.get(
            self._pipeline_name,
            self._pipeline_name.replace('_', ' ').title())
        lbl = QLabel(f"<b>{display_name}:</b>")
        lbl.setStyleSheet("font-size:9pt; color:#aaa; padding-right:4px;")
        root.addWidget(lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # Step pills
        self._pills_widget = QWidget()
        self._pills_layout = QHBoxLayout(self._pills_widget)
        self._pills_layout.setContentsMargins(0, 0, 0, 0)
        self._pills_layout.setSpacing(3)

        for i, (key, label) in enumerate(self._steps):
            # Extract the step number/letter prefix from the label text
            # e.g. '4b. Auto-crop ROI' → '4b', '10. Spatial Metrology' → '10'
            # so the pill always matches the number written in the dock widget.
            m = re.match(r'(\d+[a-z]?)\.', label.strip())
            pill_num = m.group(1) if m else str(i + 1)
            pill = _StepPill(pill_num, label)
            self._pills.append(pill)
            self._pills_layout.addWidget(pill)
            if key and key not in self._step_map:
                self._step_map[key] = i

        root.addWidget(self._pills_widget)
        root.addStretch(1)

        # Reset button
        reset_btn = QPushButton("↺")
        reset_btn.setToolTip("Reset — uncheck all steps for the next file")
        reset_btn.setFixedSize(22, 22)
        reset_btn.setStyleSheet(
            "QPushButton { border-radius:11px; background:#444; color:#ccc;"
            "  border:1px solid #666; font-size:10pt; }"
            "QPushButton:hover { background:#666; }")
        reset_btn.clicked.connect(self.reset)
        root.addWidget(reset_btn)

        # Expand / detail toggle
        self._expand_btn = QPushButton("▾")
        self._expand_btn.setToolTip("Show full step list")
        self._expand_btn.setFixedSize(22, 22)
        self._expand_btn.setStyleSheet(
            "QPushButton { border-radius:11px; background:#444; color:#ccc;"
            "  border:1px solid #666; font-size:10pt; }"
            "QPushButton:hover { background:#666; }")
        self._expand_btn.setCheckable(True)
        self._expand_btn.clicked.connect(self._toggle_detail)
        root.addWidget(self._expand_btn)

        self._refresh_pills()

    # ── Detail popup ───────────────────────────────────────────────────

    def _toggle_detail(self, checked: bool):
        self._detail_visible = checked
        self._expand_btn.setText("▴" if checked else "▾")
        if checked:
            self._show_detail()
        else:
            self._hide_detail()

    def _show_detail(self):
        if self._detail_widget is None:
            self._detail_widget = QWidget(self.window(), Qt.ToolTip)
            self._detail_widget.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
            self._detail_widget.setAttribute(Qt.WA_ShowWithoutActivating)
            vl = QVBoxLayout(self._detail_widget)
            vl.setContentsMargins(8, 6, 8, 6)
            vl.setSpacing(2)
            self._detail_labels = []
            for i, (_, label) in enumerate(self._steps):
                lbl = QLabel(label)
                lbl.setStyleSheet("font-size:9pt;")
                vl.addWidget(lbl)
                self._detail_labels.append(lbl)
            self._detail_widget.setStyleSheet(
                "background:#2a2a2a; border:1px solid #555; border-radius:4px;")

        self._update_detail_labels()
        # Position above the expand button
        btn_pos = self._expand_btn.mapToGlobal(QPoint(0, 0))
        self._detail_widget.adjustSize()
        w = self._detail_widget
        x = max(0, btn_pos.x() - w.width() + self._expand_btn.width())
        y = btn_pos.y() - w.height() - 4
        w.move(x, y)
        w.show()

    def _hide_detail(self):
        if self._detail_widget is not None:
            self._detail_widget.hide()

    def _update_detail_labels(self):
        if self._detail_widget is None:
            return
        for i, lbl in enumerate(self._detail_labels):
            if self._done[i]:
                lbl.setStyleSheet("font-size:9pt; color:#5cb85c; text-decoration:line-through;")
            else:
                # Highlight the current REQUIRED step (optional steps don't gate).
                current = next(
                    (j for j, d in enumerate(self._done)
                     if not d and not getattr(self._pills[j], '_optional', False)),
                    None)
                if i == current:
                    lbl.setStyleSheet("font-size:9pt; color:#f0a500; font-weight:bold;")
                else:
                    lbl.setStyleSheet("font-size:9pt; color:#888;")

    # ── Pill refresh ───────────────────────────────────────────────────

    def _refresh_pills(self):
        # Progress advances along the REQUIRED steps only. The "current" (red)
        # marker is the first incomplete *required* step — optional steps in
        # between are skipped and do not gate progress. Optional steps carry
        # their own state independently: blue if done/used, grey if untouched.
        # A required step becomes available (not grey) once all *required* steps
        # before it are done, regardless of any optional steps in between.
        def _is_opt(i):
            return getattr(self._pills[i], '_optional', False)

        # index of the first incomplete required step (the active required step)
        current_req = next(
            (i for i, d in enumerate(self._done) if not d and not _is_opt(i)),
            None)
        # have all required steps up to index i been completed?
        def _required_prefix_done(i):
            return all(self._done[j] for j in range(i) if not _is_opt(j))

        for i, pill in enumerate(self._pills):
            if self._done[i]:
                pill.set_done()                     # green (required) / blue (optional)
            elif _is_opt(i):
                # Optional & not yet used. Available (neutral) once the required
                # chain has reached it; otherwise locked grey. Never red, never
                # gates the required progression.
                pill.set_future()
            elif i == current_req:
                pill.set_required()                 # next required step → red
            else:
                pill.set_future()                   # later required step → grey (locked)
        if self._detail_visible:
            self._update_detail_labels()

    # ── Public step management ─────────────────────────────────────────

    def mark_step(self, step_key: str):
        idx = self._step_map.get(step_key)
        if idx is not None and not self._done[idx]:
            self._done[idx] = True
            self._refresh_pills()

    def on_step_recorded(self, step_key: str):
        self.mark_step(step_key)

    def reset(self):
        self._done = [False] * len(self._steps)
        self._refresh_pills()

    # ── Legacy compat (CondensateAnalysisUI still calls these) ─────────

    def activate(self, _pipeline_name: str):
        pass   # handled by WorkflowChecklistManager.activate()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class WorkflowChecklistManager(QObject):

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self._viewer  = viewer
        self._widget: Optional[WorkflowChecklist] = None
        self._dock    = None

    def activate(self, pipeline_name: str):
        if pipeline_name not in PIPELINE_DEFS:
            return

        if self._dock is not None:
            try:
                self._viewer.window.remove_dock_widget(self._dock)
            except Exception:
                pass
            self._dock   = None
            self._widget = None

        self._widget = WorkflowChecklist(pipeline_name)
        self._widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._widget.setMaximumHeight(34)

        self._dock = self._viewer.window.add_dock_widget(
            self._widget,
            name="Workflow",
            area='bottom',
        )
        # Keep it thin
        try:
            self._dock.setMaximumHeight(38)
        except Exception:
            pass

        # If an image is already loaded (the user opened a file, then opened the
        # workflow), mark the file-I/O step complete so Step 1 isn't misleadingly
        # shown as pending.
        try:
            import napari.layers as _nl
            if any(isinstance(l, _nl.Image) for l in self._viewer.layers):
                self._widget.mark_step('open_image')
        except Exception:
            pass

    def on_step_recorded(self, step_key: str):
        if self._widget is not None:
            self._widget.mark_step(step_key)

    def reset(self):
        if self._widget is not None:
            self._widget.reset()
