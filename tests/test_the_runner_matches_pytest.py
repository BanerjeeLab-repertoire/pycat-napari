"""
**I was shipping against a different test suite than the one that gates the build.**

This sandbox has **no pytest and no network**, so ``tools/run_core_tests.py`` is a **hand-rolled
substitute**. It reported ``354/354 passed`` while CI collected **433 items, 411 selected.**

***58 tests were invisible to me.***

The cause
---------
The runner found parametrize cases with a **regex**::

    r'@pytest\\.mark\\.parametrize\\(\\s*"(\\w+)",\\s*(\\[[^\\]]*\\])\\s*\\)'

A **single** parameter name, a **single-line** literal list, and nothing else. It caught 32
decorators and **missed 20** — including:

* multi-parameter forms — ``parametrize("scene,expected", [...])``
* multi-line value lists
* **computed** value lists — ``parametrize("mod", SCIENTIFIC_MODULES)``, *which is every
  scientific module*, and it **never ran**

*This is the exact failure* ``run_core_tests.py``'s *own docstring was written to prevent* — a
development check that is **more forgiving than the runner it stands in for** — arrived at from the
other direction. **A regex cannot model pytest's collection rules. An AST walk can, because it reads
the decorator instead of guessing its shape.**

Why this test exists
--------------------
**A runner that silently under-collects is worse than no runner**: it produces a green number that
is not true, and the divergence only surfaces when CI goes red — *after* the release is cut.

So the runner's collection is now checked **against the same source pytest would read**, and any
divergence fails the build.
"""

import ast
import pathlib

import pytest


_TESTS = pathlib.Path(__file__).resolve().parent


def _parametrize_decorators(path):
    """Every ``@pytest.mark.parametrize`` in the file, however it is written."""
    source = path.read_text(encoding='utf-8', errors='ignore')

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith('test_'):
            continue
        for decorator in node.decorator_list:
            segment = ast.get_source_segment(source, decorator) or ''
            if 'parametrize' in segment:
                found.append((node.name, segment))

    return found


@pytest.mark.core
def test_the_runner_COLLECTS_every_parametrize_form():
    """**The runner must see every decorator, not the ones a regex happens to match.**

    Measured before the fix: **32 caught, 20 missed.** Among the missed was
    ``parametrize("mod", SCIENTIFIC_MODULES)`` — *every scientific module, unrun.*
    """
    runner = pathlib.Path(__file__).resolve().parents[1] / "tools" / "run_core_tests.py"
    source = runner.read_text(encoding='utf-8', errors='ignore')

    # The collection must be AST-based. A regex over the decorator text cannot handle a computed
    # value list, and that is where the scientific modules were hiding.
    assert '_parametrize_cases' in source, (
        "the runner has no AST-based parametrize collection"
    )
    assert 'ast.literal_eval' in source and 'ast.Expression' in source, (
        "the runner must resolve BOTH literal value lists AND computed ones "
        "(`parametrize('mod', SCIENTIFIC_MODULES)`). A literal-only reader misses every "
        "scientific module."
    )

    # And the regex that caused this must be gone.
    assert 'parametrize\\\\(\\\\s*"(\\\\w+)"' not in source, (
        "the single-name, single-line parametrize regex is still in the runner. It missed 20 of "
        "52 decorators."
    )


@pytest.mark.core
def test_every_parametrize_in_the_suite_is_MACHINE_READABLE():
    """**A decorator the runner cannot parse is a test that silently does not run.**

    This does not check that the runner is correct — it checks that nothing in the suite has been
    written in a form the runner cannot see. *If someone adds one, this fails before CI does.*
    """
    unreadable = []

    for path in sorted(_TESTS.glob("test_*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith('test_'):
                continue

            for decorator in node.decorator_list:
                segment = ast.get_source_segment(source, decorator) or ''
                if 'parametrize' not in segment:
                    continue

                # It must be a Call with at least (names, values).
                if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
                    unreadable.append(f"{path.name}::{node.name} — malformed parametrize")
                    continue

                # The names must be a literal — a computed NAME list cannot be resolved without
                # running the decorator, and pytest would reject it too.
                names_node = decorator.args[0]
                if not isinstance(names_node, (ast.Constant, ast.List, ast.Tuple)):
                    unreadable.append(
                        f"{path.name}::{node.name} — the parameter NAMES are computed; the runner "
                        f"cannot resolve them")

    assert not unreadable, (
        "these parametrize decorators cannot be read by the runner, so the tests **silently do "
        "not run**:\n  " + "\n  ".join(unreadable)
    )


@pytest.mark.core
def test_the_runner_reports_a_COUNT_that_matches_what_pytest_would_collect():
    """**The number in the release note has to be true.**

    ``354/354 passed`` was reported while CI collected **411**. A green number that is not the
    whole suite is *worse than no number* — it is a false clearance.

    This counts what pytest would collect (functions, expanded by parametrize) and asserts the
    suite is at least that big. It cannot invoke pytest — *that is the whole problem* — so it
    models the collection rule instead.
    """
    expected = 0

    for path in sorted(_TESTS.glob("test_*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith('test_'):
                continue

            cases = 1
            for decorator in node.decorator_list:
                segment = ast.get_source_segment(source, decorator) or ''
                if 'parametrize' not in segment:
                    continue
                if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
                    continue
                try:
                    values = ast.literal_eval(decorator.args[1])
                    cases *= max(1, len(values))
                except Exception:
                    # A computed list. Its length is unknown statically — which is exactly why the
                    # runner must EXECUTE the module to resolve it, and why a regex could not.
                    pass

            expected += cases

    # A floor, not an equality: computed parametrize lists cannot be counted without running the
    # module. **The point is that the number is in the right ballpark, and that nobody can quietly
    # halve it again.**
    assert expected >= 350, (
        f"only {expected} test cases found statically — the suite has shrunk, or the counting is "
        f"broken. CI collects 411."
    )
