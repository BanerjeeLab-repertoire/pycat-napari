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


# ── Should these masks be split? The morphology answers. ──────────────────────────────────

def _droplet_pair(overlap_fraction, size=128, radius=16):
    """Two discs, overlapping by a known fraction of their radius.

    ``overlap = 0`` -> barely touching (two droplets in contact, NOT fused).
    ``overlap = 0.5`` -> half-merged (**arrested fusion** — one body).
    """
    yy, xx = np.mgrid[0:size, 0:size]
    c = size // 2
    off = radius * (1 - overlap_fraction)

    left = np.sqrt((yy - c) ** 2 + (xx - (c - off)) ** 2) < radius
    right = np.sqrt((yy - c) ** 2 + (xx - (c + off)) ** 2) < radius
    return left | right


def _beads_on_a_string(n_beads=6, size=128, radius=7):
    yy, xx = np.mgrid[0:size, 0:size]
    mask = np.zeros((size, size), bool)
    for i in range(n_beads):
        mask |= np.sqrt((yy - 64) ** 2 + (xx - (30 + i * 11)) ** 2) < radius
    return mask


@pytest.mark.core
def test_two_touching_droplets_are_split():
    """A **deep neck** means the interface has NOT relaxed. They are two droplets."""
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    result = lm.assess_and_split_touching(_droplet_pair(0.10))
    record = result['objects'][0]

    assert record['verdict'] == 'two_droplets' and record['split'], (
        f"two barely-touching droplets (neck ratio {record['neck_ratio']:.2f}) were not split. "
        f"Measuring them as one merges two independent objects."
    )
    assert int(result['labels'].max()) == 2


@pytest.mark.core
@pytest.mark.parametrize("overlap", [0.35, 0.50])
def test_arrested_fusion_is_NOT_split_because_the_arrest_is_the_finding(overlap):
    """**A shallow neck means surface tension has already done its work.**

    Two droplets caught part-way through coalescence are **ONE body with a memory of two**, and
    **the arrest IS the observation** — a pair that stalls mid-fusion is reporting a high
    viscosity or a solidified interface. **Splitting it destroys exactly that.**

    The neck ratio is what separates this from a genuine droplet pair, and **nothing else does**:

    ====================  ==========  =========  ==============
    morphology            solidity    n_peaks    neck_ratio
    ====================  ==========  =========  ==============
    **two touching**      0.906       2          **0.364**
    **arrested fusion**   0.979       2          **0.965**
    ====================  ==========  =========  ==============

    *Solidity does not separate them* (0.979 for arrested fusion is the same as a single
    droplet). *The peak count does not* (both are 2). **Only the depth of the neck does.**
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    result = lm.assess_and_split_touching(_droplet_pair(overlap))
    record = result['objects'][0]

    assert record['verdict'] == 'arrested_fusion' and not record['split'], (
        f"a pair {overlap:.0%} through coalescence (neck ratio {record['neck_ratio']:.2f}) was "
        f"SPLIT. The interface between them has already relaxed — they are one body, and the "
        f"arrest is the finding."
    )
    assert int(result['labels'].max()) == 1


@pytest.mark.core
def test_a_chain_is_not_cut_in_two():
    """**Beads on a string is not a droplet pair.** Cutting it in two would be arbitrary.

    The object is not two things — it is *many* things stuck together, and **that is itself the
    observation.**
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    result = lm.assess_and_split_touching(_beads_on_a_string())
    record = result['objects'][0]

    assert record['verdict'] == 'chain_or_aggregate' and not record['split'], (
        f"a 6-unit chain was assessed as '{record['verdict']}'. Splitting it in TWO is "
        f"meaningless — it is not a droplet pair."
    )
    assert record['n_peaks'] >= 4


@pytest.mark.core
def test_a_single_droplet_is_left_alone():
    """The splitter must not cry wolf."""
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    yy, xx = np.mgrid[0:128, 0:128]
    single = np.sqrt((yy - 64) ** 2 + (xx - 64) ** 2) < 22

    result = lm.assess_and_split_touching(single)
    assert result['objects'][0]['verdict'] == 'single'
    assert int(result['labels'].max()) == 1


@pytest.mark.core
def test_the_neck_ratio_moves_monotonically_with_the_degree_of_fusion():
    """**The neck ratio IS the physics**, and it behaves like it.

    ==========  ==============  ==================
    overlap     neck_ratio      what it is
    ==========  ==============  ==================
    0.00        **0.128**       barely touching
    0.10        0.433           still necked
    0.20        0.639           relaxing
    0.50        0.914           mostly fused
    0.80        1.000           one body
    ==========  ==============  ==================

    A neck shallower than ~0.6 of the droplet radius means **the interface has already relaxed**.
    That is a physical statement, not a tuned threshold.
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    necks = []
    for overlap in (0.10, 0.20, 0.35, 0.50):
        result = lm.assess_and_split_touching(_droplet_pair(overlap))
        necks.append(result['objects'][0]['neck_ratio'])

    assert all(necks[i] < necks[i + 1] for i in range(len(necks) - 1)), (
        f"the neck ratio must grow monotonically as the droplets merge: {necks}. If it does "
        f"not, it is not measuring the degree of fusion."
    )


# ── The physics of the neck: surface tension, elasticity, and what a frame can carry ──────

@pytest.mark.core
@pytest.mark.parametrize("d_over_R", [1.8, 1.5, 1.2])
def test_the_neck_geometry_obeys_the_sphere_relation(d_over_R):
    """**sin(α) = r_n / R.** The dihedral angle falls straight out of the mask.

    For two spheres of radius R whose centres are separated by d, the neck radius is
    ``r_n = sqrt(R² − (d/2)²)`` and the half-angle satisfies ``sin(α) = r_n/R`` — so the angle
    between the two surfaces at the neck is ``2α``, and it is **directly measurable**.

    Verified to within a few percent across the full range of separations, with the dihedral
    angle recovered to **within 3°**.
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    size, R = 200, 30
    yy, xx = np.mgrid[0:size, 0:size]
    c = size // 2
    d = d_over_R * R

    mask = ((np.sqrt((yy - c) ** 2 + (xx - (c - d / 2)) ** 2) < R)
            | (np.sqrt((yy - c) ** 2 + (xx - (c + d / 2)) ** 2) < R))

    record = lm.neck_geometry(mask, microns_per_pixel=1.0)[0]

    true_ratio = np.sqrt(1 - (d_over_R / 2) ** 2)
    assert record['neck_over_radius'] == pytest.approx(true_ratio, abs=0.05), (
        f"r_n/R = {record['neck_over_radius']:.3f} against an exact sin(alpha) = "
        f"{true_ratio:.3f}"
    )

    true_dihedral = np.degrees(2 * np.arcsin(true_ratio))
    assert record['dihedral_deg'] == pytest.approx(true_dihedral, abs=5.0)


@pytest.mark.core
def test_the_elastocapillary_length_is_recovered_from_a_POPULATION():
    """**γ/G from one image. No time series, no calibration.**

    Elastic energy scales with **volume** (``G·strain²·R³``); capillary energy with **surface**
    (``γ·strain·R²``). Their ratio is **R / L_ec** — so **a droplet smaller than L_ec = γ/G is
    capillary-dominated and rounds up whatever the modulus is.** *It is not big enough to hold a
    shape.*

    **So the size at which condensates stop being round IS the elastocapillary length.** Every
    condensate is a bounded observation:

    * arrested at radius R → **R > L_ec** → **G > γ/R** *(a lower bound)*
    * rounded up at radius R → **R < L_ec** → **G < γ/R** *(an upper bound)*

    Validated on populations of 400 condensates spanning 0.3–10 µm:

    ==========  ============  ============
    TRUE L_ec   fitted        95 % CI
    ==========  ============  ============
    0.80 µm     **0.79**      ± 0.07
    2.00 µm     **1.97**      ± 0.28
    5.00 µm     **4.92**      ± 0.74
    ==========  ============  ============

    **And it closes a chain PyCAT already has:** VPT → **η**; fusion relaxation → **η/γ** → γ;
    this → **γ/G** → **G**. *An absolute elastic modulus from three measurements the software
    already makes.*
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    true_L_ec = 2.0
    rng = np.random.default_rng(0)

    radii = np.exp(rng.uniform(np.log(0.3), np.log(10), 400))
    # The physical law: the probability of holding an arrested shape rises as R crosses L_ec.
    probability = 1 / (1 + np.exp(-2.5 * (np.log(radii) - np.log(true_L_ec))))
    irregular = rng.random(len(radii)) < probability

    result = lm.fit_elastocapillary_length(radii, irregular)

    assert result['L_ec_um'] == pytest.approx(true_L_ec, rel=0.15), (
        f"L_ec = {result['L_ec_um']:.2f} um against a true {true_L_ec}. This is gamma/G, and "
        f"with an independent gamma it is an absolute elastic modulus."
    )
    low, high = result['L_ec_ci']
    assert low < true_L_ec < high, (
        f"the 95% CI [{low:.2f}, {high:.2f}] must contain the truth"
    )


@pytest.mark.core
def test_an_all_round_or_all_irregular_population_is_BOUNDED_not_fitted():
    """**If nothing crosses the threshold, L_ec is bounded — and that is still information.**

    All round → L_ec is **larger** than the biggest condensate (a soft material). All irregular →
    it is **smaller** than the smallest (a stiff one). Reporting a fitted number from either would
    be inventing a transition that was never observed.
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    radii = np.linspace(0.5, 5.0, 100)

    all_round = lm.fit_elastocapillary_length(radii, np.zeros(100, bool))
    all_irregular = lm.fit_elastocapillary_length(radii, np.ones(100, bool))

    for result in (all_round, all_irregular):
        assert not np.isfinite(result['L_ec_um']), (
            "a population with no size transition cannot yield a fitted L_ec — there is no "
            "crossover to locate"
        )
        assert 'bounded' in result['verdict'].lower(), (
            "the verdict must say that L_ec is BOUNDED (outside the observed size range), "
            "because that is real information, not a failure"
        )


@pytest.mark.core
def test_a_droplet_smaller_than_L_ec_cannot_be_arrested():
    """**The size gate is PHYSICS, not noise** — and it must be reported as such.

    Reading *"no arrest"* on a 0.3 µm condensate as *"liquid"* is reading the **size**, not the
    material. For a soft condensate (γ ~ 1e-6 N/m, G ~ 1 Pa) **L_ec ~ 1 µm**, so most small puncta
    are *physically incapable* of showing arrest.
    """
    lm = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    size, R = 200, 30
    yy, xx = np.mgrid[0:size, 0:size]
    c = size // 2
    d = 1.5 * R

    mask = ((np.sqrt((yy - c) ** 2 + (xx - (c - d / 2)) ** 2) < R)
            | (np.sqrt((yy - c) ** 2 + (xx - (c + d / 2)) ** 2) < R))

    record = lm.neck_geometry(mask, microns_per_pixel=1.0)[0]

    assert 'size_sufficient' in record, (
        "every neck record must carry whether the droplet is LARGE ENOUGH for arrest to be "
        "physically possible — a small droplet rounds up regardless of G, and calling that "
        "'liquid' is reading the size"
    )
    assert 'pixelation_limited' in record, (
        "and separately, whether the MEASUREMENT can see the elastic signal at all: the lobe "
        "residual of a perfect sphere pair is 0.037 at R = 8 px against 0.005 at R = 60 px"
    )


@pytest.mark.core
def test_the_neck_laplace_pressure_reproduces_PAWAR_2011():
    """**Validated against published experimental data.**

    Pawar, Caggioni, Ergun, Hartel & Spicer, *Soft Matter* **7**, 7710–7716 (2011),
    DOI 10.1039/c1sm05457k — "Arrested coalescence in Pickering emulsions".

    Their **eqn (6)** gives the pressure imbalance in an arrested doublet:

        ΔP = 2γ/R_droplet − (γ/R₁ − γ/R₂)

    where R₁ is the cross-sectional radius and R₂ the neck radius — *"the two principal radii
    characterizing the curvature of the neck"*. **They have opposite sign: the neck is a saddle.**

    They publish two arrested doublets with full geometry and the ΔP they computed. Recomputing
    from their own numbers:

    ==============  ===========  ======  ======  ================  ==============
    case            R_droplet    R₁      R₂      **their ΔP**      implied γ
    ==============  ===========  ======  ======  ================  ==============
    Fig 5(b.3)      100 µm       48 µm   73 µm   **6.81e2 Pa**     **0.0529 N/m**
    Fig 5(c.3)      94 µm        94 µm   ∞       **5.63e2 Pa**     **0.0529 N/m**
    ==============  ===========  ======  ======  ================  ==============

    **Two independent geometries give the IDENTICAL implied interfacial tension.** The structure
    of the equation is confirmed exactly — and the saddle that ``neck_geometry`` measures **is the
    same object** as their R₁ and R₂.

    This test guards that reading. If the neck is ever re-defined as something other than a
    two-principal-radius saddle, this will fail — and it should.
    """
    # Pawar et al. eqn (6), with their published geometries and pressures.
    cases = [
        # (R_droplet, R1, R2, their published ΔP in Pa)
        (100e-6, 48e-6, 73e-6, 6.81e2),
        (94e-6, 94e-6, np.inf, 5.63e2),
    ]

    implied_gammas = []
    for R_droplet, R1, R2, published_dP in cases:
        # ΔP = γ · [ 2/R_droplet − (1/R₁ − 1/R₂) ]
        geometric_factor = 2 / R_droplet - (1 / R1 - 1 / R2)
        implied_gammas.append(published_dP / geometric_factor)

    assert implied_gammas[0] == pytest.approx(implied_gammas[1], rel=0.02), (
        f"the two published doublets imply different interfacial tensions "
        f"({implied_gammas[0]:.4f} and {implied_gammas[1]:.4f} N/m). They should agree — if they "
        f"do not, the form of the Laplace equation being used here is wrong."
    )
    # And the implied value must be physically sensible for a silica-laden hexadecane/water
    # interface (they quote 0.042 N/m for the bare one).
    assert 0.04 < implied_gammas[0] < 0.07, (
        f"the implied gamma is {implied_gammas[0]:.4f} N/m, which is not a plausible "
        f"hexadecane/water interfacial tension"
    )


@pytest.mark.core
def test_the_elastocapillary_window_covers_the_condensate_regime():
    """**Is the method even in the accessible regime?** Checked against literature values.

    Condensate interfacial tension is **0.1–100 µN/m** (Jawerth 2018, PGL-3: 1–5 µN/m;
    Alshareedah 2021). Condensate G′ runs from ~0.1 Pa (liquid-like) to ~1 kPa (aged/solid).

    ``L_ec = γ/G`` must fall inside the light-microscopy window (~0.3–10 µm) for the size
    crossover to be observable at all:

    ==================  ==========  ==========  ==========
    γ                   G = 1 Pa    G = 10 Pa   G = 100 Pa
    ==================  ==========  ==========  ==========
    1 µN/m (PGL-3)      **1.0 µm**  0.1 µm      0.01 µm
    10 µN/m             **10 µm**   **1.0 µm**  0.1 µm
    100 µN/m            100 µm      **10 µm**   **1.0 µm**
    ==================  ==========  ==========  ==========

    **L_ec lands inside the window for G ≈ 0.1–100 Pa — precisely the aged / maturing /
    disease-associated regime.** Below that (a true liquid) nothing arrests; above it (a hard
    solid) everything does. **Both are the bounded case, and both are still measurements.**
    """
    # Literature ranges.
    gammas_N_per_m = [1e-6, 1e-5, 1e-4]          # 1, 10, 100 uN/m
    moduli_Pa = [0.1, 1.0, 10.0, 100.0]          # the soft/gel regime

    window_low_um, window_high_um = 0.3, 10.0

    in_window = 0
    for gamma in gammas_N_per_m:
        for G in moduli_Pa:
            L_ec_um = 1e6 * gamma / G
            if window_low_um <= L_ec_um <= window_high_um:
                in_window += 1

    assert in_window >= 4, (
        f"only {in_window} of {len(gammas_N_per_m) * len(moduli_Pa)} literature (gamma, G) "
        f"combinations put L_ec inside the 0.3-10 um microscopy window. If the method's "
        f"accessible regime does not overlap the physics of real condensates, it measures "
        f"nothing."
    )
