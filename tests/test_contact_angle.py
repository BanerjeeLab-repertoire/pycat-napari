"""
**A hydrophobic droplet's contact angle was reported as its acute supplement.**

`estimate_contact_angle` computed `θ = arcsin(a/R)`, which can never exceed 90° -- so a droplet with a
true contact angle of, say, 130° (explicitly claimed reachable in the docstring) was reported as
50°. Two things caused the cap: (1) `arcsin` is bounded at 90°, and (2) the base line was taken as the
*widest* boundary row, which for θ>90° is the equator, not the contact line.

The fix detects the contact line as the bottom-most boundary row and computes the full angle from the
circle geometry, `θ = arccos((cy − base_row)/R)`, spanning 0–180°. These tests rasterize spherical caps
of known contact angle and assert recovery across both regimes.
"""
import numpy as np
import pytest


def _fn():
    m = pytest.importorskip("pycat.toolbox.invitro.analysis")
    return m.estimate_contact_angle


def _droplet_mask(theta_deg, R=70, size=280):
    """A sessile-droplet silhouette of known contact angle: a spherical cap cut flat at the substrate."""
    th = np.radians(theta_deg)
    cx = cy = size // 2
    base_row = cy - R * np.cos(th)          # < cy for θ<90, > cy for θ>90
    y, x = np.indices((size, size))
    disk = (x - cx) ** 2 + (y - cy) ** 2 <= R ** 2
    return (disk & (y <= base_row)).astype(np.uint8)


@pytest.mark.base
@pytest.mark.parametrize("true_theta", [40.0, 65.0, 90.0, 110.0, 135.0])
def test_contact_angle_recovered_across_the_full_range(true_theta):
    fn = _fn()
    res = fn(np.zeros_like(_droplet_mask(true_theta), dtype=float), _droplet_mask(true_theta))
    assert res['fit_success']
    assert abs(res['contact_angle_deg'] - true_theta) < 4.0, (res['contact_angle_deg'], true_theta)


@pytest.mark.base
def test_hydrophobic_droplet_is_not_folded_to_its_acute_supplement():
    """The smoking gun: θ=130° must not come back as ~50° (what the old arcsin(a/R) returned)."""
    fn = _fn()
    res = fn(np.zeros((280, 280), dtype=float), _droplet_mask(130.0))
    assert res['contact_angle_deg'] > 90.0
    assert abs(res['contact_angle_deg'] - 130.0) < 4.0
