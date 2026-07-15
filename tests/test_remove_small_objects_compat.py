"""The version-safe ``remove_small_objects_compat`` must remove objects STRICTLY SMALLER than the
threshold (the historical ``min_size`` semantics), and must NOT emit the skimage 0.26 FutureWarning.

Background: scikit-image 0.26 deprecated ``min_size`` for ``max_size`` — and it is not a rename:
``min_size=N`` removed size < N, while ``max_size=N`` removes size <= N. A naive rename shifts the
threshold by one. Several call sites had drifted (positional, ``min_size=``, and even
``max_size=min_size``); they are now all routed through this one helper.
"""

import warnings

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.utils.general_utils import remove_small_objects_compat


def _mask_with_sizes():
    # objects of area 1, 3, 5, 10 along a single row
    m = np.zeros((1, 40), bool)
    m[0, 0:1] = True
    m[0, 3:6] = True
    m[0, 10:15] = True
    m[0, 20:30] = True
    return m


def _kept_sizes(mask):
    import skimage as sk
    lab = sk.measure.label(mask)
    return sorted(int((lab == i).sum()) for i in range(1, lab.max() + 1))


def test_removes_strictly_smaller():
    out = remove_small_objects_compat(_mask_with_sizes(), 5)
    assert _kept_sizes(out) == [5, 10]     # <5 dropped, >=5 kept


def test_no_deprecation_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")     # any FutureWarning becomes an error
        remove_small_objects_compat(_mask_with_sizes(), 5)


def test_zero_or_negative_is_noop():
    m = _mask_with_sizes()
    assert remove_small_objects_compat(m.copy(), 0).sum() == m.sum()
    assert remove_small_objects_compat(m.copy(), -3).sum() == m.sum()


def test_threshold_one_keeps_all():
    m = _mask_with_sizes()
    # remove < 1 → nothing removed
    assert remove_small_objects_compat(m.copy(), 1).sum() == m.sum()
