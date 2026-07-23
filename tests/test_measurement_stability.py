"""**Per-measurement parameter stability — mask agreement is not measurement agreement.**

Two parameter settings can produce masks that agree at Dice 0.95 while the partition coefficient differs
by 40%, because a small boundary shift moves the dense/dilute split. These tests pin, on constructions
whose true sensitivity is KNOWN: a genuinely stable measurement reads `stable`; a genuinely sensitive one
(a partition coefficient whose boundary the threshold decides) reads `sensitive`/`unstable`; a sweep that
changes the object count is called a **population change**, not measurement instability; the relative range
is scale-free; and a near-zero baseline yields `nan` with a reason, never a divide-by-zero.
"""
import numpy as np
import pytest

from pycat.toolbox.measurement_stability import (
    StabilityResult, measurement_stability, stability_factor)

pytestmark = pytest.mark.base


def _threshold(image, threshold=0.5):
    return np.asarray(image) > threshold


# ── A genuinely STABLE measurement: total object intensity of flat-topped, well-separated blobs ─
def test_a_stable_measurement_reads_stable():
    shape = (120, 120)
    img = np.full(shape, 0.05, dtype=float)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    for cy, cx in ((30, 30), (30, 90), (90, 60)):
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= 8 ** 2] = 1.0     # flat-topped disks at 1.0

    def measure(labels, image):
        m = np.asarray(labels) > 0
        return {'total_object_intensity': float(np.asarray(image)[m].sum())}

    # Thresholds all sit between background (0.05) and the flat disks (1.0), so the mask — and the total
    # intensity — do not move: a genuinely stable number.
    results = measurement_stability(img, _threshold, 'threshold', [0.4, 0.5, 0.6], measure)
    r = results[0]
    assert r.verdict == 'stable' and r.relative_range < 0.05


# ── A genuinely SENSITIVE measurement: a partition coefficient whose boundary the threshold decides ─
def test_a_sensitive_measurement_reads_sensitive_or_unstable():
    shape = (120, 120)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    d = np.sqrt((yy - 60) ** 2 + (xx - 60) ** 2)
    # A SMOOTH dense bump (no flat top) on a dilute background — the threshold decides where "dense" ends,
    # so the dense area (and thus the dense mean, and the partition coefficient) moves a lot with it.
    img = 1.0 + 1.5 * np.exp(-(d ** 2) / (2 * 18 ** 2))

    def partition(labels, image):
        image = np.asarray(image); dense = np.asarray(labels) > 0
        dilute = ~dense
        if not dense.any() or not dilute.any():
            return {'partition_coefficient': float('nan')}
        return {'partition_coefficient': float(image[dense].mean() / image[dilute].mean())}

    # A plausible ± threshold sweep around the bump's mid-height.
    results = measurement_stability(img, _threshold, 'threshold', [1.3, 1.5, 1.7], partition)
    r = results[0]
    assert r.verdict in ('sensitive', 'unstable'), (
        f"a partition coefficient whose boundary the threshold decides should not read stable: "
        f"{r.relative_range:.2%} → {r.verdict}")


# ── THE population-change trap: a sweep that changes object count is not "instability" ───────────
def test_a_population_change_is_not_called_measurement_instability():
    shape = (140, 140)
    img = np.full(shape, 0.02, dtype=float)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    # Four bright disks (1.0) and five dim disks (0.3): a rising threshold drops the dim ones, changing
    # the object COUNT — so a shifting mean size reflects a different population.
    for i, (cy, cx) in enumerate([(30, 30), (30, 70), (30, 110), (70, 30),
                                  (70, 70), (70, 110), (110, 30), (110, 70), (110, 110)]):
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= 6 ** 2] = 1.0 if i < 4 else 0.3

    def mean_size(labels, image):
        from pycat.toolbox.benchmark_tools import _labelled, _object_table
        _, areas = _object_table(_labelled(labels))
        return {'mean_object_size': float(areas.mean()) if areas.size else float('nan')}

    results = measurement_stability(img, _threshold, 'threshold', [0.15, 0.25, 0.5], mean_size)
    r = results[0]
    assert r.verdict == 'population-change', (
        f"a sweep that changes the object count ({r.n_objects}) must be reported as a population change, "
        f"not measurement instability — got {r.verdict}")
    assert 'population' in r.reason.lower()


# ── relative_range is scale-free ────────────────────────────────────────────────────────────────
def test_relative_range_is_scale_free():
    shape = (120, 120)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    d = np.sqrt((yy - 60) ** 2 + (xx - 60) ** 2)
    img = 1.0 + 1.5 * np.exp(-(d ** 2) / (2 * 18 ** 2))

    def partition(labels, image):
        image = np.asarray(image); dense = np.asarray(labels) > 0
        if not dense.any() or not (~dense).any():
            return {'k': float('nan')}
        return {'k': float(image[dense].mean() / image[~dense].mean())}

    base = measurement_stability(img, _threshold, 'threshold', [1.3, 1.5, 1.7], partition)[0]
    # Multiply intensities AND thresholds by the same constant → identical masks, all measurements ×C.
    scaled = measurement_stability(10.0 * img, _threshold, 'threshold', [13.0, 15.0, 17.0], partition)[0]
    assert base.relative_range == pytest.approx(scaled.relative_range, rel=1e-9)
    assert base.verdict == scaled.verdict


# ── A near-zero baseline yields nan + a reason, never a divide-by-zero ───────────────────────────
def test_a_zero_baseline_is_undefined_not_infinite():
    shape = (80, 80)
    img = np.full(shape, 0.5, dtype=float)          # a flat field: no objects at any threshold in the sweep

    def zero_measure(labels, image):
        m = np.asarray(labels) > 0
        return {'flagged_area': float(m.sum())}     # baseline 0 (nothing above threshold)

    results = measurement_stability(img, _threshold, 'threshold', [0.6, 0.7, 0.8], zero_measure)
    r = results[0]
    assert np.isnan(r.relative_range) and r.verdict == 'undefined'
    assert 'zero' in r.reason.lower() or 'undefined' in r.reason.lower()


# ── The MRI adapter maps the verdict to a sensitivity factor ─────────────────────────────────────
def test_stability_factor_maps_verdict_to_a_reliability_factor():
    def mk(verdict):
        return StabilityResult('m', 1.0, 'p', (1.0,), 0.0, verdict, (1,))
    assert stability_factor(mk('stable')) == 1.0
    assert stability_factor(mk('sensitive')) == 0.6
    assert stability_factor(mk('unstable')) == 0.2
    assert np.isnan(stability_factor(mk('population-change')))   # unknown, not "reliable"
    assert np.isnan(stability_factor(mk('undefined')))
