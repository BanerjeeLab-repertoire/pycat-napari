"""
Regression tests for the client partition / enrichment coefficient.

KNOWN-ANSWER: with no camera offset, K = dense_mean / dilute_mean exactly, so a
synthetic two-phase scene with a known ratio must return that ratio.

Run: pytest tests/test_partition.py -v
"""

import numpy as np
import pytest

from tests.fixtures_synthetic import partition_scene

from pycat.toolbox.partition_enrichment_tools import client_enrichment


def test_partition_known_ratio_no_background():
    """K_true = dense/dilute with background=0 must be recovered exactly."""
    k_true = 5.0
    img, dense, cell = partition_scene(k_true=k_true, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(k_true, rel=1e-3)


def test_partition_unity_when_uniform():
    """A uniform image (dense == dilute intensity) must give K == 1.0."""
    img, dense, cell = partition_scene(k_true=1.0, dense_val=100.0, dilute_val=100.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] == pytest.approx(1.0, rel=1e-3)


def test_partition_background_subtraction_effect():
    """Invariant / sanity: subtracting a positive camera offset increases the
    apparent K (moves the ratio away from 1), per K=(dense-bg)/(dilute-bg)."""
    img, dense, cell = partition_scene(k_true=3.0, dilute_val=100.0)  # dense=300
    k_no_bg = client_enrichment(img, dense, cell_mask=cell, background=0.0)['enrichment']
    k_with_bg = client_enrichment(img, dense, cell_mask=cell, background=50.0)['enrichment']
    # (300-50)/(100-50) = 5.0  > 3.0
    assert k_with_bg > k_no_bg
    assert k_with_bg == pytest.approx((300 - 50) / (100 - 50), rel=1e-3)


def test_partition_non_negative():
    """Invariant: enrichment of a real positive-intensity scene is non-negative."""
    img, dense, cell = partition_scene(k_true=2.0)
    res = client_enrichment(img, dense, cell_mask=cell, background=0.0)
    assert res['enrichment'] >= 0.0
