"""**Object-level biological QC: flags that observe, never filter — and never cry wolf.**

Two properties matter as much as detection: a CLEAN population must flag nothing (the cry-wolf test),
and adding outliers must not change which INLIERS are flagged (what robust MAD buys, and what mean/SD
gets wrong). Plus the contract that makes this safe to ship: it flags, it never drops a row.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.biological_qc_tools import (
    flag_edge_touching, flag_size_outliers, flag_intensity_outliers,
    flag_containment_violations, biological_qc, _mad_outlier_mask)

pytestmark = pytest.mark.base


def _labels():
    lab = np.zeros((20, 20), dtype=int)
    lab[0:3, 5:8] = 1        # touches the TOP edge → truncated
    lab[8:12, 8:12] = 2      # interior → clean
    lab[17:20, 10:13] = 3    # touches the BOTTOM edge → truncated
    return lab


def test_edge_touching_flags_the_border_objects_only():
    edge = flag_edge_touching(_labels())
    assert edge.loc[1] and edge.loc[3]          # truncated at the border
    assert not edge.loc[2]                       # interior object is clean


def test_border_px_widens_the_edge_band():
    lab = np.zeros((20, 20), dtype=int)
    lab[8:12, 8:12] = 2                          # interior
    lab[2:5, 2:5] = 4                            # 2 px from the top-left corner
    assert not flag_edge_touching(lab, border_px=0).loc[4]   # not touching at margin 0
    assert flag_edge_touching(lab, border_px=2).loc[4]       # within a 2 px margin → flagged
    assert not flag_edge_touching(lab, border_px=2).loc[2]   # deep interior stays clean


def test_size_outliers_flag_exactly_the_injected_ones():
    t = pd.DataFrame({'area': [10, 11, 12, 10, 11, 12, 10, 250, 3]})   # 250 huge, 3 tiny fragment
    out = flag_size_outliers(t, column='area', k=3.5)
    assert out.iloc[7] and out.iloc[8]                       # the two injected outliers
    assert not out.iloc[:7].any()                            # every inlier is clean


def test_a_clean_population_flags_NOTHING_the_cry_wolf_test():
    t = pd.DataFrame({'area': [10, 11, 12, 10, 11, 12, 9, 13, 10, 11]})
    assert not flag_size_outliers(t, column='area').any()
    assert not flag_intensity_outliers(pd.DataFrame({'intensity_mean': [100, 101, 99, 102, 98, 100]})).any()


def test_MAD_robustness_adding_outliers_does_not_move_the_inliers():
    """The property mean/SD gets wrong: the outliers must not corrupt the estimator that finds them, so
    which INLIERS are flagged is unchanged whether or not extreme outliers are present."""
    inliers = [10, 11, 12, 10, 11, 12, 9, 13]
    clean = _mad_outlier_mask(np.array(inliers, float), 3.5)
    with_outliers = _mad_outlier_mask(np.array(inliers + [500, 600], float), 3.5)
    assert not clean.any()                                   # inliers clean on their own
    assert not with_outliers[:len(inliers)].any()            # STILL clean with outliers present
    assert with_outliers[len(inliers):].all()                # and the outliers are caught


def test_containment_flags_a_child_outside_its_parent():
    parent = np.zeros((20, 20), dtype=int)
    parent[5:15, 5:15] = 1                                   # one cell in the middle
    children = pd.DataFrame({'centroid_row': [10, 2], 'centroid_col': [10, 2]})  # inside, then outside
    viol = flag_containment_violations(children, parent)
    assert not viol.iloc[0]                                   # centroid inside the cell → contained
    assert viol.iloc[1]                                       # centroid on background → violation


def test_biological_qc_flags_but_NEVER_drops_a_row():
    t = pd.DataFrame({'label': [1, 2, 3], 'area': [10, 11, 300],
                      'intensity_mean': [100, 101, 99]})
    out = biological_qc(t, _labels(), k=3.5)
    assert len(out) == len(t)                                # the contract: flag, don't filter
    assert 'qc_flags' in out.columns and 'qc_report' in out.attrs
    assert out.attrs['qc_report']['size_outlier'] == 1       # the area=300 object
    # the summary string is an observation, not a verdict
    assert 'unusual size' in out.loc[2, 'qc_flags']
    assert out.loc[1, 'qc_flags'] == ''                      # the clean interior object carries no flag


def test_report_counts_match_the_flags():
    t = pd.DataFrame({'label': [1, 2, 3], 'area': [10, 11, 12]})
    out = biological_qc(t, _labels(), k=3.5)
    # labels 1 and 3 touch the border in _labels()
    assert out.attrs['qc_report']['edge_touching'] == int(out['qc_edge_touching'].sum()) == 2
