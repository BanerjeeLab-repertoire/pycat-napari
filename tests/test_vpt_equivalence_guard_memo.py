"""The GPU/CPU equivalence guard is verified ONCE per session, not once per call.

Why this is a ``core`` test with no GPU in sight
-----------------------------------------------
The guard's *verdict* is a property of the machine (driver + cupy build) and the
LoG params. **How often it runs is not** — that is pure caching logic, and it is
the part that regressed: the guard ran on every ``detect_beads_stack`` call, from
four call sites including the live preview, each paying a full CPU-detect +
GPU-detect + compare of frame 0 before the real work started. On a GPU-less
machine that cost is invisible; on Gable's it was enough to erase a marginal GPU
win and make GPU feel slower than CPU-parallel.

So the *count* is asserted here, headlessly, with both backends faked. Whether the
GPU actually agrees with skimage is a different question, answered on real CUDA in
``test_vpt_gpu_equivalence.py``.

The guard's PURPOSE is not up for negotiation and is asserted below too: a
mismatching GPU is still never trusted, and a guard that cannot prove equivalence
has not proven it.
"""

# Standard library imports
import numpy as np
import pytest

# Local application imports
# gpu_matches_cpu + the equivalence cache/probe moved to toolbox/vpt/detection.py (1.6.238); alias
# points there so monkeypatch targets bind the module the internal bare-name calls resolve against.
from pycat.toolbox.vpt import detection as vpt

pytestmark = pytest.mark.base

_PARAMS = dict(min_sigma=1.0, max_sigma=5.0, num_sigma=5, threshold=0.02)


@pytest.fixture(autouse=True)
def _clear_cache():
    """The memo is process-scoped by design, which makes it leak between tests."""
    vpt._GPU_EQUIV_CACHE.clear()
    yield
    vpt._GPU_EQUIV_CACHE.clear()


@pytest.fixture
def frame():
    return np.zeros((16, 16), dtype=np.float32)


def test_the_guard_runs_ONCE_for_many_calls_with_the_same_params(monkeypatch, frame):
    """The whole point: N detects in one session pay the double-detect once."""
    calls = []
    monkeypatch.setattr(vpt, '_run_gpu_equivalence_check',
                        lambda *a, **k: calls.append(1) or True)

    for _ in range(5):
        assert vpt.gpu_matches_cpu(lambda: frame, **_PARAMS) is True

    assert len(calls) == 1, (
        f'the equivalence guard ran {len(calls)} times for 5 calls with identical '
        f'params — it is unmemoised overhead on the hot path again')


def test_a_cache_HIT_does_not_even_read_frame_zero(monkeypatch, frame):
    """The frame getter is a callable so a hit costs one dict lookup.

    Reading frame 0 out of a lazy/file-backed stack is itself I/O — a memo that
    still paid for the read on every call would only be half a fix.
    """
    monkeypatch.setattr(vpt, '_run_gpu_equivalence_check', lambda *a, **k: True)
    reads = []

    def _getter():
        reads.append(1)
        return frame

    for _ in range(4):
        vpt.gpu_matches_cpu(_getter, **_PARAMS)

    assert len(reads) == 1, 'a cache hit read frame 0 anyway'


def test_different_LOG_PARAMS_are_a_different_question(monkeypatch, frame):
    """Params are in the key because they change what the detectors do. The stack
    is NOT in the key, because it cannot change the answer."""
    calls = []
    monkeypatch.setattr(vpt, '_run_gpu_equivalence_check',
                        lambda *a, **k: calls.append(1) or True)

    vpt.gpu_matches_cpu(lambda: frame, **_PARAMS)
    vpt.gpu_matches_cpu(lambda: frame, **{**_PARAMS, 'threshold': 0.5})

    assert len(calls) == 2, 'a param change must re-verify, not reuse the verdict'


def test_a_DIFFERENT_STACK_reuses_the_verdict(monkeypatch):
    """Equivalence is a property of the machine + params, never of the data.

    This is the assumption the memo rests on, so it is stated as a test: two
    different stacks, same params, one check.
    """
    calls = []
    monkeypatch.setattr(vpt, '_run_gpu_equivalence_check',
                        lambda *a, **k: calls.append(1) or True)

    vpt.gpu_matches_cpu(lambda: np.zeros((16, 16), np.float32), **_PARAMS)
    vpt.gpu_matches_cpu(lambda: np.ones((64, 64), np.float32), **_PARAMS)

    assert len(calls) == 1, 'the verdict was keyed on the data — it must not be'


def test_a_MISMATCHING_gpu_is_still_never_trusted(monkeypatch, frame):
    """Memoise the verdict; do not remove the check. A disagreeing GPU is a
    driver/cupy quirk that would make results silently wrong."""
    def _detect(f, *, use_gpu=False, **kw):
        # The GPU "finds" a bead the CPU does not — exactly the case the guard exists for.
        return [(1.0, 1.0), (9.0, 9.0)] if use_gpu else [(1.0, 1.0)]

    monkeypatch.setattr(vpt, 'detect_beads_frame', _detect)

    assert vpt.gpu_matches_cpu(lambda: frame, **_PARAMS) is False
    assert vpt._GPU_EQUIV_CACHE, 'the negative verdict must be cached too'


def test_an_AGREEING_gpu_is_trusted(monkeypatch, frame):
    """The mirror of the above — the guard must not be a blanket 'no'."""
    monkeypatch.setattr(vpt, 'detect_beads_frame',
                        lambda f, *, use_gpu=False, **kw: [(1.0, 1.0), (9.0, 9.0)])

    assert vpt.gpu_matches_cpu(lambda: frame, **_PARAMS) is True


def test_a_guard_that_CANNOT_prove_equivalence_has_not_proven_it(monkeypatch, frame):
    """A cupy explosion mid-check reads as 'do not trust the GPU', not as a crash
    and not as a pass."""
    def _boom(*a, **k):
        raise RuntimeError('cuLaunchKernel failed')

    monkeypatch.setattr(vpt, '_run_gpu_equivalence_check', _boom)

    assert vpt.gpu_matches_cpu(lambda: frame, **_PARAMS) is False


def test_the_check_itself_really_does_detect_on_BOTH_backends(monkeypatch, frame):
    """Guard the guard: if `_run_gpu_equivalence_check` stopped exercising both
    paths it would be comparing something to itself and always agreeing."""
    seen = []
    monkeypatch.setattr(vpt, 'detect_beads_frame',
                        lambda f, *, use_gpu=False, **kw: seen.append(use_gpu) or [(1.0, 1.0)])

    assert vpt._run_gpu_equivalence_check(frame, **_PARAMS) is True
    assert sorted(seen) == [False, True], f'expected one CPU + one GPU detect, got {seen}'
