"""Optical halo fragments are removed; the parent and real neighbours are not touched."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.halo import find_halo_fragments, reject_halo_fragments

pytestmark = pytest.mark.base

N = 160
YY, XX = np.mgrid[:N, :N]
C = (80, 80)
R_PARENT = 15
R_RING = (19.0, 22.0)          # a halo ~1.3x the parent radius, 3 px wide


def _r(c=C):
    return np.hypot(YY - c[0], XX - c[1])


def _scene(ring_mask, extra=None, ring_level=0.05, extra_level=0.4):
    """Mask = parent disc + ring (+ extra object); raw = blurred parent + a dim ring (+ extra)."""
    parent = _r() <= R_PARENT
    mask = parent | ring_mask | (extra if extra is not None else False)
    raw = 0.05 + parent * 1.0 + ring_mask * ring_level
    if extra is not None:
        raw = raw + extra * extra_level
    raw = ndi.gaussian_filter(raw, 1.0) + np.random.default_rng(0).normal(0, 0.002, raw.shape)
    return mask, raw, parent


def _arcs(n, gap_deg=20):
    ang = (np.degrees(np.arctan2(YY - C[0], XX - C[1])) + 360) % 360
    band = (_r() >= R_RING[0]) & (_r() <= R_RING[1])
    step = 360 / n
    return band & ((ang % step) < step - gap_deg)


def test_a_full_halo_ring_is_rejected_and_the_parent_is_untouched():
    ring = (_r() >= R_RING[0]) & (_r() <= R_RING[1])
    mask, raw, parent = _scene(ring)
    kept, rejected = reject_halo_fragments(mask, raw)
    assert np.array_equal(rejected, ring)
    assert np.array_equal(kept, parent)                    # parent mask exactly unchanged


def test_a_halo_fragmented_into_five_arcs_is_rejected_entirely():
    arcs = _arcs(5)
    assert ndi.label(arcs, structure=np.ones((3, 3)))[1] == 5
    mask, raw, parent = _scene(arcs)
    kept, rejected = reject_halo_fragments(mask, raw)
    assert np.array_equal(rejected, arcs)
    assert np.array_equal(kept, parent)


@pytest.mark.parametrize('gap', [1, 2, 3, 5, 8, 12])
def test_a_real_small_condensate_beside_a_large_one_is_kept(gap):
    """The negative control: a round neighbour at several standoffs, dim or bright, is never rejected."""
    r_small = 4
    centre = (C[0], C[1] + R_PARENT + gap + r_small)
    small = _r(centre) <= r_small
    for level in (0.1, 0.4):
        mask, raw, parent = _scene(np.zeros_like(small), extra=small, extra_level=level)
        kept, rejected = reject_halo_fragments(mask, raw)
        assert not rejected.any(), (gap, level)
        assert np.array_equal(kept, mask)


def test_an_elongated_neighbour_pointing_away_is_kept():
    rod = (np.abs(YY - C[0]) <= 1) & (XX >= C[1] + R_PARENT + 3) & (XX <= C[1] + R_PARENT + 25)
    mask, raw, _ = _scene(np.zeros_like(rod), extra=rod, extra_level=0.1)
    assert not reject_halo_fragments(mask, raw)[1].any()


def test_a_bright_arc_is_not_a_halo():
    arcs = _arcs(5)
    mask, raw, _ = _scene(arcs, ring_level=0.8)            # 80% of the parent: real material, not optics
    assert not reject_halo_fragments(mask, raw)[1].any()


def test_nothing_happens_with_a_single_object():
    mask, raw, parent = _scene(np.zeros((N, N), bool))
    kept, rejected = reject_halo_fragments(mask, raw)
    assert np.array_equal(kept, mask) and not rejected.any()
    assert find_halo_fragments(ndi.label(mask)[0], raw) == {}


# ── Plumbing: segmentation records, the table reports, the parent is untouched ───────────────────────

def test_the_per_cell_step_removes_records_and_clears_provenance_only_for_the_fragment():
    from pycat.toolbox.segmentation.subcellular import _drop_halo_fragments
    arcs = _arcs(5)
    mask, raw, parent = _scene(arcs)
    source = np.where(mask, 1, 0).astype(np.uint8)
    rejected = np.zeros(mask.shape, bool)
    cell = np.ones(mask.shape, bool)
    kept = _drop_halo_fragments(mask, raw, cell, (0, N, 0, N), source, rejected)
    assert np.array_equal(kept, parent)
    assert np.array_equal(rejected, arcs)
    assert not source[arcs].any() and (source[parent] == 1).all()


def test_the_library_default_is_off_and_the_2d_cellular_workflow_turns_it_on():
    import inspect
    import pathlib
    from pycat.toolbox.segmentation.subcellular import (run_segment_subcellular_objects,
                                                         segment_subcellular_objects)
    from pycat.batch.steps.analysis_steps import _condensate_refit_kwargs
    assert inspect.signature(segment_subcellular_objects).parameters['ring_rejection'].default is False
    assert inspect.signature(run_segment_subcellular_objects).parameters['ring_rejection'].default is True
    assert _condensate_refit_kwargs({})['ring_rejection'] is True
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src/pycat/ui/ui_segmentation_mixin.py').read_text(
        encoding='utf-8')
    assert "widgets['ring_rejection'].setChecked(True)" in src and "'ring_rejection': ring_cb.isChecked()" in src


class _Data:
    def __init__(self):
        self.data_repository = {}

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)

    def set_data(self, key, value):
        self.data_repository[key] = value


def test_rejected_fragments_are_reported_in_the_table_but_never_counted():
    import pandas as pd
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func
    arcs = _arcs(5)
    mask, raw, parent = _scene(arcs)
    cells = np.zeros(mask.shape, int)
    cells[2:-2, 2:-2] = 1                               # label 0 is background
    data = _Data()
    data.data_repository.update({'ball_radius': 6, 'microns_per_pixel_sq': 1.0,
                                 'cell_df': pd.DataFrame({'label': [1]}), 'ring_rejected_map': arcs})
    puncta_analysis_func(parent, raw, cells, data)
    df = data.get_data('puncta_df')
    assert df['ring_rejected'].sum() == 5 and (~df['ring_rejected']).sum() == 1
    assert (df.loc[df['ring_rejected'], 'global_punctum_label'] == 0).all()
    assert int(data.get_data('cell_df').loc[0, 'number_of_puncta']) == 1
