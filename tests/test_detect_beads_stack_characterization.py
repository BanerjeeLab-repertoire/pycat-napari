"""**Serial-path characterization net for `detect_beads_stack` — the VPT-validated detection stage.**

`detect_beads_stack` is the shared detection stage feeding the whole VPT viscosity chain (the ~8.325
baseline through TrackMate). It is being decomposed by pipeline stage, and the split must be
**byte-identical**: the same detections, in the same order (downstream linking is order-sensitive), with
the same coordinates, area, and counts.

The existing `test_vpt_gpu_equivalence` pins GPU/CPU-parallel against serial — but it SKIPS without a GPU,
and the sandbox has none. This pins the SERIAL path directly, on a small seeded synthetic stack, so the
decomposition is guarded on every machine. The reference values were recorded from the pre-split code.
"""
import hashlib

import numpy as np
import pytest

pytestmark = pytest.mark.base


def _synth(n_frames=4, shape=(96, 96), n_beads=12, seed=0):
    """A deterministic synthetic bead movie — bright Gaussian spots with small per-frame jitter. The rng
    draw ORDER (bead positions, then per-frame noise interleaved with per-bead jitter) is load-bearing:
    it fixes the exact detections the reference below was recorded against."""
    rng = np.random.default_rng(seed)
    ys = rng.integers(10, shape[0] - 10, n_beads)
    xs = rng.integers(10, shape[1] - 10, n_beads)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    frames = []
    for _t in range(n_frames):
        img = rng.normal(50, 3, shape).astype(np.float32)
        for by, bx in zip(ys, xs):
            img += 200.0 * np.exp(
                -((yy - (by + rng.normal(0, 0.3))) ** 2 + (xx - (bx + rng.normal(0, 0.3))) ** 2)
                / (2 * 2.0 ** 2))
        frames.append(img.astype(np.float32))
    return np.stack(frames, 0)


def _detect(stack):
    from pycat.toolbox.vpt_tools import detect_beads_stack
    return detect_beads_stack(stack, use_gpu='false', parallel='none', quality_mode='fast',
                              min_sigma=1.0, max_sigma=4.0, num_sigma=4, threshold=0.02)


def test_the_serial_detection_table_is_unchanged():
    df = _detect(_synth())

    assert len(df) == 40, "detection count changed — the split altered detection"
    assert df.groupby('frame').size().to_dict() == {0: 10, 1: 10, 2: 10, 3: 10}
    assert float(df['y_um'].sum()) == pytest.approx(1770.156694, rel=1e-9)
    assert float(df['x_um'].sum()) == pytest.approx(2230.702098, rel=1e-9)
    assert float(df['area_um2'].iloc[0]) == pytest.approx(100.530965, rel=1e-9)


def test_the_detection_ORDER_is_unchanged():
    """Downstream linking is order-sensitive, so the order-sensitive coordinate hash is pinned exactly —
    a reordered (even if same-set) detection list fails here."""
    df = _detect(_synth())
    blob = ",".join(f"{round(float(y), 4)}:{round(float(x), 4)}"
                    for y, x in zip(df['y_um'], df['x_um']))
    assert hashlib.sha256(blob.encode()).hexdigest()[:16] == '23217a141e7b6f53'


def test_frame0_coordinates_are_exact():
    df = _detect(_synth())
    got = [(round(float(r['y_um']), 6), round(float(r['x_um']), 6))
           for _, r in df[df['frame'] == 0].iterrows()]
    expected = [(14.076442, 51.699213), (58.558559, 58.063585), (79.009673, 9.823759),
                (74.237608, 47.761243), (71.049646, 72.051065), (47.920115, 82.826477),
                (11.379627, 81.117765), (23.34449, 31.160658), (33.09833, 58.248961),
                (30.442776, 64.919302)]
    assert got == pytest.approx(expected, rel=1e-5)
