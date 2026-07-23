"""**In-vitro fluorescence: the per-droplet table is brush-ready, and clicking a droplet selects it.**

Phase 2 of the brushable-results-workspace spec. The in-vitro field-summary produced a per-droplet table
with no bbox, no circularity, and no identity — position-linked, not brushable. `_finalize_droplet_table`
makes it brush-ready additively (size + circularity + bbox + `_pycat_entity_id` + `_pycat_layer_id`), and
`BrushableImageTier` turns the droplet mask into a brushing tier: a click on a labeled droplet selects its
entity everywhere. No existing droplet number changes.
"""
import types

import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN, LAYER_ID_COLUMN
from pycat.utils.selection_service import SelectionService, SelectionState
from pycat.ui.brushable_workspace import BrushableImageTier
from pycat.toolbox.invitro_fluor_ui import _finalize_droplet_table
from tests.selection_view_contract import assert_selection_view_contract

pytestmark = pytest.mark.core


def _service():
    return SelectionService(defer=lambda fn: fn())


def _mask():
    m = np.zeros((20, 20), dtype=np.int32)
    m[2:6, 2:6] = 1          # droplet 1 — 16 px
    m[10:16, 10:16] = 2      # droplet 2 — 36 px
    return m


def _fake_layer(returns_label=0):
    return types.SimpleNamespace(
        name='IVF Droplet Mask (2 droplets)',
        metadata={'pycat_layer_id': 'LID-abc'},
        mouse_drag_callbacks=[],
        get_value=lambda position, world=True: returns_label)


def _brush_ready_df():
    part = pd.DataFrame({'droplet_label': [1, 2], 'I_dense': [100.0, 200.0],
                         'partition_coefficient': [5.0, 10.0]})
    return _finalize_droplet_table(part, _mask(), 0.1, _fake_layer(), 'img.tif')


# ── the additive data augmentation ────────────────────────────────────────────────────────────────
def test_finalize_droplet_table_adds_size_circularity_bbox_and_identity():
    out = _brush_ready_df()
    for col in ('area_um2', 'circularity', 'bbox_y0', 'bbox_x0', 'bbox_y1', 'bbox_x1',
                ENTITY_ID_COLUMN, LAYER_ID_COLUMN):
        assert col in out.columns, f"missing {col}"
    assert out['area_um2'].iloc[0] == pytest.approx(16 * 0.1 ** 2)      # droplet 1: 16 px * mpx^2
    assert out['area_um2'].iloc[1] == pytest.approx(36 * 0.1 ** 2)      # droplet 2: 36 px
    assert 0.0 < out['circularity'].iloc[0] <= 1.0
    assert (out[LAYER_ID_COLUMN] == 'LID-abc').all()                    # bound to the droplet mask layer
    assert out[ENTITY_ID_COLUMN].nunique() == 2                         # a distinct id per droplet
    # the original numbers are untouched (additive only)
    assert list(out['I_dense']) == [100.0, 200.0]
    assert list(out['partition_coefficient']) == [5.0, 10.0]


# ── the droplet image tier ─────────────────────────────────────────────────────────────────────────
def test_a_click_on_a_droplet_selects_its_entity_everywhere():
    df = _brush_ready_df()
    service = _service()
    seen = []
    service.subscribe('probe', lambda st: seen.append(st.primary_id))

    layer = _fake_layer(returns_label=2)               # a click lands on droplet 2
    tier = BrushableImageTier(None, layer, df, service, 'ivf.image', label_col='droplet_label')
    assert tier._cb in layer.mouse_drag_callbacks       # the click handler is wired

    expected = df.loc[df['droplet_label'] == 2, ENTITY_ID_COLUMN].iloc[0]
    tier._on_click(layer, types.SimpleNamespace(position=(12, 12)))
    assert seen[-1] == expected                          # droplet 2's entity was selected
    tier.close()
    assert tier._cb not in layer.mouse_drag_callbacks     # ...and unwired on close


def test_the_droplet_image_tier_satisfies_the_selection_view_contract():
    df = _brush_ready_df()
    service = _service()
    an_entity = df[ENTITY_ID_COLUMN].iloc[0]

    def make_view():
        return BrushableImageTier(None, _fake_layer(returns_label=1), df, service, 'ivf.image',
                                  label_col='droplet_label')

    def do_user_select(view):
        view._on_click(view.labels_layer, types.SimpleNamespace(position=(4, 4)))

    assert_selection_view_contract(
        service, make_view, do_user_select, an_entity=an_entity,
        other_state=SelectionState(selected=frozenset({'x/y/z/9/9'}), primary='x/y/z/9/9'))
