"""
Every method, measured through a realistic acquisition. The audit's validation layer 2.

The external audit asked each quantitative method to declare which validation layer it has
reached::

    Implemented → Analytically validated → Simulation validated → Experimentally validated

**"Analytically validated" is a low bar, and PyCAT was mostly at it.** A method tested only on a
clean synthetic scene has been tested against a microscope that does not exist.

This file closes that gap. It runs real methods through ``tests/imaging_realism.acquire()`` —
which applies photobleaching, drift, motion blur, the PSF, an illumination gradient, Poisson
noise, camera gain / pedestal / read noise, ADC clipping and binning, **in the order a
microscope applies them** — and asserts that each method either **recovers the truth** or
**refuses**.

Every degradation here was found the hard way, one bug at a time. Eight of the audit's eleven
have already broken a real PyCAT measurement:

============================  ===============================================  =========
degradation                   what it broke, measured                          release
============================  ===============================================  =========
sCMOS pedestal                Kp 30 → **5.8**; N&B number inflated **120×**    1.5.422/453
saturation                    Kp of 655, 1500, 4000 **all read 655**           1.5.392
PSF blur (the halo)           client enrichment 30 → **14.9**                  1.5.460
photobleaching                FRAP t½ **2.5× too fast**, R² = 0.94             1.5.455
drift                         MSD α → **1.91**, reported as superdiffusion     1.5.456
illumination gradient         vignetting QC measured object placement          1.5.404
segmentation error            over-inclusive mask: Kp 30 → **4.4**             1.5.459
Poisson noise                 N&B shot-noise floor is B = 1, not 0             1.5.453
============================  ===============================================  =========

**The bar these tests enforce is: recover the truth, or refuse. Never return a confident wrong
number.**
"""

import numpy as np
import pytest

from tests.imaging_realism import acquire


# ── A droplet scene with a KNOWN partition coefficient ───────────────────────────────────

_TRUE_KP = 30.0
_DILUTE = 100.0
_DENSE = 2900.0                       # + dilute = 3000 → Kp = 30


def _droplets(edge_px=0.5, size=200):
    """Four droplets on a dilute background. ``edge_px`` sets the PSF-like softness."""
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.full((size, size), _DILUTE)
    labels = np.zeros((size, size), np.int32)

    for i, (cy, cx) in enumerate([(60, 60), (60, 140), (140, 60), (140, 140)], start=1):
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        img += _DENSE * 0.5 * (1 - np.tanh((r - 13) / edge_px))
        labels[r < 13] = i

    return img, labels


@pytest.mark.base
@pytest.mark.parametrize("pedestal", [0.0, 100.0, 500.0, 2000.0])
def test_partition_survives_the_camera_pedestal_given_a_dark_reference(pedestal):
    """With a dark reference, Kp must be pedestal-INDEPENDENT — on a realistic acquisition.

    The pedestal is the single most destructive degradation in the harness, because it is
    invisible: it adds a constant that *looks like signal*, and it sits in **both** the
    numerator and the denominator of Kp. Without correction, a true Kp of 30 reads as **5.8**
    on a 500-count pedestal (1.5.422).
    """
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")

    clean, labels = _droplets()

    image = acquire(clean, pedestal=pedestal, read_noise=3.0, seed=0)
    dark = acquire(np.zeros_like(clean), pedestal=pedestal, read_noise=3.0, seed=7)

    result = invitro.partition_coefficient_local(
        image, labels, sample_type="in_vitro", dark_reference=dark)

    assert result["partition_coefficient"] == pytest.approx(_TRUE_KP, rel=0.15), (
        f"Kp = {result['partition_coefficient']:.2f} against a true {_TRUE_KP} on a "
        f"{pedestal:.0f}-count pedestal. The dark reference measures the pedestal directly and "
        f"removes it from BOTH phases — if this fails, that correction is broken, and the "
        f"uncorrected value would be 5.8 at pedestal 500."
    )


@pytest.mark.base
def test_saturated_partition_is_refused_not_reported():
    """A clipped measurement is MEANINGLESS, not conservative.

    This is the one that feels safe — *"the value is at least this large"* — and it is not. The
    numerator is truncated by an **unknown** amount. With a bulk of 100 counts, a true Kp of
    655, 1500 and 4000 **all read as 655** once the dense phase clips (1.5.392).
    """
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")

    ceiling = 400.0                    # well below the dense phase (3000)
    reported = []

    for dense in (2900.0, 6900.0, 19900.0):        # true Kp = 30, 70, 200
        yy, xx = np.mgrid[0:200, 0:200]
        img = np.full((200, 200), _DILUTE)
        labels = np.zeros((200, 200), np.int32)
        for i, (cy, cx) in enumerate([(60, 60), (60, 140), (140, 60), (140, 140)], start=1):
            r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
            img += dense * 0.5 * (1 - np.tanh((r - 13) / 0.5))
            labels[r < 13] = i

        image = acquire(img, saturate_at=ceiling, seed=0)
        dark = acquire(np.zeros_like(img), seed=7)

        result = invitro.partition_coefficient_local(
            image, labels, sample_type="in_vitro", dark_reference=dark,
            saturation_level=ceiling)

        assert result["n_saturated_droplets"] == 4, (
            f"only {result['n_saturated_droplets']}/4 droplets were detected as saturated, "
            f"with the dense phase at {dense:.0f} counts and the ceiling at {ceiling:.0f}"
        )
        reported.append(result["partition_coefficient"])

    assert all(not np.isfinite(kp) for kp in reported), (
        f"the saturated measurements returned {reported} instead of refusing. These are three "
        f"scenes with true Kp of 30, 70 and 200 — a SEVEN-FOLD range — and once the dense "
        f"phase clips they are indistinguishable. Reporting any number here is reporting the "
        f"ceiling, not the sample."
    )


@pytest.mark.base
@pytest.mark.parametrize("psf_sigma", [0.0, 1.0, 2.5])
def test_partition_with_a_realistic_psf_recovers_or_warns(psf_sigma):
    """The PSF halo is not the dilute phase — and the annulus gap exists to step past it.

    Every real droplet has a soft edge. The pixels immediately outside the mask are **halo**,
    and including them inflates the dilute reference: a true enrichment of 30 reads as **14.9**
    with a 5 px edge (1.5.460).
    """
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")

    clean, labels = _droplets(edge_px=max(psf_sigma, 0.5))
    image = acquire(clean, psf_sigma_px=psf_sigma or None, pedestal=100.0,
                    read_noise=2.0, seed=0)
    dark = acquire(np.zeros_like(clean), pedestal=100.0, read_noise=2.0, seed=7)

    result = invitro.partition_coefficient_local(
        image, labels, sample_type="in_vitro", dark_reference=dark)

    kp = result["partition_coefficient"]

    # The annulus gap is measured from the interface width, so it should track the blur.
    assert kp == pytest.approx(_TRUE_KP, rel=0.30), (
        f"with a PSF sigma of {psf_sigma} px, Kp = {kp:.2f} against a true {_TRUE_KP}. The "
        f"annular dilute phase is offset from the droplet edge by a gap sized from the "
        f"interface width (1.5.423) precisely so that the halo does not contaminate it. If "
        f"this fails, that gap is not tracking the blur."
    )


@pytest.mark.base
def test_transfection_filter_survives_a_realistic_acquisition():
    """The cell-selection gate must not depend on the camera.

    ``filter_cells_by_transfection`` decides which cells are analysed **at all**, so a mistake
    here is a selection effect on the entire dataset. Until 1.5.415 it used a mean/background
    RATIO, and on a 500-count pedestal **every transfected cell was called untransfected.**
    """
    ts = pytest.importorskip("pycat.toolbox.ts_cellpose_tools")

    size = 200
    yy, xx = np.mgrid[0:size, 0:size]

    clean = np.full((size, size), 20.0)
    mask = np.zeros((size, size), np.int32)
    expression = [0.0, 15.0, 60.0, 200.0]          # cell 1 is untransfected
    for i, ((cy, cx), expr) in enumerate(
            zip([(50, 50), (50, 150), (150, 50), (150, 150)], expression), start=1):
        sel = ((yy - cy) ** 2 + (xx - cx) ** 2) < 400
        mask[sel] = i
        clean[sel] += expr

    verdicts = {}
    for pedestal in (0.0, 500.0, 2000.0):
        image = acquire(clean, pedestal=pedestal, read_noise=3.0, seed=0)
        _kept, _dropped, df, fraction = ts.filter_cells_by_transfection(mask, image)
        verdicts[pedestal] = (
            {int(r.cell_label): bool(r.transfected) for r in df.itertuples()}, fraction)

    for pedestal, (calls, fraction) in verdicts.items():
        assert not calls[1], f"cell 1 has ZERO expression, called transfected at {pedestal}"
        for label in (2, 3, 4):
            assert calls[label], (
                f"cell {label} (expression {expression[label - 1]:.0f} counts) was called "
                f"UNTRANSFECTED on a {pedestal:.0f}-count pedestal. This is the 1.5.415 "
                f"failure: a mean/background RATIO is dragged toward 1 by the pedestal, so on "
                f"a real sensor every transfected cell was rejected. The gate must be a "
                f"CONTRAST."
            )
        assert fraction == pytest.approx(0.75), (
            f"transfected fraction {fraction:.2f} at pedestal {pedestal:.0f}, expected 0.75 — "
            f"the same cells must give the same answer on any camera"
        )
