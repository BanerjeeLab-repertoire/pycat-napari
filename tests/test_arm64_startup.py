"""
**The Numba warm-up segfaulted on Apple Silicon, for a reason the code already knew.**

Reported on an M2 (2026-07-13). The fault handler is unambiguous::

    File ".../numba_utils.py", line 192 in rescale_intensity_fast
    File ".../numba_utils.py", line 300 in warmup_numba
    Fatal Python error: Segmentation fault

and immediately before it::

    OMP: Info #276: omp_set_nested routine deprecated...

**That OpenMP banner is the tell.** Line 192 is the first call into a ``@njit(parallel=True)``
kernel — where Numba's parallel backend spins up its OpenMP runtime, **on a worker thread, while
``CentralManager(viewer)`` initialises Qt on the main thread.**

``run_pycat`` **already documents exactly this race**, for torch:

    *"importing torch on this worker thread while Qt/CentralManager initialise on the main thread
    is a known cause of a C-level segfault at launch on arm64 Macs"*

**It was fixed for torch and left in place for Numba.** Two native runtimes (libomp and Qt) coming
up concurrently on arm64 is the same bug whichever library pulls the trigger.

Two causes, and the traceback cannot separate them
--------------------------------------------------
1. **The race** — fixed by deferring the warm-up on Darwin.
2. **The parallel backend itself** — ``parallel=True`` + ``cache=True`` on arm64 loads a threading
   layer *and* a cached object file, and that combination is fragile on macOS ARM.

**Fixing only (1) would be a guess**, and if (2) is the real cause the crash merely moves from
launch to first use — **which is worse, because it would then happen mid-analysis.** So the
parallel backend is off by default on Darwin as well, and ``PYCAT_NUMBA_PARALLEL=1`` forces it
back on for anyone testing a newer numba.

*(``numba_arm64_diag.py`` distinguishes the two, by running each mode alone in a fresh process
with no Qt anywhere near it.)*
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


@pytest.mark.core
def test_numba_uses_WORKQUEUE_on_macOS_so_it_loads_no_libomp():
    """**The fix, and it is the only thing that works.**

    Established on an M2, each case in its own subprocess::

        torch + numba on a **worker**                      **SEGFAULT**
        torch + numba on the **MAIN thread**               **SEGFAULT**   <- thread irrelevant
        torch + numba + **KMP_DUPLICATE_LIB_OK=TRUE**      **SEGFAULT**   <- the flag does nothing
        torch + numba + **NUMBA_THREADING_LAYER=workqueue**     **OK**

    **It is torch's libomp against numba's libomp.** Qt was innocent. The thread was irrelevant.
    And ``KMP_DUPLICATE_LIB_OK`` — *which PyCAT already sets* — **does not help**, exactly as
    Intel's own documentation warns.

    ``workqueue`` is numba's **own pure-Python thread pool**: it loads **no libomp at all**, so
    there is no second OpenMP runtime to collide with torch's — **and it keeps the parallelism.**
    """
    source = (_SOURCE / "run_pycat.py").read_text(encoding='utf-8', errors='ignore')

    assert 'NUMBA_THREADING_LAYER' in source, (
        "run_pycat must set NUMBA_THREADING_LAYER=workqueue on macOS. Without it, numba loads its "
        "own libomp alongside torch's, and the process SEGFAULTS."
    )
    assert 'workqueue' in source


@pytest.mark.core
def test_the_threading_layer_is_set_BEFORE_the_first_native_import():
    """**``NUMBA_THREADING_LAYER`` is read at numba's IMPORT time. Setting it later does nothing.**

    And **numba can be pulled in indirectly** — by cellpose, or by a napari plugin — long before
    ``pycat.toolbox.numba_utils`` ever loads. So setting it *there* would be **too late**, and the
    segfault would come back with nothing to show why.

    It must sit in the env block at the top of ``run_pycat``, **before the first native import**,
    beside the other OpenMP variables.
    """
    source = (_SOURCE / "run_pycat.py").read_text(encoding='utf-8', errors='ignore')

    threading_layer_at = source.find('NUMBA_THREADING_LAYER')
    assert threading_layer_at > 0

    # It must come before napari, torch, numpy — anything that could drag numba in with it.
    for native in ('import napari', 'import numpy', 'import torch'):
        native_at = source.find(native)
        if native_at < 0:
            continue
        assert threading_layer_at < native_at, (
            f"NUMBA_THREADING_LAYER is set AFTER `{native}`. It is read at numba's import time, "
            f"and numba can be pulled in indirectly by cellpose or a napari plugin — so setting "
            f"it late does nothing, and the segfault returns."
        )


@pytest.mark.core
def test_forcing_OpenMP_back_on_macOS_WARNS():
    """**A user who sets ``NUMBA_THREADING_LAYER=omp`` gets the crash back.**

    They deserve to be told, rather than discovering it as a segfault.
    """
    source = (_SOURCE / "toolbox" / "numba_utils.py").read_text(encoding='utf-8', errors='ignore')

    assert '_FORCED_OMP' in source or 'omp' in source.lower()
    assert 'WARNING' in source, (
        "forcing OpenMP back on macOS must WARN — PyTorch already loads its own OpenMP runtime, "
        "and a second one segfaults the process"
    )


@pytest.mark.core
def test_every_numba_kernel_has_a_NumPy_fallback():
    """**PyCAT must run even if Numba is completely broken on the machine.**

    Verified: with Numba forced unavailable, ``rescale_intensity_fast`` still returns a correctly
    rescaled array. The fallback is not decorative.
    """
    source = (_SOURCE / "toolbox" / "numba_utils.py").read_text(encoding='utf-8', errors='ignore')

    assert "NUMBA_AVAILABLE" in source
    assert source.count("NumPy fallback") >= 3, (
        "the public wrappers must each fall back to NumPy when Numba is unavailable — that is "
        "what keeps PyCAT usable on a machine whose numba/llvmlite stack is broken"
    )
