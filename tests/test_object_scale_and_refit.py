"""Tests for the two scale-awareness stages: `object_scale` and `boundary_refit`.

Both were added because a measurement said the pipeline was scale-BLIND, not
because a threshold looked wrong (see each module's docstring and
``benchmarks/condensate_scale.py``). So these tests assert the two properties
that measurement depends on:

  * `object_scale_spectrum` reports the size that was actually placed — and does
    NOT report the CELL, which is the failure mode that made the obvious
    scale-space implementation useless; and
  * `refit_object_boundaries` moves boundaries and ONLY boundaries: it never
    creates an object, never deletes one, never merges two, and never leaves a
    cell.

Both modules are pure numpy/scipy/skimage and import without napari, so this
runs in CI.

Run: pytest tests/test_object_scale_and_refit.py -v
"""

import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.object_scale import (
    object_scale_spectrum, recommend_working_scales, area_fraction_above)
from pycat.toolbox.segmentation.boundary_refit import refit_object_boundaries


# ── scenes ──────────────────────────────────────────────────────────────────────
def _cell(shape=(220, 220), radius=95):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return ((yy - shape[0] // 2) ** 2 + (xx - shape[1] // 2) ** 2) <= radius ** 2


def _scene(object_radii, positions, shape=(220, 220), amplitude=900.0,
           nucleoplasm=160.0, offset=100.0, psf=1.2, noise=4.0, seed=0):
    """Discs of KNOWN radius inside one cell, blurred and noised. Returns
    (image, truth mask, cell mask)."""
    rng = np.random.default_rng(seed)
    cell = _cell(shape)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    image = np.zeros(shape, np.float32)
    image[cell] = nucleoplasm
    truth = np.zeros(shape, bool)
    for r, (cy, cx) in zip(object_radii, positions):
        disc = ((yy - cy) ** 2 + (xx - cx) ** 2) <= r ** 2
        image[disc] = nucleoplasm + amplitude
        truth |= disc
    image = ndi.gaussian_filter(image, psf) + offset
    image = image + rng.normal(0, noise, shape).astype(np.float32)
    return image.astype(np.float32), truth, cell


# ── object_scale ────────────────────────────────────────────────────────────────
@pytest.mark.core
def test_spectrum_reports_the_radius_that_was_placed():
    image, _truth, cell = _scene([6, 6, 6, 6],
                                 [(70, 70), (70, 150), (150, 70), (150, 150)])
    spectrum = object_scale_spectrum(image, cell)
    assert spectrum is not None
    # Generous, because the foreground threshold necessarily includes some of the
    # PSF skirt: the assertion that matters is that it is the OBJECT's scale and
    # not something else entirely.
    assert 4.0 <= spectrum['r_dominant'] <= 9.0, spectrum


@pytest.mark.core
def test_spectrum_does_not_report_the_cell_itself():
    """The failure mode that ruled out a Laplacian-of-Gaussian scale-space probe.

    A nucleus is a far better blob than anything inside it, so a scale-space bank
    measured on a real cell returns the CELL's radius as the dominant object
    scale — which then sets every downstream band-pass to the size of the cell.
    """
    cell_radius = 95
    image, _truth, cell = _scene([5, 5, 5], [(70, 70), (70, 150), (150, 110)])
    spectrum = object_scale_spectrum(image, cell)
    assert spectrum is not None
    assert spectrum['r_dominant'] < 0.25 * cell_radius, (
        f"the spectrum reported r={spectrum['r_dominant']:.1f} for a cell of "
        f"radius {cell_radius} — it is measuring the cell, not the objects")


@pytest.mark.core
def test_spectrum_separates_a_two_population_field():
    """A field of small AND large objects must show area at BOTH ends.

    Asked the right way: how much area is well above the SMALL population's scale,
    and how much is at or below it. Asking instead how much is above the
    distribution's own centre gets this case backwards — a few large condensates
    carry most of the area, so they are the centre.
    """
    image, _truth, cell = _scene(
        [4, 4, 4, 4, 4, 16, 16],
        [(60, 60), (60, 90), (90, 60), (60, 120), (90, 120),
         (150, 80), (150, 145)])
    spectrum = object_scale_spectrum(image, cell)
    assert spectrum is not None
    assert area_fraction_above(spectrum, 10.0) > 0.3, spectrum   # the large ones
    assert area_fraction_above(spectrum, 10.0) < 0.98, spectrum  # and the small ones


@pytest.mark.core
def test_a_single_scale_field_gets_a_single_pass():
    image, _truth, cell = _scene([6, 6, 6, 6],
                                 [(70, 70), (70, 150), (150, 70), (150, 150)])
    plan = recommend_working_scales(object_scale_spectrum(image, cell),
                                    measured_object_radius=6.0)
    assert plan['multiscale'] is False
    assert len(plan['ball_radii']) == 1


@pytest.mark.core
def test_a_two_scale_field_gets_a_second_larger_pass():
    image, _truth, cell = _scene(
        [4, 4, 4, 4, 4, 16, 16],
        [(60, 60), (60, 90), (90, 60), (60, 120), (90, 120),
         (150, 80), (150, 145)])
    plan = recommend_working_scales(object_scale_spectrum(image, cell),
                                    measured_object_radius=4.0)
    assert plan['multiscale'] is True
    assert len(plan['ball_radii']) == 2
    assert plan['ball_radii'][1] > plan['ball_radii'][0]


@pytest.mark.core
def test_the_users_own_scale_is_never_overridden():
    """`ball_radii[0]` must always be the caller's, so the extra pass is purely
    ADDITIVE and a result stays reproducible from the drawn annotation."""
    image, _truth, cell = _scene(
        [4, 4, 4, 4, 4, 16, 16],
        [(60, 60), (60, 90), (90, 60), (60, 120), (90, 120),
         (150, 80), (150, 145)])
    spectrum = object_scale_spectrum(image, cell)
    for measured_r in (3.0, 5.0, 8.0):
        plan = recommend_working_scales(spectrum, measured_object_radius=measured_r)
        assert plan['ball_radii'][0] == max(2, int(np.ceil(1.5 * measured_r)))


@pytest.mark.core
def test_an_empty_cell_yields_no_spectrum_rather_than_a_guess():
    shape = (120, 120)
    cell = _cell(shape, 45)
    rng = np.random.default_rng(0)
    image = np.zeros(shape, np.float32)
    image[cell] = 160.0
    image = image + rng.normal(0, 4.0, shape).astype(np.float32)
    assert object_scale_spectrum(image, cell) is None
    plan = recommend_working_scales(None, measured_object_radius=4.0)
    assert plan['multiscale'] is False
    assert plan['ball_radii'] == [6]


# ── boundary_refit ──────────────────────────────────────────────────────────────
@pytest.mark.core
def test_refit_grows_an_undercovered_object_toward_the_truth():
    image, truth, cell = _scene([12], [(110, 110)])
    eroded = ndi.binary_erosion(truth, np.ones((11, 11)))     # a "core-only" mask
    refitted = refit_object_boundaries(image, eroded, cell)
    before = int((eroded & truth).sum()) / int(truth.sum())
    after = int((refitted & truth).sum()) / int(truth.sum())
    assert after > before
    assert after > 0.75, f"re-fit recovered only {after:.2f} of the true object"


@pytest.mark.core
def test_refit_shrinks_an_overcovered_object_toward_the_truth():
    """Boundaries must be able to come IN as well as go out — the same pipeline
    over-covers small elongated objects while under-covering large round ones."""
    image, truth, cell = _scene([10], [(110, 110)])
    dilated = ndi.binary_dilation(truth, np.ones((13, 13)))
    refitted = refit_object_boundaries(image, dilated, cell)
    assert int(refitted.sum()) < int(dilated.sum())
    assert int(refitted.sum()) >= 0.5 * int(truth.sum())


@pytest.mark.core
def test_refit_never_changes_the_object_count():
    image, truth, cell = _scene([6, 6, 6], [(70, 70), (70, 150), (150, 110)])
    refitted = refit_object_boundaries(image, truth, cell)
    assert ndi.label(refitted)[1] == ndi.label(truth)[1]


@pytest.mark.core
def test_refit_does_not_merge_neighbours():
    """Two close objects both flooding outward must stay two objects — this is
    what the watershed basins in the re-fit are for."""
    image, truth, cell = _scene([8, 8], [(110, 96), (110, 128)])
    assert ndi.label(truth)[1] == 2, "fixture must start as two objects"
    refitted = refit_object_boundaries(image, truth, cell)
    assert ndi.label(refitted)[1] == 2


@pytest.mark.core
def test_refit_stays_inside_the_cell():
    image, truth, cell = _scene([10], [(110, 110)])
    refitted = refit_object_boundaries(image, truth, cell)
    assert not (refitted & ~cell).any()


@pytest.mark.core
def test_refit_of_nothing_is_nothing():
    image, _truth, cell = _scene([6], [(110, 110)])
    empty = np.zeros(image.shape, bool)
    assert not refit_object_boundaries(image, empty, cell).any()
