"""
Group C — segmentation geometry. **Draw a known object, segment it, get it back.**

One serious bug, three modules verified correct, and one that is among the best in the codebase.

``split_touching_objects`` computed the right answer and threw it away
----------------------------------------------------------------------
The watershed inside it **works**: it separates two touching discs at every real overlap, and
correctly **declines** when they have genuinely merged into one blob.

===========  ===============  ==================
overlap      components in    watershed labels
===========  ===============  ==================
0 px         2                **2**
4 px         1                **2**
8 px         1                **2**
14 px        1                1  *(one object now)*
20 px        1                1
===========  ===============  ==================

The function then **discarded the labels** and rebuilt a **boolean mask** by subtracting Sobel
edges. **A boolean mask cannot express a split.** The two halves stay 8-connected through the
corner of the one-pixel cut, so ``label()`` on the output returned **ONE object at every
overlap** — including the case where the discs merely *touch* and were already two separate
components on the way in. **It merged them.**

**Touching condensates were always counted as one**, and every count, size distribution and
per-object measurement downstream inherited it.

``partial_volume_tools`` is excellent, and says the thing that matters
----------------------------------------------------------------------
It predicts an intensity bias **it cannot remove**, which is exactly the right thing to do. The
PV weight fixes the **area** (a 1.5 px object: plain mask **−43 %**, PV **−3.6 %**) — but it
**cannot fix the intensity**, because the PSF has physically moved photons *out* of the object.
They are not in those pixels to be re-weighted.

And ``intensity_bias_for_size`` predicts that residual bias accurately:

==========  ============  ============
radius      predicted     MEASURED
==========  ============  ============
2 px        −51 %         **−55 %**
3 px        −36 %         **−37 %**
5 px        −22 %         **−22 %**
10 px       −11 %         **−11 %**
==========  ============  ============

**This is a size-dependent intensity bias**: small condensates read as less dense than large ones
*purely from optics*, which can manufacture a spurious intensity-vs-size trend. The module's own
docstring states it plainly — *"PV weighting minimises the software-added bias; it cannot undo the
optics"* — and reporting the predicted bias per object is what lets a user tell a real difference
from a size artefact.
"""

import numpy as np
import pytest
from skimage.measure import label as sk_label


def _two_discs(overlap_px, size=96, radius=12):
    """Two discs of KNOWN radius, overlapping by a known amount. The answer is always **2**."""
    yy, xx = np.mgrid[0:size, 0:size]
    centre = size // 2
    offset = radius - overlap_px / 2

    left = np.sqrt((yy - centre) ** 2 + (xx - (centre - offset)) ** 2) < radius
    right = np.sqrt((yy - centre) ** 2 + (xx - (centre + offset)) ** 2) < radius
    return left | right


@pytest.mark.core
@pytest.mark.parametrize("overlap", [0, 4, 8])
def test_touching_objects_are_actually_split(overlap):
    """**A boolean mask cannot express a split**, and the function returned one.

    The watershed computed the right answer and it was **discarded**. The rebuilt boolean output
    left the two halves 8-connected through the corner of a one-pixel cut, so ``label()`` on it
    returned **ONE object at every overlap** — including at ``overlap=0``, where the discs merely
    touch and were **already two separate components on the way in**. *It merged them.*
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    mask = _two_discs(overlap)
    split = np.asarray(lm.split_touching_objects(mask))

    n_objects = int(split.max()) if split.dtype != bool else sk_label(split).max()

    assert n_objects == 2, (
        f"two discs overlapping by {overlap} px were split into {n_objects} object(s). The "
        f"watershed inside this function gets it right — the labels were being thrown away and "
        f"a boolean mask returned in their place, and a boolean mask cannot represent a split."
    )


@pytest.mark.core
@pytest.mark.parametrize("overlap", [14, 20])
def test_genuinely_merged_objects_are_not_split(overlap):
    """It must not cry wolf: two discs that have truly merged are **one** object."""
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    split = np.asarray(lm.split_touching_objects(_two_discs(overlap)))
    n_objects = int(split.max()) if split.dtype != bool else sk_label(split).max()

    assert n_objects == 1, (
        f"two discs overlapping by {overlap} px (they have merged into a single blob) were "
        f"split into {n_objects}. A splitter that over-segments is as damaging as one that "
        f"under-segments."
    )


@pytest.mark.core
@pytest.mark.parametrize("radius", [1.5, 2.0, 5.0])
def test_partial_volume_recovers_the_area_of_a_sub_resolution_object(radius):
    """The plain mask is **43 % too small** on a 1.5 px object. PV gets it to 3.6 %."""
    from scipy import ndimage as ndi  # noqa: F401  (kept for parity with the scene builder)

    pv = pytest.importorskip("pycat.toolbox.partial_volume_tools")

    size, factor = 64, 4
    yy, xx = np.mgrid[0:size * factor, 0:size * factor]

    hi_res = np.sqrt((yy - 32 * factor) ** 2 + (xx - 32 * factor) ** 2) < radius * factor
    weights = pv.partial_volume_weights(hi_res, factor)

    true_area = np.pi * radius ** 2
    pv_area = float(weights.sum())

    assert pv_area == pytest.approx(true_area, rel=0.10), (
        f"the partial-volume area is {pv_area:.2f} px against a true {true_area:.2f}"
    )


@pytest.mark.core
@pytest.mark.parametrize("radius,expected_bias", [(2.0, -0.55), (5.0, -0.22), (10.0, -0.11)])
def test_the_predicted_intensity_bias_matches_the_real_one(radius, expected_bias):
    """**It predicts a bias it cannot remove.** That is exactly the right thing to do.

    The PSF has physically moved photons OUT of a small object — they are not in those pixels to
    be re-weighted, so no masking strategy recovers them. **A 2 px condensate reads 55 % too
    dim.** That is a *size-dependent* bias, and it can manufacture a spurious intensity-vs-size
    trend where none exists.

    ``intensity_bias_for_size`` predicts it to within a few percent, which is what lets a user
    tell a real intensity difference from a size artefact.
    """
    from scipy import ndimage as ndi

    pv = pytest.importorskip("pycat.toolbox.partial_volume_tools")

    size, psf = 64, 1.5
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((yy - 32) ** 2 + (xx - 32) ** 2)

    image = ndi.gaussian_filter(np.where(d < radius, 1000.0, 100.0).astype(float), psf)
    measured = (float(image[d < radius].mean()) - 1000.0) / 1000.0

    predicted = pv.intensity_bias_for_size(radius, psf)

    assert measured == pytest.approx(expected_bias, abs=0.05), (
        "the simulation must reproduce the known bias before the prediction can be checked"
    )
    assert predicted == pytest.approx(measured, abs=0.06), (
        f"predicted a {predicted:.0%} intensity bias for a {radius} px object; the real one is "
        f"{measured:.0%}. This prediction is what separates a real intensity difference from a "
        f"size-driven optical artefact."
    )


@pytest.mark.core
@pytest.mark.parametrize("pedestal", [0.0, 500.0])
def test_gaussian_localization_is_exact_and_pedestal_invariant(pedestal):
    """Audited and **correct** — and it is the one estimator that fits its own offset.

    Sub-pixel position to **0.008 px**, σ exact, and **fully pedestal-invariant** — because
    ``gaussian_2d_offset`` has a background term. *The modules that fit an offset do not have
    the bug that the ones without it do* (compare the CCF and ACF fits, 1.5.481).
    """
    loc = pytest.importorskip("pycat.toolbox.gaussian_localization_tools")

    window = 15
    yy, xx = np.mgrid[0:window, 0:window]
    rng = np.random.default_rng(0)

    true_x, true_y, true_sigma = 7.3, 6.8, 2.0
    patch = (500.0 * np.exp(-(((xx - true_x) ** 2 + (yy - true_y) ** 2)) / (2 * true_sigma ** 2))
             + pedestal + rng.normal(0, 5, (window, window)))

    fit = loc.fit_gaussian_2d_spot(patch, sigma_guess=true_sigma)

    assert np.hypot(fit['x0'] - true_x, fit['y0'] - true_y) < 0.05, (
        f"localized to ({fit['x0']:.2f}, {fit['y0']:.2f}) against a true "
        f"({true_x}, {true_y})"
    )
    measured_sigma = 0.5 * (fit['sigma_x'] + fit['sigma_y'])
    assert measured_sigma == pytest.approx(true_sigma, rel=0.05)


@pytest.mark.core
@pytest.mark.parametrize("n_spots,separation", [(4, 20.0), (16, 12.0), (9, 6.0)])
def test_clean_detection_finds_the_right_number_of_spots(n_spots, separation):
    """Audited and **correct** — exact at every separation, down to 3x the PSF sigma."""
    clean = pytest.importorskip("pycat.toolbox.clean_spot_detection_tools")

    size = 128
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(0)

    image = np.full((size, size), 100.0)
    side = int(np.ceil(np.sqrt(n_spots)))
    for i in range(n_spots):
        cy = 20 + (i // side) * separation
        cx = 20 + (i % side) * separation
        image += 500 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 2.0 ** 2))
    image += rng.normal(0, 15, image.shape)

    psf = clean.gaussian_psf_2d(size=11, sigma=2.0)
    detected = clean.clean_detect(image.astype(np.float32), psf=psf)
    components = detected[0] if isinstance(detected, tuple) else detected

    assert len(components) == n_spots, (
        f"{len(components)} spots detected; {n_spots} were placed, {separation} px apart"
    )


# ── 3D: anisotropic voxels, which is every confocal stack ─────────────────────────────────

def _ellipsoid_stack(radius_z_um, radius_xy_um, mpp=0.1, dz=0.5, shape=(31, 64, 64)):
    """An ellipsoid of KNOWN physical size, in an ANISOTROPIC voxel grid.

    ``dz / mpp = 5`` — a typical confocal stack. The z step is almost never the xy pixel size,
    and that is exactly what breaks a metric that assumes isotropy.
    """
    nz, ny, nx = shape
    zz, yy, xx = np.mgrid[0:nz, 0:ny, 0:nx]

    d = np.sqrt((((zz - nz // 2) * dz) / radius_z_um) ** 2
                + (((yy - ny // 2) * mpp) / radius_xy_um) ** 2
                + (((xx - nx // 2) * mpp) / radius_xy_um) ** 2)

    labels = (d < 1.0).astype(np.int32)
    intensity = np.where(labels > 0, 1000.0, 100.0).astype(np.float32)
    return labels, intensity


@pytest.mark.core
def test_3d_volume_uses_the_z_step_not_the_xy_pixel():
    """Audited and **correct** — and the failure it avoids is a **5x** error.

    A 1 µm sphere in a 0.1 × 0.1 × 0.5 µm voxel occupies 787 voxels. Assuming isotropy gives
    ``787 × 0.1³ = 0.787 µm³``; the truth is **4.19 µm³**. The module reports **3.94** — a −6 %
    voxelization residual on a small sphere, not a units error.
    """
    z = pytest.importorskip("pycat.toolbox.zstack_segmentation_tools")

    labels, intensity = _ellipsoid_stack(1.0, 1.0)
    df = z.condensate_metrics_3d(labels, intensity, microns_per_pixel=0.1, z_step_um=0.5)

    true_volume = (4.0 / 3.0) * np.pi * 1.0 ** 3
    assert float(df.volume_um3.iloc[0]) == pytest.approx(true_volume, rel=0.10), (
        f"volume {float(df.volume_um3.iloc[0]):.3f} um3 against a true {true_volume:.3f}. An "
        f"isotropic assumption would give 0.787 — a 5x error."
    )


@pytest.mark.core
@pytest.mark.parametrize("radius_z,radius_xy,true_major", [
    (1.0, 1.0, 2.0),          # sphere — the case that HID the bug
    (2.0, 0.5, 4.0),          # elongated in Z — a 4x underestimate
    (3.0, 0.5, 6.0),
    (0.5, 2.0, 4.0),          # elongated in XY — was always fine
])
def test_the_3d_axis_lengths_respect_the_z_anisotropy(radius_z, radius_xy, true_major):
    """**A 4 µm object elongated in z was reported as 1 µm.**

    ``prop`` came from a ``regionprops`` call with **no spacing**, so its axis lengths were in
    VOXELS — and the code multiplied them by ``microns_per_pixel``, the **xy** pitch. On a
    confocal stack the z step is 3–5× the xy pixel, so **every z extent was divided by that
    factor.**

    ===============================  ==============  ============
    object (voxel 0.1×0.1×0.5 µm)    true major      reported
    ===============================  ==============  ============
    sphere, r = 1 µm                 2.00 µm         2.06  *(fine)*
    **Z-elongated, 4 µm long**       **4.00 µm**     **0.98**
    XY-elongated, 4 µm long          4.00 µm         4.45  *(fine)*
    ===============================  ==============  ============

    **The error is invisible on anything round**, which is exactly why it survived — the sphere
    case is right. And the ``spacing`` argument *was* being passed a few lines above, to the
    marching-cubes surface area. **The axis lengths simply never used it.**
    """
    z = pytest.importorskip("pycat.toolbox.zstack_segmentation_tools")

    labels, intensity = _ellipsoid_stack(radius_z, radius_xy)
    df = z.condensate_metrics_3d(labels, intensity, microns_per_pixel=0.1, z_step_um=0.5)

    measured = float(df.major_axis_um.iloc[0])

    assert measured == pytest.approx(true_major, rel=0.15), (
        f"the major axis is {measured:.2f} um against a true {true_major:.2f}. A z-elongated "
        f"object used to read 4x SMALL, because its z extent was being scaled by the XY pixel "
        f"size."
    )
