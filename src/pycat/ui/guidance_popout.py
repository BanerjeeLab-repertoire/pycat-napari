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
from PyQt5.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                            QVBoxLayout, QWidget)

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


def _op_guidance_html(op_id: str) -> str:
    """Just the op's own guidance (heading + authored body), no alternatives — the interactive dialog renders the
    alternatives as its own widgets so each can carry a swap button."""
    popout = section_guidance(op_id)
    return "".join([f"<h3 style='margin:0 0 6px'>{_esc(op_id)}</h3>", _render_guidance_html(popout["guidance"])])


def _alt_note_html(alt: dict) -> str:
    """The one-line 'when to use' snippet for an alternative row (or an honest 'not documented yet')."""
    g = alt["guidance"] or {}
    return _esc(g.get("when_to_use")) if alt["documented"] else "<i style='color:#b9770e'>not documented yet</i>"


def _score_note_html(scores, op_id: str) -> str:
    """The 'why this one' annotation for an op (Spec 5): its context match + preference, and a ✓ if it is the
    planner's current pick. Empty when the op has no reasoning (not a scored role) — never a fabricated score."""
    s = (scores or {}).get(op_id)
    if not s:
        return ""
    cs = s.get("context_score", 0)
    ctx = f"context {'+' if cs > 0 else ''}{cs}"
    mark = " ✓ chosen" if s.get("chosen") else ""
    return (f"<span style='color:#6b6b6b;font-size:11px'> · {ctx} · pref "
            f"{float(s.get('preference', 0)):.2f}{mark}</span>")


def guidance_popout_html(op_id: str, *, alternatives=None, scores=None) -> str:
    """The full pop-out body (op + its alternatives), as static rich text. Retained for headless inspection and as
    the no-revise fallback rendering; the live dialog builds interactive alternative rows instead."""
    popout = section_guidance(op_id, alternatives=alternatives)
    html = [_op_guidance_html(op_id), _score_note_html(scores, op_id)]
    if popout["alternatives"]:
        html.append("<hr><p style='margin-bottom:4px'><b>Alternatives</b> — other ops for this step:</p>")
        for alt in popout["alternatives"]:
            html.append(f"<p style='margin:2px 0'><code>{_esc(alt['op_id'])}</code>"
                        f"{_score_note_html(scores, alt['op_id'])} — {_alt_note_html(alt)}</p>")
    return "".join(html)


class GuidancePopout(QDialog):
    """A small dockable-feeling dialog: the op's guidance, then its alternatives. When ``on_revise`` is supplied
    (Spec 4 live revision) each alternative row carries a **Use this instead** button that calls
    ``on_revise(alt_op_id)`` — the panel then rebuilds from the amended plan and this dialog closes. Without it the
    alternatives render as static text (guidance-only mode)."""

    def __init__(self, op_id: str, *, alternatives=None, on_revise=None, scores=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Guidance — {op_id}")
        self.setMinimumWidth(440)
        self._on_revise = on_revise
        self._scores = scores
        layout = QVBoxLayout(self)

        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(self._rich(_op_guidance_html(op_id) + _score_note_html(scores, op_id)))

        popout = section_guidance(op_id, alternatives=alternatives)
        if popout["alternatives"]:
            hl.addWidget(self._rich("<hr><p style='margin-bottom:2px'><b>Alternatives</b> — "
                                    "other ops for this step:</p>"))
            for alt in popout["alternatives"]:
                hl.addWidget(self._alternative_row(alt))
        hl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        layout.addWidget(scroll)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    @staticmethod
    def _rich(html: str) -> QLabel:
        lbl = QLabel(html)
        lbl.setTextFormat(Qt.RichText)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignTop)
        return lbl

    def _alternative_row(self, alt: dict) -> QWidget:
        """One alternative: its name + 'when to use' snippet, and — when revision is wired — a button that swaps
        the step to this op and rebuilds the panel."""
        row = QFrame()
        row.setFrameShape(QFrame.StyledPanel)
        rl = QHBoxLayout(row)
        rl.addWidget(self._rich(f"<code>{_esc(alt['op_id'])}</code>"
                                f"{_score_note_html(self._scores, alt['op_id'])} — {_alt_note_html(alt)}"), 1)
        if self._on_revise is not None:
            use = QPushButton("Use this instead")
            use.setCursor(Qt.PointingHandCursor)
            use.clicked.connect(lambda _=False, o=alt["op_id"]: self._revise_to(o))
            rl.addWidget(use, 0)
        return row

    def _revise_to(self, new_op_id: str):
        """Fire the revision callback for the chosen alternative, then close — the panel rebuilds from the amended
        plan. Best-effort: a failed revision must not leave a half-open dialog with no feedback."""
        try:
            self._on_revise(new_op_id)
        except Exception as exc:  # broad-ok: ui_cleanup — a failed revise must not crash the pop-out
            from pycat.utils.general_utils import debug_log
            debug_log(f"live revision to {new_op_id!r} failed", exc)
        self.accept()


def open_guidance_popout(parent, op_id: str, *, alternatives=None, on_revise=None, scores=None):
    """Construct and show the guidance pop-out for ``op_id`` (non-modal). Pass ``on_revise`` to enable the
    'Use this instead' swap buttons and ``scores`` for the Spec 5 'why this one' annotations. Returns the dialog,
    or ``None`` on failure — a guidance pop-out must never take down the panel it decorates."""
    try:
        dlg = GuidancePopout(op_id, alternatives=alternatives, on_revise=on_revise, scores=scores, parent=parent)
        dlg.show()
        return dlg
    except Exception as exc:  # broad-ok: ui_cleanup — a guidance pop-out failure must not disturb the panel
        from pycat.utils.general_utils import debug_log
        debug_log(f"guidance pop-out failed for {op_id!r}", exc)
        return None
