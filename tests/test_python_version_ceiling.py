"""**`requires-python` and the `Programming Language :: Python ::` classifiers must agree.**

PyCAT once declared `requires-python = ">=3.12,<3.14"` (permitting 3.13) while advertising only a 3.12
classifier — internally inconsistent, and worse, 3.13 could not actually install (`cellpose<4` → numpy<2.1 →
no cp313 wheel → source build fails). The ceiling was reverted to `<3.13` so pip refuses 3.13 cleanly. This
guard keeps the pair from drifting again in either direction: every minor the ceiling PERMITS must be
advertised, and no advertised minor may fall OUTSIDE the ceiling. It is the check that would have caught the
original state. See docs/source/known_issues.md for the unblock condition.
"""
import pathlib
import re

import pytest

pytestmark = pytest.mark.core

_PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load():
    try:
        import tomllib
        return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    except ModuleNotFoundError:                      # pragma: no cover - py<3.11
        tomli = pytest.importorskip("tomli")
        return tomli.loads(_PYPROJECT.read_text(encoding="utf-8"))


def _permitted_minors(requires_python):
    """The 3.x minor versions a `requires-python` string permits, as a set of ints. Handles the `>=3.a,<3.b`
    form PyCAT uses (lower inclusive, upper exclusive)."""
    lo = re.search(r">=\s*3\.(\d+)", requires_python)
    hi = re.search(r"<\s*3\.(\d+)", requires_python)
    assert lo and hi, f"requires-python {requires_python!r} is not the expected `>=3.a,<3.b` form"
    return set(range(int(lo.group(1)), int(hi.group(1))))     # [lo, hi)


def _advertised_minors(classifiers):
    """The 3.x minors advertised by `Programming Language :: Python :: 3.x` classifiers (the bare `:: 3` is
    not a specific minor and is ignored)."""
    minors = set()
    for c in classifiers:
        m = re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", c)
        if m:
            minors.add(int(m.group(1)))
    return minors


def test_requires_python_and_classifiers_agree():
    project = _load()["project"]
    permitted = _permitted_minors(project["requires-python"])
    advertised = _advertised_minors(project.get("classifiers", []))

    assert permitted, "no permitted 3.x minors parsed from requires-python"
    assert advertised, "no specific `Programming Language :: Python :: 3.x` classifier is declared"
    # every permitted minor is advertised, and nothing is advertised outside the ceiling
    assert permitted == advertised, (
        f"requires-python permits 3.x minors {sorted(permitted)} but classifiers advertise "
        f"{sorted(advertised)} — they must match (declaring support that fails at install, or advertising a "
        f"version outside the ceiling, is exactly the drift this guards).")


def test_the_ceiling_excludes_313_until_the_upstream_cellpose_numpy_pin_is_relaxed():
    # A focused pin on the current, verified reality: 3.13 cannot install (see known_issues.md). If this ever
    # changes, it is a DELIBERATE re-enable (bump the ceiling + add the classifier + re-verify segmentation),
    # so updating this test is the intended signal that the change was made on purpose.
    permitted = _permitted_minors(_load()["project"]["requires-python"])
    assert 13 not in permitted, "3.13 is not installable while cellpose<4 forces numpy<2.1; see known_issues.md"
