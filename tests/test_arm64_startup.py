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
def test_the_numba_warmup_is_deferred_on_macOS():
    """**The warm-up is a nice-to-have. It is not worth a crash at launch.**

    It exists purely to hide a first-use compile delay. The kernels still compile on first real
    use — on the main thread, with nothing else initialising alongside them.
    """
    source = (_SOURCE / "run_pycat.py").read_text(encoding='utf-8', errors='ignore')

    warmup_start = source.find("def _warmup():")
    assert warmup_start > 0, "the warm-up function is gone"

    warmup_end = source.find("threading.Thread(target=_warmup", warmup_start)
    body = source[warmup_start:warmup_end]

    assert "darwin" in body, (
        "the Numba warm-up must be deferred on macOS. It initialises OpenMP on a worker thread "
        "while Qt comes up on the main thread — the SAME race this file already documents (and "
        "already avoids) for torch, which sits fifteen lines below it."
    )

    # And the guard must come BEFORE the warmup call, not after it.
    darwin_at = body.find("darwin")
    warmup_at = body.find("warmup_numba()")
    assert darwin_at < warmup_at, (
        "the platform check must guard the warm-up call, not follow it"
    )


@pytest.mark.core
def test_the_parallel_backend_is_OFF_by_default_on_macOS():
    """**Deferring the warm-up alone would be a guess.**

    If the parallel backend itself is what is broken on arm64, the crash simply moves from launch
    to **first use** — mid-analysis, which is worse than at startup.

    Every kernel has a NumPy fallback, and single-threaded Numba is fine. **Parallelism is a
    speed-up, not a capability — and it is not worth a segfault.**
    """
    source = (_SOURCE / "toolbox" / "numba_utils.py").read_text(encoding='utf-8', errors='ignore')

    assert "NUMBA_PARALLEL" in source, "the parallel backend must be switchable"
    assert "darwin" in source, (
        "the parallel backend must default OFF on Darwin — that is where it segfaults"
    )
    assert "PYCAT_NUMBA_PARALLEL" in source, (
        "there must be an environment override, so testing a newer numba/llvmlite does not "
        "require editing the source. **That is the experiment worth running.**"
    )

    # No kernel may hardcode parallel=True any more.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'id', '') != 'njit':
            continue
        for keyword in node.keywords:
            if keyword.arg == 'parallel':
                assert not (isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True), (
                    "a kernel hardcodes parallel=True. It must use NUMBA_PARALLEL, which is "
                    "False on macOS — otherwise it segfaults on Apple Silicon."
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
