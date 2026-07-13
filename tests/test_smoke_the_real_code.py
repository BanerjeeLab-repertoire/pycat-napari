"""
**Nothing in this test suite actually RUNS PyCAT.**

That is how a one-line bug made cell segmentation — **the single most-used feature** — completely
non-functional for **every Cellpose 3.x user**, survived 300+ tests and a fifteen-bug audit, and
shipped::

    if _cellpose_major_version() >= 4:
        from cellpose import models              # imported HERE
        ...
    else:
        model = models.CellposeModel(...)        # used HERE. UnboundLocalError.

**A test that merely CALLED the function would have caught it in one second.**

The shape of every test we had
-------------------------------
* **source-reading** — walks the AST, checks a property of the *text*
* **unit** — calls a pure function with numpy arrays

**Neither imports the package and runs a workflow.** So an integration failure — a version branch,
a missing import, a broken decorator, a signature drift between a caller and its callee — is
**invisible.**

And it is not a hypothetical class. **All three user-blocking bugs reported this month were
integration failures:**

============================  ============================================
Meet — arm64 segfault          torch's libomp against numba's
Abhradeep — OpenGL corruption  a GPU driver against Qt
Meet — Cellpose dead           a version branch that never imported
============================  ============================================

**Zero of them were unit-testable. All of them would have been caught by actually running the
thing.**

What this test does
-------------------
The cheapest possible version of "run the thing": **import every module, and call every registered
operation on a tiny synthetic image.** It will not check that the *answers* are right — 38 other
test files do that. It checks that **the code runs at all.**
"""

import importlib
import io
import contextlib
import pathlib

import numpy as np
import pytest


_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"


@pytest.mark.core
def test_every_toolbox_module_IMPORTS():
    """**The floor.** A module that does not import is a feature that does not exist.

    This catches a syntax error, a missing dependency, a circular import, and a decorator that
    throws at registration time — **none of which any other test in this suite can see.**
    """
    broken = []

    for path in sorted(_TOOLBOX.glob("*.py")):
        if path.stem.startswith('_'):
            continue

        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                 contextlib.redirect_stdout(io.StringIO()):
                importlib.import_module(f'pycat.toolbox.{path.stem}')
        except ImportError as exc:
            # A missing OPTIONAL dependency is not a PyCAT bug. A missing required one is —
            # but `test_ci_dependencies` already guards the requirements list.
            if any(optional in str(exc) for optional in
                   ('napari', 'PyQt5', 'qtpy', 'cellpose', 'torch', 'stardist',
                    'aicsimageio', 'trackmate', 'cupy', 'lumicks')):
                continue
            broken.append(f"{path.stem}: {exc}")
        except Exception as exc:
            broken.append(f"{path.stem}: {type(exc).__name__}: {exc}")

    assert not broken, (
        "these toolbox modules do not import:\n  " + "\n  ".join(broken)
    )


@pytest.mark.core
def test_the_cellpose_model_builder_actually_RUNS_on_both_versions():
    """**The bug that got through, and the test that would have stopped it.**

    ``_build_cellpose_model`` is called by every cell segmentation. On Cellpose 3.x it raised
    ``UnboundLocalError`` — **the import was inside the version-4 branch.**

    This calls it. That is all it takes.
    """
    import sys
    import types

    segmentation = pytest.importorskip("pycat.toolbox.segmentation_tools")

    built = {}
    for version, expected_api in (('3.1.0', 'model_type'), ('4.0.1', 'pretrained_model')):

        class _Model:
            def __init__(self, gpu=False, model_type=None, pretrained_model=None):
                built[version] = 'model_type' if model_type else 'pretrained_model'

        cellpose = types.ModuleType('cellpose')
        cellpose.version = version
        models = types.ModuleType('cellpose.models')
        models.CellposeModel = _Model
        models.MODEL_NAMES = ['cyto', 'cyto2', 'nuclei']
        cellpose.models = models

        saved = {k: sys.modules.get(k) for k in ('cellpose', 'cellpose.models')}
        sys.modules['cellpose'] = cellpose
        sys.modules['cellpose.models'] = models
        segmentation._CELLPOSE_MODEL_CACHE.clear()

        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                 contextlib.redirect_stdout(io.StringIO()):
                segmentation._build_cellpose_model('cyto2')
        except UnboundLocalError as exc:
            pytest.fail(
                f"Cellpose {version} raised UnboundLocalError: {exc}\n\n"
                f"**This is the bug that shipped.** `from cellpose import models` was inside the "
                f"version-4 branch, so every 3.x user hit it and cell segmentation did not work "
                f"at all.")
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            segmentation._CELLPOSE_MODEL_CACHE.clear()

        assert built.get(version) == expected_api, (
            f"Cellpose {version} should build via `{expected_api}`, got `{built.get(version)}`"
        )
