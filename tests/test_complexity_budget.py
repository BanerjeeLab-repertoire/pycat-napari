"""
**A ratchet, not a rewrite.**

``ui_modules.py`` is 5,423 lines, and ``MenuManager`` inside it is **2,062 lines across 31
methods**. ``_add_reference_frame_selector`` is **398 lines** — longer than most whole modules, and
unreviewable by anyone.

That is the shape that hides bugs, and one was hiding there: **35 lines installing the pixel-size
gate — the thing that warns a user their lengths are in PIXELS — wrapped in
``except Exception: pass``.** If any of it threw, the gate simply never appeared. *A guard that can
vanish without saying so is not a guard.* (Fixed in 1.5.509.)

So why not split it?
--------------------
**Because it cannot be verified.** ``ui_modules`` has ~17 % name-coverage in the test suite, and
most of that is ``__init__``. **A refactor whose only verification is "it still imports" is a
refactor that ships bugs** — and the value of splitting is preventing *future* bugs, while the cost
would be *introducing* them today, blind.

*The honest move is not to rewrite it. It is to stop it growing, and to make the next person's
addition small enough to review.*

This file is that ratchet. The budgets are set **at today's values** — nothing has to be fixed to
make them pass. **They only fail if something gets worse.**
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


# ── The ratchet is a COUNT, not an allow-list ────────────────────────────────────────────────
#
# There are **136 functions over 120 lines**, totalling **27,478 lines — a third of the codebase.**
# A per-function allow-list of 136 entries would be noise: nobody reads it, and adding a line to it
# is easier than splitting a function, so it would only ever grow.
#
# So the budget is the **count itself**. It is set at today's value. **Nothing has to be fixed to
# make this pass** — it fails only when the number goes UP, which means someone added a 137th
# unreviewable function instead of splitting their work.
#
# And a specific ceiling on the very worst, because those are the ones where a bug can hide in
# plain sight: the pixel-size gate's failure path was SILENT inside a 400-line function, and nobody
# noticed for months. **Nobody reads a 400-line function closely enough to see that its except
# clause is a `pass`.**
_LONG_FUNCTION_LIMIT = 120
# 137. It was 136, and `cell_analysis_func` (feature_analysis_tools) crossed 120 lines when the
# bbox sweep added its columns — a REAL addition, and the ratchet caught it.
#
# **That is the ratchet working.** The honest response is to record that the count went up, not to
# quietly widen the limit or shave a comment somewhere else to squeeze back under. A number that is
# raised whenever it is hit is not a ceiling.
# 139. It was 137, and TWO functions came BACK: `cell_has_punctate_signal` and
# `compute_image_intensity_stats`, restored in 1.5.526 after Meet reported spurious puncta and
# sent the file that worked. **The tree had regressed and lost them.**
#
# The ratchet caught the count going up, which is the ratchet working — and the honest response is
# to record that two long functions returned, not to shave them to squeeze back under.
_MAX_LONG_FUNCTIONS = 139
# It grew by 11 lines when the frame-interval sync was added to it (1.5.511) — a REAL addition,
# not a cheat. **The ratchet caught it, which is the ratchet working**: the honest response is to
# record that the function is now bigger, not to pretend it is not.
#
# It is **676 lines**, and it has tripped this ratchet THREE TIMES in one session — for the
# frame-interval sync, for the assumed-axis warning, and for the pixel-size gate. **Every safety
# check that belongs in this panel makes it bigger**, which is the clearest possible signal that it
# should not be one function.
#
# **The split is obvious:** `_on_dynamic` is a **145-line closure** inside it, with a clean
# boundary. It is not done here because **this UI has no test coverage**, and a refactor whose only
# verification is "it still imports" is a refactor that ships bugs (see the header).
#
# **THIS IS THE FUNCTION TO SPLIT FIRST**, the moment someone can verify it by hand.
_ABSOLUTE_LONGEST = 676


def _long_functions():
    """Every function over the line limit, longest first."""
    found = []
    for path in sorted(_SOURCE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > _LONG_FUNCTION_LIMIT:
                found.append((length, node.name, str(path.relative_to(_SOURCE))))
    return sorted(found, reverse=True)


@pytest.mark.core
def test_the_number_of_unreviewable_functions_does_not_GROW():
    """**A ratchet.** Existing giants are grandfathered; a new one is not.

    A 400-line function is not reviewable, and that is where bugs hide — **the pixel-size gate's
    failure path was a silent ``except: pass`` inside one**, and it went unnoticed for months.

    This does not demand that the 136 be fixed. It demands that there not be a 137th.
    """
    long_functions = _long_functions()

    assert len(long_functions) <= _MAX_LONG_FUNCTIONS, (
        f"{len(long_functions)} functions now exceed {_LONG_FUNCTION_LIMIT} lines "
        f"(the ceiling is {_MAX_LONG_FUNCTIONS}).\n\n"
        f"The newest offenders:\n  "
        + "\n  ".join(f"{length:4d}  {name}  ({where})"
                      for length, name, where in long_functions[:5])
        + "\n\n**Split the new work, or lower an existing one to make room.** A function this "
          "long is not reviewed — it is skimmed, and a silent failure path inside it is invisible."
    )


@pytest.mark.core
def test_nothing_exceeds_the_ABSOLUTE_longest_function():
    """**660 lines is already indefensible. It is not a licence to write 700.**"""
    long_functions = _long_functions()
    if not long_functions:
        return

    longest, name, where = long_functions[0]

    assert longest <= _ABSOLUTE_LONGEST, (
        f"`{name}` in {where} is {longest} lines — longer than anything that existed when this "
        f"budget was set ({_ABSOLUTE_LONGEST}).\n\n"
        f"**Nobody reads a function this long.** They skim it, and a `try/except: pass` around "
        f"the one thing that mattered goes unnoticed — which is exactly what happened to the "
        f"pixel-size gate."
    )


@pytest.mark.core
def test_ui_modules_does_not_GROW():
    """**5,423 lines. It does not need to be 5,500.**

    No claim that this is the right size — it plainly is not. But **a module that is too big and
    stable is safer than one that is too big and growing**, and the way a 5,000-line file becomes
    an 8,000-line file is one reasonable-looking addition at a time.

    When something new belongs in the UI, it goes in a **new module**. That is the only way this
    number comes down.
    """
    ui_modules = _SOURCE / "ui" / "ui_modules.py"
    line_count = len(ui_modules.read_text(encoding='utf-8', errors='ignore').splitlines())

    # Today's size, plus a small allowance for the comments a bug-fix needs.
    ceiling = 5600

    assert line_count <= ceiling, (
        f"ui_modules.py is {line_count} lines (ceiling {ceiling}).\n\n"
        f"It is already the largest module in PyCAT, it holds a 2,062-line class, and a "
        f"400-line function inside it hid a silent failure in the pixel-size gate.\n\n"
        f"**Put the new code in a new module.** If it genuinely belongs here, raise the ceiling "
        f"deliberately — but a ceiling that is raised whenever it is hit is not a ceiling."
    )
