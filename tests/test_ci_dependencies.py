"""
The CI dependency list must be DERIVED from the code, not maintained by hand.

Why this exists
---------------
``.github/workflows/core.yml`` installs a minimal compute set — the GUI stack is excluded on
purpose, because the whole point of the headless job is to prove the science imports without
it. **It is not supposed to exclude the maths.**

That list was hand-maintained, and it drifted **twice**:

* **1.5.442** — ``largestinteriorrectangle`` (a declared dependency of the package, but absent
  from the CI install) made ``spatial_acf_tools`` unimportable.
* **1.5.444** — ``scikit-learn``. ``segmentation_tools`` imports ``RandomForestClassifier`` at
  module scope, and ``two_channel_coloc_tools`` and ``timeseries_condensate_tools`` import
  ``segmentation_tools``, so **all three** inherit it.

The second one is the instructive one. The dependency had **always** been there — it was
invisible because ``segmentation_tools`` imported napari at module scope and **could not be
imported at all**, so its own dependencies never surfaced. **Decoupling the science exposed a
dependency that had been hiding behind a GUI import for the life of the project.**

Both times the sandbox had *stubbed* the missing package, so it looked fine locally and went red
in CI.

What this does
--------------
Walks the module-scope imports of every module the headless test guards, following
``pycat.toolbox`` imports **transitively** (which is how the two time-series modules inherit
sklearn from segmentation_tools), and asserts that each third-party package appears in the
workflow's install step.

A hand-maintained list of a derivable fact will drift. This derives it.
"""

import ast
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOLBOX = _ROOT / "src" / "pycat" / "toolbox"
_WORKFLOW = _ROOT / ".github" / "workflows" / "core.yml"
_HEADLESS_TEST = _ROOT / "tests" / "test_headless_science.py"

# The GUI stack is excluded from the headless job ON PURPOSE — that is what it tests.
_GUI = {"napari", "PyQt5", "PyQt6", "qtpy"}

# Lazily imported on purpose (heavy, optional, or GPU-only). These must NOT be at module
# scope in a guarded module, and the headless test already enforces that.
_LAZY_BY_DESIGN = {"cellpose", "torch", "cupy", "numba", "trackmate", "jpype"}

_STDLIB = {
    "math", "os", "sys", "re", "json", "warnings", "pathlib", "typing", "collections",
    "functools", "itertools", "dataclasses", "enum", "abc", "copy", "time", "datetime",
    "tempfile", "shutil", "subprocess", "importlib", "contextlib", "traceback", "logging",
    "random", "hashlib", "csv", "io", "glob", "uuid", "textwrap", "__future__", "concurrent",
    "threading", "queue", "pickle", "inspect", "operator", "string", "struct", "base64",
    "zipfile", "platform", "builtins",
}

# import name -> pip name
_PIP_NAME = {
    "skimage": "scikit-image",
    "cv2": "opencv-python-headless",
    "pywt": "pywavelets",
    "SimpleITK": "simpleitk",
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "tifffile": "tifffile",
}


def _guarded_modules():
    """The modules the headless test asserts must import."""
    source = _HEADLESS_TEST.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"SCIENTIFIC_MODULES\s*=\s*\[(.*?)\]", source, re.S)
    assert match, "could not find SCIENTIFIC_MODULES in test_headless_science.py"
    return re.findall(r'"([\w_]+)"', match.group(1))


def _every_toolbox_module():
    """**Every module the smoke test imports** — which is every toolbox module.

    *Not* ``SCIENTIFIC_MODULES``: that is a hand-curated list of 24, and the two modules whose
    dependencies CI was missing (``data_viz_tools`` → ``seaborn``, ``fibril_tools`` → ``networkx``)
    **were not on it.**
    """
    toolbox = _ROOT / "src" / "pycat" / "toolbox"
    # rglob, dotted names: the six god-file decompositions (1.6.256) moved module-scope COMPUTE
    # imports (SimpleITK, scikit-learn, cv2 …) down into sub-packages. A non-recursive glob would
    # see only the re-export shims and miss every one of them — the exact "list drifts as the code
    # moves underneath it" failure this guard was rewritten to prevent. `_module_scope_dependencies`
    # resolves the dotted name back to its file.
    return sorted(
        str(path.relative_to(toolbox).with_suffix("")).replace("\\", "/").replace("/", ".")
        for path in toolbox.rglob("*.py")
        if path.name != "__init__.py")


def _module_scope_dependencies(module, seen=None):
    """Third-party packages imported at MODULE SCOPE, following pycat.toolbox transitively."""
    if seen is None:
        seen = set()
    if module in seen:
        return set()
    seen.add(module)

    path = _TOOLBOX / f"{module.replace('.', '/')}.py"   # dotted sub-package name -> file path
    if not path.exists():
        return set()

    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    found = set()

    for node in tree.body:                       # module scope ONLY — not inside functions
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]

        for name in names:
            top = name.split(".")[0]
            if top == "pycat":
                # A guarded module that imports another guarded module inherits its
                # dependencies. This is how two_channel_coloc_tools gets sklearn.
                if name.startswith("pycat.toolbox."):
                    # keep the full sub-package path (e.g. "vpt.detection"), not just the leaf, so the
                    # recursion resolves to the right file after the decompositions.
                    found |= _module_scope_dependencies(name[len("pycat.toolbox."):], seen)
                continue
            if top in _STDLIB or top in _GUI or top in _LAZY_BY_DESIGN:
                continue
            found.add(top)

    return found


@pytest.mark.core
def test_ci_installs_every_module_scope_dependency():
    """Every package a guarded module imports at module scope must be installed by CI."""
    if not _WORKFLOW.exists():
        pytest.skip("no core.yml workflow")

    # ── Read the INSTALL COMMANDS, not the whole file ──────────────────────────
    #
    # A first version searched the raw workflow text for the pip name. It passed even with
    # the `pip install scikit-learn` line DELETED — because the COMMENT above that line still
    # contained the word "scikit-learn". **The guard was checking a comment.**
    #
    # Only the actual `pip install ...` commands count.
    install_commands = " ".join(
        line.split("#", 1)[0]                       # strip trailing comments
        for line in _WORKFLOW.read_text(encoding="utf-8", errors="ignore").splitlines()
        if "pip install" in line.split("#", 1)[0]   # and skip fully-commented lines
    )

    # ── EVERY toolbox module, not a hand-curated list ───────────────────────────
    #
    # This guard used to walk only ``SCIENTIFIC_MODULES`` — a hand-maintained list of 24 names in
    # ``test_headless_science``. **``data_viz_tools`` and ``fibril_tools`` are not on it**, so their
    # module-scope imports of ``seaborn`` and ``networkx`` were **never checked**, and CI never
    # installed them.
    #
    # ***Two lists, drifting apart.*** The smoke test — which imports **every** toolbox module —
    # is what found it, and it found it in CI, *after the release was cut.*
    #
    # A guard whose scope is narrower than the thing it guards will eventually miss something. So
    # the scope is now **derived**: every module the smoke test actually imports.
    required = set()
    for module in _every_toolbox_module():
        required |= _module_scope_dependencies(module)

    missing = []
    for package in sorted(required):
        pip_name = _PIP_NAME.get(package, package)
        if pip_name not in install_commands:
            missing.append(f"{package}  (pip install {pip_name})")

    assert not missing, (
        "These packages are imported at MODULE SCOPE by a module the headless test guards, "
        "but are NOT installed by .github/workflows/core.yml:\n  "
        + "\n  ".join(missing)
        + "\n\nThe headless job excludes the GUI stack on purpose. It is NOT supposed to "
          "exclude the maths — a missing compute dependency makes the module unimportable "
          "and the test fails for the wrong reason.\n\n"
          "Add it to the install step. This list has drifted twice already "
          "(largestinteriorrectangle in 1.5.442, scikit-learn in 1.5.444), both times "
          "because it was maintained by hand while the code moved underneath it."
    )


# ── Undeclared module-scope imports: the bug that only appears on a clean install ────────

# import name -> the pip name, NORMALISED the same way `_declared_packages` normalises
# (lowercase, hyphens to underscores). A first version mapped `skimage` -> "scikit-image"
# while the declared set held "scikit_image", so every one of them looked undeclared —
# 23 files' worth of false positives from a normalisation mismatch inside the guard itself.
_ALIAS = {
    "skimage": "scikit_image",
    "cv2": "opencv_python_headless",
    "pywt": "pywavelets",
    "SimpleITK": "simpleitk",
    "sklearn": "scikit_learn",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "qtpy": "pyqt5",
    "PyQt5": "pyqt5",
}


def _declared_packages():
    import tomllib

    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        re.split(r"[<>=!\[ ]", dep.strip())[0].lower().replace("-", "_")
        for dep in data["project"]["dependencies"]
    }


@pytest.mark.core
def test_no_undeclared_module_scope_imports():
    """A package imported at module scope must be declared in pyproject.toml.

    ``fibril_tools`` imported ``networkx`` at module scope and **never declared it**. It
    worked everywhere only because ``scikit-image`` depends on networkx, so it arrived
    **transitively** — and if skimage ever dropped that dependency, fibril analysis would
    break for every user, on a clean install, with no warning.

    **A transitive dependency you rely on is a dependency you have not declared.**

    LAZY imports are exempt and deliberately so: a package imported *inside a function* is an
    optional feature that degrades gracefully when it is absent (cupy, stardist, imagej,
    lumicks). Twelve packages are used that way, and that is a design choice, not a bug. Only
    a MODULE-SCOPE import is a hard requirement — it fails at import time, before any code the
    user wrote has run.
    """
    import sys

    declared = _declared_packages()
    stdlib = set(sys.stdlib_module_names)

    offenders = {}
    for path in sorted((_ROOT / "src" / "pycat").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue

        for node in tree.body:                   # MODULE SCOPE only
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]

            for name in names:
                top = name.split(".")[0]
                if top in stdlib or top == "pycat":
                    continue
                key = _ALIAS.get(top, top.lower().replace("-", "_"))
                if key not in declared:
                    offenders.setdefault(top, set()).add(path.name)

    assert not offenders, (
        "These packages are imported at MODULE SCOPE but are NOT declared in "
        "pyproject.toml:\n  "
        + "\n  ".join(f"{pkg}  ({', '.join(sorted(files))})"
                      for pkg, files in sorted(offenders.items()))
        + "\n\nA module-scope import is a HARD requirement — it fails at import time, on a "
          "clean install, before any user code runs. `networkx` was undeclared for the life "
          "of the project and worked only because scikit-image happens to depend on it. A "
          "transitive dependency you rely on is a dependency you have not declared.\n\n"
          "If the package is an OPTIONAL feature, move the import inside the function that "
          "uses it — that is what the other twelve undeclared packages do, correctly."
    )
