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

# What each state MEANS — the reported confusion was unexplained blue/green sections. One vocabulary, said out
# loud: a legend at the top of the plan and a tooltip per step (navigator-UX items 3b/4).
_STATE_MEANING = {
    "ok":         "Ready to run.",
    "downgraded": "Runs, but the result carries a caveat (see the note).",
    "blocked":    "Can't run yet — a precondition is unmet (see the reason).",
    "probe":      "A quality-control step that runs first to measure a signal a later step needs.",
}
_PLAN_LEGEND = ("Step colour — green: ready · ⏳ blue: a QC probe runs first · ⚠ amber: runs with a caveat · "
                "⛔ red: blocked (the reason is shown).")


def _label(text, style=""):
    from PyQt5.QtWidgets import QLabel
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if style:
        lbl.setStyleSheet(style)
    return lbl


def _row_frame(row):
    """One plan-step row: its name coloured by gate state, its reason, and the inline quality-gate notes."""
    from PyQt5.QtWidgets import QFrame, QVBoxLayout
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    frame.setToolTip(_STATE_MEANING.get(row.state, ""))     # every step explains its own colour/state
    fl = QVBoxLayout(frame)
    glyph, colour = _STATE_STYLE.get(row.state, ("", "#444"))
    fl.addWidget(_label(f"{glyph}{row.name}", f"font-weight: bold; color: {colour};"))
    if row.reason:
        fl.addWidget(_label(row.reason, "color: gray; font-size: 11px;"))
    for note in row.gates:                           # the inline quality-gate verdicts (the point of inc 2)
        g, c = _STATE_STYLE.get(note.kind, ("", "#444"))
        fl.addWidget(_label(f"{g}{note.reason}", f"color: {c}; font-size: 11px;"))
    return frame


def _render_question(widget, body_layout, spec, on_answer):
    """Render a question (prompt + rationale + one button per choice) into ``body_layout``; a click answers."""
    from PyQt5.QtWidgets import QPushButton
    widget._state = "question"
    widget._choice_buttons = []
    body_layout.addWidget(_label(spec.prompt, "font-size: 13px; margin-top: 6px;"))
    if spec.rationale:
        body_layout.addWidget(_label(spec.rationale, "color: gray; font-size: 11px;"))
    for choice in spec.choices:
        text = choice.label + (f"   ({choice.hint})" if choice.hint else "")
        btn = QPushButton(text)
        btn.clicked.connect(lambda _=False, s=spec, v=choice.value: on_answer(s, v))
        body_layout.addWidget(btn)
        widget._choice_buttons.append(btn)


def _wire_reevaluation(widget, central_manager, render):
    """Wire the plan to viewer state: a layer inserted/removed or a calibration change RE-GATES (not
    recompiles) the compiled plan so a blocked step flips to satisfied and the run action re-enables — the fix
    for a panel that never tracked the data. Debounced: bursts of layer events coalesce into one cheap re-gate."""
    from PyQt5.QtCore import QTimer

    def _reevaluate_now():
        widget._reeval_pending = False
        if widget._state != "plan" or widget._session._plan is None:
            return
        widget._regate_only = True
        render()

    def _reevaluate(*_):
        if widget._reeval_pending:
            return
        widget._reeval_pending = True
        QTimer.singleShot(120, _reevaluate_now)

    widget._reevaluate_now = _reevaluate_now
    if central_manager is None:
        return
    try:
        _ev = getattr(getattr(getattr(central_manager, "viewer", None), "layers", None), "events", None)
        if _ev is not None:
            _ev.inserted.connect(_reevaluate)
            _ev.removed.connect(_reevaluate)
    except Exception:      # broad-ok: optional_probe — no live viewer events → still correct on mount
        pass
    try:
        central_manager.register_data_switch_callback(_reevaluate)   # pixel-size / data changes
    except Exception:      # broad-ok: optional_probe — the callback registry is optional
        pass


def _render_observations(widget, body_layout):
    """The 'What we can tell from your data' section (navigator-UX item 3): metadata-derived observations with
    their evidence, shown as SUGGESTIONS — the user's answers take precedence. Renders nothing when no data is
    loaded or nothing is knowable (no guessing)."""
    cm = getattr(widget, "_cm", None)
    if cm is None:
        return
    from pycat.navigator.session import data_observations
    obs = data_observations(cm)
    if not obs:
        return
    body_layout.addWidget(_label("\U0001f50d  What we can tell from your data", "font-weight: bold; font-size: 12px;"))
    for o in obs:
        body_layout.addWidget(_label(f"• {o['text']}  —  {o['evidence']}", "color: #2a7ab0; font-size: 11px;"))
    body_layout.addWidget(_label("Read from the file — your answers below take precedence.",
                                 "color: gray; font-size: 10px; margin-bottom: 4px;"))


def _guided_run_note(plan):
    """The honest guided-run message, made ACTIONABLE with the gate-respecting run ORDER. Auto-running the
    whole plan needs a per-operation execution adapter (PyCAT ops have bespoke, panel-collected signatures —
    there is no uniform "run this op"), so that is a separate layer; until it lands, the user runs the steps in
    their method panels in exactly this sequence (probes first, a blocker stops)."""
    order = ""
    try:
        from pycat.navigator.execution import execution_order
        order = " → ".join(s.name for s in execution_order(plan) if s.status in ("run", "caveat"))
    except Exception:      # broad-ok: ui_cleanup — a briefing failure must not break the plan view
        order = ""
    msg = ("This plan is ready. Auto-running from the guided panel is coming; for now run the steps in their "
           "method panels")
    return msg + (f", in this order:\n{order}." if order else ".")


def _add_template_save(widget, body_layout):
    """Add a 'Save as template…' button that persists the answered plan under a user-supplied name (the answers
    + step names, NOT the gate verdicts — see navigator.templates). Reusable on other data without
    re-answering. Best-effort: a save failure never breaks the plan view."""
    from PyQt5.QtWidgets import QPushButton, QInputDialog

    def _save():
        try:
            name, ok = QInputDialog.getText(widget, "Save guided template", "Template name:")
            if not ok or not str(name).strip():
                return
            from pycat.navigator.templates import template_from_plan, save_template
            plan = widget._session.compile_plan()
            save_template(template_from_plan(str(name).strip(), widget._session.intent, plan))
            from napari.utils.notifications import show_info
            show_info(f"Saved guided template '{str(name).strip()}' — reuse it on other data from Home.")
        except Exception as exc:      # broad-ok: ui_cleanup — a save failure must not break the plan view
            from pycat.utils.general_utils import debug_log
            debug_log("navigator: could not save the template", exc)

    btn = QPushButton("\U0001f4be  Save as template…")     # 💾
    btn.setToolTip("Save these answers as a reusable method template — apply it to other data without "
                   "re-answering (the quality gates re-evaluate on the new data).")
    btn.clicked.connect(_save)
    body_layout.addWidget(btn)


def build_navigator_widget(session, *, on_run=None, central_manager=None, parent=None):
    """Build (do not mount) the Navigator widget over ``session``, or return ``None`` if Qt is unavailable
    (headless). ``on_run(plan)`` is called when the user runs the compiled plan. When ``central_manager`` is
    given, the plan **tracks viewer state**: loading an image, adding/removing a layer, or a calibration change
    RE-GATES the compiled plan (cheap; never recompiles) so a blocked step flips to satisfied and the run
    action re-enables — the fix for a panel that was inert. Exposes ``_render()`` / ``_state`` /
    ``_choice_buttons`` / ``_reevaluate_now`` for a Qt-smoke test to drive it."""
    try:
        from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QPushButton, QScrollArea)
        from PyQt5.QtCore import Qt
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no widget, callers guard on None
        return None
    from pycat.navigator.session import plan_rows, NavigatorSession, context_from_session

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
    widget._cm = central_manager
    widget._state = "question"          # "question" | "plan" — inspected by the smoke test
    widget._plan = None
    widget._rows = []
    widget._choice_buttons = []
    widget._run_button = None
    widget._regate_only = False         # a viewer event re-gates; answering/restart recompiles
    widget._reeval_pending = False

    def _clear():
        while body_layout.count():
            item = body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render_plan():
        widget._state = "plan"
        # Refresh the context from the loaded image before (re)evaluating, so a panel opened AFTER data is
        # already loaded starts correct (run-once-on-mount), and re-gate rather than recompile on a viewer
        # event (recompiling could re-select modules and change the plan under the user).
        if widget._cm is not None:
            try:
                context_from_session(widget._cm, widget._session.ctx)
            except Exception:      # broad-ok: optional_probe — a bad repository must not break the panel
                pass
        if widget._regate_only and widget._session._plan is not None:
            plan = widget._session.regate()
        else:
            plan = widget._session.compile_plan()
        widget._regate_only = False
        widget._plan = plan
        widget._rows = plan_rows(plan, widget._session.ctx)
        body_layout.addWidget(_label(
            "Proposed analysis — review, then run. Each step shows any quality caveats.", "font-size: 12px;"))
        body_layout.addWidget(_label(_PLAN_LEGEND, "color: gray; font-size: 10px; margin-bottom: 4px;"))
        for row in widget._rows:
            body_layout.addWidget(_row_frame(row))
        run = QPushButton("▶  Run analysis")
        reason = widget._session.run_blocked_reason()
        run.setEnabled(reason is None and callable(widget._on_run))
        run.clicked.connect(_run)
        body_layout.addWidget(run)
        widget._run_button = run
        # Say WHY, always — a disabled run action is never a dead control.
        if reason is not None:
            body_layout.addWidget(_label("⛔  " + reason, "color: #c0392b; font-size: 11px;"))
        elif not callable(widget._on_run):
            body_layout.addWidget(_label(_guided_run_note(widget._plan), "color: gray; font-size: 11px;"))
        over = QPushButton("↺  Start over")
        over.clicked.connect(_restart)
        body_layout.addWidget(over)
        _add_template_save(widget, body_layout)

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
        _render_observations(widget, body_layout)      # 'What we can tell from your data' — item 3
        q = widget._session.next_question()
        if q is not None:
            _render_question(widget, body_layout, q, _answer)
        else:
            _render_plan()

    _wire_reevaluation(widget, central_manager, render)
    widget._render = render
    render()
    return widget


def install_navigator_action(viewer, *, on_run=None, central_manager=None):
    """Add a '\U0001f9ed Navigator' menu-bar action that opens the guided-analysis dock, mounted with the
    tabify behaviour (1.6.297) so it is visible rather than squeezed. ``central_manager`` lets the opened plan
    track viewer state (re-gate on load). Installed from ``central_manager`` (not the line-capped menu
    god-file). Returns the ``QAction`` or ``None`` (headless)."""
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
        widget = build_navigator_widget(NavigatorSession(), on_run=on_run, central_manager=central_manager)
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
