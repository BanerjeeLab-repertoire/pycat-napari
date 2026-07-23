"""
**The pixel-size gate was installed inside `except Exception: pass`. In seven panels.**

    try:
        self._pixel_gate_refresh = add_pixel_size_gate(layout, ...)
    except Exception:
        pass

***If that throws, the gate never installs.*** `_pixel_gate_refresh` is never set, the reset hook
finds `None` and does nothing, and **the panel builds perfectly.** The image then loads at
1.0 µm/px, and **every length, every area, every diffusion coefficient is silently in pixels while
the column header says microns.**

Nothing is printed. Nothing looks wrong.

*That is the pixel-size gate regression that cost a night to find. **It was unfindable by
construction.***

── The distinction this file enforces ───────────────────────────────────────────────────

The audit counted **122 broad `except Exception` blocks** in `file_io.py` and called it an
observability problem. It is — but the count is not the finding. Most of those are wrapped around a
colormap, a tooltip, a thumbnail: **a failure there costs nothing and should stay quiet.**

**A scientific gate is different.** It is the thing standing between a number and a wrong number.
When it fails to install, the analysis does not stop — *it proceeds without the check.*

So the rule is not *"no broad excepts"*. The rule is:

***A `try` that installs a scientific guarantee may not end in a silent swallow.***

`debug_log` does not count. **It prints only when `PYCAT_DEBUG=1`** — which is exactly right for a
colormap and exactly wrong for the gate that decides whether your microns are microns.
`report_guarantee_failure` prints unconditionally, and raises a napari warning so a GUI user sees it
too.
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

# The calls that install a scientific guarantee. Each one exists to stop a number being wrong.
#
#   add_pixel_size_gate        the in-dock gate: is this image calibrated?
#   prompt_pixel_size_on_load  the modal prompt: the last line of defence on load
#   warn_if_assumed_axis       is this stack really TIME, or is it Z?
#   sync_spinbox_from_metadata seeds the frame interval — every dynamics result scales with it
#   record_time_axis           whether the frame-interval warning can fire at all
_GUARANTEES = (
    'add_pixel_size_gate',
    'prompt_pixel_size_on_load',
    'warn_if_assumed_axis',
    'sync_spinbox_from_metadata',
    'record_time_axis',
)

# The one function that reports loudly enough. `debug_log` is NOT here, deliberately.
_LOUD = 'report_guarantee_failure'


def _installs_a_guarantee(try_node, source):
    """Does this `try` body install one of the guarantees?"""
    for node in ast.walk(try_node):
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
            if name in _GUARANTEES:
                return name
    return None


def _handler_is_silent(handler):
    """Does this `except` swallow without saying anything a user would ever see?

    A bare ``pass``/``continue``/``return None`` is silent. **So is `debug_log`** — it prints only
    under ``PYCAT_DEBUG=1``, and a scientist who has just lost their calibration is not running with
    a debug flag set.
    """
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
            if name == _LOUD:
                return False
            # A raise, or a visible warning, also counts as not-silent.
            if name in ('show_warning', 'napari_show_warning'):
                return False
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
    return True


def _broad(handler):
    exception_type = handler.type
    if exception_type is None:
        return True
    return isinstance(exception_type, ast.Name) and exception_type.id in ('Exception', 'BaseException')


@pytest.mark.base
def test_no_SCIENTIFIC_GATE_is_silently_swallowed():
    """**A gate that fails to install must say so. Every time, to every user.**"""
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue

            guarantee = _installs_a_guarantee(node, source)
            if guarantee is None:
                continue

            for handler in node.handlers:
                if not _broad(handler):
                    continue
                if _handler_is_silent(handler):
                    offenders.append(
                        f"{path.relative_to(_SOURCE)}:{handler.lineno}  "
                        f"({guarantee} — swallowed silently)")

    assert not offenders, (
        "**these scientific gates fail silently:**\n  " + "\n  ".join(offenders)
        + "\n\nIf the gate does not install, the analysis **does not stop** — it proceeds without "
          "the check. An uncalibrated image keeps its 1.0 µm/px default and *every length, area and "
          "diffusion coefficient is silently in pixels while the column header says microns.*\n\n"
          "***That is the pixel-size gate regression that cost a night to find.***\n\n"
          "Use `report_guarantee_failure(context, exc)` from `utils.general_utils`. It prints "
          "unconditionally and raises a napari warning — the panel still builds, but the failure is "
          "no longer invisible.\n\n"
          "**`debug_log` does not count.** It prints only under `PYCAT_DEBUG=1`, and a scientist "
          "who has just lost their calibration is not running with a debug flag set."
    )


@pytest.mark.base
def test_the_LOUD_reporter_is_actually_loud_without_a_debug_flag():
    """*A reporter that only speaks under `PYCAT_DEBUG=1` is the bug, not the fix.*

    **Test the metric against the bug.** If `report_guarantee_failure` were quietly wired to
    `debug_log`, every assertion above would still pass and nothing would ever be printed.
    """
    import contextlib
    import io
    import os

    from pycat.utils.general_utils import report_guarantee_failure

    # Explicitly WITHOUT the debug flag — the condition a real user is in.
    previous = os.environ.pop('PYCAT_DEBUG', None)
    try:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            report_guarantee_failure("a test gate", RuntimeError("it broke"))
        printed = captured.getvalue()
    finally:
        if previous is not None:
            os.environ['PYCAT_DEBUG'] = previous

    assert printed.strip(), (
        "`report_guarantee_failure` printed NOTHING without `PYCAT_DEBUG=1`. **That is the bug it "
        "exists to fix.** A silent reporter would let every gate in the codebase fail invisibly "
        "while this test file reported green."
    )
    assert 'it broke' in printed, "the underlying error was not surfaced"
    assert 'a test gate' in printed, "the context was not surfaced — which gate failed?"


@pytest.mark.base
def test_debug_log_stays_QUIET_so_the_distinction_is_real():
    """The other half: a cosmetic failure must **not** shout.

    *If `debug_log` were also loud, every colormap hiccup would look like a lost calibration — and
    the warning that matters would be lost in the noise.* **That is how real warnings get trained
    away.**
    """
    import contextlib
    import io
    import os

    from pycat.utils.general_utils import debug_log

    previous = os.environ.pop('PYCAT_DEBUG', None)
    try:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            debug_log("an optional colormap", ValueError("meh"))
        printed = captured.getvalue()
    finally:
        if previous is not None:
            os.environ['PYCAT_DEBUG'] = previous

    assert printed == '', (
        "`debug_log` printed without `PYCAT_DEBUG=1`. It is for the failures that do not matter — "
        "if it shouts, the failures that DO matter get scrolled past."
    )
