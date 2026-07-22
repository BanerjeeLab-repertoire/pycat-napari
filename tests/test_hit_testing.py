"""**Honest hit-testing — nearest curve wins, empty space and AMBIGUOUS clicks select nothing.**

The interaction-layer Gap 2 contract, pure geometry (display coordinates supplied by the caller): the
nearest curve within tolerance is the hit; a click in empty space hits nothing; a click where two curves are
too close to distinguish refuses and NAMES the candidates rather than guessing (the dishonesty this
removes); endpoint projection is clamped; one click yields at most one selection.
"""
import numpy as np
import pytest

from pycat.utils.hit_testing import (
    HitResult, hit_test, point_segment_distance, nearest_distance_to_curve)

pytestmark = pytest.mark.core


def test_point_segment_distance_clamps_to_the_endpoints():
    # perpendicular within the segment
    assert point_segment_distance(1, 1, 0, 0, 2, 0) == pytest.approx(1.0)
    # past the b end → distance to b, not the infinite line
    assert point_segment_distance(5, 0, 0, 0, 2, 0) == pytest.approx(3.0)
    # a degenerate segment measures to the point
    assert point_segment_distance(3, 4, 0, 0, 0, 0) == pytest.approx(5.0)


def test_nearest_distance_scans_all_segments_and_skips_nonfinite():
    xs, ys = [0, 10, 10], [0, 0, 10]                 # an L-shape
    assert nearest_distance_to_curve(5, 2, xs, ys) == pytest.approx(2.0)   # nearest to the bottom segment
    assert nearest_distance_to_curve(0, 0, [np.nan], [np.nan]) == float('inf')


def _curves():
    return {
        'A': (np.array([0.0, 100.0]), np.array([0.0, 0.0])),        # horizontal at y=0
        'B': (np.array([0.0, 100.0]), np.array([50.0, 50.0])),      # horizontal at y=50
    }


def test_the_nearest_curve_within_tolerance_wins():
    r = hit_test(_curves(), (50.0, 2.0))                            # 2 px from A, 48 from B
    assert r.is_hit and r.primary == 'A' and r.candidates == ('A',)
    assert r.distance_px == pytest.approx(2.0)


def test_a_click_in_empty_space_selects_nothing():
    r = hit_test(_curves(), (50.0, 25.0))                          # 25 px from each — beyond tolerance
    assert r.primary is None and r.candidates == () and not r.is_ambiguous


def test_an_AMBIGUOUS_click_selects_nothing_and_names_the_candidates():
    curves = {'A': (np.array([0.0, 100.0]), np.array([0.0, 0.0])),
              'B': (np.array([0.0, 100.0]), np.array([1.5, 1.5]))}  # 1.5 px apart
    r = hit_test(curves, (50.0, 0.7))                              # ~equidistant → ambiguous
    assert r.primary is None and r.is_ambiguous
    assert set(r.candidates) == {'A', 'B'} and r.ambiguity_px < 3.0


def test_a_clearly_nearer_curve_is_not_ambiguous_even_with_a_neighbour():
    curves = {'A': (np.array([0.0, 100.0]), np.array([0.0, 0.0])),
              'B': (np.array([0.0, 100.0]), np.array([20.0, 20.0]))}
    r = hit_test(curves, (50.0, 1.0))                             # 1 from A, 19 from B → clear
    assert r.primary == 'A'


def test_one_click_yields_at_most_one_primary_and_it_is_a_HitResult():
    r = hit_test(_curves(), (50.0, 2.0))
    assert isinstance(r, HitResult)
    assert (r.primary is None) or isinstance(r.primary, str)      # never a list/set of selections


def test_no_curves_is_a_clean_miss():
    r = hit_test({}, (0.0, 0.0))
    assert r.primary is None and r.candidates == () and r.distance_px == float('inf')


def test_log_scale_is_handled_in_DISPLAY_space_by_the_caller():
    # the caller transforms data→display; hit_test just works in whatever coords it is given. A log-log
    # curve, transformed to display pixels, hit-tests linearly there — the whole reason coords are display.
    disp = {'t': (np.array([10.0, 200.0]), np.array([300.0, 300.0]))}   # already display px
    assert hit_test(disp, (100.0, 302.0)).primary == 't'
    assert hit_test(disp, (100.0, 320.0)).primary is None              # 20 px away → miss
