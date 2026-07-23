"""**The preferences panel — a thin Qt surface over the Qt-free preferences registry.**

Renders one group of radio buttons per preference from :func:`pycat.utils.preferences.list_preferences`, checks
the current value, and on a change forwards it through :func:`~pycat.utils.preferences.set_preference` (which
the owning module validates and persists). All the logic — what preferences exist, their options, validation —
lives in the registry and is core-tested; this file only builds widgets and wires signals, so it is deliberately
thin and exercised by a Qt-smoke integration test. Changes apply immediately (no OK/Apply), matching how the
underlying preferences already notify their subscribers live."""
from __future__ import annotations


def build_preferences_dialog(store=None, parent=None):
    """Construct (do not exec) the preferences ``QDialog`` from the current registry, or return ``None`` if Qt
    is unavailable (headless). ``store`` is injectable for tests; ``None`` uses the process-wide settings. The
    dialog applies each change immediately via the registry's ``set_preference``."""
    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QGroupBox, QRadioButton,
                                     QLabel, QDialogButtonBox)
        from PyQt5.QtCore import Qt
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no dialog, callers guard on None
        return None

    from pycat.utils.preferences import list_preferences, set_preference

    dlg = QDialog(parent)
    dlg.setWindowTitle("PyCAT Preferences")
    dlg.setMinimumWidth(460)
    outer = QVBoxLayout(dlg)

    intro = QLabel("Changes apply immediately and are remembered for next time.")
    intro.setWordWrap(True)
    outer.addWidget(intro)

    # Keep references so a Qt-smoke test (and future live re-render) can find the controls by preference key.
    dlg._pref_buttons = {}          # key -> {value: QRadioButton}

    for view in list_preferences(store):
        box = QGroupBox(view.label)
        bv = QVBoxLayout(box)
        help_lbl = QLabel(view.description)
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: gray;")
        bv.addWidget(help_lbl)

        buttons = {}
        for opt in view.options:
            rb = QRadioButton(opt.label)
            rb.setChecked(opt.value == view.current)
            # bind key/value per-button; a checked radio forwards the change to the owning module
            def _make_handler(k=view.key, val=opt.value):
                def _on_toggled(checked):
                    if checked:
                        set_preference(k, val, store)
                return _on_toggled
            rb.toggled.connect(_make_handler())
            bv.addWidget(rb)
            buttons[opt.value] = rb
        dlg._pref_buttons[view.key] = buttons
        outer.addWidget(box)

    btns = QDialogButtonBox(QDialogButtonBox.Close)
    btns.rejected.connect(dlg.reject)
    btns.accepted.connect(dlg.accept)
    outer.addWidget(btns)
    return dlg


def install_preferences_action(viewer):
    """Add a '⚙ Preferences' action to the napari menu bar that opens the panel. Installed once at startup
    (from ``central_manager``) so the entry point lives beside the panel it opens, NOT accreted onto the menu
    god-file (which is pinned at its line ceiling). Returns the created ``QAction``, or ``None`` when Qt or the
    menu bar is unavailable (headless) — the caller stores it so it is not garbage-collected."""
    try:
        from PyQt5.QtWidgets import QAction
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no menu action, caller guards on None
        return None
    qt = getattr(getattr(viewer, 'window', None), '_qt_window', None)
    if qt is None:
        return None

    _held = {}

    def _open(*_):
        dlg = build_preferences_dialog(parent=qt)
        if dlg is not None:
            _held['dialog'] = dlg          # keep the modeless dialog alive while it is open
            dlg.show()

    try:
        action = QAction('⚙ Preferences', qt)
        action.setToolTip('PyCAT preferences — interface level (beginner/advanced) and results-dock placement.')
        action.triggered.connect(_open)
        qt.menuBar().addAction(action)
        return action
    except Exception:      # broad-ok: ui_cleanup — a missing/odd menu bar must never break startup
        return None
