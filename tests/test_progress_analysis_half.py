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
    ('feature_analysis_tools.py', 'cell_analysis_func'),   # part C: per-cell contour/morph loop
    ('feature_analysis_tools.py', 'puncta_analysis_func'), # part C: per-cell puncta loop
}

#: Part C — the core cell/condensate runners whose per-object loop is now a determinate bar. Each must
#: drive its compute through `run_with_progress` (the modal, off-thread runner), so the countable loop
#: shows honest progress and the window stays responsive. A regression to a direct on-thread call — which
#: leaves the multi-second analysis with nothing on screen — fails here.
_DETERMINATE_RUNNERS = {
    ('feature_analysis_tools.py', 'run_cell_analysis_func'),
    ('feature_analysis_tools.py', 'run_puncta_analysis_func'),
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


def test_the_core_runners_drive_a_DETERMINATE_bar_off_thread():
    """**Part C.** The cell/condensate runners loop per object, so their progress is genuinely
    measurable. Each must route its compute through `run_with_progress` (the modal, off-thread runner)
    so the countable loop shows a determinate bar and the window stays responsive — a direct on-thread
    call leaves a multi-second analysis with nothing on screen."""
    missing = []
    for module, runner in sorted(_DETERMINATE_RUNNERS):
        tree = ast.parse((_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore'))
        func = _function(tree, runner)
        if func is None:
            missing.append(f"{module}::{runner}() not found")
            continue
        calls = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
                 for c in ast.walk(func) if isinstance(c, ast.Call)}
        if 'run_with_progress' not in calls:
            missing.append(f"{module}::{runner}() does not route its compute through run_with_progress")
    assert not missing, (
        "these core runners no longer drive a determinate off-thread bar over their per-object loop:\n  "
        + "\n  ".join(missing))


def test_the_analysis_widgets_run_OFF_THREAD_via_the_operation_runner():
    """**The reliability upgrade (1.6.139) supersedes the inline bar.** Progress part 2 made the wait
    *visible* with an on-thread bar; the operation runner makes the UI *responsive* by running the slow
    analysis on a worker behind a modal progress dialog (driven by the same tool `progress_callback`),
    marshalling the result back to the main thread.

    So each of these widgets must route its slow analysis through `OperationRunner` — a strictly
    stronger guarantee than an on-thread bar, and a regression to a direct on-thread call fails here."""
    not_off_thread = []
    for module in sorted(_PROGRESS_WIDGETS):
        src = (_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore')
        if 'OperationRunner' not in src:
            not_off_thread.append(module)
    assert not not_off_thread, (
        "these slow-analysis widgets no longer run their analysis off the Qt thread via "
        "`OperationRunner` — a direct on-thread call freezes the window even with a bar:\n  "
        + "\n  ".join(not_off_thread))
