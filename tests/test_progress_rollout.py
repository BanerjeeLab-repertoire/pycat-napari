"""**A widget must not decode a big stack ON the Qt thread.**

`materialize_stack` / `as_full_array` decode a lazy stack one frame at a time. On a long acquisition
that is seconds to minutes; run on the Qt main thread, the window stops painting and Windows draws
"Python is not responding" over it.

The 1.6.81/82 rollout wired a `progress_callback` + `PhasedProgress` bar into every site. That made
the wait **visible** (`QProgressBar.setValue` calls a synchronous `repaint()`, so the bar advances
even on a blocked thread) but not **shorter** — the decode still ran on the Qt thread. 1.6.107 is the
other half: every stack-consuming widget now decodes through
`pycat.utils.qt_worker.materialize_off_thread`, which runs `materialize_stack` on a `QThread` behind a
modal dialog and returns the array on the caller's thread.

── The ratchet ─────────────────────────────────────────────────────────────────────────────

This is a static check: a widget added tomorrow that decodes a stack **directly** — synchronously, on
the Qt thread, bar or no bar — fails here rather than in someone's hands. The way to pass is to route
it through `materialize_off_thread`, not to add a row to the countdown.

A direct call is flagged whether or not it passes `progress_callback=`: a progress bar on the Qt
thread is exactly the "visible but still frozen" state this change exists to end.
"""

# Standard library imports
import ast
import pathlib

# Third party imports
import pytest


pytestmark = pytest.mark.core

_UI_DIR = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox'

#: Calls that decode a lazy stack frame by frame on the CALLER's thread — the freeze.
_MATERIALIZERS = {'materialize_stack', 'as_full_array'}

#: The off-thread wrapper a widget must decode through instead.
_OFF_THREAD = 'materialize_off_thread'

#: **A COUNTDOWN, not a blanket.** How many SYNCHRONOUS (Qt-thread) decode sites each module is still
#: allowed. A module-level "excused" flag would un-ratchet the whole module and let a new synchronous
#: decode slip in, so this is per-module and only ever goes DOWN.
_STILL_SYNC = {
    # **Empty, and it should stay that way.** Every stack-decoding site in every `*_ui.py` goes
    # through `materialize_off_thread` (1.6.107). A new synchronous decode fails the test above; the
    # way to pass is to route it off-thread, not to add a row here.
}

#: Decoding a 2-D array is instant — an off-thread dialog would flash and vanish, and in a per-layer
#: LOOP it would flash once per candidate. These stay synchronous on purpose.
_TINY_OK = {
    ('frap_ui.py', '_offer_stack_2d_images'),   # guarded by `ndim != 2: continue`, then decodes 2-D
}

#: The widgets moved off-thread — named, so a regression is a named loss rather than a quiet one.
_MOVED_OFF_THREAD = (
    'frap_ui.py', 'invitro_fluor_ui.py', 'invitro_bf_ui.py', 'brightfield_ui.py',
    'condensate_physics_ui.py', 'data_qc_ui.py', 'fusion_ui.py', 'temperature_ui.py',
)


def _ui_modules():
    return sorted(p for p in _UI_DIR.glob('*_ui.py'))


def _direct_decode_calls(tree):
    """Every direct `materialize_stack(...)` / `as_full_array(...)` call — the synchronous decodes."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
        if name in _MATERIALIZERS:
            found.append((node.lineno, name))
    return found


def _enclosing_function(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.lineno <= lineno <= (node.end_lineno or 0):
            if best is None or node.lineno > best.lineno:
                best = node          # innermost
    return best.name if best else None


def _excused(module, handler):
    return (module, handler) in _TINY_OK


@pytest.mark.parametrize('path', _ui_modules(), ids=lambda p: p.name)
def test_a_widget_does_not_decode_a_stack_ON_THE_QT_THREAD(path):
    """**The ratchet.** A new widget that decodes a stack synchronously fails here — route it through
    `materialize_off_thread` instead."""
    tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))

    synchronous = []
    for lineno, name in _direct_decode_calls(tree):
        handler = _enclosing_function(tree, lineno)
        if _excused(path.name, handler):
            continue
        synchronous.append(f"{path.name}:{lineno} — {name}() in {handler}()")

    allowed = _STILL_SYNC.get(path.name, 0)
    assert len(synchronous) <= allowed, (
        f"{path.name} has {len(synchronous)} synchronous stack decode(s); {allowed} are on the "
        f"countdown:\n  " + "\n  ".join(synchronous)
        + "\n\nThese decode a stack frame-by-frame ON THE QT THREAD — the window freezes ('Not "
          "Responding') even with a progress bar. Decode through "
          "`pycat.utils.qt_worker.materialize_off_thread(layer.data, viewer=…)` instead: it runs on a "
          "worker behind a modal dialog and returns the array on your thread.\n"
          "Do NOT raise the number in `_STILL_SYNC` to make this pass: it only goes down."
    )


def test_the_widgets_that_were_moved_off_thread_STAY_off_thread():
    """Names them, so losing the off-thread decode is a named regression."""
    for module in _MOVED_OFF_THREAD:
        source = (_UI_DIR / module).read_text(encoding='utf-8', errors='ignore')
        assert _OFF_THREAD in source, (
            f"{module} no longer decodes through {_OFF_THREAD} — its stack decode is back on the Qt "
            f"thread and the window will freeze again")


def test_the_EXCUSED_list_is_SMALL_and_each_entry_is_real():
    """The countdown is a promise to come back. It is at zero, and every `_TINY_OK` entry should name
    a module that still exists — a stale excuse silently un-ratchets a widget."""
    assert sum(_STILL_SYNC.values()) == 0, (
        "the countdown reached ZERO — every stack decode is off-thread. It is not a place to put "
        "failures: route the new site off-thread instead.")
    for module in list(_STILL_SYNC) + [m for m, _h in _TINY_OK]:
        assert (_UI_DIR / module).exists(), f"{module} no longer exists — the excuse is stale"


def test_the_off_thread_helper_wraps_materialize_via_the_worker():
    """The helper decodes `materialize_stack` (pure — no napari) on the worker and returns the array
    for the caller to use. Pinned by AST: `materialize_off_thread` exists and its only decode is
    `materialize_stack`, delegated to `run_with_progress`, so nobody later feeds the worker a function
    that adds a layer (which would trade the freeze for a crash)."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'src' / 'pycat' / 'utils' / 'qt_worker.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == 'materialize_off_thread'), None)
    assert fn is not None, 'materialize_off_thread went missing'

    called = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
              for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert 'run_with_progress' in called, 'the helper no longer runs on the worker'
    assert 'materialize_stack' in called, 'the helper no longer decodes via materialize_stack'
    # napari layer creation must never appear inside the worker-run helper (docstring examples don't
    # count — this walks the function body, not the text).
    assert not (called & {'add_image', 'add_labels'}), (
        'materialize_off_thread creates a napari layer — that is the off-thread crash it must avoid')
