"""**Characterization pin for `run_timeseries_condensate_analysis` — written BEFORE it moves.**

The time-series analysis entry point (`run_timeseries_condensate_analysis` + its per-frame worker
`_ts_analyze_frame_worker`) is about to be relocated into `toolbox/timeseries/analysis.py`. Per the
decomposition discipline — **no test, no move** — this pins its exact output on a fixed synthetic stack so
the relocation is provably behaviour-preserving: the same DataFrame and condensate mask must come out
after the move as before it.

The scenario is deterministic and headless: a seeded 3-frame stack with two labelled cells, each holding a
bright punctum (cell 1's grows across frames), analysed on the SERIAL path (`use_parallel=False`) with drift
correction off. Segmentation runs on CPU (numpy/skimage), so the pinned integers are machine-independent —
the same net the existing segmentation characterization tests rely on.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base


def _synthetic_scene():
    rng = np.random.default_rng(0)
    T, H, W = 3, 64, 64
    stack = rng.normal(120, 5, (T, H, W)).astype(np.float32)
    for t in range(T):
        stack[t, 28:34, 28:34] += 250 + 40 * t     # cell 1: a punctum that brightens each frame
        stack[t, 40:45, 44:49] += 200              # cell 2: a steady punctum
    cell_mask = np.zeros((H, W), dtype=np.int32)
    cell_mask[20:40, 20:40] = 1
    cell_mask[36:56, 38:58] = 2
    return stack, cell_mask


def _run():
    from pycat.toolbox.timeseries_condensate_tools import run_timeseries_condensate_analysis
    stack, cell_mask = _synthetic_scene()
    return run_timeseries_condensate_analysis(
        stack, stack.copy(), cell_mask, ball_radius=5, microns_per_pixel_sq=0.01,
        use_drift_correction=False, use_parallel=False, compute_spatial=False,
        per_frame_normalize=False)


def test_the_analysis_output_is_byte_identical_on_a_fixed_scene():
    """Every number the time-series analysis produces on the fixed scene, pinned exactly."""
    df, cstack = _run()

    # shape + column contract
    assert list(df.columns) == [
        'frame', 'cell_label', 'total_condensate_area_px', 'total_condensate_area_um2',
        'cell_area_px', 'condensate_fraction', 'n_condensates', 'mean_condensate_area_um2',
        'drift_row_px', 'drift_col_px']
    assert list(df['frame']) == [0, 0, 1, 1, 2, 2]
    assert list(df['cell_label']) == [1, 2, 1, 2, 1, 2]

    # the segmentation-derived integers — the numbers a silent regression would move
    assert list(df['total_condensate_area_px']) == [54, 39, 56, 39, 52, 37]
    assert list(df['cell_area_px']) == [392, 400, 392, 400, 392, 400]
    assert list(df['n_condensates']) == [1, 1, 1, 1, 1, 1]
    assert list(df['drift_row_px']) == [0, 0, 0, 0, 0, 0]
    assert list(df['drift_col_px']) == [0, 0, 0, 0, 0, 0]

    # derived quantities, exact
    np.testing.assert_allclose(
        df['total_condensate_area_um2'], np.array([54, 39, 56, 39, 52, 37]) * 0.01, rtol=0, atol=1e-12)
    # condensate_fraction is area/cell_area rounded to 6 decimals (the function's own rounding, pinned).
    np.testing.assert_allclose(
        df['condensate_fraction'],
        np.round(np.array([54, 39, 56, 39, 52, 37]) / np.array([392, 400, 392, 400, 392, 400]), 6),
        rtol=0, atol=1e-9)

    # the returned condensate mask stack
    assert cstack.shape == (3, 64, 64)
    assert cstack.dtype == np.uint8
    assert int(cstack.sum()) == 277


def test_the_analysis_is_deterministic():
    """Two runs of the same scene agree exactly — the pin above is not a fluke of one run."""
    df1, c1 = _run()
    df2, c2 = _run()
    assert df1.equals(df2)
    assert np.array_equal(c1, c2)
