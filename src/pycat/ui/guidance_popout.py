"""Pop-out operation guidance for a generated method panel (Method-Widget Spec 4).

Each section of a `GeneratedMethodUI` carries a small **?** affordance on its header; clicking it pops out the
op's authored guidance IN PLACE — when to use it, its advantages and limitations, when it does NOT apply, its
references — and the same for the alternatives it could be swapped for, side by side. This is where the Navigator
stops being a wizard you exit and becomes the panel's editing surface.

The DECISION of what to show is `navigator.guidance.section_guidance` (a pure function, tested headlessly). This
module is the thin Qt shell that renders that dict: an unauthored op shows an honest "not documented yet — author
it in the guidance workbook", never a fabricated stand-in. GUI-bound (imports Qt); acceptance is a manual napari
run once content exists.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from pycat.navigator.guidance import section_guidance

_WORKBOOK = "docs/operation_guidance_authoring.xlsx"


def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_guidance_html(entry: dict) -> str:
    """The rich-text body for one op's authored guidance (or the 'not documented' note). Kept small and
    string-only so the shape is obvious; the six fields render in a fixed, readable order."""
    if not entry:
        return (f"<p style='color:#b9770e'><i>Not documented yet.</i> Author it in "
                f"<code>{_WORKBOOK}</code> and ingest — see the guidance workbook.</p>")
    parts = []
    when = entry.get("when_to_use")
    if when:
        parts.append(f"<p>{_esc(when)}</p>")
    for field, title in (("advantages", "Advantages"), ("limitations", "Limitations"),
                         ("not_applicable_when", "Not applicable when"), ("references", "References")):
        items = entry.get(field) or []
        if items:
            lis = "".join(f"<li>{_esc(i)}</li>" for i in items)
            parts.append(f"<p style='margin-bottom:2px'><b>{title}</b></p><ul style='margin-top:0'>{lis}</ul>")
    return "".join(parts) or "<p><i>(no fields authored)</i></p>"


def guidance_popout_html(op_id: str, *, alternatives=None) -> str:
    """The full pop-out body (op + its alternatives), as rich text. Split out so the rendering is inspectable
    without a live dialog."""
    popout = section_guidance(op_id, alternatives=alternatives)
    html = [f"<h3 style='margin:0 0 6px'>{_esc(op_id)}</h3>", _render_guidance_html(popout["guidance"])]
    if popout["alternatives"]:
        html.append("<hr><p style='margin-bottom:4px'><b>Alternatives</b> — other ops for this step:</p>")
        for alt in popout["alternatives"]:
            g = alt["guidance"] or {}
            note = _esc(g.get("when_to_use")) if alt["documented"] else "<i style='color:#b9770e'>not documented yet</i>"
            html.append(f"<p style='margin:2px 0'><code>{_esc(alt['op_id'])}</code> — {note}</p>")
    return "".join(html)


class GuidancePopout(QDialog):
    """A small dockable-feeling dialog rendering :func:`guidance_popout_html` for one section's op."""

    def __init__(self, op_id: str, *, alternatives=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Guidance — {op_id}")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        body = QLabel(guidance_popout_html(op_id, alternatives=alternatives))
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(body)
        hl.addStretch(1)
        scroll.setWidget(holder)
        layout.addWidget(scroll)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


def open_guidance_popout(parent, op_id: str, *, alternatives=None):
    """Construct and show the guidance pop-out for ``op_id`` (non-modal). Returns the dialog, or ``None`` on
    failure — a guidance pop-out must never take down the panel it decorates."""
    try:
        dlg = GuidancePopout(op_id, alternatives=alternatives, parent=parent)
        dlg.show()
        return dlg
    except Exception as exc:  # broad-ok: ui_cleanup — a guidance pop-out failure must not disturb the panel
        from pycat.utils.general_utils import debug_log
        debug_log(f"guidance pop-out failed for {op_id!r}", exc)
        return None
