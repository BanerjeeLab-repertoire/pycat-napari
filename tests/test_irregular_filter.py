"""Large irregular objects are flagged, never deleted, and left out of per-cell summaries on request."""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.feature_analysis_tools import flag_irregular_puncta, puncta_analysis_func

pytestmark = pytest.mark.base


class BaseDataClass:
    """The slice of pycat's data class the table builder uses (the real one is GUI-bound)."""

    def __init__(self):
        self.data_repository = {}

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)

    def set_data(self, key, value):
        self.data_repository[key] = value


def _scene():
    cells = np.zeros((120, 120), int)
    cells[5:115, 5:115] = 1
    mask = np.zeros(cells.shape, bool)
    yy, xx = np.mgrid[:120, :120]
    mask |= (yy - 30) ** 2 + (xx - 30) ** 2 <= 16          # round punctum, r=4
    mask |= (yy - 30) ** 2 + (xx - 80) ** 2 <= 16          # round punctum, r=4
    mask[70:74, 20:100] = True                             # long thin bar ...
    mask[74:100, 20:24] = True                             # ... with a leg: large and non-convex
    image = np.where(mask, 500.0, 50.0)
    data = BaseDataClass()
    data.data_repository.update({'ball_radius': 6, 'microns_per_pixel_sq': 1.0,
                                 'cell_df': pd.DataFrame({'label': [1]})})
    return mask, image, cells, data


def test_the_aggregate_is_flagged_and_kept():
    mask, image, cells, data = _scene()
    puncta_analysis_func(mask, image, cells, data)
    df = data.get_data('puncta_df')
    assert len(df) == 3                                    # nothing deleted
    assert df['shape_filtered'].sum() == 1
    flagged = df[df['shape_filtered']].iloc[0]
    assert flagged['area'] > 4 * np.pi * 4 ** 2 and flagged['solidity'] < 0.8


@pytest.mark.parametrize('filter_on, expected', [(True, 2), (False, 3)])
def test_per_cell_summaries_leave_it_out_only_when_asked(filter_on, expected):
    mask, image, cells, data = _scene()
    puncta_analysis_func(mask, image, cells, data, filter_irregular=filter_on)
    assert int(data.get_data('cell_df').loc[0, 'number_of_puncta']) == expected


def test_round_puncta_are_never_flagged_however_large():
    data = BaseDataClass()
    data.data_repository['ball_radius'] = 3
    df = flag_irregular_puncta(pd.DataFrame({'area': [5000.0], 'solidity': [0.97]}), data)
    assert not df['shape_filtered'].any()


def test_no_measured_scale_flags_nothing():
    data = BaseDataClass()
    data.data_repository['ball_radius'] = 0
    df = flag_irregular_puncta(pd.DataFrame({'area': [5000.0], 'solidity': [0.3]}), data)
    assert not df['shape_filtered'].any()


def test_boundary_source_is_reported_or_honestly_unrecorded():
    mask, image, cells, data = _scene()
    puncta_analysis_func(mask, image, cells, data)
    assert set(data.get_data('puncta_df')['boundary_source']) == {'unrecorded'}   # no map: no guess
    source = np.where(mask, 2, 0).astype(np.uint8)
    source[20:40, 20:40][mask[20:40, 20:40]] = 1                    # the first punctum went regional
    data.data_repository['boundary_source_map'] = source
    puncta_analysis_func(mask, image, cells, data)
    df = data.get_data('puncta_df').sort_values(['bbox_y0', 'bbox_x0'])
    assert list(df['boundary_source']) == ['regional', 'half-max', 'half-max']
