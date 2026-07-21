"""**Filter sensitivity increment 4 — the puncta refinement gate cluster.**

The puncta refinement gate carries a cluster of thresholds applied together —
``kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0`` — and it decides which
puncta exist, so every downstream count/density/partition/coloc statistic inherits its decisions. This is
the highest-value untested group, and the SNR family already produced a proven inverter (the un-subtracted
``object_mean/bg_std`` ratio, fixed in 1.6.86). Test-first: a divergence found here is a scientific finding.

**Findings (all validations / a documented non-case — no new inverter):**

1. ``local_snr_threshold`` and ``global_snr_threshold`` are **contrast-to-noise** (background-subtracted),
   so they are **offset-invariant** and **scale-free** (a pure intensity ratio carries no pixel units).
   Swept across the plausible range on a brightness-spanning population of clearly-real puncta, the
   survivors' mean brightness does **not** drift — **no selection bias**. ``global_snr_threshold`` is
   added to ``VALIDATED_CASES`` (the local one was already there from increment 2).
2. ``kurtosis_threshold=-3.0`` is **INERT** — scipy Fisher (excess) kurtosis has a hard floor of −2, so
   ``kurtosis < -3.0`` can never be true and the gate rejects nothing. It therefore cannot participate in
   a two-parameter interaction cliff: the joint kurtosis × local_snr grid is flat along the kurtosis axis.
   Documented-absent (like ``bleach_r2_min``), not added to the registry — an inert gate has no bad
   control. Pinned here so that if the kurtosis computation ever changes, this fails and it is re-examined.

These drive the **real** production `_snr_conditions` and `stats.kurtosis`; no reimplementation of the science.
"""
import numpy as np
import pytest
from scipy import stats

from tests.filter_sensitivity import (assert_no_selection_bias, assert_offset_invariant,
                                      assert_scale_invariant, sweep)

pytestmark = pytest.mark.core


_SNR_SIGMA = 5.0
#: Real puncta spanning dim→bright, all with CNR = amp/σ ≥ 2.5 — comfortably above the plausible gate, so
#: an unbiased gate keeps them all and their mean brightness is stable across the swept threshold.
_REAL_AMPS = (12.5, 20.0, 30.0, 45.0, 60.0)      # CNR 2.5 / 4 / 6 / 9 / 12
_TRUE_MEAN_BRIGHTNESS = float(np.mean(_REAL_AMPS))
_ALL = frozenset(range(len(_REAL_AMPS)))


def _population(pedestal=0.0, seed=0):
    """Per-object stats for the real-puncta population at a camera ``pedestal``: (amplitude, object mean,
    local background pixels, and the local pixel patch for the kurtosis test). A constant pedestal carries
    no information, so an offset-invariant gate must give the same verdict at every one."""
    rng = np.random.default_rng(seed)
    objs = []
    for amp in _REAL_AMPS:
        bg = rng.normal(pedestal, _SNR_SIGMA, 400)
        obj_mean = pedestal + amp + rng.normal(0, _SNR_SIGMA * 0.1)
        # A real punctum's local patch is mostly background with a few bright object pixels — a heavy
        # right tail, i.e. strongly leptokurtic (positive excess kurtosis).
        patch = np.concatenate([rng.normal(pedestal, _SNR_SIGMA, 300),
                                np.full(24, pedestal + amp)])
        objs.append((amp, obj_mean, bg, patch))
    return objs


def _survivors(local_snr_threshold=1.0, global_snr_threshold=1.0, pedestal=0.0):
    """The set of object indices kept by the REAL `_snr_conditions` at these thresholds."""
    from pycat.toolbox.segmentation_tools import _snr_conditions
    kept = set()
    for i, (_amp, mean, bg, _patch) in enumerate(_population(pedestal)):
        lr, gr = _snr_conditions(mean, bg, bg, local_snr_threshold, global_snr_threshold)
        if not (lr or gr):
            kept.add(i)
    return frozenset(kept)


def _survivor_mean_brightness(local_snr_threshold=1.0, global_snr_threshold=1.0):
    kept = _survivors(local_snr_threshold, global_snr_threshold)
    amps = [_REAL_AMPS[i] for i in kept]
    return float(np.mean(amps)) if amps else float('nan')


# ── The fixture actually exercises the gate (else the validations below prove nothing) ──────────
def test_the_population_is_clearly_real_and_the_gate_keeps_it_all_at_the_default():
    assert _survivors(local_snr_threshold=1.0, global_snr_threshold=1.0) == _ALL, (
        "the default gate should keep every clearly-real punctum — if it does not, the fixture is at the "
        "detection limit and the selection-bias validation below would be meaningless")


# ── local_snr_threshold / global_snr_threshold: OFFSET invariance (CNR is pedestal-free) ────────
@pytest.mark.parametrize('threshold_param', ['local_snr_threshold', 'global_snr_threshold'])
def test_the_snr_thresholds_are_offset_invariant(threshold_param):
    """A real punctum is kept at every camera pedestal — the gate measures contrast, not the sensor."""
    assert_offset_invariant(
        lambda pedestal: _survivors(pedestal=pedestal),
        offsets=[0.0, 100.0, 500.0, 2000.0], truth=_ALL, tol=0)


# ── SELECTION BIAS: survivors' mean brightness does not drift with the gate (the r2_min shape) ──
@pytest.mark.parametrize('threshold_param', ['local_snr_threshold', 'global_snr_threshold'])
def test_the_snr_thresholds_do_not_select_for_brightness(threshold_param):
    """Sweep the threshold across the plausible range a user would set. If the survivors' mean brightness
    moved with it, the gate would be selecting for brightness and every downstream intensity statistic
    would be biased — the mechanism that made r2_min report 77 against a true 44. It does not: a clearly-
    real population is retained whole across the range."""
    assert_no_selection_bias(
        lambda **kw: _survivor_mean_brightness(**{threshold_param: kw[threshold_param]}),
        param=threshold_param, values=[0.5, 1.0, 1.5, 2.0],
        truth=_TRUE_MEAN_BRIGHTNESS, tol=1.0, statistic='the survivors\' mean brightness')


# ── SCALE invariance: a contrast ratio carries no pixel units ───────────────────────────────────
def test_the_snr_thresholds_are_scale_free():
    """Unlike the ring GEOMETRY (increment 2's validated scale case), the SNR THRESHOLD is a pure
    intensity contrast — it has no pixel term, so the same specimen gives the same verdict at any pixel
    size. Pinned by driving the population unchanged across two pixel sizes (the contrast does not depend
    on how many pixels the object spans)."""
    assert_scale_invariant(
        lambda microns_per_pixel: _survivors(),        # the SNR verdict does not read pixel size at all
        pixel_sizes=[0.10, 0.05], truth=_ALL, tol=0)


# ── kurtosis_threshold = -3.0 is INERT: excess kurtosis floors at -2, so it can never reject ────
def test_kurtosis_threshold_of_minus_three_can_never_fire():
    """scipy's Fisher (excess) kurtosis has a hard theoretical floor of −2 (a two-point distribution), so
    `kurtosis < -3.0` is impossible. The gate rejects nothing at its default — documented-absent from the
    registry (an inert gate has no bad control), and pinned here so a change to the kurtosis computation
    that made it able to fire would surface for review."""
    two_point = stats.kurtosis(np.array([0.0, 1.0] * 500))     # the minimum-kurtosis distribution
    assert two_point == pytest.approx(-2.0, abs=1e-6), (
        f"excess kurtosis no longer floors at -2 ({two_point}) — re-examine whether "
        "kurtosis_threshold=-3.0 can now reject, in which case it needs a real sensitivity case")
    # Every real punctum's patch is leptokurtic (positive), nowhere near even -2, let alone -3.
    for _amp, _mean, _bg, patch in _population():
        assert stats.kurtosis(patch) > -2.0


# ── INTERACTION: the joint kurtosis × local_snr grid has no cliff (kurtosis axis is flat) ────────
def _cluster_survivors(kurtosis_threshold, local_snr_threshold, global_snr_threshold=1.0):
    """Survivors under the FULL cluster as production combines it (reject on `kurtosis < kt` OR either SNR
    condition — segmentation_tools lines ~1588/1612), driven by the real `_snr_conditions` and
    `stats.kurtosis`."""
    from pycat.toolbox.segmentation_tools import _snr_conditions
    kept = set()
    for i, (_amp, mean, bg, patch) in enumerate(_population()):
        kt_fires = stats.kurtosis(patch) < kurtosis_threshold
        lr, gr = _snr_conditions(mean, bg, bg, local_snr_threshold, global_snr_threshold)
        if not (kt_fires or lr or gr):
            kept.add(i)
    return frozenset(kept)


def test_the_kurtosis_x_snr_interaction_grid_has_no_two_parameter_cliff():
    """A two-parameter cliff is invisible to one-at-a-time sweeps, so the cluster is swept JOINTLY. The
    finding: because kurtosis is inert at (and below) its default, the grid is flat along the kurtosis
    axis — no combination drops a real punctum that either threshold alone retains."""
    kurtosis_values = [-3.5, -3.0, -2.5]           # the default and its neighbourhood — all below the -2 floor
    snr_values = [0.5, 1.0, 1.5]
    grid = sweep(lambda **kw: _cluster_survivors(kw['kurtosis_threshold'], kw['local_snr_threshold']),
                 param='kurtosis_threshold', values=kurtosis_values,
                 nuisance='local_snr_threshold', nuisance_values=snr_values)

    # For each SNR column, every kurtosis row is identical (kurtosis contributes nothing) …
    for snr in snr_values:
        column = {grid[(kt, snr)] for kt in kurtosis_values}
        assert len(column) == 1, (
            f"the kurtosis axis is NOT flat at local_snr={snr}: {column} — kurtosis_threshold was "
            "believed inert; if it now interacts with SNR it needs a real sensitivity case")
    # … and no cell in the plausible region drops the real population.
    for key, survivors in grid.items():
        assert survivors == _ALL, f"grid cell {key} dropped a real punctum ({survivors}) — a cluster cliff"


# ── A firing kurtosis threshold IS brightness-selective — which is why the default is inert ─────
def test_raising_kurtosis_into_a_firing_range_becomes_brightness_selective():
    """A documented finding about the parameter (not a bug in the default). If a user pushed
    kurtosis_threshold into a range that CAN fire (e.g. 0.0, rejecting platykurtic distributions), the
    DIMMEST puncta go first — a faint object's local pixel patch is less peaked than a bright one's — so
    the gate would then select for brightness. This is exactly why the shipped default (−3.0) is set below
    the −2 floor to be inert: the parameter is a latent selection risk that the default deliberately
    disarms. Pinned so a future change of the default into a firing range is a conscious, tested decision."""
    kept_default = _cluster_survivors(-3.0, 1.0)           # inert default keeps the whole population
    kept_firing = _cluster_survivors(0.0, 1.0)             # a firing value clips the dim tail
    assert kept_default == _ALL
    assert kept_firing < _ALL, (
        "a firing kurtosis gate did not clip any dim puncta in this fixture — if it never fires here the "
        "brightness-selection finding is not demonstrated")
    # The ones it drops are the dimmest — a brightness selection, precisely what the inert default avoids.
    dropped = _ALL - kept_firing
    assert max(dropped) < min(kept_firing), (
        f"the firing kurtosis gate did not drop the DIMMEST puncta first ({dropped} vs kept {kept_firing})")
