"""**The lazy wrappers must be reachable without a GUI.**

``_TiffPageStack`` and ``_LazyArraySource`` used to live in ``file_io.py``, next to two ``QDialog``
subclasses in a module that imports PyQt5 at module scope. Their bodies never needed Qt — only
their address did. **So reaching a TIFF lazy wrapper dragged in the whole GUI stack**, and the
wrappers could not be exercised headlessly: exactly what a perf harness or a CI perf gate wants to
do, and the reason the roadmap called this out.

``lazy_sources.py`` is the fix, and these tests are its contract:

* the module imports with **no Qt and no napari** — checked statically (module scope) *and* at
  runtime (in a subprocess, see below);
* a plane read through the wrapper is **bit-identical** to a full read, in the ``[0, 1]`` range the
  analysis stack is written for — because a faster reader that returns different pixels is not a
  reader, and a wrapper that returns raw counts is the 1.6.x intensity bug.

**Why a subprocess for the runtime check.** ``'PyQt5' not in sys.modules`` is worthless in-process:
``test_ui_smoke.py`` imports PyQt5 at module scope, so by the time this file runs in a full session
Qt is already loaded and the assertion would fail for reasons that have nothing to do with
``lazy_sources``. A fresh interpreter is the only honest way to ask "does importing THIS module
pull in Qt?".

**Why the pycat imports are inside the test bodies.** ``conftest.py``'s ``pytest_ignore_collect``
drops any test module whose MODULE-SCOPE imports name ``pycat.file_io`` when the GUI stack is
absent. A headless-contract test that silently vanishes from the headless CI job would be worse
than no test at all.
"""

# Standard library imports
import ast
import os
import pathlib
import subprocess
import sys
import tempfile

# Third party imports
import numpy as np
import pytest


pytestmark = pytest.mark.core

_LAZY_SOURCES = (pathlib.Path(__file__).resolve().parents[1]
                 / "src" / "pycat" / "file_io" / "lazy_sources.py")

# The same roots `test_headless_science` forbids. `lazy_sources` is held to the science modules'
# standard because it is what the perf harness must be able to import.
_FORBIDDEN_ROOTS = {"napari", "PyQt5", "PyQt6", "qtpy"}


def test_lazy_sources_makes_no_GUI_import_at_MODULE_scope():
    """The static half. Imports inside a function body are fine — those run only when the caller
    already asked. It is the module-scope ones that decide what a bare ``import`` costs."""
    tree = ast.parse(_LAZY_SOURCES.read_text(encoding="utf-8", errors="ignore"))

    offenders = []
    for node in tree.body:                       # module scope only, deliberately
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_ROOTS:
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _FORBIDDEN_ROOTS:
                offenders.append(f"line {node.lineno}: from {node.module} import ...")

    assert not offenders, (
        "`lazy_sources.py` imports the GUI stack at module scope:\n  " + "\n  ".join(offenders)
        + "\n\nThat re-creates the exact problem the module was extracted to fix — the lazy "
          "wrappers become unreachable without Qt, and the perf harness cannot touch them."
    )


def test_IMPORTING_lazy_sources_does_not_drag_in_Qt():
    """The runtime half, in a fresh interpreter — the static check cannot see a GUI import made
    by something ``lazy_sources`` imports *transitively* (``stack_access``, ``lazy_guard`` and the
    ``pycat.file_io`` package ``__init__`` all get a vote)."""
    program = (
        "import sys\n"
        "import pycat.file_io.lazy_sources\n"
        "bad = [m for m in ('napari', 'PyQt5', 'PyQt6', 'qtpy') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    # Hand the child the parent's import path so it resolves the SAME pycat the suite is testing
    # (this repo is commonly installed non-editable, so a bare subprocess could import a stale
    # site-packages copy and report a green that means nothing).
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)

    done = subprocess.run([sys.executable, "-c", program],
                          capture_output=True, text=True, env=env)

    assert done.returncode == 0, (
        "`import pycat.file_io.lazy_sources` FAILED in a fresh interpreter:\n" + done.stderr
    )
    dragged = done.stdout.strip()
    assert not dragged, (
        f"importing `lazy_sources` pulled in: {dragged}.\n\n"
        "The module is Qt/napari-free BY CONTRACT — that is the entire reason it exists as a "
        "separate module. Whatever was just added imports the GUI stack at module scope, either "
        "here or in something this module imports."
    )


def test_a_TiffPageStack_plane_is_BIT_IDENTICAL_to_a_full_read():
    """**The floor.** A faster reader that returns different pixels is not a reader.

    Mirrors ``test_tiff_planes.py::test_a_plane_is_BIT_IDENTICAL_to_a_full_read``, but exercises
    the WRAPPER — and now does so without Qt anywhere in the process.

    The comparison is against the full read **normalised the same way**, because ``[0, 1]`` from
    the source dtype is the wrapper's contract (17 toolbox functions declare that range;
    ``equalize_adapthist`` raises without it). Comparing to raw counts would be asserting the
    1.6.x intensity bug.
    """
    tifffile = pytest.importorskip("tifffile")
    # Not at module scope: see the module docstring (conftest un-collects on module-scope
    # pycat.file_io imports).
    lazy_sources = pytest.importorskip("pycat.file_io.lazy_sources")
    from pycat.file_io.stack_access import to_unit_float32

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    wrapper = None
    try:
        truth = np.random.default_rng(0).integers(0, 4096, (5, 32, 48), dtype=np.uint16)
        tifffile.imwrite(path, truth)

        wrapper = lazy_sources._TiffPageStack(path, 5, 32, 48, truth.dtype,
                                              channel_idx=0, n_channels=1)

        assert wrapper.shape == (5, 32, 48)
        assert wrapper.dtype == np.dtype('float32')

        for t in range(5):
            expected = to_unit_float32(truth[t], truth.dtype)
            assert np.array_equal(wrapper[t], expected), (
                f"frame {t} differs from a full tifffile read put through the same normalisation"
            )

        # …and it is genuinely NORMALISED, not float32-shaped raw counts. This is the specific
        # regression that made the same pixels read a factor of 65535 apart depending on which
        # loader you came through.
        assert wrapper[0].max() <= 1.0, (
            "the wrapper handed back raw counts as float32 — the 1.6.x intensity bug. It must "
            "normalise by the SOURCE dtype max (`to_unit_float32`), as the 2-D loader does."
        )
    finally:
        # The wrapper holds the TIFF open for page seeks; Windows will not unlink an open file.
        if wrapper is not None:
            wrapper.close()
        try:
            os.unlink(path)
        except OSError:
            pass


def test_a_TiffPageStack_still_REFUSES_an_implicit_full_read():
    """The guard travelled with the class. ``np.asarray(wrapper)`` silently returning frame 0 is
    the bug that has bitten four times; moving the class must not have loosened it."""
    tifffile = pytest.importorskip("tifffile")
    lazy_sources = pytest.importorskip("pycat.file_io.lazy_sources")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    wrapper = None
    try:
        tifffile.imwrite(path, np.zeros((4, 16, 16), dtype=np.uint16))
        wrapper = lazy_sources._TiffPageStack(path, 4, 16, 16, np.dtype('uint16'),
                                              channel_idx=0, n_channels=1)
        with pytest.raises(RuntimeError, match="implicit full-stack read"):
            np.asarray(wrapper)
    finally:
        if wrapper is not None:
            wrapper.close()
        try:
            os.unlink(path)
        except OSError:
            pass


def test_file_io_still_RE_EXPORTS_both_wrappers():
    """Callers do ``from pycat.file_io.file_io import _TiffPageStack`` (the VPT GPU equivalence
    test does, twice). The move must be invisible to them — same NAME, same OBJECT."""
    file_io = pytest.importorskip("pycat.file_io.file_io")
    lazy_sources = pytest.importorskip("pycat.file_io.lazy_sources")

    assert file_io._TiffPageStack is lazy_sources._TiffPageStack
    assert file_io._LazyArraySource is lazy_sources._LazyArraySource
    # The OME helpers moved too (they are `_TiffPageStack`'s multi-file machinery).
    assert file_io.resolve_ome_file_set is lazy_sources.resolve_ome_file_set
    assert file_io.build_ome_page_map is lazy_sources.build_ome_page_map
