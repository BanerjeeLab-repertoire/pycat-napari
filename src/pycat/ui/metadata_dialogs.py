"""Metadata dialog feature — extracted from MenuManager (ui_decomposition Part 2).

The "Show Metadata" dialog (per-file acquisition metadata, contradiction panel, CSV export) lives here.
``MenuManager._show_metadata_dialog`` is a thin wrapper that calls ``_show_metadata_dialog(self)``, so the
menu action and its label are unchanged. Moved VERBATIM.
"""
from __future__ import annotations

import napari
from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame
from PyQt5.QtCore import Qt, QObject


def _show_metadata_dialog(self):
    """Show acquisition metadata for the loaded file.

    Displays the curated 'common' fields by default, with a checkbox that
    reveals the full raw metadata dump. Also offers a JSON export button.
    """
    from PyQt5.QtWidgets import (QDialog, QTableWidget, QTableWidgetItem, QHeaderView,
                                  QFileDialog)
    from napari.utils.notifications import (show_info as _info,
                                            show_warning as _warn)
    dr = self.central_manager.active_data_class.data_repository
    md = dr.get('file_metadata')
    if not md or not isinstance(md, dict):
        _warn("No metadata available — open an image first.")
        return

    common = md.get('common', {}) or {}
    raw = md.get('raw', {}) or {}

    dialog = QDialog(self.viewer.window._qt_window)
    dialog.setWindowTitle("File Metadata")
    dialog.resize(560, 620)
    layout = QVBoxLayout(dialog)

    fname = common.get('file_name') or 'Unknown file'
    header = QLabel(f"<b>{fname}</b>")
    layout.addWidget(header)
    try:      # tag_confidence Part 3: contradictions listed first, with the reversible 'expected' control
        from pycat.ui.metadata_contradiction_panel import build_contradiction_panel
        _cp = build_contradiction_panel(md)
        if _cp is not None:
            layout.addWidget(_cp)
    except Exception:  # broad-ok: ui_cleanup — the contradiction panel must never break the metadata dialog
        pass

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(['Field', 'Value'])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    layout.addWidget(table)

    # Curated-field display order and labels.
    _labels = [
        ('file_type', 'File type'),
        ('dimensions', 'Dimensions (T,C,Z,Y,X)'),
        ('pixel_size_um', 'Pixel size (µm/px)'),
        ('pixel_size_source', 'Pixel size source'),
        ('bit_depth', 'Bit depth'),
        ('n_channels', 'Channels'),
        ('n_timepoints', 'Timepoints'),
        ('n_z', 'Z slices'),
        ('objective', 'Objective'),
        ('numerical_aperture', 'Numerical aperture'),
        ('modality', 'Modality'),
        ('excitation_nm', 'Excitation (nm)'),
        ('emission_nm', 'Emission (nm)'),
        ('acquisition_date', 'Acquisition date'),
        ('software', 'Software'),
        ('camera_name', 'Camera'),
        ('exposure_s', 'Exposure (s)'),
        ('frame_interval_s', 'Frame interval (s)'),
        ('frame_interval_source', 'Frame interval source'),
        ('z_step_um', 'Z step (µm)'),
    ]

    def _fmt(v):
        if v is None:
            return '—'
        if isinstance(v, dict):
            return ', '.join(f"{k.upper()}={v.get(k)}" for k in ('t', 'c', 'z', 'y', 'x')
                             if v.get(k) is not None)
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)

    def _fmt_interval(c):
        """Frame interval with IQR appended when measured per-frame."""
        fi = c.get('frame_interval_s')
        if fi is None:
            return '—'
        txt = f"{float(fi):.6g}"
        iqr = c.get('frame_interval_iqr_s')
        if iqr is not None:
            txt += f"  (IQR {float(iqr):.4g})"
        return txt

    def _populate(show_raw):
        rows = []
        for key, lbl in _labels:
            if key == 'frame_interval_s':
                rows.append((lbl, _fmt_interval(common)))
            else:
                rows.append((lbl, _fmt(common.get(key))))
        if show_raw:
            # Full per-frame timing (the measured deltas) live in the
            # expanded view so the curated panel stays compact.
            deltas = common.get('frame_deltas_s')
            if deltas:
                rows.append(('— frame timing (measured) —', ''))
                rows.append(('n frames', _fmt(common.get('n_frames'))))
                rows.append(('acquisition start', _fmt(common.get('acquisition_start_time'))))
                rows.append(('frame deltas (s)',
                             ', '.join(f"{float(d):.5g}" for d in deltas)))
            if raw:
                rows.append(('— raw metadata —', ''))
                for k in sorted(raw.keys()):
                    rows.append((k, _fmt(raw.get(k))))
        table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(str(k)))
            table.setItem(i, 1, QTableWidgetItem(str(v)))

    _populate(False)

    controls = QHBoxLayout()
    raw_check = QCheckBox("Show all raw metadata")
    raw_check.toggled.connect(_populate)
    controls.addWidget(raw_check)
    controls.addStretch(1)

    export_btn = QPushButton("Export JSON…")

    def _export():
        import json
        path, _ = QFileDialog.getSaveFileName(
            dialog, "Export metadata as JSON",
            (common.get('file_name') or 'metadata') + '_metadata.json',
            "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(md, f, indent=2, default=str)
                _info(f"Metadata exported to {path}")
            except Exception as e:
                _warn(f"Export failed: {e}")

    export_btn.clicked.connect(_export)
    controls.addWidget(export_btn)

    compare_btn = QPushButton("Compare loaded images…")
    compare_btn.setToolTip(
        "Diff acquisition settings across the currently visible images and "
        "flag differences (exposure, laser, objective, filters, etc.) that "
        "can make a quantitative comparison untrustworthy.")
    compare_btn.clicked.connect(lambda: self._show_metadata_comparison())
    controls.addWidget(compare_btn)
    layout.addLayout(controls)

    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dialog.accept)
    layout.addWidget(close_btn)

    dialog.exec_()


# ── metadata comparison (contradiction warning across files) — moved from MenuManager (Part 2, feature 7) ──


def _show_metadata_comparison(self, result=None):
    """Show a table diffing acquisition metadata across the compared images,
    highlighting settings that differ. Can be called standalone; if no
    result is passed it gathers the current visible-image metadata."""
    from qtpy.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget,
                                QTableWidgetItem, QPushButton)
    from qtpy.QtGui import QColor
    if result is None:
        names, metas = self._gather_compared_metadata()
        if len(names) < 2:
            from napari.utils.notifications import show_info as _info
            _info("Load/show at least two images to compare their metadata.")
            return
        from pycat.file_io.metadata_extract import compare_acquisition_metadata
        result = compare_acquisition_metadata(metas, names=names)

    names = result['names']
    rows = result['rows']
    dlg = QDialog()
    dlg.setWindowTitle("Acquisition Metadata Comparison")
    lay = QVBoxLayout(dlg)

    verdict = QLabel(result['summary'])
    verdict.setWordWrap(True)
    if result['n_critical_diff'] > 0:
        verdict.setStyleSheet("color:#c0392b; font-weight:bold;")
    elif result['any_diff']:
        verdict.setStyleSheet("color:#b8860b;")
    else:
        verdict.setStyleSheet("color:#2e7d32;")
    lay.addWidget(verdict)

    table = QTableWidget(len(rows), len(names) + 1)
    table.setHorizontalHeaderLabels(['Setting'] + list(names))
    for r, row in enumerate(rows):
        lbl = QTableWidgetItem(row['label']
                               + ('  \u26a0' if row['differs'] and
                                  row['severity'] == 'critical' else ''))
        table.setItem(r, 0, lbl)
        for c, val in enumerate(row['values']):
            item = QTableWidgetItem('—' if val is None else str(val))
            if row['differs']:
                # Highlight differing rows: red for critical, amber for info.
                item.setBackground(QColor('#f9d6d5') if row['severity'] ==
                                   'critical' else QColor('#fdf1cf'))
            table.setItem(r, c + 1, item)
    table.resizeColumnsToContents()
    lay.addWidget(table)

    note = QLabel("Rows highlighted red are acquisition settings that can "
                  "make a quantitative comparison untrustworthy; amber rows "
                  "differ but are less critical. '—' means the value wasn't "
                  "recorded in that file's metadata.")
    note.setWordWrap(True)
    note.setStyleSheet("color:#888; font-size:9pt;")
    lay.addWidget(note)

    close = QPushButton("Close")
    close.clicked.connect(dlg.accept)
    lay.addWidget(close)
    dlg.resize(600, 400)
    dlg.exec_()


def _gather_compared_metadata(self):
    """Collect per-layer acquisition metadata for the currently VISIBLE image
    layers (the ones being compared in grid mode). Returns (names, metas).
    Reads the metadata stashed on each layer at load time."""
    names, metas = [], []
    try:
        for lyr in self.viewer.layers:
            if isinstance(lyr, napari.layers.Image) and bool(getattr(lyr, 'visible', True)):
                md = None
                try:
                    full = lyr.metadata.get('pycat_file_metadata')
                    if isinstance(full, dict):
                        md = full.get('common', full)
                except Exception:
                    md = None
                names.append(lyr.name)
                metas.append(md or {})
    except Exception:
        pass
    return names, metas


def _maybe_warn_metadata_diff(self):
    """When grid comparison starts with 2+ images, run the acquisition-
    metadata diff and, if critical settings differ, pop the comparison table
    so the user knows the comparison may be untrustworthy. If everything
    matches (or metadata is absent), stay quiet."""
    names, metas = self._gather_compared_metadata()
    if len(names) < 2:
        return
    # Only show automatically when there's something worth warning about.
    try:
        from pycat.file_io.metadata_extract import compare_acquisition_metadata
        result = compare_acquisition_metadata(metas, names=names)
    except Exception:
        return
    if result['n_critical_diff'] > 0:
        self._show_metadata_comparison(result)
