"""**The generic plot is a view on the one selection bus** (Completion Spec F5).

`PlottingWidget._wire_brushing` called `make_pickable` without the service, so the only "linking" most pipelines
had was one-way and keyed on row position. With the service, a pick publishes the point's ``_pycat_entity_id``
and a selection made elsewhere marks the point carrying that id -- whatever the row order.
"""
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import pytest

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.selection_service import SelectionService

pytestmark = pytest.mark.base


def _stamped():
    return pd.DataFrame({'label': [1, 2, 3], 'area': [10.0, 30.0, 20.0], 'mean': [1.0, 3.0, 2.0],
                         ENTITY_ID_COLUMN: ['d/op/cell/-/1', 'd/op/cell/-/2', 'd/op/cell/-/3']})


def _plot(df, service):
    from pycat.utils.brushing import make_pickable
    from pycat.utils.object_ref import refs_from_dataframe
    fig, ax = plt.subplots()
    artist = ax.scatter(df['area'], df['mean'], picker=5)
    state_fig = make_pickable(fig, artist, refs_from_dataframe(df), service=service, view_id='plot.test')
    return fig, artist, state_fig


def test_a_pick_publishes_the_points_entity_id():
    service = SelectionService(defer=lambda fn: fn())
    df = _stamped().sort_values('area', ascending=False).reset_index(drop=True)    # row order != label order
    fig, artist, _ = _plot(df, service)
    pick = next(cid for cid, ref in fig.canvas.callbacks.callbacks['pick_event'].items())
    handler = fig.canvas.callbacks.callbacks['pick_event'][pick]()
    handler(SimpleNamespace(artist=artist, ind=[0], mouseevent=None, canvas=fig.canvas))
    assert service.state.primary_id == 'd/op/cell/-/2'                   # row 0 after the sort is label 2
    plt.close(fig)


def test_a_selection_elsewhere_marks_the_point_with_that_id_and_close_unsubscribes():
    service = SelectionService(defer=lambda fn: fn())
    df = _stamped().sort_values('area').reset_index(drop=True)           # labels 1, 3, 2
    fig, artist, _ = _plot(df, service)
    service.select_entity('d/op/cell/-/2', source='table')
    assert fig._pycat_brush_state['indices'] == [2]
    from pycat.utils.brushing import _teardown_pickable
    _teardown_pickable(fig)
    assert 'plot.test' not in service._subscribers
    plt.close(fig)
