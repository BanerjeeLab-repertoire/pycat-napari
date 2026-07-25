"""Tag inspector feature — extracted from MenuManager (ui_decomposition Part 2).

The per-layer tag inspector/editor dialog lives here. ``MenuManager.open_tag_inspector`` is a thin
wrapper that calls ``open_tag_inspector(self)``, so the menu action and its label are unchanged.
Moved VERBATIM (the body references only ``self`` and locally-imported Qt widgets).
"""
from __future__ import annotations


def open_tag_inspector(self):
    """Open the Layer Tag Inspector — shows each layer's tags with their
    source and confidence, its lineage edges, and lets the user override any
    tag (an override locks against re-inference). This is the trust layer for
    the tagging system: you can always see *why* a tag is set and correct it.
    """
    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
            QComboBox, QTableWidget, QTableWidgetItem, QLabel, QPushButton,
            QLineEdit, QHeaderView, QWidget)
        from PyQt5.QtCore import Qt
        from pycat.utils import layer_tags as _LT
    except Exception as _e:
        print(f"[PyCAT tags] inspector unavailable: {_e}")
        return

    dlg = QDialog(self.viewer.window._qt_window)
    dlg.setWindowTitle("Layer Tag Inspector")
    dlg.setMinimumWidth(560); dlg.setMinimumHeight(420)
    v = QVBoxLayout(dlg)

    # Layer picker.
    row = QHBoxLayout()
    row.addWidget(QLabel("Layer:"))
    picker = QComboBox()
    layer_names = [l.name for l in self.viewer.layers]
    picker.addItems(layer_names)
    row.addWidget(picker, 1)
    v.addLayout(row)

    # Tags table: key | value | source | confidence.
    table = QTableWidget()
    table.setColumnCount(4)
    table.setHorizontalHeaderLabels(["key", "value", "source", "confidence"])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    v.addWidget(table, 1)

    # Lineage summary.
    lineage_lbl = QLabel("")
    lineage_lbl.setWordWrap(True)
    lineage_lbl.setStyleSheet("QLabel { color:#888; font-size:11px; }")
    v.addWidget(lineage_lbl)

    # Override row: key + value -> set as user_set.
    orow = QHBoxLayout()
    orow.addWidget(QLabel("Override:"))
    key_edit = QComboBox(); key_edit.setEditable(True)
    key_edit.addItems(sorted(_LT.CORE_KEYS))
    val_edit = QLineEdit(); val_edit.setPlaceholderText("value")
    set_btn = QPushButton("Set (locks)")
    orow.addWidget(key_edit); orow.addWidget(val_edit, 1); orow.addWidget(set_btn)
    v.addLayout(orow)
    hint = QLabel("Core values: role∈{image,mask,bead_stack,host_mask,roi,"
                  "annotation,result}, modality∈{fluorescence,brightfield}, "
                  "dimensionality∈{2d,2d+t,z-stack,multi-position}, "
                  "scale∈{calibrated,uncalibrated}. Free keys allowed as "
                  "'user:name'.")
    hint.setWordWrap(True)
    hint.setStyleSheet("QLabel { color:#999; font-size:10px; }")
    v.addWidget(hint)

    def _current_layer():
        nm = picker.currentText()
        return self.viewer.layers[nm] if nm in self.viewer.layers else None

    def _refresh_table():
        lyr = _current_layer()
        table.setRowCount(0)
        if lyr is None:
            lineage_lbl.setText(""); return
        tags = _LT.get_tags(lyr)
        table.setRowCount(len(tags))
        for i, t in enumerate(tags):
            src = t.get('source', '')
            for j, key in enumerate(('key', 'value', 'source', 'confidence')):
                val = t.get(key, '')
                if key == 'confidence' and isinstance(val, (int, float)):
                    val = f"{val:.2f}"
                it = QTableWidgetItem(str(val))
                # Colour user_set rows so overrides are obvious.
                if src == 'user_set':
                    from PyQt5.QtGui import QColor
                    it.setForeground(QColor('#c8102e'))
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                table.setItem(i, j, it)
        # Lineage.
        edges = _LT.get_edges(lyr)
        if edges:
            # map target ids -> names via the session index
            idx = _LT.rebuild_index(self.viewer)
            def _nm(tid):
                e = idx.get(tid)
                return (e.get('name') if e else tid) or tid
            parts = [f"{e['relation']} → {_nm(e['target'])}"
                     + (f" (via {e['via']})" if e.get('via') else "")
                     for e in edges]
            lineage_lbl.setText("Lineage: " + "; ".join(parts))
        else:
            lineage_lbl.setText("Lineage: (none — this layer is not derived "
                                "from another)")

    def _apply_override():
        lyr = _current_layer()
        if lyr is None:
            return
        k = key_edit.currentText().strip()
        val = val_edit.text().strip()
        if not k or not val:
            return
        ok = _LT.set_user_tag(lyr, k, val)
        if not ok:
            from napari.utils.notifications import show_warning
            show_warning(f"Could not set {k}={val} (not a valid controlled "
                         f"value for core key '{k}').")
        val_edit.clear()
        _refresh_table()

    picker.currentIndexChanged.connect(lambda *_: _refresh_table())
    set_btn.clicked.connect(_apply_override)
    _refresh_table()
    dlg.exec_()
