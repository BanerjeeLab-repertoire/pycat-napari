"""**The Navigator dock — a thin Qt view over `NavigatorSession` (navigator increment 3).**

The engine, planner, and quality gates all exist and were unreachable from the GUI; this is the last mile.
The dock asks the session's next question (prompt + one button per choice), records the answer, repeats until
a leaf is reached, then renders the compiled plan as a step list with **each step's quality-gate verdict
inline** — a blocked step says why it cannot run, a downgraded step shows its caveat but stays runnable, an
unknown shows the probe that will decide it (increment 2 wasted otherwise). A Run button hands the plan to the
caller's executor; a Start-over button resets.

All the logic (drive, compile, gate rendering) is the Qt-free `navigator.session`, exercised by core/base
tests; this file only builds widgets and wires signals, so it is deliberately thin and Qt-smoke tested. It
never builds a new engine, gating vocabulary, or plan model — those exist."""
from __future__ import annotations

# Colour per gate state — deliberately NOT the field_status step-status red (a different concept), matching
# the metadata-contradiction indicator's discipline.
_STATE_STYLE = {
    "ok":         ("", "#3c9a5f"),        # green
    "downgraded": ("⚠ ", "#c98a00"),  # ⚠ amber — runnable with a caveat
    "blocked":    ("⛔ ", "#c0392b"),  # ⛔ red — cannot run
    "probe":      ("⏳ ", "#2a7ab0"),  # ⏳ blue — QC probe
}


def build_navigator_widget(session, *, on_run=None, parent=None):
    """Build (do not mount) the Navigator widget over ``session``, or return ``None`` if Qt is unavailable
    (headless). ``on_run(plan)`` is called when the user runs the compiled plan. The widget re-renders itself
    as questions are answered and exposes ``_render()`` / ``_state`` / ``_choice_buttons`` for a Qt-smoke
    test to drive it."""
    try:
        from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QPushButton, QScrollArea, QFrame)
        from PyQt5.QtCore import Qt
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no widget, callers guard on None
        return None
    from pycat.navigator.session import plan_rows, NavigatorSession

    widget = QWidget(parent)
    root = QVBoxLayout(widget)
    title = QLabel("\U0001f9ed  Guided analysis")     # 🧭
    title.setStyleSheet("font-weight: bold; font-size: 14px;")
    root.addWidget(title)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setAlignment(Qt.AlignTop)
    scroll.setWidget(body)
    root.addWidget(scroll, 1)

    widget._session = session
    widget._on_run = on_run
    widget._state = "question"          # "question" | "plan" — inspected by the smoke test
    widget._plan = None
    widget._rows = []
    widget._choice_buttons = []
    widget._run_button = None

    def _clear():
        while body_layout.count():
            item = body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _label(text, style):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(style)
        return lbl

    def _render_question(spec):
        widget._state = "question"
        widget._choice_buttons = []
        body_layout.addWidget(_label(spec.prompt, "font-size: 13px; margin-top: 6px;"))
        if spec.rationale:
            body_layout.addWidget(_label(spec.rationale, "color: gray; font-size: 11px;"))
        for choice in spec.choices:
            text = choice.label + (f"   ({choice.hint})" if choice.hint else "")
            btn = QPushButton(text)
            btn.clicked.connect(lambda _=False, s=spec, v=choice.value: _answer(s, v))
            body_layout.addWidget(btn)
            widget._choice_buttons.append(btn)

    def _row_frame(row):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        fl = QVBoxLayout(frame)
        glyph, colour = _STATE_STYLE.get(row.state, ("", "#444"))
        fl.addWidget(_label(f"{glyph}{row.name}", f"font-weight: bold; color: {colour};"))
        if row.reason:
            fl.addWidget(_label(row.reason, "color: gray; font-size: 11px;"))
        for note in row.gates:                       # the inline quality-gate verdicts (the point of inc 2)
            g, c = _STATE_STYLE.get(note.kind, ("", "#444"))
            fl.addWidget(_label(f"{g}{note.reason}", f"color: {c}; font-size: 11px;"))
        return frame

    def _render_plan():
        widget._state = "plan"
        plan = widget._session.compile_plan()
        widget._plan = plan
        widget._rows = plan_rows(plan)
        body_layout.addWidget(_label(
            "Proposed analysis — review, then run. Each step shows any quality caveats.", "font-size: 12px;"))
        for row in widget._rows:
            body_layout.addWidget(_row_frame(row))
        run = QPushButton("▶  Run analysis")
        run.setEnabled(any(r.runnable for r in widget._rows) and callable(widget._on_run))
        run.clicked.connect(_run)
        body_layout.addWidget(run)
        widget._run_button = run
        over = QPushButton("↺  Start over")
        over.clicked.connect(_restart)
        body_layout.addWidget(over)

    def _answer(spec, value):
        widget._session.answer(spec, value)
        render()

    def _run():
        if callable(widget._on_run) and widget._plan is not None:
            widget._on_run(widget._plan)

    def _restart():
        widget._session = NavigatorSession()
        render()

    def render():
        _clear()
        q = widget._session.next_question()
        if q is not None:
            _render_question(q)
        else:
            _render_plan()

    widget._render = render
    render()
    return widget


def install_navigator_action(viewer, *, on_run=None):
    """Add a '\U0001f9ed Navigator' menu-bar action that opens the guided-analysis dock, mounted with the
    tabify behaviour (1.6.297) so it is visible rather than squeezed. Installed from ``central_manager`` (not
    the line-capped menu god-file). Returns the ``QAction`` or ``None`` (headless)."""
    try:
        from PyQt5.QtWidgets import QAction
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no action
        return None
    qt = getattr(getattr(viewer, "window", None), "_qt_window", None)
    if qt is None:
        return None

    held = {}

    def _open(*_):
        from pycat.navigator.session import NavigatorSession
        from pycat.utils.dock_space import add_results_dock
        widget = build_navigator_widget(NavigatorSession(), on_run=on_run)
        if widget is None:
            return
        held["widget"] = widget
        held["dock"] = add_results_dock(viewer.window, widget, name="Navigator")   # tabify + raise

    try:
        action = QAction("\U0001f9ed  Navigator", qt)
        action.setToolTip("Guided analysis — answer a few questions and get a runnable, quality-gated plan.")
        action.triggered.connect(_open)
        qt.menuBar().addAction(action)
        return action
    except Exception as exc:      # broad-ok: ui_cleanup — a missing menu bar must never break startup
        from pycat.utils.general_utils import debug_log
        debug_log("navigator: could not install the menu action", exc)
        return None
