"""**Analysis-aware kymographs — correctness against known dynamics, and the traps.**

A kymograph is a time-axis tool, so the lazy-stack collapse landmine is the worst place to hit it — pinned
here. The other tests prove the base kymograph recovers a known band velocity, labels axes in real units
only when calibrated (px/frame otherwise), the colocalization variant's per-slice Pearson matches an
independent computation, the object-property variant recovers a shrinking-diameter trend, and a wider
averaging band cuts noise without shifting the recovered slope.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.kymograph_tools import (kymograph, colocalization_kymograph,
                                           object_property_kymograph, Kymograph)

pytestmark = pytest.mark.core


class _LazyStack:
    """A lazy wrapper whose ``__array__`` deliberately collapses to FRAME 0 (napari's view), but whose
    per-frame indexing exposes every frame — the exact shape of PyCAT's lazy stacks."""
    def __init__(self, arr):
        self._a = np.asarray(arr)
        self.shape = self._a.shape
        self.ndim = self._a.ndim

    def __getitem__(self, i):
        return self._a[i]

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        return np.asarray(self._a[0], dtype=dtype)          # COLLAPSE — the landmine


def _moving_band(n_t=10, h=40, w=60, y=20, speed=3.0, x0=10.0, sigma=2.0):
    """A bright Gaussian band that moves along x with a known speed, on a dim background."""
    xx = np.arange(w)
    stack = np.full((n_t, h, w), 5.0, dtype=float)
    for t in range(n_t):
        band = 200.0 * np.exp(-((xx - (x0 + speed * t)) ** 2) / (2 * sigma ** 2))
        stack[t, y, :] += band
        stack[t, y - 1, :] += band * 0.6
        stack[t, y + 1, :] += band * 0.6
    return stack


def test_the_band_velocity_is_recovered_from_the_kymograph_slope():
    stack = _moving_band(speed=3.0, x0=10.0)
    k = kymograph(stack, ((20, 0), (20, 59)))               # horizontal line at y=20
    assert isinstance(k, Kymograph) and k.image.shape[1] == 10   # one column per frame
    peak_pos = np.argmax(k.image, axis=0).astype(float)     # band position per time
    slope = np.polyfit(np.arange(10), peak_pos, 1)[0]
    assert slope == pytest.approx(3.0, abs=0.15), f"recovered band speed {slope}, expected 3.0 px/frame"


def test_a_LAZY_stack_produces_a_full_kymograph_not_a_collapsed_frame():
    stack = _moving_band(n_t=8)
    lazy = _LazyStack(stack)
    assert np.asarray(lazy).ndim == 2, "the stub must collapse under np.asarray (else the test is moot)"
    k = kymograph(lazy, ((20, 0), (20, 59)))
    assert k.image.shape[1] == 8, "kymograph collapsed a lazy stack to one frame — the landmine"


def test_axes_are_labelled_in_real_units_only_when_calibrated():
    stack = _moving_band()
    uncal = kymograph(stack, ((20, 0), (20, 59)))
    assert uncal.units == {'position': 'px', 'axis': 'frame'} and uncal.position_um is None
    cal = kymograph(stack, ((20, 0), (20, 59)), pixel_size_um=0.1, frame_interval_s=2.0)
    assert cal.units == {'position': 'µm', 'axis': 's'}
    assert cal.time_or_depth[1] == pytest.approx(2.0) and cal.position_um[10] == pytest.approx(1.0)


def test_the_colocalization_kymograph_per_slice_pearson_matches_an_independent_computation():
    a = _moving_band(speed=3.0)
    b = _moving_band(speed=3.0)                              # identical → Pearson ≈ 1 per slice
    res = colocalization_kymograph(a, b, ((20, 0), (20, 59)))
    per = res['per_slice']
    assert len(per) == 10
    # independent check on the first slice
    pa = res['kymograph_a'].image[:, 0]; pb = res['kymograph_b'].image[:, 0]
    assert per['pearson'].iloc[0] == pytest.approx(float(np.corrcoef(pa, pb)[0, 1]), rel=1e-9)
    assert per['pearson'].dropna().min() > 0.99             # identical channels co-vary perfectly


def test_the_object_property_kymograph_recovers_a_shrinking_trend():
    df = pd.DataFrame({
        'track_id': [1, 1, 1, 1, 2, 2],
        'frame': [0, 1, 2, 3, 0, 1],
        'diameter': [10.0, 8.0, 6.0, 4.0, 99.0, 99.0]})
    series = object_property_kymograph(df, id_col='track_id', time_col='frame',
                                       property_col='diameter', object_id=1)
    assert list(series['value']) == [10.0, 8.0, 6.0, 4.0]   # object 1's shrink, ordered by time
    assert list(series['time']) == [0, 1, 2, 3]


def test_a_wider_band_reduces_noise_without_shifting_the_slope():
    rng = np.random.default_rng(0)
    stack = _moving_band(speed=3.0) + rng.normal(0, 8.0, (10, 40, 60))
    narrow = kymograph(stack, ((20, 0), (20, 59)), width_px=1)
    wide = kymograph(stack, ((20, 0), (20, 59)), width_px=3)
    assert wide.width_px == 3
    # both recover the same slope; the wide band has lower background noise (off-band rows)
    sl_n = np.polyfit(np.arange(10), np.argmax(narrow.image, axis=0), 1)[0]
    sl_w = np.polyfit(np.arange(10), np.argmax(wide.image, axis=0), 1)[0]
    assert sl_n == pytest.approx(3.0, abs=0.3) and sl_w == pytest.approx(3.0, abs=0.3)
    assert np.std(wide.image[:5]) <= np.std(narrow.image[:5]) * 1.05   # band-edge noise not increased
