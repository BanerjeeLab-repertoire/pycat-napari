"""**No tier gets to win by default. Each one is costed and the cheapest wins.**

The old rule was a fixed preference order — "GPU > CPU-parallel > serial" — with
the pool made *unreachable* whenever a GPU existed (`if ... and not gpu_on`). It
was never a competitor; it was a fallback. Two separate things were wrong, both
measured on this tree (GTX 1080, 7 CPU workers, constant bead density, per frame):

    xy      serial       GPU      CPU-pool(7w)      T=1000 total
    512    136.9 ms    49.5 ms   46.2 ms + 5.0 s    GPU  50 s | pool  51 s
    1024   528.1 ms   249.0 ms  166.8 ms + 5.4 s    GPU 250 s | pool 172 s
    2048  2817.2 ms  1123.1 ms 1068.4 ms + 6.8 s    GPU 1124 s | pool 1075 s

1. **The GPU is only ~2-3x one CPU core here** — not enough to beat seven of them.
   At 1024 and 2048 the fixed order picked the *slower* tier, which is the "GPU
   felt slower than CPU-parallel" report from the real workflow run.
2. **The pool was gated on `n_frames > 1`**, which is not a threshold. A 20-frame
   stack got a 7-worker pool: 5043 ms instead of 451 ms, an ~11x LOSS, because a
   spawn costs ~4.9 s and that stack is 0.27 s of work.

These tests are the arithmetic, headless. Which tier wins is a calculation, and it
should be checkable without a GPU, 8 cores, or a 5-second spawn — the numbers
above are the inputs, and they are pinned as the cases that actually occurred.
"""

# Third party imports
import pytest

# Local application imports
# The detection/backend machinery moved to toolbox/vpt/detection.py (1.6.238). Alias points at its
# new home so monkeypatch targets bind the same module the internal bare-name calls resolve against.
from pycat.toolbox.vpt import detection as vpt

pytestmark = pytest.mark.base

# Measured per-frame costs, seconds. These are the real numbers from the sweep.
M = {
    512:  dict(ser=0.1369, gpu=0.0495),
    1024: dict(ser=0.5281, gpu=0.2490),
    2048: dict(ser=2.8172, gpu=1.1231),
}
FIXTURE_SER = 0.0137     # the 171x201 crop: 13.7 ms/frame

# Captured before the autouse fixture below replaces it — the platform-cost test
# needs the real function, not the pinned stand-in every other test wants.
_REAL_SPAWN_COST_S = vpt._pool_spawn_cost_s


@pytest.fixture(autouse=True)
def _spawn_is_expensive(monkeypatch):
    """Pin the platform cost so these are arithmetic, not a Windows-vs-Linux quiz."""
    monkeypatch.setattr(vpt, '_pool_spawn_cost_s', lambda: 4.0)


def _tier(xy, n_frames, workers=7, gpu_ok=True, pool_ok=True):
    return vpt._choose_detection_tier(
        n_frames=n_frames, t_ser=M[xy]['ser'], t_gpu=M[xy]['gpu'],
        workers=workers, gpu_ok=gpu_ok, pool_ok=pool_ok)


# ── the regression that started this: a tiny stack got a 7-worker pool ───────

def test_a_TINY_stack_does_not_get_a_pool():
    """20 frames x 13.7 ms = 0.27 s of work cannot repay a ~4 s spawn, no matter
    how many cores are idle. This took 5043 ms instead of 451 ms."""
    assert vpt._choose_detection_tier(
        n_frames=20, t_ser=FIXTURE_SER, t_gpu=None, workers=7,
        gpu_ok=False, pool_ok=True) == 'serial'


def test_the_threshold_tracks_FRAME_COST_not_frame_count():
    """Why the cost is probed and not assumed: the same frame COUNT lands on either
    side of the line depending on what a frame costs. No fixed `n_frames >= N` rule
    is right at both ends."""
    cheap = vpt._choose_detection_tier(n_frames=50, t_ser=0.010, t_gpu=None,
                                       workers=7, gpu_ok=False, pool_ok=True)
    dear = vpt._choose_detection_tier(n_frames=50, t_ser=0.500, t_gpu=None,
                                      workers=7, gpu_ok=False, pool_ok=True)
    assert (cheap, dear) == ('serial', 'pool')


def test_a_CHEAP_spawn_lowers_the_bar(monkeypatch):
    """`fork` clones a warm interpreter; `spawn` re-imports numpy/skimage/pandas in
    every worker. The start method, not the OS, moves the break-even — so a Linux
    box should pool work a Windows box correctly declines."""
    args = dict(n_frames=20, t_ser=FIXTURE_SER, t_gpu=None, workers=7,
                gpu_ok=False, pool_ok=True)
    assert vpt._choose_detection_tier(**args) == 'serial'

    monkeypatch.setattr(vpt, '_pool_spawn_cost_s', lambda: 0.05)
    assert vpt._choose_detection_tier(**args) == 'pool'


# ── the bug the sweep found: the pool never got to compete with the GPU ──────

def test_the_POOL_BEATS_THE_GPU_on_a_real_stack():
    """**The headline.** 2048x2048x1000 is the real workflow size, and seven CPU
    cores finish it before a GTX 1080 does. The old fixed order could not express
    this: the pool was unreachable whenever a GPU existed."""
    assert _tier(2048, 1000) == 'pool'
    assert _tier(1024, 1000) == 'pool'


def test_the_GPU_still_wins_where_it_actually_wins():
    """The point is not "prefer the pool" — that would be the same mistake with the
    other tier. At 512 the GPU is genuinely the fastest thing available."""
    assert _tier(512, 200) == 'gpu'


def test_a_SHORT_stack_of_expensive_frames_still_goes_to_the_GPU():
    """The spawn has to be repaid out of the per-frame win, so a very short stack
    still belongs to the GPU even at 2048 where the pool is faster per frame.

    The runway is shorter than it looks: at 2048 the pool overtakes the GPU at
    T ~= 21 frames (1.1231*T == 4.0 + 2.8172*T/3.02). Expensive frames repay a
    spawn almost immediately — which is exactly why the fixed "GPU first" order
    was wrong for real data.
    """
    assert _tier(2048, 10) == 'gpu'
    assert _tier(2048, 1000) == 'pool'


def test_NO_gpu_means_the_pool_competes_with_serial_only():
    """The GPU-less lab machine: still a real choice, just a two-horse one."""
    assert _tier(2048, 1000, gpu_ok=False) == 'pool'
    assert vpt._choose_detection_tier(
        n_frames=20, t_ser=FIXTURE_SER, t_gpu=None, workers=7,
        gpu_ok=False, pool_ok=True) == 'serial'


def test_a_pool_that_is_not_ALLOWED_is_not_chosen():
    """ring_merge / hot_pixel_reject / a non-file-backed stack rule the pool out on
    correctness grounds, before cost is considered at all."""
    assert _tier(2048, 1000, pool_ok=False) == 'gpu'
    assert _tier(2048, 1000, pool_ok=False, gpu_ok=False) == 'serial'


def test_one_worker_is_not_a_pool():
    """Dividing the work one way saves nothing and still pays the spawn."""
    assert _tier(2048, 1000, workers=1) == 'gpu'


# ── the model behind the pool's number ──────────────────────────────────────

def test_seven_workers_do_not_give_a_sevenfold_speedup():
    """The pool parallelises DETECTION only — template building, scoring and
    classification stay in the parent (`_detect_frame_worker`). That serial tail is
    why the sweep measured 2.6-3.2x from 7 workers, and modelling it as 7x would
    pick the pool in cases where it actually loses."""
    speedup = vpt._pool_speedup(7)
    assert 2.5 < speedup < 3.5, f'{speedup:.2f}x does not match the measured 2.6-3.2x'


def test_the_speedup_model_is_monotonic_and_saturates():
    """Amdahl: more workers never hurt, and never buy more than the parallel
    fraction allows."""
    assert vpt._pool_speedup(1) == 1.0
    assert vpt._pool_speedup(2) < vpt._pool_speedup(4) < vpt._pool_speedup(16)
    assert vpt._pool_speedup(10_000) < 1.0 / (1.0 - vpt._POOL_PARALLEL_FRACTION) + 0.01


def test_fork_is_cheap_and_spawn_is_not(monkeypatch):
    """The estimate itself, pinned because the whole gate hangs off it — and
    because "just measure it" is not available: measuring a spawn means paying for
    one, which is the cost we are deciding whether to incur."""
    import multiprocessing
    monkeypatch.setattr(multiprocessing, 'get_start_method',
                        lambda allow_none=False: 'fork')
    assert _REAL_SPAWN_COST_S() < 0.5
    monkeypatch.setattr(multiprocessing, 'get_start_method',
                        lambda allow_none=False: 'spawn')
    assert _REAL_SPAWN_COST_S() > 1.0


# ── the probe that feeds the model ──────────────────────────────────────────

def test_the_probe_times_BOTH_backends_when_the_gpu_is_a_candidate(monkeypatch):
    """The selector needs a GPU cost to compare against, and the only honest source
    is this machine on this data."""
    seen = []
    monkeypatch.setattr(vpt, 'detect_beads_frame',
                        lambda f, *, use_gpu=False, **kw: seen.append(use_gpu) or [(1.0, 1.0)])
    vpt._FRAME_COST_CACHE.clear()

    import numpy as np
    ser, gpu = vpt._frame_costs_s(np.zeros((8, 8), np.float32), gpu_ok=True,
                                  min_sigma=1.0, max_sigma=5.0, num_sigma=5,
                                  threshold=0.02)
    assert sorted(seen) == [False, True]
    assert ser >= 0.0 and gpu is not None


def test_the_probe_SKIPS_the_gpu_when_it_is_not_a_candidate(monkeypatch):
    """No GPU (or one that failed the equivalence guard) means there is nothing to
    time — and timing it anyway would run the detector we just refused to trust."""
    seen = []
    monkeypatch.setattr(vpt, 'detect_beads_frame',
                        lambda f, *, use_gpu=False, **kw: seen.append(use_gpu) or [(1.0, 1.0)])
    vpt._FRAME_COST_CACHE.clear()

    import numpy as np
    ser, gpu = vpt._frame_costs_s(np.zeros((8, 8), np.float32), gpu_ok=False,
                                  min_sigma=1.0, max_sigma=5.0, num_sigma=5,
                                  threshold=0.02)
    assert seen == [False], 'the probe timed a GPU it was told not to trust'
    assert gpu is None


def test_the_probe_is_MEMOISED_per_shape_and_params(monkeypatch):
    """Probing costs a real detect (2.8 s at 2048), so a session must not re-probe
    on every call. Keyed on SHAPE as well as params, because shape is what the cost
    depends on — unlike the equivalence VERDICT, which is a property of the machine
    and is deliberately not keyed on the data."""
    import numpy as np
    calls = []
    monkeypatch.setattr(vpt, 'detect_beads_frame',
                        lambda f, *, use_gpu=False, **kw: calls.append(1) or [(1.0, 1.0)])
    vpt._FRAME_COST_CACHE.clear()
    p = dict(gpu_ok=False, min_sigma=1.0, max_sigma=5.0, num_sigma=5, threshold=0.02)

    for _ in range(4):
        vpt._frame_costs_s(np.zeros((8, 8), np.float32), **p)
    assert len(calls) == 1, f'the probe re-ran {len(calls)} times for one shape'

    vpt._frame_costs_s(np.zeros((64, 64), np.float32), **p)
    assert len(calls) == 2, 'a different frame SIZE must be re-probed, not reused'


def test_a_probe_that_EXPLODES_falls_back_rather_than_crashing(monkeypatch):
    """A stack whose frame 0 will not detect is for the detector to report, not for
    the tier selector to raise on. Unknown cost -> the tier that needs no
    justification."""
    import numpy as np
    def _boom(*a, **k):
        raise RuntimeError('unreadable frame')

    monkeypatch.setattr(vpt, 'detect_beads_frame', _boom)
    vpt._FRAME_COST_CACHE.clear()

    ser, gpu = vpt._frame_costs_s(np.zeros((8, 8), np.float32), gpu_ok=True,
                                  min_sigma=1.0, max_sigma=5.0, num_sigma=5,
                                  threshold=0.02)
    assert (ser, gpu) == (0.0, None)
    assert vpt._choose_detection_tier(n_frames=1000, t_ser=0.0, t_gpu=None,
                                      workers=7, gpu_ok=True, pool_ok=True) == 'gpu'
