"""**The status-marker widgets render the fixed states — ready≠done, cycling-button, optional-blue.**

The pure decision is pinned Qt-free in ``test_marker_logic``; this pins that ``field_status`` actually
WIRES it into the painted circle. Integration-marked (needs a real QApplication via ``qtbot``), so it
skips in a headless core run. Covers the tester's three mechanism bugs:
  Fix 3 — an action button whose inputs are satisfied shows the READY ring (outlined amber), NOT solid
          green; green only after the run.
  Fix 1 — a CYCLING button (complete_on_click=False) does not go green on a raw click; only mark_done().
  Fix 2 — an optional action goes blue (kept, meaning made explicit) on run.
"""
import pytest


@pytest.mark.integration
def test_ready_is_outlined_amber_and_green_only_after_run(qtbot):
    from PyQt5.QtWidgets import QPushButton, QComboBox
    from pycat.ui.field_status import button_with_circle, _COLORS

    dd = QComboBox(); dd.addItems(["Select image", "MyLayer"])
    btn = QPushButton("Run segmentation")
    w = button_with_circle(btn, watch_dropdowns=[dd])
    c = w._status_circle

    assert c._color == _COLORS['red'] and c._filled is True         # nothing selected → resting red

    dd.setCurrentIndex(1)                                            # valid input → READY, not done
    assert c._color == _COLORS['ready'], "ready must be the amber key, never green"
    assert c._color != _COLORS['green']
    assert c._filled is False, "ready must render OUTLINED so it can't be misread as done"

    btn.click()                                                     # actually run → DONE
    assert c._color == _COLORS['green'] and c._filled is True


@pytest.mark.integration
def test_cycling_button_does_not_complete_on_a_raw_click(qtbot):
    from PyQt5.QtWidgets import QPushButton
    from pycat.ui.field_status import button_with_circle, _COLORS

    btn = QPushButton("Draw Lines")
    w = button_with_circle(btn, complete_on_click=False)
    c = w._status_circle

    btn.click()                                                     # a Draw-phase click must NOT green
    assert c._color == _COLORS['red'], "cycling button greened on a raw click (the Fix 1 bug)"

    w.mark_done()                                                   # the real Measure completion
    assert c._color == _COLORS['green']


@pytest.mark.integration
def test_optional_action_goes_blue_on_run(qtbot):
    from PyQt5.QtWidgets import QPushButton
    from pycat.ui.field_status import button_with_circle, _COLORS

    btn = QPushButton("Run upscaling")
    w = button_with_circle(btn, optional=True)
    assert w._status_circle._color == _COLORS['yellow']            # optional at rest → yellow
    btn.click()
    assert w._status_circle._color == _COLORS['blue']             # optional done → blue (kept)
