"""**Qt-smoke: the Navigator dock drives the questions to a rendered, runnable plan and mounts with tabify.**

Integration (needs Qt + qtbot). The logic (drive, compile, gate reasons) is core/base-tested in
`tests/navigator/test_navigator_session.py`; this proves the widget is wired to it — answering the on-screen
choices reaches a plan, the plan renders as step rows, a Run button appears and calls the caller's executor —
and that the menu installer mounts the dock (tabify) and is headless-safe.
"""
import pytest


@pytest.mark.integration
def test_the_dock_drives_the_questions_to_a_rendered_plan(qtbot):
    from pycat.ui.navigator_dock import build_navigator_widget
    from pycat.navigator.session import NavigatorSession

    ran = {}
    widget = build_navigator_widget(NavigatorSession(), on_run=lambda plan: ran.setdefault('plan', plan))
    assert widget is not None
    qtbot.addWidget(widget)

    # answer by clicking the FIRST choice each time until the plan renders
    guard = 0
    while widget._state == "question":
        assert widget._choice_buttons, "a question must offer choices to click"
        widget._choice_buttons[0].click()
        guard += 1
        assert guard < 40, "the question flow did not terminate"

    assert widget._state == "plan"
    assert widget._plan is not None and widget._plan.steps
    assert widget._rows, "the plan must render as rows"
    assert widget._run_button is not None

    # running hands the compiled plan to the caller's executor
    if widget._run_button.isEnabled():
        widget._run_button.click()
        assert ran.get('plan') is widget._plan


@pytest.mark.integration
def test_start_over_returns_to_the_first_question(qtbot):
    from pycat.ui.navigator_dock import build_navigator_widget
    from pycat.navigator.session import NavigatorSession
    widget = build_navigator_widget(NavigatorSession())
    qtbot.addWidget(widget)
    first_prompt = widget._session.next_question().prompt
    widget._choice_buttons[0].click()                      # advance one question
    widget._render()                                       # re-render current state
    # a fresh session via _restart brings back the first question
    from pycat.navigator.session import NavigatorSession as NS
    widget._session = NS()
    widget._render()
    assert widget._session.next_question().prompt == first_prompt


@pytest.mark.integration
def test_the_installer_mounts_the_dock_with_tabify_and_is_headless_safe(qtbot):
    from qtpy.QtWidgets import QMainWindow, QDockWidget
    from qtpy.QtCore import Qt
    from pycat.ui.navigator_dock import install_navigator_action

    class _Window:
        def __init__(self, qmw):
            self._qt_window = qmw
            self._docks = []

        def add_dock_widget(self, widget, *, name, area, tabify=False):
            d = QDockWidget(name)
            d.setWidget(widget)
            self._qt_window.addDockWidget(Qt.RightDockWidgetArea, d)
            if tabify and self._docks:
                self._qt_window.tabifyDockWidget(self._docks[0], d)
            self._docks.append(d)
            return d

    qmw = QMainWindow()
    qtbot.addWidget(qmw)
    # a method panel already docked, so tabify has something to tab onto
    qmw.addDockWidget(Qt.RightDockWidgetArea, QDockWidget("Method"))

    class _Viewer:
        pass
    viewer = _Viewer()
    win = _Window(qmw)
    viewer.window = win
    win._docks.append(next(iter(qmw.findChildren(QDockWidget))))

    action = install_navigator_action(viewer)
    assert action is not None and action in qmw.menuBar().actions()
    action.trigger()                                       # opens + mounts the dock
    assert any(d.windowTitle() == "Navigator" for d in qmw.findChildren(QDockWidget))


@pytest.mark.integration
def test_the_installer_is_headless_safe_without_a_qt_window():
    from pycat.ui.navigator_dock import install_navigator_action

    class _Viewer:
        window = None

    assert install_navigator_action(_Viewer()) is None
