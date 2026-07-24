"""**Linear spectral / bleed-through unmixing — from controls, refusing to invert garbage.**

The load-bearing assertions: the mixing matrix is estimated from single-label CONTROLS (never the mixed
data), background is removed before it is formed, mix→unmix round-trips to the true abundances, a
singular/ill-conditioned matrix is REFUSED with a reason (not pseudo-inverted), and the negative fraction is
reported as the honesty check (with clipping reserved for display only).
"""
import numpy as np
import pytest

from pycat.toolbox.unmixing_tools import (
    estimate_mixing_matrix, unmix, negative_fraction, clip_for_display, mixing_matrix_warnings)
from pycat.utils.errors import ScientificAssumptionError

pytestmark = pytest.mark.core

# A known 2-channel crosstalk: fluorophore 0 leaks 8 % into ch1; fluorophore 1 leaks 15 % into ch0.
_M_TRUE = np.array([[1.00, 0.15],
                    [0.08, 1.00]])


def test_estimate_recovers_the_matrix_from_single_label_controls():
    # control j = M[:, j] * (its own brightness); estimation normalises by the own channel → recovers M.
    controls = [(_M_TRUE[:, 0] * 500.0), (_M_TRUE[:, 1] * 320.0)]
    M = estimate_mixing_matrix(controls)
    assert np.allclose(M, _M_TRUE, atol=1e-9)
    assert np.allclose(np.diag(M), 1.0)                       # own channel reads 1 by construction


def test_background_is_removed_BEFORE_the_matrix_is_formed():
    """An un-subtracted pedestal inflates every crosstalk ratio — subtracting it recovers the true matrix,
    NOT subtracting it does not."""
    ped = 100.0
    controls = [(_M_TRUE[:, 0] * 500.0) + ped, (_M_TRUE[:, 1] * 320.0) + ped]
    assert np.allclose(estimate_mixing_matrix(controls, background=ped), _M_TRUE, atol=1e-9)
    # Ignoring the pedestal biases the off-diagonal crosstalk (toward the pedestal ratio).
    assert not np.allclose(estimate_mixing_matrix(controls, background=0.0), _M_TRUE, atol=1e-3)


def test_mix_then_unmix_round_trips_to_the_true_abundances():
    rng = np.random.default_rng(0)
    a_true = np.stack([rng.random((16, 16)), rng.random((16, 16))]) * 1000.0   # (2,H,W)
    observed = np.einsum('ij,jhw->ihw', _M_TRUE, a_true)                        # c = M · a
    M = estimate_mixing_matrix([_M_TRUE[:, 0], _M_TRUE[:, 1]])
    recovered = unmix(observed, M)
    assert np.allclose(recovered, a_true, atol=1e-6)


def test_unmix_REFUSES_a_singular_matrix_rather_than_inverting_garbage():
    singular = np.array([[1.0, 1.0], [1.0, 1.0]])           # channels linearly dependent
    with pytest.raises(ScientificAssumptionError, match="singular|ill-conditioned"):
        unmix(np.ones((2, 4, 4)), singular)
    near = np.array([[1.0, 1.0], [1.0, 1.0 + 1e-9]])         # ill-conditioned, not exactly singular
    with pytest.raises(ScientificAssumptionError):
        unmix(np.ones((2, 4, 4)), near)


def test_estimate_REFUSES_a_control_dark_in_its_own_channel():
    # control 1's own channel (ch1) is ~0 → cannot normalise; assignment/background is wrong.
    with pytest.raises(ScientificAssumptionError, match="OWN channel"):
        estimate_mixing_matrix([np.array([500.0, 40.0]), np.array([50.0, 0.0])])


def test_only_2_to_4_channels_are_supported():
    with pytest.raises(ScientificAssumptionError, match="2.4 channels"):
        estimate_mixing_matrix([np.array([1.0])])                       # 1 channel
    with pytest.raises(ScientificAssumptionError, match="2.4 channels"):
        estimate_mixing_matrix([np.zeros(5) for _ in range(5)])         # 5 channels


def test_the_negative_fraction_is_the_honesty_check():
    assert negative_fraction(np.array([[1.0, 2.0], [3.0, 0.0]])) == 0.0
    assert negative_fraction(np.array([1.0, -1.0, -1.0, 1.0])) == 0.5
    assert negative_fraction(np.array([])) == 0.0
    # A wrong model produces negatives the right one does not: only fluorophore 0 is present, so the true
    # unmix leaves ch1 at ~0; a matrix that OVER-claims fluorophore 0's leak into ch1 (0.30 vs the true
    # 0.08) over-subtracts and drives the recovered ch1 abundance negative.
    a_true = np.stack([np.full((8, 8), 800.0), np.zeros((8, 8))])        # only fluorophore 0 present
    observed = np.einsum('ij,jhw->ihw', _M_TRUE, a_true)
    assert negative_fraction(unmix(observed, _M_TRUE)) == 0.0            # the right matrix: no negatives
    wrong = unmix(observed, np.array([[1.0, 0.15], [0.30, 1.0]]))       # over-claims the 0→1 leak
    assert negative_fraction(wrong) > 0.0


def test_clip_for_display_removes_negatives_without_touching_positives():
    a = np.array([-2.0, 0.0, 3.5])
    assert np.array_equal(clip_for_display(a), np.array([0.0, 0.0, 3.5]))


def test_mixing_matrix_warnings_flag_a_swapped_assignment_and_a_negative_leak():
    swapped = np.array([[1.0, 1.4], [0.1, 1.0]])            # fluorophore 1 brighter in ch0 than its own
    assert any('assignment may be swapped' in w for w in mixing_matrix_warnings(swapped))
    over_sub = np.array([[1.0, -0.1], [0.1, 1.0]])          # negative leak → over-subtracted background
    assert any('over-subtracted' in w for w in mixing_matrix_warnings(over_sub))
    assert mixing_matrix_warnings(_M_TRUE) == []            # a sane matrix warns about nothing


def test_a_3d_image_control_is_reduced_to_its_channel_means():
    # control as a (K,H,W) image, not a pre-computed vector — estimation means over space per channel.
    c0 = np.stack([np.full((4, 4), 500.0), np.full((4, 4), 40.0)])       # ch0=500, ch1=40 → col [1, .08]
    c1 = np.stack([np.full((4, 4), 75.0), np.full((4, 4), 500.0)])       # ch0=75,  ch1=500 → col [.15, 1]
    M = estimate_mixing_matrix([c0, c1])
    assert np.allclose(M, _M_TRUE, atol=1e-9)


# ── Round-trip property: recovery cannot encode a hand-computed wrong expectation (unmixing_test_fixtures) ──
# Two unmixing failures were test-side: an expected value that did not follow from the fixture's linear algebra
# (a channel that is pure crosstalk correctly unmixes to ~0, not to its measured value). This constructs the
# true abundances A FIRST, forms measured = M·A (+ background), and asserts unmix RECOVERS A — so the expected
# value IS the input and there is no number to get wrong. unmixing_tools.py is unchanged.

@pytest.mark.parametrize("M, A, background", [
    # a channel with ZERO true abundance (the CI failing case, correctly expressed) — pure bleed-through → ~0
    (np.array([[1.0, 0.15], [0.08, 1.0]]),
     np.stack([np.full((6, 6), 800.0), np.zeros((6, 6))]), 0.0),
    # both channels present, ASYMMETRIC crosstalk (0.15 vs 0.08)
    (np.array([[1.0, 0.15], [0.08, 1.0]]),
     np.stack([np.full((5, 5), 300.0), np.full((5, 5), 120.0)]), 0.0),
    # 3-channel matrix
    (np.array([[1.0, 0.10, 0.05], [0.08, 1.0, 0.12], [0.03, 0.07, 1.0]]),
     np.stack([np.full((4, 4), 200.0), np.full((4, 4), 90.0), np.full((4, 4), 350.0)]), 0.0),
    # scalar background offset — unmix subtracts it before inversion
    (np.array([[1.0, 0.15], [0.08, 1.0]]),
     np.stack([np.full((5, 5), 300.0), np.full((5, 5), 120.0)]), 100.0),
    # per-channel background vector
    (np.array([[1.0, 0.15], [0.08, 1.0]]),
     np.stack([np.full((5, 5), 300.0), np.full((5, 5), 120.0)]), np.array([50.0, 80.0])),
])
def test_mix_then_unmix_round_trips_across_the_parameter_sweep(M, A, background):
    bg = np.asarray(background, dtype=float)
    measured = np.einsum('kj,j...->k...', M, A)
    measured = measured + (bg.reshape((-1,) + (1,) * (A.ndim - 1)) if bg.ndim else bg)
    recovered = unmix(measured, M, background=background)
    assert np.allclose(recovered, A, atol=1e-9)          # tolerance, never equality — −1e-15 is zero
