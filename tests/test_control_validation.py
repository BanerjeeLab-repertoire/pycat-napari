"""**Positive/negative control validation — recommend an operating point, or REFUSE when none separates.**

The scientific contract: recommend the parameters that maximize detection in a positive control *subject
to* the negative control staying near zero — and when no setting distinguishes the two, return ``None``
with a stated reason rather than a least-bad setting (which would launder an assay problem into a software
recommendation). These tests pin: a recommendation recovers ~N with ~0 false positives; the **refusal case**
(indistinguishable controls → ``None`` + reason) — the most important one; mismatched acquisition warns;
density is normalized so different field sizes are comparable; and a declared non-empty negative count is
honored rather than flagging a legitimate baseline.
"""
import math

import numpy as np
import pytest

from pycat.toolbox.control_validation import (
    ControlResult, control_report_figure, recommend_parameters, validate_against_controls)

pytestmark = pytest.mark.base


def _threshold(image, threshold=0.5):
    """A trivial intensity segmenter — the method under validation."""
    return np.asarray(image) > threshold


def _disks_on_grid(shape, n, radius=3, value=1.0):
    """Place ``n`` separated disks of intensity ``value`` on a grid — a deterministic object count."""
    H, W = shape
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    img = np.zeros(shape, dtype=float)
    yy, xx = np.mgrid[0:H, 0:W]
    placed = 0
    for r in range(rows):
        for c in range(cols):
            if placed >= n:
                break
            cy = (r + 0.5) * H / rows
            cx = (c + 0.5) * W / cols
            img[(yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2] = value
            placed += 1
    return img


def _bright_and_dim(shape=(180, 180), bright=4, dim=5, radius=3, dim_value=0.3):
    """A positive control: ``bright`` disks at 1.0 and ``dim`` disks at ``dim_value`` — so a lower
    threshold recovers MORE objects (bright+dim), which is what a good recommendation should prefer."""
    img = _disks_on_grid(shape, bright, radius=radius, value=1.0)
    # place the dim disks in a shifted grid so they do not overlap the bright ones
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W]
    for i in range(dim):
        cy = (i + 0.5) * H / dim
        cx = W * 0.8
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2] = dim_value
    return img


# ── The recommendation recovers ~N with ~0 false positives ─────────────────────────────────────
def test_recommendation_recovers_the_objects_with_no_false_positives():
    positive = _bright_and_dim(bright=4, dim=5)          # 9 objects total (4 bright, 5 dim)
    negative = np.zeros((180, 180), dtype=float)         # empty negative control
    grid = [{'threshold': t} for t in (0.15, 0.25, 0.5, 0.8)]

    df = validate_against_controls(positive, negative, _threshold, grid, microns_per_px=0.1)
    rec = recommend_parameters(df)

    assert rec is not None
    assert rec.n_positive == 9, f"the recommended setting should recover all 9 objects, got {rec.n_positive}"
    assert rec.n_negative == 0 and rec.false_positive_rate == 0.0
    assert rec.verdict == 'usable'
    # It maximized detection: a low-enough threshold to catch the dim objects too.
    assert rec.params['threshold'] <= 0.3


# ── THE refusal case: indistinguishable controls → None with a reason (never a fabricated pick) ──
def test_it_refuses_when_no_setting_separates_the_controls():
    """The most important test. When the positive and negative are statistically indistinguishable, no
    parameter set separates them — the function must return None and state why, not launder an assay
    problem into a least-bad recommendation."""
    field = _bright_and_dim(bright=4, dim=5)
    # The SAME field as both controls: every threshold detects equally in "positive" and "negative".
    df = validate_against_controls(field, field, _threshold,
                                   [{'threshold': t} for t in (0.15, 0.25, 0.5)], microns_per_px=0.1)

    # Every eligible-by-count setting has a false-positive rate of 1.0 → nothing qualifies.
    assert (df['false_positive_rate'] >= 1.0).all() or (df['n_positive'] == df['n_negative']).all()

    with pytest.warns(UserWarning, match="do not separate|distinguishes your positive"):
        rec = recommend_parameters(df)
    assert rec is None, "a least-bad setting must NOT be returned when the controls do not separate"


# ── Mismatched acquisition between the controls warns loudly ────────────────────────────────────
def test_mismatched_acquisition_warns():
    positive = _bright_and_dim()
    negative = np.zeros((180, 180), dtype=float)
    with pytest.warns(UserWarning, match="acquisition mismatch|not acquired comparably"):
        validate_against_controls(
            positive, negative, _threshold, [{'threshold': 0.5}], microns_per_px=0.1,
            positive_metadata={'exposure_s': 0.1}, negative_metadata={'exposure_s': 0.5})


# ── Density normalization: two positives of different field size give comparable densities ───────
def test_density_is_field_size_independent():
    small = _disks_on_grid((90, 90), n=4, radius=3)      # 4 objects in a 90×90 field
    large = _disks_on_grid((180, 180), n=16, radius=3)   # 16 objects in a 180×180 field — SAME density
    empty_small = np.zeros((90, 90))
    empty_large = np.zeros((180, 180))
    px = 0.1

    d_small = validate_against_controls(small, empty_small, _threshold, [{'threshold': 0.5}],
                                        microns_per_px=px).attrs['control_results'][0].positive_density
    d_large = validate_against_controls(large, empty_large, _threshold, [{'threshold': 0.5}],
                                        microns_per_px=px).attrs['control_results'][0].positive_density

    assert d_small > 0 and d_large > 0
    assert abs(d_small - d_large) / d_small < 0.01, (
        f"objects/µm² should be field-size independent: {d_small} vs {d_large}")


def test_without_a_pixel_size_density_is_nan_not_faked():
    """The pixel-size gate: objects-per-pixel² is not a scientific quantity, so density is left NaN
    rather than computed against an assumed 1.0."""
    positive = _bright_and_dim()
    negative = np.zeros((180, 180))
    df = validate_against_controls(positive, negative, _threshold, [{'threshold': 0.5}])  # no pixel size
    assert math.isnan(df.attrs['control_results'][0].positive_density)


# ── A declared non-empty negative count is honored, not flagged ─────────────────────────────────
def test_a_declared_negative_baseline_is_not_counted_as_false_positives():
    positive = _disks_on_grid((180, 180), n=9, radius=3, value=1.0)
    negative = _disks_on_grid((180, 180), n=3, radius=3, value=1.0)   # a legitimate 3-object baseline

    # Declaring the expected baseline: the 3 negative objects are NOT false positives.
    df_declared = validate_against_controls(positive, negative, _threshold, [{'threshold': 0.5}],
                                            microns_per_px=0.1, expected_negative=3)
    rec = recommend_parameters(df_declared)
    assert rec is not None and rec.false_positive_rate == 0.0 and rec.n_positive == 9

    # Assuming an empty negative (default): the same 3 objects ARE counted as false positives.
    df_default = validate_against_controls(positive, negative, _threshold, [{'threshold': 0.5}],
                                           microns_per_px=0.1)
    assert df_default.attrs['control_results'][0].false_positive_rate == pytest.approx(3 / 9, rel=1e-6)


# ── The report artifact builds ──────────────────────────────────────────────────────────────────
def test_the_report_figure_builds():
    positive = _bright_and_dim()
    negative = np.zeros((180, 180))
    grid = [{'threshold': t} for t in (0.15, 0.25, 0.5, 0.8)]
    df = validate_against_controls(positive, negative, _threshold, grid, microns_per_px=0.1)
    rec = recommend_parameters(df)

    fig = control_report_figure(df, recommended=rec)
    assert fig is not None
    ax = fig.axes[0]
    # both controls plotted, and the recommended operating point marked
    labels = [ln.get_label() for ln in ax.get_lines()]
    assert 'positive control' in labels and 'negative control' in labels
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_control_result_is_frozen_and_carries_a_reason():
    r = ControlResult(method='m', params={'threshold': 0.5}, n_positive=9, n_negative=0,
                      false_positive_rate=0.0, positive_density=1.0, separation=1.0,
                      verdict='usable', reason='clean')
    with pytest.raises(Exception):
        r.verdict = 'nope'                              # frozen
    assert r.reason                                     # a verdict always carries a stated reason
