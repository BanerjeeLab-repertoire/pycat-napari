"""**Characterization pins for the object-size estimators — written BEFORE they move.**

`estimate_object_size_px` (top-hat + Otsu → median equivalent diameter → ball_radius) feeds the batch
auto-object-size path that drives downstream segmentation, so a silent change propagates. Its coverage is
thin, so — per the image_processing decomposition discipline (**no characterization test, no move**) — this
pins its exact output on a fixed synthetic scene before `size_estimation.py` is split out. The brightfield
variant and the workflow-validity gate are pinned alongside.

The scene is deterministic and headless: seven bright radius-4 disks on a flat noisy background.
"""
import warnings

import numpy as np
import pytest
from skimage.draw import disk

pytestmark = pytest.mark.core


def _scene():
    rng = np.random.default_rng(0)
    img = np.full((128, 128), 50.0, np.float32)
    img += rng.normal(0, 2, (128, 128)).astype(np.float32)
    for (cy, cx) in [(32, 32), (32, 96), (96, 32), (96, 96), (64, 64), (32, 64), (96, 64)]:
        rr, cc = disk((cy, cx), 4, shape=img.shape)
        img[rr, cc] = 300.0
    return img


def test_estimate_object_size_px_is_pinned():
    from pycat.toolbox.image_processing_tools import estimate_object_size_px
    r = estimate_object_size_px(_scene(), tophat_radius=15)
    assert r['n_objects'] == 7
    assert r['ball_radius'] == 4
    assert r['object_size_px'] == pytest.approx(7.569397566060481, rel=0, abs=1e-9)


def test_estimate_object_size_px_brightfield_is_pinned():
    from pycat.toolbox.image_processing_tools import estimate_object_size_px_brightfield
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        r = estimate_object_size_px_brightfield(_scene())
    assert r['n_objects'] == 7
    assert r['ball_radius'] == 5
    assert r['object_size_px'] == pytest.approx(9.373021315815206, rel=0, abs=1e-9)


def test_auto_object_size_validity_gate_is_pinned():
    from pycat.toolbox.image_processing_tools import (
        auto_object_size_valid, AUTO_OBJECT_SIZE_VALID_WORKFLOWS)
    assert set(AUTO_OBJECT_SIZE_VALID_WORKFLOWS) == {'condensate', 'invitro_fluor'}
    assert auto_object_size_valid('condensate') is True
    assert auto_object_size_valid('invitro_fluor') is True
    assert auto_object_size_valid('single_cell') is False


def test_estimate_object_size_px_rejects_an_invalid_workflow():
    """The validity gate: a workflow not in the valid set raises rather than returning a bogus size."""
    from pycat.toolbox.image_processing_tools import estimate_object_size_px
    with pytest.raises(ValueError):
        estimate_object_size_px(_scene(), workflow='single_cell', tophat_radius=15)
