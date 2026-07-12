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


def _module_scope_dependencies(module, seen=None):
    """Third-party packages imported at MODULE SCOPE, following pycat.toolbox transitively."""
    if seen is None:
        seen = set()
    if module in seen:
        return set()
    seen.add(module)

    path = _TOOLBOX / f"{module}.py"
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
                    found |= _module_scope_dependencies(name.split(".")[-1], seen)
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

    required = set()
    for module in _guarded_modules():
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
