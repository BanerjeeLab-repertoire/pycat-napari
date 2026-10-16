"""**Every checklist step has a key, and the fibril image chain replays in batch** (Completion Spec B / E).

A checklist step with an empty key cannot be recorded, so it can never be replayed or bound by the navigator.
Eight had one -- seven in fibril (bilateral filter, peak & edge, morphological Gaussian, random forest, local
thresholding, labelling, measuring) and the in-vitro time series' "Preprocess" box, which has no operation at
all. The fibril steps now record the layer they ran on; their replays must compute exactly what the buttons do.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.base


def test_no_checklist_step_has_an_empty_key():
    from pycat.ui.pipeline_steps import PIPELINE_DEFS
    empty = [(p, label) for p, steps in PIPELINE_DEFS.items() for key, label in steps if not key]
    assert not empty, f'unrecordable checklist steps (empty key): {empty}'


def _fibril_image(seed=0):
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 4, (96, 96))
    yy, xx = np.mgrid[:96, :96]
    for k in range(4):
        img += 120 * (np.abs((yy - 10 - 20 * k) - 0.4 * xx) < 1.5)
    return np.clip(img, 0, None).astype(np.uint16)


def test_the_fibril_chain_replays_exactly_what_the_buttons_compute(tmp_path):
    import skimage as sk

    from pycat.batch_step_registry import _STEP_MAP
    from pycat.toolbox.image_processing.filters import (
        apply_bilateral_filter,
        morphological_gaussian_filter,
        peak_and_edge_enhancement_func,
    )
    from pycat.toolbox.segmentation.local_thresholding import local_thresholding_func
    raw = _fibril_image()
    state = {'image': raw, 'data_instance': SimpleNamespace(data_repository={'ball_radius': 3}),
             '_primary_channel_name': 'fibrils'}
    steps = [('bilateral_filter', {'layer': 'fibrils', 'radius': 2.0}),
             ('peak_edge_enhancement', {'layer': 'Bilateral Filtered fibrils', 'ball_radius': 3}),
             ('morphological_gaussian', {'layer': 'Peak & Edge Enhanced Bilateral Filtered fibrils', 'filter_size': 1}),
             ('local_thresholding', {'layer': 'Filtered Peak & Edge Enhanced Bilateral Filtered fibrils',
                                     'method': 'Sauvola', 'k_value': 0.0, 'window_size': 15}),
             ('label_binary_mask', {'mask_layer': 'Locally Thresholded Filtered Peak & Edge Enhanced '
                                                  'Bilateral Filtered fibrils'})]
    for name, params in steps:
        _STEP_MAP[name](state, Path('fibrils.tif'), params, tmp_path)

    # the interactive computation, step by step, on the same raw image
    b = apply_bilateral_filter(raw, 2.0)
    p = peak_and_edge_enhancement_func(b, 3)
    m = morphological_gaussian_filter(p, 1)
    t = local_thresholding_func(m, 15, k_val=0.0, mode='Sauvola')
    labels = sk.measure.label(t > 0)
    assert np.array_equal(state['fibril_labels'], labels)
    assert labels.max() > 0
    assert (tmp_path / 'fibrils_fibril_labels.tiff').is_file()


def test_the_recorded_local_threshold_k_is_the_k_that_ran():
    """The panel recorded and displayed k = slider/50 - 1 while the computation used slider*0.01 - 0.5: at the
    slider's ends it recorded +/-1.0 for a run at +/-0.5. One function now defines it for all three."""
    from pycat.toolbox.segmentation.local_thresholding import local_threshold_k
    assert local_threshold_k(0) == pytest.approx(-0.5)
    assert local_threshold_k(50) == pytest.approx(0.0)
    assert local_threshold_k(100) == pytest.approx(0.5)
