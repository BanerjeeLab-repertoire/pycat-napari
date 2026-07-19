"""**The ANALYSIS half of the progress work — a widget whose slow work is the analysis itself.**

`test_progress_rollout.py` ratchets the *materialization* half (decoding a lazy stack must go
off-thread). This is the sibling ratchet for the other problem the roadmap parked: widgets whose slow
work is the **analysis**, which therefore need a `progress_callback` on the TOOL function, not just UI
wiring. There was nothing on screen during a multi-second Cascade-RF segmentation or a per-cycle rip
fit — because the slowness is the computation, a bar alone would have nothing to drive it.

Two things are pinned here, by AST (so it runs headless, without importing the Qt widgets):

1. the slow tool entry points accept `progress_callback(done, total)` — the signature `materialize_stack`
   uses, so `PhasedProgress` composes;
2. the two widgets construct a real **`QProgressBar`** and drive it from that callback via
   `PhasedProgress` — a `QLabel` is NOT a progress reporter (measured: `setValue` repaints
   synchronously and moves on a busy thread; `setText` only schedules an `update()` a blocked event
   loop never runs).

**A ratchet:** to add a new slow analysis widget, add its (module, function) here and give it a bar —
so a future zero-feedback slow widget fails this test rather than freezing in someone's hands.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox'

#: Slow tool entry points whose cost IS the analysis — each must accept `progress_callback`. Grows as
#: new slow analyses are wired; it only ever gains rows (a removed callback is a regression).
_SLOW_TOOL_ENTRYPOINTS = {
    ('contrast_cascade_tools.py', 'cascade_rf_segment'),   # features → train → predict (3 stages)
    ('fd_curve_tools.py', 'detect_all_rips'),              # a WLC fit per half-cycle (the real loop)
}

#: The widgets that run those slow analyses — each must build a real QProgressBar and drive it from the
#: tool callback via PhasedProgress.
_PROGRESS_WIDGETS = {
    'contrast_cascade_ui.py',
    'fd_curve_ui.py',
}


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _params(func):
    a = func.args
    return {arg.arg for arg in (a.args + a.posonlyargs + a.kwonlyargs)}


def test_the_slow_tool_entrypoints_accept_a_progress_callback():
    """A slow analysis function with no `progress_callback` cannot drive a bar — the widget freezes with
    nothing on screen. This names any that lost (or never had) it."""
    missing = []
    for module, func_name in sorted(_SLOW_TOOL_ENTRYPOINTS):
        tree = ast.parse((_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore'))
        func = _function(tree, func_name)
        if func is None:
            missing.append(f"{module}::{func_name}() not found")
        elif 'progress_callback' not in _params(func):
            missing.append(f"{module}::{func_name}() has no `progress_callback` parameter")
    assert not missing, (
        "slow analysis entry points must accept `progress_callback(done, total)` so a widget can show "
        "progress during the computation:\n  " + "\n  ".join(missing))


def test_the_analysis_widgets_construct_a_real_QProgressBar():
    """A `QProgressBar` (repaints synchronously on `setValue`), never a `QLabel` (whose `setText` a
    blocked event loop never paints). This asserts the bar exists in each slow-analysis widget."""
    missing = []
    for module in sorted(_PROGRESS_WIDGETS):
        tree = ast.parse((_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore'))
        builds_bar = any(
            (getattr(node.func, 'id', None) == 'QProgressBar')
            for node in ast.walk(tree) if isinstance(node, ast.Call))
        if not builds_bar:
            missing.append(module)
    assert not missing, (
        "these slow-analysis widgets do not construct a QProgressBar (a QLabel is not a progress "
        "reporter on a busy Qt thread):\n  " + "\n  ".join(missing))


def test_the_bar_is_DRIVEN_by_the_tool_callback_not_faked():
    """A bar that is not fed the tool's `progress_callback` is decoration. Each widget must route the
    callback through `PhasedProgress` — the piece that maps `callback(done, total)` onto the bar."""
    undriven = []
    for module in sorted(_PROGRESS_WIDGETS):
        src = (_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore')
        if not ('PhasedProgress' in src and 'progress_callback=' in src):
            undriven.append(module)
    assert not undriven, (
        "these widgets construct a bar but do not drive it from the tool's progress_callback via "
        "PhasedProgress — a bar with nothing driving it is worse than none:\n  " + "\n  ".join(undriven))
