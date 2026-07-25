"""Command palette feature — extracted from MenuManager (ui_decomposition Part 2).

The fuzzy action-search dialog lives here. ``MenuManager.open_command_palette`` is a thin wrapper that
calls ``open_command_palette(self)``, so the menu action and its label are unchanged. Moved VERBATIM
(the body references only ``self`` and locally-imported Qt widgets).
"""
from __future__ import annotations


def open_command_palette(mm):
    """Open the fuzzy command palette for the given MenuManager instance."""
    self = mm
    """Open a fuzzy-search command palette (Ctrl+Shift+P).

    Phase 1: find and launch any analysis method / toolbox function by name
    (over the flat registry accumulated in _add_actions_to_menu).
    Phase 2: find and select a layer by name (over viewer.layers).

    A single filterable list mixes both; picking a command runs it, picking a
    layer selects + reveals it. Deliberately scoped to these two easy wins;
    finding a step *within* a widget (phase 3) needs step-addressing infra
    and is left for later.
    """
    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLineEdit,
                                     QListWidget, QListWidgetItem)
        from PyQt5.QtCore import Qt
    except Exception:
        return

    registry = getattr(self, '_command_registry', {}) or {}

    # Build the combined entry list: (label, kind, payload).
    entries = []
    for name, (method, kwargs, group) in registry.items():
        label = f"{name}"
        if group:
            label = f"{name}   ·   {group}"
        entries.append((label, 'command', (method, kwargs)))
    try:
        for lyr in self.viewer.layers:
            entries.append((f"{lyr.name}   ·   layer", 'layer', lyr.name))
    except Exception:
        pass

    dlg = QDialog(self.viewer.window._qt_window)
    dlg.setWindowTitle("Command Palette")
    dlg.setMinimumWidth(480)
    v = QVBoxLayout(dlg)
    box = QLineEdit(); box.setPlaceholderText(
        "Type to search methods, toolbox functions, and layers…")
    lst = QListWidget()
    v.addWidget(box); v.addWidget(lst)

    def _score(query, label):
        # Rank match QUALITY, not just yes/no, so the best match sorts first.
        q = query.lower().replace(' ', ''); s = label.lower()
        if not q:
            return 0.0
        # Strong signal: contiguous substring — score high, higher the earlier
        # it appears (a layer named "Bead Detections" should beat a command
        # that merely contains b-e-a-d scattered through it).
        idx = s.find(q)
        if idx >= 0:
            return 1000.0 - idx - 0.1 * len(s)
        # Fallback: subsequence match (all query chars in order). Lower band,
        # rewarding an early start and few gaps.
        i = 0; first = -1; last = -1; gaps = 0
        for pos, ch in enumerate(s):
            if i < len(q) and ch == q[i]:
                if first < 0:
                    first = pos
                if last >= 0 and pos - last > 1:
                    gaps += 1
                last = pos; i += 1
        if i != len(q):
            return -1.0
        return 500.0 - first - 5.0 * gaps - 0.1 * len(s)

    def _refresh():
        query = box.text()
        lst.clear()
        scored = []
        for label, kind, payload in entries:
            sc = _score(query, label)
            if sc >= 0:
                scored.append((sc, label, kind, payload))
        # Best match first (score desc); commands before layers on ties, then
        # alphabetical for a stable feel.
        scored.sort(key=lambda x: (-x[0], x[2] == 'layer', x[1].lower()))
        for _sc, label, kind, payload in scored:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, (kind, payload))
            lst.addItem(it)
        if lst.count():
            lst.setCurrentRow(0)

    def _activate(item=None):
        item = item or lst.currentItem()
        if item is None:
            return
        kind, payload = item.data(Qt.UserRole)
        dlg.accept()
        try:
            if kind == 'command':
                method, kwargs = payload
                if kwargs:
                    method(**kwargs)
                else:
                    method()
            elif kind == 'layer':
                lyr = self.viewer.layers[payload]
                self.viewer.layers.selection = {lyr}
                try:
                    self.viewer.layers.move(
                        self.viewer.layers.index(lyr),
                        len(self.viewer.layers) - 1)
                except Exception:
                    pass
        except Exception as _e:
            try:
                from napari.utils.notifications import show_warning
                show_warning(f"Command palette: could not run '{payload}': {_e}")
            except Exception:
                print(f"[PyCAT palette] failed: {_e}")

    box.textChanged.connect(_refresh)
    lst.itemActivated.connect(_activate)

    # Enter activates the highlighted row; Down from the search box moves into
    # the list. Keep it keyboard-first like a real command palette.
    def _key(ev):
        from PyQt5.QtCore import Qt as _Qt
        if ev.key() in (_Qt.Key_Return, _Qt.Key_Enter):
            _activate(); return
        if ev.key() == _Qt.Key_Down and lst.count():
            lst.setFocus(); lst.setCurrentRow(min(1, lst.count() - 1)); return
        QLineEdit.keyPressEvent(box, ev)
    box.keyPressEvent = _key

    _refresh()
    box.setFocus()
    dlg.exec_()
