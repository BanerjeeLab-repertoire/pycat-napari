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


# ── The core lane must stay HEADLESS: no core test may reach for Qt ───────────────────────
#
# `core` is declared "no napari, no Qt, no GPU. Must pass headlessly." Four brushing tests were
# marked `core` while requesting the `qtbot` fixture (pytest-qt). It passed wherever pytest-qt
# happened to be installed and turned into a hard `fixture 'qtbot' not found` collection ERROR in
# the minimal CI lane that deliberately omits it. Installing pytest-qt would have hidden the
# mis-marking and left `core` silently Qt-dependent — so instead the mis-marked tests moved to
# `integration`, and this guard makes the contract enforceable so it can't drift back.

_TESTS_DIR = _ROOT / "tests"
_QT_FIXTURES = {"qtbot", "qapp", "qapp_args", "qtlog", "qtmodeltester"}
_GUI_MODULES = {"napari", "PyQt5", "PyQt6", "qtpy", "pytestqt"}


def _mark_names(expr):
    """The mark names in a `pytest.mark.<name>` (or `pytest.mark.<name>(...)`) expression, or a
    list/tuple of them — used for both the `pytestmark = ...` assignment and `@pytest.mark.*` decorators."""
    names = set()
    for e in (expr.elts if isinstance(expr, (ast.List, ast.Tuple)) else [expr]):
        target = e.func if isinstance(e, ast.Call) else e          # unwrap `pytest.mark.core()`
        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute)
                and target.value.attr == "mark"):
            names.add(target.attr)
    return names


def _module_level_marks(tree):
    """Marks applied to the whole module via `pytestmark = ...` (empty if none)."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            return _mark_names(node.value)
    return set()


def _is_core_selected(func, module_marks):
    """Would `pytest -m core` select this test? True if it carries the `core` mark from its own
    decorators OR the module-level `pytestmark` (marks are additive — a per-test `integration`
    does NOT cancel a module-level `core`, which is exactly why blanket `pytestmark` is unsafe here)."""
    decorator_marks = set()
    for d in func.decorator_list:
        decorator_marks |= _mark_names(d)
    return "core" in decorator_marks or "core" in module_marks


def _is_headless_selected(func, module_marks):
    """Would EITHER headless lane (`core` = numpy-only, `base` = the scientific stack) select this test? Both
    run WITHOUT napari/Qt/pytest-qt, so a test carrying `core` OR `base` may not need a Qt fixture and its file
    may not import the GUI stack at module scope."""
    decorator_marks = set()
    for d in func.decorator_list:
        decorator_marks |= _mark_names(d)
    return bool({"core", "base"} & (decorator_marks | set(module_marks)))


def _local_fixtures(tree):
    """Names of fixtures DEFINED in this file (a `@pytest.fixture`-decorated function). A file that
    supplies its own Qt fixture — e.g. a `qapp` guarded by `importorskip('PyQt5')`, which SKIPS rather
    than errors when Qt is absent — is self-sufficient and headless-safe; it is not relying on pytest-qt."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                target = d.func if isinstance(d, ast.Call) else d
                if isinstance(target, ast.Attribute) and target.attr == "fixture":
                    names.add(node.name)
                elif isinstance(target, ast.Name) and target.id == "fixture":
                    names.add(node.name)
    return names


@pytest.mark.core
def test_no_core_marked_test_requests_a_qt_fixture():
    """A `core` test must run headlessly, so it may not depend on a pytest-qt-provided fixture.

    Only fixtures NOT defined in the same file count: a file that defines its own `qapp` via
    `importorskip` skips (never errors) when Qt is absent, which is the safe headless pattern. It is
    reliance on pytest-qt to supply `qtbot`/`qapp` that turned into a hard collection error in CI."""
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        module_marks = _module_level_marks(tree)
        supplied_locally = _local_fixtures(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                if not _is_headless_selected(node, module_marks):
                    continue
                params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
                for qt in sorted((params & _QT_FIXTURES) - supplied_locally):
                    offenders.append(f"{path.name}::{node.name}  requests '{qt}' (from pytest-qt)")

    assert not offenders, (
        "These `core`-marked tests depend on a pytest-qt fixture, but `core` is declared headless "
        "(no napari/Qt/GPU) and CI's minimal lane omits pytest-qt — so this is a hard "
        "`fixture not found` collection error there:\n  " + "\n  ".join(offenders)
        + "\n\nMove the test to `integration` (it needs a real QApplication), or — if it does NOT "
          "actually create a Qt widget — drop the fixture so it stays genuinely headless. Do NOT "
          "'fix' this by installing pytest-qt into the core lane: that hides the mis-marking and "
          "leaves core Qt-dependent, so it breaks again on the next minimal run."
    )


@pytest.mark.core
def test_no_core_test_file_imports_the_gui_stack_at_module_scope():
    """A file containing a `core` test may not import napari/PyQt at module scope — that import
    fails at COLLECTION time in the headless lane, erroring every test in the file (core ones
    included) before any of them runs. GUI imports for integration tests must be lazy (in-function)."""
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        module_marks = _module_level_marks(tree)
        has_core = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
            and _is_headless_selected(n, module_marks)
            for n in ast.walk(tree))
        if not has_core:
            continue
        for node in tree.body:                       # MODULE SCOPE only
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in _GUI_MODULES:
                    offenders.append(f"{path.name}  imports '{name}' at module scope")

    assert not offenders, (
        "These files contain a `core` test but import the GUI stack at MODULE SCOPE:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe import runs at collection time and fails in the headless core lane (which omits "
          "napari/Qt on purpose), erroring the file's core tests for the wrong reason. Move the GUI "
          "import inside the integration test that needs it."
    )


# ── A test may not exercise an option the module does not declare ────────────────────────────────────
#
# A recurring CI failure class is a TEST DEFECT, not a product bug: a test asserts behaviour for an option
# the module never declared. The dock_space `collapse` failure was exactly this — a mode asserted in tests
# while absent from `VALID_MODES`, so the setter raised and the planner had no branch. This guards the class:
# for option setters that validate against a declared `VALID_*` vocabulary, every string literal a test
# passes them must be in that vocabulary — UNLESS the call sits inside a `with pytest.raises(...)` block,
# where asserting that an invalid value is REJECTED is legitimate. Narrow and mechanical by design.


def _declared_option_setters():
    """Map an option-setter NAME to a callable returning its module's declared valid-value set. Scoped to
    setters backed by a ``VALID_*`` vocabulary (a broad 'tests must match specs' check would false-positive
    and get disabled). Extend this as more such setters appear."""
    def _reflow_modes():
        from pycat.utils.dock_space import VALID_MODES
        return set(VALID_MODES)
    return {'set_reflow_mode': _reflow_modes}


def _call_simple_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _calls_inside_pytest_raises(tree):
    """Every ``Call`` node that lives inside a ``with pytest.raises(...):`` block — those deliberately test
    that an INVALID value is rejected, so they are exempt from the vocabulary check."""
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and _call_simple_name(ctx) == "raises":
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            guarded.add(sub)
    return guarded


@pytest.mark.core
def test_no_test_exercises_an_option_the_module_does_not_declare():
    """Guard B (collapse_mode_and_test_guards): a test passing a string literal to a declared-vocabulary
    option setter must use a value the module actually declares — otherwise it is testing a deferred/rejected
    alternative (the `collapse`-not-in-`VALID_MODES` failure). Calls inside `with pytest.raises(...)` are
    exempt (rejection tests)."""
    setters = _declared_option_setters()
    offenders = []
    for path in sorted(_TESTS_DIR.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        guarded = _calls_inside_pytest_raises(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or node in guarded:
                continue
            name = _call_simple_name(node)
            if name not in setters:
                continue
            valid = setters[name]()
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in valid:
                    offenders.append(f"{path.name}: {name}(..., {arg.value!r}) — not in {sorted(valid)}")

    assert not offenders, (
        "These tests pass an option-setter a value its module does not declare (outside a `pytest.raises` "
        "rejection test) — i.e. they exercise a deferred/rejected alternative, the class of defect that let "
        "`collapse` be tested before it was implemented:\n  " + "\n  ".join(offenders)
        + "\n\nEither the module should DECLARE and implement the option (add it to its VALID_* set), or the "
          "test should not assert working behaviour for it. If you are testing that the value is REJECTED, "
          "wrap the call in `with pytest.raises(...)`."
    )


# ── Undeclared AND unguarded LAZY (function-scope) imports: the class the module-scope guard can't see ──
#
# The guards above walk only `# module scope ONLY` imports. A package imported INSIDE a function is invisible
# to them — yet a test that calls that function still needs it. This is the THIRD failure of that class (after
# the `qtbot` fixture and the marker/environment mismatch): a `core`/`base` navigator test reached
# `navigator/loader._rows`'s lazy `import openpyxl`, which was undeclared and unguarded, and CI went red.
#
# Rule (the same declared-or-fallback discipline, extended to the imports the module-scope guard cannot see):
# a function-scope third-party import must be (a) a DECLARED dependency, (b) guarded by a try/except so an
# absent package degrades to a fallback, or (c) a known lazy-by-design package.
_LAZY_OK = {
    # (c1) optional backends — GPU / Java bridge / legacy readers / optional plot backends, feature-gated at
    # call sites (declared in pyproject's optional-dependencies extras, not the base set).
    "cellpose", "torch", "cupy", "cupyx", "numba", "trackmate", "jpype", "stardist", "imagej",
    "lumicks", "bioformats", "aicsimageio", "scyjava", "plotly", "pyqtgraph",
    # (c2) present TRANSITIVELY via a declared reader/plotter (scikit-image / matplotlib / bioio) and imported
    # directly at call sites. Declaring each EXPLICITLY is a separate dependency-hygiene audit (flagged in the
    # openpyxl spec, not silently patched here), so they are allow-listed rather than mislabelled optional.
    "tifffile", "pillow", "imageio", "packaging",
}


def _import_is_try_guarded(node, tree):
    """True if ``node`` sits inside a ``try:`` whose handlers include ImportError/ModuleNotFoundError/Exception
    — i.e. an absent package has a fallback path rather than crashing at import."""
    for anc in ast.walk(tree):
        if not isinstance(anc, ast.Try):
            continue
        if any(node is n for n in ast.walk(anc)) and node not in anc.finalbody:
            for h in anc.handlers:
                t = h.type
                if t is None:
                    return True
                names = ([t.id] if isinstance(t, ast.Name)
                         else [e.id for e in getattr(t, "elts", []) if isinstance(e, ast.Name)])
                if {"ImportError", "ModuleNotFoundError", "Exception"} & set(names):
                    return True
    return False


@pytest.mark.core
def test_no_undeclared_unguarded_lazy_import():
    """A function-scope (lazy) third-party import must be DECLARED, GUARDED by a fallback, or known-lazy — the
    declared-or-fallback contract, extended to the imports the module-scope guard cannot see. This is what
    would have caught the `openpyxl` failure at the source instead of in CI."""
    import sys

    declared = _declared_packages()
    stdlib = set(sys.stdlib_module_names)
    offenders = {}

    for path in sorted((_ROOT / "src" / "pycat").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top in stdlib or top == "pycat":
                        continue
                    key = _ALIAS.get(top, top.lower().replace("-", "_"))
                    if key in declared or top in _LAZY_OK or key in _LAZY_OK:
                        continue
                    if _import_is_try_guarded(node, tree):
                        continue
                    offenders.setdefault(top, set()).add(path.name)

    assert not offenders, (
        "These packages are imported at FUNCTION scope (lazy) but are neither declared in pyproject, guarded "
        "by a try/except fallback, nor known-lazy:\n  "
        + "\n  ".join(f"{pkg}  ({', '.join(sorted(files))})" for pkg, files in sorted(offenders.items()))
        + "\n\nA lazy import a `core`/`base` test can reach must be DECLARED (so it is installed) or GUARDED "
          "(so an absent package degrades to a fallback) — `openpyxl` was neither, and CI went red on it. "
          "Declare it, wrap the import in try/except with a fallback, or — if it is a genuinely optional "
          "backend — add it to _LAZY_OK with a one-line justification."
    )
