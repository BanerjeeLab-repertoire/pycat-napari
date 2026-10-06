"""The minimum contrast floor: measured once, applied by lookup, flagged rather than deleted."""
import time

import numpy as np
import pandas as pd
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.contrast_floor import (CONTRAST_FLOOR_DEFAULT, apply_floor, floor_counts,
                                                       measure_for_floor, object_local_cnr, split_by_floor)

pytestmark = pytest.mark.base

N = 200
YY, XX = np.mgrid[:N, :N]


class _Data:
    def __init__(self, **repo):
        self.data_repository = dict(repo)

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)

    def set_data(self, key, value):
        self.data_repository[key] = value


def _field(amplitudes, seed=0):
    """Gaussian puncta of the given amplitudes on a noisy background; mask = each at its half max."""
    rng = np.random.default_rng(seed)
    img = 100 + rng.normal(0, 5.0, (N, N))
    mask = np.zeros((N, N), bool)
    for k, a in enumerate(amplitudes):
        cy, cx = 30 + 40 * (k // 4), 30 + 45 * (k % 4)
        g = np.exp(-((YY - cy) ** 2 + (XX - cx) ** 2) / (2 * 3.0 ** 2))
        img += a * g
        mask |= g >= 0.5
    return img, ndi.label(mask)[0]


def test_contrast_tracks_amplitude_and_noise_reads_near_zero():
    img, lab = _field([0, 10, 40, 160])
    cnr = object_local_cnr(lab, img)
    assert abs(cnr[1]) < 1.0                       # an "object" with no signal
    assert cnr[2] < cnr[3] < cnr[4]
    assert cnr[4] > 3                              # the ring sits in the skirt, as in the refinement gate


def test_the_slider_minimum_reproduces_the_unfiltered_result_exactly():
    img, lab = _field([0, 5, 10, 40, 160, 3])
    cnr = object_local_cnr(lab, img)
    above, below = split_by_floor(lab, cnr, -np.inf)
    assert np.array_equal(above, lab) and not below.any()
    data = _Data()
    measure_for_floor(data, lab, img)
    above_off, _ = apply_floor(data, CONTRAST_FLOOR_DEFAULT, apply=False)
    assert np.array_equal(above_off, lab)
    assert floor_counts(data, CONTRAST_FLOOR_DEFAULT, apply=False) == (int(lab.max()), 0)


def test_the_floor_moves_whole_objects_by_contrast_alone():
    img, lab = _field([2, 10, 40, 160])
    data = _Data()
    cnr = measure_for_floor(data, lab, img)
    floor = float(np.sort(cnr[1:])[1]) + 0.01        # between the 2nd and 3rd objects
    above, below = apply_floor(data, floor)
    assert set(np.unique(below)) - {0} == {i for i in range(1, 5) if cnr[i] < floor}
    assert np.array_equal(above + below, lab)        # nothing changed shape, nothing lost
    assert floor_counts(data, floor) == (2, 2)
    assert data.data_repository['below_floor_map'].sum() == (below > 0).sum()


def test_a_slider_step_is_a_lookup_well_under_a_frame():
    rng = np.random.default_rng(0)
    m = np.zeros((512, 512), bool)
    for _ in range(250):
        y, x = rng.integers(5, 507, 2)
        m[y - 3:y + 3, x - 3:x + 3] = True
    lab, n = ndi.label(m)
    data = _Data(contrast_floor_state={'labels': lab, 'cnr': np.r_[np.nan, rng.uniform(0, 8, n)]})
    apply_floor(data, 1.0)
    t = time.perf_counter()
    for f in np.linspace(0, 8, 50):
        apply_floor(data, f)
    assert (time.perf_counter() - t) / 50 < 0.016


def test_below_floor_objects_are_listed_in_the_table_with_their_cnr_and_not_counted():
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func
    img, lab = _field([2, 10, 40, 160])
    data = _Data(ball_radius=6, microns_per_pixel_sq=1.0, cell_df=pd.DataFrame({'label': [1]}))
    cnr = measure_for_floor(data, lab, img)
    above, below = apply_floor(data, float(np.sort(cnr[1:])[1]) + 0.01)
    cells = np.zeros(lab.shape, int)
    cells[1:-1, 1:-1] = 1
    puncta_analysis_func(above > 0, img, cells, data)
    df = data.get_data('puncta_df')
    flagged = df[df['below_contrast_floor']]
    assert len(flagged) == 2 and flagged['local_cnr'].notna().all()
    assert (flagged['local_cnr'] < data.data_repository['contrast_floor']).all()
    assert (df.loc[~df['below_contrast_floor'], 'local_cnr'] >= data.data_repository['contrast_floor']).all()
    assert int(data.get_data('cell_df').loc[0, 'number_of_puncta']) == 2


def test_batch_replays_the_recorded_floor_and_old_recordings_are_unchanged():
    from pycat.batch.steps.analysis_steps import _apply_recorded_contrast_floor
    img, lab = _field([2, 10, 40, 160])
    cells = np.ones(lab.shape, int)
    cnr = object_local_cnr(lab, img, cells)
    floor = float(np.sort(cnr[1:])[1]) + 0.01
    kept = _apply_recorded_contrast_floor(_Data(), lab > 0, img, cells, {'contrast_floor': floor})
    assert ndi.label(kept)[1] == 2
    import inspect
    from pycat.batch.steps import analysis_steps
    src = inspect.getsource(analysis_steps.replay_condensate_segmentation)
    assert "params.get('contrast_floor_on', False)" in src     # absent from a pre-1.6.472 recording = off


def test_the_gui_default_is_on_at_the_calibrated_value():
    import inspect
    import pathlib
    from pycat.toolbox.segmentation.subcellular import run_segment_subcellular_objects
    assert CONTRAST_FLOOR_DEFAULT == 1.75
    assert inspect.signature(run_segment_subcellular_objects).parameters['contrast_floor_on'].default is True
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src/pycat/ui/contrast_floor_controls.py').read_text(
        encoding='utf-8')
    assert 'self.apply_cb.setChecked(True)' in src and 'CONTRAST_FLOOR_DEFAULT / _STEP' in src
