"""Recorded-steps dialog — extracted from MenuManager (ui_decomposition Part 2).

The "Recorded Steps" dialog (lists the analysis steps recorded for batch replay, with their parameters)
lives here. ``MenuManager._show_recorded_steps_dialog`` is a thin wrapper that calls this; the menu action
(wired in batch_processor) and its label are unchanged. Moved VERBATIM.
"""
from __future__ import annotations

from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame



def _show_recorded_steps_dialog(self):
    """Show the batch workflow recorded so far.

    Top-level rows are the recorded steps (number, name, timestamp). Each
    step expands to reveal the layers/parameters it captured, so the user
    can review exactly what will be replayed.
    """
    from PyQt5.QtWidgets import (QDialog, QTreeWidget, QTreeWidgetItem,
                                  QHeaderView)
    from napari.utils.notifications import show_info as _info

    bp = getattr(self.central_manager, '_pycat_batch_processor', None)
    steps = (bp.config.get('steps', []) if bp and getattr(bp, 'config', None)
             else [])
    rec_on = bool(getattr(bp, 'recording_enabled', False)) if bp else False

    dialog = QDialog(self.viewer.window._qt_window)
    dialog.setWindowTitle("Recorded Batch Steps")
    dialog.resize(620, 640)
    layout = QVBoxLayout(dialog)

    status = ("<span style='color:#5cb85c;'>● Recording ON</span>" if rec_on
              else "<span style='color:#aaa;'>○ Recording off</span>")
    header = QLabel(f"<b>{len(steps)} step(s) recorded</b> &nbsp; {status}")
    layout.addWidget(header)

    if not steps:
        layout.addWidget(QLabel(
            "<span style='color:#aaa;'>No steps recorded yet. Turn on "
            "recording in the Batch dialog, then run your workflow.</span>"))

    tree = QTreeWidget()
    tree.setColumnCount(2)
    tree.setHeaderLabels(['Step', 'Value'])
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
    layout.addWidget(tree)

    # Parameter keys that are internal debugging snapshots — shown last and
    # de-emphasised rather than as primary parameters.
    _debug_keys = {'_active_layer_at_record', '_all_layers_at_record'}

    def _fmt(v):
        if v is None:
            return '—'
        if isinstance(v, (list, tuple)):
            return ', '.join(str(x) for x in v) if v else '(none)'
        if isinstance(v, float):
            return f"{v:.4g}"
        return str(v)

    from pycat.batch_step_registry import step_operations
    for i, step in enumerate(steps, 1):
        name = step.get('step', '?')
        ts = step.get('timestamp', '')
        params = step.get('params', {}) or {}
        top = QTreeWidgetItem([f"{i}.  {name}", ts])
        tree.addTopLevelItem(top)
        _ops = step_operations(name)   # the step's declared op composition — auditable replay in the UI
        if _ops:
            top.addChild(QTreeWidgetItem(["operations", ", ".join(_ops)]))
        # Primary params first, debug snapshots last.
        primary = [(k, v) for k, v in params.items() if k not in _debug_keys]
        debug   = [(k, v) for k, v in params.items() if k in _debug_keys]
        for k, v in primary:
            top.addChild(QTreeWidgetItem([str(k), _fmt(v)]))
        for k, v in debug:
            child = QTreeWidgetItem([f"{k}  (snapshot)", _fmt(v)])
            top.addChild(child)
    tree.expandToDepth(0)  # show steps collapsed; user expands to see params

    btn_row = QHBoxLayout()
    for _lbl, _fn in [("Expand all", tree.expandAll), ("Collapse all", tree.collapseAll)]:
        _b = QPushButton(_lbl); _b.clicked.connect(_fn); btn_row.addWidget(_b)
    btn_row.addStretch(1)
    _close = QPushButton("Close"); _close.clicked.connect(dialog.accept); btn_row.addWidget(_close)
    layout.addLayout(btn_row)

    dialog.exec_()
