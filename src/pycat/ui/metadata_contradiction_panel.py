"""**The metadata dialog's contradiction section — list them first, mark one 'expected for this instrument'.**

tag_confidence Part 3 (the anti-numbing surface). The engine (``utils.metadata_contradictions``) already
detects contradictions, sorts them critical-first, and keeps a reversible per-pattern/per-fingerprint
'expected' judgement; the metadata button already warns when a critical one is present. This is the last
piece: the dialog LISTS the contradictions (ahead of the raw metadata, so they're seen) and offers, on each,
a **reversible** "expected for this instrument" control. Marking one greys it here immediately and — keyed to
the acquisition fingerprint, never the file — demotes it to info everywhere (the button stops flagging it on
the next refresh). There is deliberately no 'ignore all'; suppression is per-pattern only.

All logic is the Qt-free engine (``contradiction_rows`` / ``mark_expected``); this file only builds the
widget, so it is thin and Qt-smoke tested. It returns ``None`` when there are no contradictions or Qt is
unavailable, so the dialog shows a section only when there is something to say."""
from __future__ import annotations

# A warning amber — deliberately NOT the field_status step-status red (which means 'required input missing');
# a metadata contradiction is a different concept, per the spec's colour caution.
_CRITICAL_COLOUR = "#c98a00"
_EXPECTED_COLOUR = "#8a8a8a"


def build_contradiction_panel(file_metadata, store=None, *, on_change=None, parent=None):
    """Build the contradiction section for ``file_metadata`` (a loaded ``{common, raw}`` dict), or ``None`` if
    there are no contradictions or Qt is unavailable. ``store`` defaults to the process-wide user settings.
    ``on_change()`` fires after a mark/unmark so the caller can refresh the toolbar indicator. Exposes
    ``_buttons`` / ``_render`` for a Qt-smoke test."""
    try:
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
        from PyQt5.QtCore import Qt
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no widget, callers guard on None
        return None
    from pycat.utils.metadata_contradictions import contradiction_rows, mark_expected

    if store is None:
        try:
            from pycat.utils.user_settings import settings
            store = settings()
        except Exception:      # broad-ok: optional_probe — no settings → no reversible marking, still list them
            store = None

    rows, fingerprint = contradiction_rows(file_metadata, store=store)
    if not rows:
        return None

    widget = QWidget(parent)
    root = QVBoxLayout(widget)
    root.setContentsMargins(0, 0, 0, 6)
    widget._buttons = []        # (pattern, QPushButton) — the smoke-test seam
    widget._fingerprint = fingerprint

    def _label(text, style=""):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        if style:
            lbl.setStyleSheet(style)
        return lbl

    def _mark(pattern, expected):
        if store is not None:
            mark_expected(pattern, fingerprint, store, expected=expected)
        _render()
        if callable(on_change):
            try:
                on_change()
            except Exception:      # broad-ok: ui_cleanup — a bad refresh must not break the dialog
                pass

    def _render():
        while root.count():
            item = root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        widget._buttons = []
        fresh, _fp = contradiction_rows(file_metadata, store=store)
        n_crit = sum(1 for r in fresh if r.severity == "critical" and not r.expected)
        head = ("⚠  Metadata contradictions" + (f" — {n_crit} need attention" if n_crit else "")
                if n_crit else "ℹ  Metadata notes")
        root.addWidget(_label(head, "font-weight: bold;"))

        for row in fresh:
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            fl = QVBoxLayout(frame)
            if row.expected:
                colour, note = _EXPECTED_COLOUR, "  (you marked this expected for this instrument)"
            elif row.severity == "critical":
                colour, note = _CRITICAL_COLOUR, ""
            else:
                colour, note = "", ""
            fl.addWidget(_label(("⚠ " if row.severity == "critical" and not row.expected else "• ")
                                + row.message + note,
                                f"color: {colour};" if colour else ""))
            # The reversible per-pattern control (only where it is meaningful: a critical rule or an undo).
            if row.severity == "critical" or row.expected:
                btn = QPushButton("Unmark" if row.expected else "Expected for this instrument")
                btn.setToolTip("A known quirk of this instrument, not a real problem — greys it here and stops "
                               "the button flagging it, for this acquisition only. Reversible.")
                btn.clicked.connect(lambda _=False, p=row.pattern, e=not row.expected: _mark(p, e))
                brow = QHBoxLayout()
                brow.addStretch(1)
                brow.addWidget(btn)
                holder = QWidget()
                holder.setLayout(brow)
                fl.addWidget(holder)
                widget._buttons.append((row.pattern, btn))
            root.addWidget(frame)

    widget._render = _render
    _render()
    return widget
