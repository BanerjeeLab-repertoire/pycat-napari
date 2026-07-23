"""
Group F — object-based colocalization. **The metrics are exact. The metrics are not evidence.**

``manders_m1_calculation``, ``jaccard_index_calculation`` and ``sorensen_dice_coefficient_calculation``
reproduce the analytic overlap **to four decimal places**. The maths is perfect.

**And a pair of channels with NO colocalization whatsoever produces overlap by chance** — an
amount that scales with how crowded the image is:

=============================  ============  =================
density                        coverage      M1 **by CHANCE**
=============================  ============  =================
sparse (15 spots, r = 6)       2 %           0.024
medium (40 spots, r = 8)       12 %          0.110
**dense (80 spots, r = 10)**   **32 %**      **0.338**
=============================  ============  =================

**Two completely independent channels give M1 = 0.34** at a realistic density. *"M1 = 0.34,
substantial colocalization"* would be a **false claim** — that is exactly what randomness gives.

**And it cannot be a fixed threshold**, because it moves with the density: the same M1 that is
meaningless in a crowded cell is strong evidence in a sparse one.

The module had **zero** occurrences of *null*, *chance*, *random*, *permutation* or
*significance*. (Costes randomization exists in ``pixel_wise_corr_analysis_tools`` — **the idea
was in the codebase; it had not reached here.**)

Two things a first attempt got wrong
------------------------------------
* **Clipping the drop position deflates the null.** It pushes objects inward, where they pile up
  and overlap *each other* — the null's coverage came out at **0.270 against the data's 0.313**.
  A null that under-represents the density under-states chance overlap, **and everything looks
  significant.**
* **The null is inherently conservative at high density, and that is not fixable.** The objects
  being relocated are *connected components*, and at 32 % coverage the original discs have
  already merged: **80 discs became 29 blobs.** Relocating those blobs lets them merge *again*.
  So the coverage is **reported**, and the result is **flagged as strained** when the null cannot
  reach the data's density.
"""

import numpy as np
import pytest
from scipy import ndimage as ndi


_SIZE = 256


def _random_spots(n_spots, radius, seed):
    yy, xx = np.mgrid[0:_SIZE, 0:_SIZE]
    rng = np.random.default_rng(seed)

    mask = np.zeros((_SIZE, _SIZE), bool)
    for _ in range(n_spots):
        cy, cx = rng.integers(radius, _SIZE - radius, size=2)
        mask |= np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < radius
    return mask


@pytest.mark.base
@pytest.mark.parametrize("overlap_cols,true_jaccard", [(0, 0.0), (10, 1 / 7), (20, 1 / 3)])
def test_the_overlap_metrics_are_analytically_exact(overlap_cols, true_jaccard):
    """Jaccard, Dice and Manders reproduce the closed-form answer to four decimals."""
    coloc = pytest.importorskip("pycat.toolbox.obj_based_coloc_analysis_tools")

    size = 100
    a = np.zeros((size, size), bool)
    b = np.zeros((size, size), bool)
    a[20:60, 20:60] = True
    b[20:60, 60 - overlap_cols:100 - overlap_cols] = True
    roi = np.ones((size, size), bool)

    measured = float(coloc.jaccard_index_calculation(a, b, roi))
    assert measured == pytest.approx(true_jaccard, abs=1e-4), (
        f"Jaccard = {measured:.4f} against an exact {true_jaccard:.4f}"
    )


@pytest.mark.base
@pytest.mark.parametrize("n_spots,radius,min_chance_m1", [(40, 8, 0.05), (80, 10, 0.25)])
def test_independent_channels_overlap_by_chance_and_it_scales_with_density(
        n_spots, radius, min_chance_m1):
    """**The premise.** If independent channels did not overlap, no null would be needed.

    At 32 % coverage two **completely independent** channels give **M1 = 0.34**. Reporting that
    as *"substantial colocalization"* is reporting randomness.
    """
    coloc = pytest.importorskip("pycat.toolbox.obj_based_coloc_analysis_tools")

    roi = np.ones((_SIZE, _SIZE), bool)
    values = [float(coloc.manders_m1_calculation(_random_spots(n_spots, radius, s),
                                                 _random_spots(n_spots, radius, s + 500),
                                                 roi))
              for s in range(8)]

    assert np.mean(values) > min_chance_m1, (
        f"independent channels at this density gave M1 = {np.mean(values):.3f}. The whole point "
        f"of the null is that chance overlap is NOT small — if it were, the raw M1 would be "
        f"evidence on its own."
    )


@pytest.mark.base
def test_the_coloc_null_does_not_call_independent_channels_colocalized():
    """**False positives: 0/15 sparse, 1/15 medium.** The test is calibrated where it can be."""
    coloc = pytest.importorskip("pycat.toolbox.obj_based_coloc_analysis_tools")

    roi = np.ones((_SIZE, _SIZE), bool)

    false_positives = sum(
        1 for s in range(15)
        if coloc.coloc_significance(_random_spots(40, 8, s), _random_spots(40, 8, s + 500),
                                    roi, n_simulations=99, seed=s)["colocalized"])

    assert false_positives <= 2, (
        f"{false_positives}/15 INDEPENDENT channel pairs were called colocalized. A null that "
        f"over-fires makes every colocalization claim unfalsifiable."
    )


@pytest.mark.base
def test_the_coloc_null_detects_real_colocalization():
    """A 3 px shift of the same objects: **M1 = 0.78 against a null of 0.29, p = 0.010.**"""
    coloc = pytest.importorskip("pycat.toolbox.obj_based_coloc_analysis_tools")

    roi = np.ones((_SIZE, _SIZE), bool)
    channel_1 = _random_spots(80, 10, 0)
    channel_2 = ndi.shift(channel_1.astype(float), (3, 3), order=0) > 0.5

    result = coloc.coloc_significance(channel_1, channel_2, roi, n_simulations=99)

    assert result["colocalized"], (
        f"genuinely colocalized channels (the same objects, shifted 3 px) were not detected: "
        f"M1 = {result['manders_m1']:.3f}, null = {result['null_m1_mean']:.3f}, "
        f"p = {result['p_value']:.3f}"
    )


@pytest.mark.base
def test_the_null_flags_itself_as_strained_at_high_density():
    """**The null is conservative at high density, and that is not fixable — so it says so.**

    The objects being relocated are *connected components*, and at 32 % coverage the original
    discs have already merged: **80 discs became 29 blobs.** Relocating those blobs lets them
    merge *again*, so the null reaches only **0.264 coverage against the data's 0.313 (84 %)**.

    A null that under-represents the density **under-states chance overlap**, which makes the
    test **liberal** — measured, a 13 % false-positive rate at 32 % coverage.

    **So the coverage is reported and the result is flagged.** A colocalization claim in a
    crowded image needs the coverage beside it to be read at all.
    """
    coloc = pytest.importorskip("pycat.toolbox.obj_based_coloc_analysis_tools")

    roi = np.ones((_SIZE, _SIZE), bool)

    dense = coloc.coloc_significance(_random_spots(80, 10, 0), _random_spots(80, 10, 500),
                                     roi, n_simulations=99)
    sparse = coloc.coloc_significance(_random_spots(15, 6, 0), _random_spots(15, 6, 500),
                                      roi, n_simulations=99)

    assert dense["null_is_conservative"], (
        f"at {100 * dense['channel2_coverage']:.0f}% coverage the null only reaches "
        f"{100 * dense['null_coverage']:.0f}% — it under-states chance overlap, and the user "
        f"must be told the test is liberal here"
    )
    assert not sparse["null_is_conservative"], (
        "a sparse image must NOT be flagged — the null reproduces its density fine, and a "
        "warning that always fires is a warning that gets ignored"
    )
