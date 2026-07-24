"""**The Home dock — a friendly front door: guided analysis + every capability as a card (navigator inc 4, part 2).**

Increment 4's visible surface. It composes three things that already exist: the increment-3 **navigator**
question-flow (guided analysis → a quality-gated, editable plan), the **feature-card** catalogue grouped by
category (navigator inc 4 part 1), and the **app-mode** toggle (Guided ⇆ Full). A beginner is greeted with it
on first run; an advanced user flips to Full once and is never re-greeted — but the menu action reopens it
any time, and nothing is ever hidden (guide, don't cage).

All logic lives in the composed pieces (`navigator.session`, `feature_registry`, `app_mode`), so this file
only builds widgets and wires signals — deliberately thin and Qt-smoke tested; it returns ``None`` headlessly."""
from __future__ import annotations


def _open_card(card):
    """Invoke a card's opener, guarded — one feature failing to open must never kill the Home dock."""
    if not callable(card.entry):
        return
    try:
        card.entry()
    except Exception as exc:      # broad-ok: ui_cleanup — one feature failing to open must not kill Home
        from pycat.utils.general_utils import debug_log
        debug_log(f"home: could not open feature {card.key}", exc)


def _label(text, style=""):
    from PyQt5.QtWidgets import QLabel
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    if style:
        lbl.setStyleSheet(style)
    return lbl


def _card_frame(widget, card):
    from PyQt5.QtWidgets import QVBoxLayout, QPushButton, QFrame
    frame = QFrame()
    frame.setFrameShape(QFrame.StyledPanel)
    fl = QVBoxLayout(frame)
    btn = QPushButton(card.title)
    btn.setToolTip(card.summary)
    btn.clicked.connect(lambda _=False, c=card: _open_card(c))
    fl.addWidget(btn)
    fl.addWidget(_label(card.summary, "color: gray; font-size: 11px;"))
    widget._cards.append((card, btn))
    return frame


def _render_home(widget, body_layout, store, reg, central_manager):
    """Render the Home surface: a header + a Guided/Full toggle that labels the OUTCOME (item 5), then two
    tabs — the guided navigator and the capability explorer, which serve different intents and get their own
    space (item 6). Re-run whenever the app mode changes."""
    from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTabWidget
    from PyQt5.QtCore import Qt
    from pycat.utils.app_mode import current_mode, toggle_mode, AppMode
    while body_layout.count():
        item = body_layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    widget._cards = []
    mode = current_mode(store)
    widget._shown_mode = mode

    header = QHBoxLayout()
    header.addWidget(_label("\U0001f3e0  PyCAT — Home", "font-weight: bold; font-size: 15px;"), 1)   # 🏠
    mb = QPushButton("Switch to Full" if mode is AppMode.BEGINNER else "Switch to Guided")
    mb.setToolTip("Guided: answer a few questions and PyCAT proposes a workflow.\n"
                  "Full: every analysis method, menu-first, no guidance.\nNothing is hidden either way.")
    mb.clicked.connect(lambda _=False: toggle_mode(store))     # the subscription re-renders
    widget._mode_button = mb
    header.addWidget(mb)
    header_widget = QWidget()
    header_widget.setLayout(header)
    body_layout.addWidget(header_widget)
    body_layout.addWidget(_label(
        "Guided mode — answer questions, get a proposed workflow. Full mode has every method, no guidance."
        if mode is AppMode.BEGINNER else
        "Full mode — every analysis method, menu-first. Switch to Guided for a proposed workflow.",
        "color: gray; font-size: 11px;"))

    tabs = QTabWidget()
    guided = QWidget()
    gl = QVBoxLayout(guided)
    gl.setAlignment(Qt.AlignTop)
    from pycat.ui.navigator_dock import build_navigator_widget
    from pycat.navigator.session import NavigatorSession
    nav = build_navigator_widget(
        NavigatorSession(), on_run=getattr(central_manager, "run_navigator_plan", None),
        central_manager=central_manager)
    if nav is not None:
        widget._navigator = nav
        gl.addWidget(nav)
    else:
        gl.addWidget(_label("Guided analysis is unavailable here.", "color: gray;"))
    tabs.addTab(guided, "\U0001f9ed  Guided")           # 🧭

    explore = QWidget()
    el = QVBoxLayout(explore)
    el.setAlignment(Qt.AlignTop)
    el.addWidget(_label("Everything PyCAT can do — click a card to open it.", "color: gray; font-size: 11px;"))
    grouped = reg.by_category(mode)
    if not grouped:
        el.addWidget(_label("No capabilities registered yet.", "color: gray;"))
    for category, cards in grouped.items():
        el.addWidget(_label(category, "font-weight: bold; color: #2a7ab0; margin-top: 6px;"))
        for card in cards:
            el.addWidget(_card_frame(widget, card))
    tabs.addTab(explore, "\U0001f9f0  Explore capabilities")   # 🧰

    widget._tabs = tabs                                  # smoke-test seam
    body_layout.addWidget(tabs)


def build_home_widget(central_manager, *, reg=None, store=None, parent=None):
    """Build (do not mount) the Home widget, or ``None`` if Qt is unavailable (headless). ``reg`` defaults to
    the process-wide feature registry and ``store`` to the real user-settings; both are injectable for tests.
    Exposes ``_render`` / ``_cards`` / ``_mode_button`` / ``_navigator`` for a Qt-smoke test to drive it."""
    try:
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QScrollArea
        from PyQt5.QtCore import Qt
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no widget, callers guard on None
        return None
    from pycat.utils.feature_registry import registry as _registry
    from pycat.utils.app_mode import on_mode_change

    reg = reg if reg is not None else _registry()

    widget = QWidget(parent)
    root = QVBoxLayout(widget)
    widget._reg = reg
    widget._store = store
    widget._cards = []                 # (card, button) pairs currently shown — inspected by the smoke test
    widget._mode_button = None
    widget._navigator = None
    widget._shown_mode = None

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setAlignment(Qt.AlignTop)
    scroll.setWidget(body)
    root.addWidget(scroll)

    def _render():
        _render_home(widget, body_layout, store, reg, central_manager)

    def _on_mode_changed(_mode):
        try:
            _render()
        except Exception:      # broad-ok: ui_cleanup — a stale subscription on a torn-down Home must not raise
            pass

    widget._render = _render
    try:
        widget._unsubscribe = on_mode_change(_on_mode_changed, store)
    except Exception:          # broad-ok: optional_probe — live re-render is best-effort
        widget._unsubscribe = None

    def detach():
        if callable(getattr(widget, "_unsubscribe", None)):
            try:
                widget._unsubscribe()
            except Exception:  # broad-ok: ui_cleanup — teardown must not raise
                pass
            widget._unsubscribe = None
    widget.detach = detach

    _render()
    return widget


def install_home_action(central_manager):
    """Add a '\U0001f3e0 Home' menu-bar action that opens the Home dock (tabify mount), and — on a genuine
    first run — greet the user with it once. Guarded end-to-end: neither the action nor the auto-open may
    ever break startup. Returns the ``QAction`` or ``None`` (headless). Installed from ``central_manager``."""
    try:
        from PyQt5.QtWidgets import QAction
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no action
        return None
    viewer = getattr(central_manager, "viewer", None)
    qt = getattr(getattr(viewer, "window", None), "_qt_window", None)
    if qt is None:
        return None

    held = {}

    def _open(*_):
        from pycat.utils.dock_space import add_results_dock
        widget = build_home_widget(central_manager)
        if widget is None:
            return
        held["widget"] = widget
        held["dock"] = add_results_dock(viewer.window, widget, name="Home")   # tabify + raise (1.6.297)

    try:
        action = QAction("\U0001f3e0  Home", qt)
        action.setToolTip("The guided home — analysis questions and every capability as a card.")
        action.triggered.connect(_open)
        qt.menuBar().addAction(action)
    except Exception as exc:      # broad-ok: ui_cleanup — a missing menu bar must never break startup
        from pycat.utils.general_utils import debug_log
        debug_log("home: could not install the menu action", exc)
        return None

    # First run defaults to the guided home. We greet ONCE — persist the (beginner) default so later launches
    # don't re-open it automatically; the menu action reopens it any time. Conservative on purpose: it does
    # not change the surface for anyone who has already chosen a mode.
    try:
        from pycat.utils.user_settings import settings
        from pycat.utils.app_mode import set_mode, AppMode
        s = settings()
        if not s.get_str("app.mode", ""):        # the mode key has never been written → genuine first run
            set_mode(AppMode.BEGINNER, s)
            _open()
    except Exception as exc:      # broad-ok: ui_cleanup — first-run auto-open is best-effort
        from pycat.utils.general_utils import debug_log
        debug_log("home: first-run auto-open skipped", exc)

    return action
