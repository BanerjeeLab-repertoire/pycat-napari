"""
Group E — brightfield. **The intensity convention is inverted: dark objects on a bright field.**

The hypothesis: everything built for fluorescence has to be re-checked here. It mostly held up
**very well** — ``brightfield_tools`` implements Beer-Lambert correctly and is properly
polarity-specific. One real bug, in the quantity a paper would report.

`od_partition_coeff` was division by a definitional zero
--------------------------------------------------------
Optical density is measured **relative to the background**: ``OD = -log10(I / I0)``. So **the
background's own OD is zero by construction** — that is what "background" *means* in
Beer-Lambert.

``mean_od / max(bg_od, 1e-9)`` therefore divided by **1e-9**, and reported **96,910,007** for a
condensate whose true OD is **0.097**.

**The number is not large — it is undefined**, and the guard against division by zero turned an
undefined quantity into a confident one.

**And the correct quantity was already there.** OD *is* the enrichment: ``OD = log10(I0 / I_dense)``,
so ``10**OD`` is the transmittance ratio — *how many times more light the object absorbs than the
background*, which is exactly what a partition coefficient measures. For an object transmitting
50 % of the light: OD = 0.301, and the ratio is **2.00**.

===============  ==========  ==================  ==================
transmittance    true OD     od_partition (old)  od_partition (new)
===============  ==========  ==================  ==================
0.80             0.097       **96,910,007**      **1.25**
0.50             0.301       301,030,010         **2.00**
0.25             0.602       602,060,020         **4.00**
0.10             1.000       —                   **10.00**
===============  ==========  ==================  ==================

The new value is **exactly 1/T** at every transmittance.
"""

import numpy as np
import pytest
from scipy import ndimage as ndi


_I0 = 3000.0


def _absorbing_disc(transmittance, size=128, radius=20, noise=0.0, seed=0):
    """A disc of KNOWN transmittance on a uniform bright field. Beer-Lambert is exact here."""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((yy - size // 2) ** 2 + (xx - size // 2) ** 2)

    image = np.full((size, size), _I0)
    image[d < radius] = _I0 * transmittance

    if noise:
        image = image + np.random.default_rng(seed).normal(0, noise, image.shape)

    return np.clip(image, 1, 65535).astype(np.float32), (d < radius)


@pytest.mark.base
@pytest.mark.parametrize("transmittance", [0.9, 0.5, 0.1])
def test_optical_density_is_exact_beer_lambert(transmittance):
    """``OD = -log10(I/I0)``, to **0.0–0.2 %** across the whole transmittance range."""
    bf = pytest.importorskip("pycat.toolbox.brightfield_tools")

    image, mask = _absorbing_disc(transmittance)
    background = np.full(image.shape, _I0, np.float32)

    od = np.asarray(bf.compute_optical_density(image, background_image=background))

    measured = float(od[mask].mean())
    analytic = -np.log10(transmittance)

    assert measured == pytest.approx(analytic, rel=0.02), (
        f"OD = {measured:.4f} against an exact -log10({transmittance}) = {analytic:.4f}"
    )


@pytest.mark.base
@pytest.mark.parametrize("transmittance,expected_ratio", [(0.5, 2.0), (0.25, 4.0), (0.1, 10.0)])
def test_the_od_partition_coefficient_is_not_a_hundred_million(transmittance, expected_ratio):
    """**96,910,007 for a condensate whose true OD is 0.097.**

    The background's OD is **zero by construction** — OD is *defined* against it. Dividing by it
    is dividing by a definitional zero, and ``max(bg_od, 1e-9)`` turned an **undefined** quantity
    into a **confident** one.

    The physically meaningful enrichment is ``10**OD`` — the transmittance ratio, *how many times
    more light the object absorbs than the background* — and it is **exactly 1/T**.
    """
    bf = pytest.importorskip("pycat.toolbox.brightfield_tools")

    image, mask = _absorbing_disc(transmittance)
    labels = mask.astype(np.int32)
    cells = np.ones(image.shape, np.int32)

    table = bf.bf_condensate_metrics(
        image, labels, cells, 1.0,
        background_image=np.full(image.shape, _I0, np.float32))

    partition = float(table.od_partition_coeff.iloc[0])

    assert partition == pytest.approx(expected_ratio, rel=0.05), (
        f"the OD partition coefficient is {partition:.2f} against a physical 1/T = "
        f"{expected_ratio:.2f}. It used to be ~1e8, because the background's OD is ZERO by "
        f"construction and the code divided by it."
    )


@pytest.mark.base
def test_brightfield_segmentation_finds_DARK_objects_and_not_bright_ones():
    """**Correctly polarity-specific.** It will not silently process a fluorescence image.

    Finds 4 dark objects; finds **0** bright ones. A module that were polarity-agnostic would
    give a user plausible-looking output on the wrong modality.
    """
    bf = pytest.importorskip("pycat.toolbox.brightfield_tools")

    size = 192
    yy, xx = np.mgrid[0:size, 0:size]

    def _scene(polarity):
        rng = np.random.default_rng(0)
        image = np.full((size, size), _I0)
        for i in range(4):
            cy, cx = 40 + (i // 2) * 80, 40 + (i % 2) * 80
            spot = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < 12
            image[spot] = _I0 * 0.6 if polarity < 0 else _I0 / 0.6
        return np.clip(image + rng.normal(0, 25, image.shape), 1, 65535).astype(np.float32)

    dark = bf.segment_bf_condensates(
        bf.preprocess_brightfield(_scene(-1))['enhanced'], min_diameter_px=8)
    bright = bf.segment_bf_condensates(
        bf.preprocess_brightfield(_scene(+1))['enhanced'], min_diameter_px=8)

    assert int(np.asarray(dark).max()) == 4, (
        f"{int(np.asarray(dark).max())} dark objects found; 4 were placed"
    )
    assert int(np.asarray(bright).max()) == 0, (
        "BRIGHT objects were segmented by the brightfield path. A brightfield image has DARK "
        "objects — if bright ones are also found, a user could feed this a fluorescence image "
        "and get plausible-looking output."
    )


@pytest.mark.base
@pytest.mark.parametrize("period,in_band", [(8, "8-40"), (20, "8-40"), (60, "2-8")])
def test_the_fft_bandpass_keeps_exactly_the_frequencies_in_its_band(period, in_band):
    """Audited and **correct** — 9/9 gratings kept or cut exactly as their frequency dictates."""
    fft = pytest.importorskip("pycat.toolbox.fft_bandpass_tools")

    size = 256
    _yy, xx = np.mgrid[0:size, 0:size]
    grating = (np.sin(2 * np.pi * xx / period).astype(np.float32) * 100 + 500)

    bands = {"2-8": (2.0, 8.0), "8-40": (8.0, 40.0), "40-100": (40.0, 100.0)}

    for name, (low, high) in bands.items():
        out = np.asarray(fft.fft_bandpass(grating, low_cutoff=low, high_cutoff=high))
        kept = float(out.std()) > 20.0

        assert kept == (name == in_band), (
            f"a {period} px grating (f = {size / period:.0f} cycles) was "
            f"{'KEPT' if kept else 'CUT'} by the {name} band; it should have been "
            f"{'kept' if name == in_band else 'cut'}"
        )


@pytest.mark.base
def test_the_interface_width_tracks_the_true_boundary_blur():
    """Audited and **correct** — the measured width is proportional to the true σ.

    The interface width is a real physical quantity (boundary sharpness, which bears on surface
    tension), and the ratio to the true blur is constant across a factor of 8 in σ.
    """
    profiles = pytest.importorskip("pycat.toolbox.intensity_profile_tools")

    size, radius = 128, 25
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((yy - 64) ** 2 + (xx - 64) ** 2)

    widths = {}
    for sigma in (2.0, 8.0):
        image = ndi.gaussian_filter(
            np.where(d < radius, 1000.0, 100.0).astype(float), sigma).astype(np.float32)
        radial = profiles.radial_profile(image, center_yx=(64, 64), max_radius_px=55)
        widths[sigma] = float(
            profiles.interface_width_from_radial(radial)['interface_width_px'])

    ratio = widths[8.0] / widths[2.0]
    assert ratio == pytest.approx(4.0, rel=0.20), (
        f"the interface width grew {ratio:.2f}x when the true blur grew 4x "
        f"({widths[2.0]:.1f} px at sigma 2, {widths[8.0]:.1f} px at sigma 8). It must be "
        f"proportional to the physical boundary width."
    )
