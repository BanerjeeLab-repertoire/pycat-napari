"""
Every requirements file must be parseable by pip.

Why this exists
---------------
``config/requirements-arm-mac.txt`` contained::

    pytorch==2.1.2=cpu_generic_py39hef92293_4

which is **conda** syntax (``name=version=build``) with an extra ``=``, copied from the conda
environment YAML. It is invalid for **pip** *and* invalid for **conda** — pip's parser rejects
it outright with ``InvalidRequirement``.

**So that file could never be installed by anything.** It had been broken since it was written,
and nothing noticed, because nothing reads it: it is referenced by no workflow, no
``pyproject.toml``, and no documentation. Dependabot was simply the first tool to try — and it
**aborted the entire dependency scan** on line 2, so no security updates were being checked at
all.

A file that no build step reads is a file whose breakage is invisible until an external tool
trips over it. This test reads them, so it is not invisible.

The conda YAMLs are a separate matter and are NOT checked here: ``name=version=build`` is
correct *there*, and it is only the ``.txt`` copies that mixed the two syntaxes.
"""

import pathlib

import pytest

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "config"


def _requirement_lines(path):
    """The lines pip would try to parse: not blanks, comments, or ``-r``/``-e`` directives."""
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(),
                                 start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        yield lineno, line


@pytest.mark.core
def test_requirements_files_parse_as_pip():
    """Every config/requirements*.txt must be valid pip syntax."""
    packaging = pytest.importorskip(
        "packaging.requirements",
        reason="`packaging` is needed to parse requirements the way pip does")

    failures = []
    for path in sorted(_CONFIG.glob("requirements*.txt")):
        for lineno, line in _requirement_lines(path):
            try:
                packaging.Requirement(line)
            except Exception as exc:                      # noqa: BLE001
                failures.append(
                    f"{path.name}:{lineno}: {line!r}\n      -> {type(exc).__name__}: {exc}")

    assert not failures, (
        "These requirement lines are not valid pip syntax:\n  "
        + "\n  ".join(failures)
        + "\n\nThe usual cause is CONDA syntax in a pip file: conda pins are "
          "`name=version=build`, pip needs `name==version`. A line like "
          "`pytorch==2.1.2=cpu_generic_py39hef92293_4` is valid for NEITHER — and it made "
          "Dependabot abort its entire dependency scan, so no security updates were being "
          "checked."
    )
