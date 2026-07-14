"""
**The Z step is not the XY pixel size, and assuming it is corrupts every 3-D volume.**

``zstack_segmentation_tools`` computed::

    voxel_volume_um3 = (microns_per_pixel ** 2) * z_step_um

with ``z_step_um: float = 1.0`` — **a default that nothing ever overrode.** The UI had a Z-step
spinbox hardcoded to 1.0, whose tooltip said *"from the acquisition metadata"*; it was not.

A typical confocal pairs a **0.108 µm** lateral pixel with a **0.300 µm** Z step, because the axial
PSF is three times worse and nobody oversamples a dimension they cannot resolve. Left at 1.0, every
volume PyCAT reported was out by ``1.0 / 0.300 = 3.33x`` — *and the same number feeds the
marching-cubes ``spacing=`` and the 3-D centroids, so the surface areas and the axial distances
were wrong in the same breath.*

***All of it reported as numbers that look entirely normal.***

**The true value was already in the repository.** ``metadata_extract`` reads
``physical_pixel_sizes.Z`` and stores it as ``z_step_um``, where it was **displayed in the metadata
panel and read by nothing.** This is the same disease as the ``1.0 µm/px`` sentinel: the honest
number exists, nobody consults it, and the fallback is a plausible-looking lie.
"""

import numpy as np
import pytest

from pycat.toolbox.zstack_segmentation_tools import condensate_metrics_3d
from pycat.utils.pixel_size import z_step_um, z_step_um_or_default


def _block(nz=4, ny=6, nx=6):
    """A solid rectangular condensate of known voxel count."""
    labelled = np.zeros((10, 10, 10), np.uint16)
    labelled[2:2 + nz, 2:2 + ny, 2:2 + nx] = 1
    intensity = np.ones_like(labelled, np.float32)
    return labelled, intensity, nz * ny * nx


@pytest.mark.core
def test_the_voxel_is_ANISOTROPIC_and_the_volume_says_so():
    """A 0.3 µm Z step must give a volume 3.3x smaller than an assumed 1.0 µm one."""
    labelled, intensity, n_voxels = _block()
    microns_per_pixel = 0.108

    true_volume = condensate_metrics_3d(
        labelled, intensity, microns_per_pixel, 0.300)['volume_um3'].iloc[0]

    assert np.isclose(true_volume, n_voxels * (microns_per_pixel ** 2) * 0.300), (
        "the voxel volume is not microns_per_pixel^2 x z_step"
    )

    assumed_isotropic = condensate_metrics_3d(
        labelled, intensity, microns_per_pixel, 1.0)['volume_um3'].iloc[0]

    error = assumed_isotropic / true_volume
    assert np.isclose(error, 1.0 / 0.300, rtol=1e-6), (
        f"assuming an isotropic voxel overstates the volume by {error:.2f}x. That is the bug — "
        f"this test exists to keep the number visible."
    )


@pytest.mark.core
def test_an_UNKNOWN_z_step_gives_NaN_not_a_plausible_LIE():
    """**NaN propagates. A 3.3x overestimate does not.**

    The default was ``1.0``. A volume computed from it is wrong by the ratio of the true Z step to
    1.0 — *and looks entirely normal*. The default is now NaN, so a caller that forgets to supply
    the Z step gets a visibly-unusable number rather than a quietly-wrong one.
    """
    labelled, intensity, _ = _block()

    volume = condensate_metrics_3d(labelled, intensity, 0.108)['volume_um3'].iloc[0]

    assert np.isnan(volume), (
        "an unspecified z_step produced a NUMBER. It must produce NaN — a volume computed from an "
        "assumed isotropic voxel is wrong by the anisotropy ratio and nothing about it looks wrong."
    )


@pytest.mark.core
def test_the_z_step_is_READ_from_the_file_not_guessed():
    """``metadata_extract`` already stores it. The accessor must actually consult it."""
    repository = {'file_metadata': {'common': {'z_step_um': 0.3}}}
    assert z_step_um(repository) == pytest.approx(0.3)

    # A value set on the repository directly (a UI field, a batch step) OVERRIDES the file — the
    # user correcting bad metadata is the whole point of being able to.
    repository['z_step_um'] = 0.25
    assert z_step_um(repository) == pytest.approx(0.25)


@pytest.mark.core
def test_a_MISSING_z_step_is_NaN_and_an_IMPLAUSIBLE_one_is_too():
    """Silence is not 1.0, and a corrupt tag is not a measurement."""
    assert np.isnan(z_step_um({}))
    assert np.isnan(z_step_um({'file_metadata': {'common': {'z_step_um': None}}}))

    # Same physical bounds as the lateral pixel: 2.3 pm per slice is not an acquisition.
    assert np.isnan(z_step_um({'z_step_um': 2.3e-6}))
    assert np.isnan(z_step_um({'z_step_um': -1.0}))
    assert np.isnan(z_step_um({'z_step_um': 0.0}))

    # `_or_default` may proceed — but only because it WARNS, putting the assumption on the record.
    assert z_step_um_or_default({}, default=1.0) == pytest.approx(1.0)
