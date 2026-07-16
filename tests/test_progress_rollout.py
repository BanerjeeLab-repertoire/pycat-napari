"""**A widget that materializes a big stack must show that it is working.**

`materialize_stack` decodes a lazy stack one frame at a time. On a long acquisition that is seconds
to minutes, on the Qt main thread, with no indication anything is happening — the UI simply stops.
`materialize_stack(..., progress_callback=...)` and `PhasedProgress` have existed for a while; almost
nothing used them.

This is the **ratchet**: a static check, so a widget added tomorrow that materializes a stack without
progress fails here rather than in someone's hands. Modelled on `test_silent_fallbacks.py`, which
checks UI-wide contracts the same way.

── What this does and does not buy ─────────────────────────────────────────────────────────

`QProgressBar.setValue` calls `repaint()`, which is **synchronous** — so the bar genuinely moves even
though the thread is blocked. Measured: 50 updates in a busy loop produce 50 paints, against 0 for a
control loop that does not touch the bar.

A `QLabel`, by contrast, produces **0** — `setText` only schedules an `update()` that the blocked
event loop never runs. So a status label is not a progress reporter here, whatever it says.

**The work is still synchronous.** This makes the wait visible; it does not remove it. The window may
still report "Not Responding" while the bar advances. Moving materialization to a worker is a
separate, larger change (see `roadmap.rst`).
"""

# Standard library imports
import ast
import pathlib

# Third party imports
import pytest


pytestmark = pytest.mark.core

_UI_DIR = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox'

#: Calls that decode a lazy stack frame by frame — the slow thing a user must see.
_MATERIALIZERS = {'materialize_stack', 'as_full_array'}

#: **A COUNTDOWN, not a blanket.** How many silent materialize sites each module is still allowed —
#: because a module-level "excused" flag un-ratchets the whole module, and a new silent call slips
#: straight in. (It did: a mutation adding a second silent materialize to `fusion_ui` passed.) Same
#: discipline as the complexity budget — the number only ever goes DOWN.
#:
#: Each of these needs a bar ADDED to its form, which is a UI change rather than the one-line wiring
#: this pass is. Listed, not hidden: this IS the remaining work.
_STILL_SILENT = {
    # module                      allowed  why
    'condensate_physics_ui.py':   1,     # _on_fusion has no bar; its siblings do
    'data_qc_ui.py':              1,     # no QProgressBar is ever constructed here
    'fusion_ui.py':               1,     # imports QProgressBar; never constructs one
    # `_ivbf_focus_qc` has no bar of its own, though `_ivbf_dynamics` next door does. Worth naming
    # how that was found: wiring it to the sibling's `prog` LOOKED right and would have raised
    # NameError on the first click — `test_no_undefined_names` caught it. A bar is not in scope just
    # because a sibling section has one.
    'invitro_bf_ui.py':           1,     # _ivbf_focus_qc
    # `_get_stack` is a shared, CACHED helper called from several sections, so no single bar is
    # "its own" — wiring it means every caller passing a reporter down, a signature change across
    # the module. Worth recording that the spec calls this file "the ONE reference implementation
    # that does it right" and says not to touch it: it IS right for its batch/export paths, and
    # this stack load was simply missed. The ratchet found it on its first run.
    'temperature_ui.py':          1,     # _get_stack
}

#: Materializing a 2-D array is instant — a bar would flash and vanish. Only STACKS need one.
_TINY_OK = {
    ('frap_ui.py', '_offer_stack_2d_images'),   # guarded by `ndim != 2: continue` first
}


def _ui_modules():
    return sorted(p for p in _UI_DIR.glob('*_ui.py'))


def _materialize_calls(tree):
    """Every `materialize_stack(...)` / `as_full_array(...)` call, with whether it got progress."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
        if name not in _MATERIALIZERS:
            continue
        has_progress = any(kw.arg == 'progress_callback' for kw in node.keywords)
        found.append((node.lineno, name, has_progress))
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
def test_a_widget_that_MATERIALIZES_a_stack_reports_progress(path):
    """**The ratchet.** A new widget that decodes a stack silently fails here."""
    tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))

    silent = []
    for lineno, name, has_progress in _materialize_calls(tree):
        if has_progress:
            continue
        handler = _enclosing_function(tree, lineno)
        if _excused(path.name, handler):
            continue
        silent.append(f"{path.name}:{lineno} — {name}() in {handler}()")

    allowed = _STILL_SILENT.get(path.name, 0)
    assert len(silent) <= allowed, (
        f"{path.name} has {len(silent)} materialize call(s) with no progress; {allowed} are on the "
        f"countdown:\n  " + "\n  ".join(silent)
        + "\n\nThese decode a stack frame-by-frame with no sign to the user that anything is "
          "happening. Pass `progress_callback=` — build a `PhasedProgress` on the widget's own "
          "QProgressBar (see any wired sibling). If the widget has no bar, add one.\n"
          "Do NOT raise the number in `_STILL_SILENT` to make this pass: it only goes down."
    )


def test_the_widgets_that_were_ROLLED_OUT_stay_rolled_out():
    """Names them, so a regression is a named loss rather than a quiet one."""
    wired = ('frap_ui.py', 'invitro_fluor_ui.py', 'invitro_bf_ui.py', 'brightfield_ui.py',
             'condensate_physics_ui.py')
    for module in wired:
        source = (_UI_DIR / module).read_text(encoding='utf-8', errors='ignore')
        assert 'progress_callback=' in source, f"{module} lost its progress wiring"


def test_the_reference_implementation_is_UNTOUCHED():
    """`temperature_ui` already passed a callback and is the pattern the rest copied."""
    source = (_UI_DIR / 'temperature_ui.py').read_text(encoding='utf-8', errors='ignore')
    assert source.count('progress_callback=') >= 2


def test_the_EXCUSED_list_is_SMALL_and_each_entry_is_real():
    """An allowlist is a promise to come back. It should be short, and every entry should name a
    module that actually exists — a stale excuse silently un-ratchets a widget."""
    assert sum(_STILL_SILENT.values()) <= 5, (
        "the countdown is going UP — it is the remaining work, not a place to put failures")
    for module in list(_STILL_SILENT) + [m for m, _h in _TINY_OK]:
        assert (_UI_DIR / module).exists(), f"{module} no longer exists — the excuse is stale"


def test_the_excused_widgets_REALLY_have_no_progress_bar():
    """The excuse is "there is no bar to wire", so it had better be true. If someone adds one, the
    excuse expires and the ratchet should start demanding the wiring.

    Checks for a CONSTRUCTED bar, not the string: `fusion_ui` imports `QProgressBar` and never
    builds one, so matching the name alone made this test fail against its own allowlist — which
    is how the sloppiness got caught.
    """
    for module in ('data_qc_ui.py', 'fusion_ui.py'):
        source = (_UI_DIR / module).read_text(encoding='utf-8', errors='ignore')
        assert 'QProgressBar(' not in source, (
            f"{module} constructs a QProgressBar now — wire it and drop it from _NO_REPORTER_YET")
