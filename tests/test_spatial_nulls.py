"""
The spatial null models must be CALIBRATED and must actually run.

Two things this guards, both of which have already gone wrong:

1. **Calibration.** The CSR line is the wrong null for objects confined to a cell: the
   confinement itself produces an apparent signal. Objects placed *uniformly at random
   inside a real non-convex cell* — where the truth is no structure at all — gave
   L(r) = −4.95 ("strong regularity") at one scale and +6.18 ("strong clustering") at
   another. The artefact points in **either direction** depending on the scale, so
   eyeballing L(r) against the CSR line is worse than useless.

2. **The code path actually running.** ``spatial_null_envelope(statistic='pcf')`` was
   shipped in 1.5.397 and **had never worked**: it passed ``r_values`` to
   ``pair_correlation_function``, which takes ``r_max``/``dr``, so every call raised
   ``TypeError``. A static guard cannot catch a signature mismatch in a branch nothing
   exercises — only running it can.
"""

import numpy as np
import pytest

from pycat.toolbox.spatial_metrology_tools import spatial_null_envelope


def _cell_and_points(n=30, clustered=False, seed=0):
    """A realistic, irregular, NON-CONVEX cell, and objects inside it."""
    rng = np.random.default_rng(seed)
    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]
    cell = (((yy - 100) / 70.0) ** 2 + ((xx - 100) / 30.0) ** 2 < 1)
    cell |= (((yy - 60) / 25.0) ** 2 + ((xx - 140) / 25.0) ** 2 < 1)
    ys, xs = np.nonzero(cell)

    if not clustered:
        idx = rng.choice(ys.size, n, replace=False)
        pts = np.column_stack([ys[idx], xs[idx]]).astype(float)
        return cell, pts

    centres = rng.choice(ys.size, 4, replace=False)
    pts = []
    for c in centres:
        for _ in range(n // 4):
            for _try in range(60):
                y = int(round(ys[c] + rng.normal(0, 4)))
                x = int(round(xs[c] + rng.normal(0, 4)))
                if 0 <= y < h and 0 <= x < w and cell[y, x]:
                    pts.append([y, x])
                    break
    return cell, np.asarray(pts, dtype=float)


@pytest.mark.core
@pytest.mark.parametrize("statistic", ["ripley_l", "pcf"])
def test_null_actually_runs(statistic):
    """The code path must execute. This is how statistic='pcf' shipped broken."""
    cell, pts = _cell_and_points(clustered=False, seed=1)
    _df, stats = spatial_null_envelope(pts, cell, microns_per_pixel=0.25,
                                       statistic=statistic, n_simulations=19)
    assert np.isfinite(stats["p_value"]), (
        f"{statistic}: the null did not run. ``statistic='pcf'`` shipped in 1.5.397 and "
        f"had NEVER worked — it passed ``r_values`` to ``pair_correlation_function``, "
        f"which takes ``r_max``/``dr``, so every call raised TypeError. A static guard "
        f"cannot catch a signature mismatch in a branch nothing exercises."
    )


@pytest.mark.core
@pytest.mark.parametrize("statistic", ["ripley_l", "pcf"])
def test_null_false_positive_rate(statistic):
    """The FALSE-POSITIVE RATE must be near alpha — not a single-seed assertion.

    A statistical test at alpha = 0.05 is *supposed* to reject 5 % of null-true cases. An
    earlier version of this test asserted ``not significant`` on ONE seed and duly failed
    at p = 0.040 — which is the test being wrong, not the null. Measured over many seeds:
    ripley_l 5 %, pcf 2 %. Assert the RATE.
    """
    n_sig = 0
    n_rep = 20
    for seed in range(n_rep):
        cell, pts = _cell_and_points(clustered=False, seed=seed)
        _df, stats = spatial_null_envelope(pts, cell, microns_per_pixel=0.25,
                                           statistic=statistic, n_simulations=49,
                                           seed=seed)
        n_sig += bool(stats["significant"])

    rate = n_sig / n_rep
    assert rate <= 0.20, (
        f"{statistic}: objects placed UNIFORMLY AT RANDOM inside the cell were called "
        f"significant in {rate:.0%} of {n_rep} trials. The null is not calibrated — this "
        f"is the CSR failure the compartment-constrained null exists to fix (the CSR line "
        f"called the SAME data 'strong regularity' at one scale and 'strong clustering' "
        f"at another). The bound is generous because 20 trials is a noisy estimate of a "
        f"5 % rate; a genuinely broken null fails it by a wide margin."
    )


@pytest.mark.core
@pytest.mark.parametrize("statistic", ["ripley_l", "pcf"])
def test_null_retains_power(statistic):
    """Genuinely clustered objects MUST be detected — a calibrated-but-blind test is
    worse than none."""
    cell, pts = _cell_and_points(n=32, clustered=True, seed=2)
    df, stats = spatial_null_envelope(pts, cell, microns_per_pixel=0.25,
                                      statistic=statistic, n_simulations=49)
    assert stats["significant"], (
        f"{statistic}: genuine clustering was NOT detected (p = {stats['p_value']:.3f}). "
        f"A null that says 'not significant' to everything is calibrated and useless."
    )
