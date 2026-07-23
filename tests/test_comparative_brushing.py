"""**Increment 3, Part D: single-entity brushing on comparative figures — and the `entity_id` that unblocks it.**

The blocker was that the consolidated table carried `image_stem`/`object_id` but not the global entity id
the `SelectionService` speaks. The object tables were already stamped with `_pycat_entity_id`; the melt
just dropped it. Now it is carried through into an `entity_id` column, so a click on an object point in a
comparative plot routes through the EXISTING selection contract — no second selection path. Cohort
selection (clicking a unit marker) stays the noted-blocked seam (typed/cohort target still deferred).
"""
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import pytest

from pycat.utils.consolidated_table import melt_object_measurements
from pycat.utils.comparative_figures import condition_comparison
from pycat.utils.selection_service import SelectionService
from pycat.utils.entity_ref import ENTITY_ID_COLUMN

pytestmark = pytest.mark.base


def test_melt_carries_the_entity_id_through():
    df = pd.DataFrame({'object_id': [1, 2], 'area': [10.0, 20.0],
                       ENTITY_ID_COLUMN: ['ds/obj/1', 'ds/obj/2']})
    long = melt_object_measurements(df, 'punctum')
    assert 'entity_id' in long.columns
    assert set(long['entity_id']) == {'ds/obj/1', 'ds/obj/2'}
    assert ENTITY_ID_COLUMN not in set(long['measurement'])   # the id column is not melted as a measurement


def test_melt_blank_entity_id_when_the_table_was_never_stamped():
    long = melt_object_measurements(pd.DataFrame({'object_id': [1], 'area': [10.0]}), 'punctum')
    assert (long['entity_id'] == '').all()


def _long():
    rows, eid = [], 0
    for cond in ('WT', 'mut'):
        for rep in ('r1', 'r2'):
            for k in range(4):
                rows.append(dict(condition=cond, image_stem=rep, measurement='area',
                                 value=(10 if cond == 'WT' else 20) + k, entity_id=f"ds/obj/{eid}"))
                eid += 1
    return pd.DataFrame(rows)


def test_object_click_emits_that_entity_through_the_service():
    svc = SelectionService()
    got = []
    svc.subscribe('image', lambda st: got.append(tuple(getattr(st, 'entity_ids', ()))))
    fig, _ = condition_comparison(_long(), measurement='area', condition_cols=['condition'],
                                  unit_cols=['image_stem'], selection_service=svc)
    br = fig._pycat_brushing
    ax = fig.axes[0]
    px, py = ax.transData.transform((0.0, 10.0))          # near a WT object at x=0
    emitted = br['emit_nearest'](px, py)
    assert emitted is not None
    assert got and got[-1] == (emitted,)                  # propagated to the other view


def test_a_selection_from_another_view_rings_the_point():
    svc = SelectionService()
    fig, _ = condition_comparison(_long(), measurement='area', condition_cols=['condition'],
                                  unit_cols=['image_stem'], selection_service=svc)
    ax = fig.axes[0]

    class _State:
        entity_ids = ('ds/obj/0',)

    fig._pycat_brushing['apply_selection'](_State())
    assert any(l.get_markeredgecolor() == '#ff8c00' for l in ax.lines)


def test_no_brushing_wired_without_a_service():
    fig, _ = condition_comparison(_long(), measurement='area', condition_cols=['condition'])
    assert not hasattr(fig, '_pycat_brushing')


def test_clicking_a_condition_selects_it_as_a_COHORT():
    """The cohort-spec's box/violin case: clicking a condition's unit-mean marker selects the whole
    GROUP — a cohort of that condition's objects, carrying the condition as its definition — while an
    object point still selects one entity (nearest-wins)."""
    from pycat.utils.selection_service import SelectionService, Cohort
    svc = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: fn())
    got = []
    svc.subscribe('img', lambda st: got.append(st))
    fig, _ = condition_comparison(_long(), measurement='area', condition_cols=['condition'],
                                  unit_cols=['image_stem'], selection_service=svc)
    ax = fig.axes[0]
    px, py = ax.transData.transform((0.0, 11.5))       # WT unit-mean marker (r1/r2 means both 11.5)
    res = fig._pycat_brushing['emit_nearest'](px, py)
    assert isinstance(res, Cohort) and res.kind == 'group' and res.n == 8 and 'WT' in res.definition
    # propagated to the other view: cohort carried AND its members ride in `selected` for degradation
    assert got[-1].cohort is not None and len(got[-1].selected) == 8


def test_ui_condition_fields_excludes_the_fixed_schema():
    from pycat.ui.comparative_figures_ui import _condition_fields
    from pycat.utils.consolidated_table import consolidated_columns
    cols = consolidated_columns(['genotype', 'dose'])
    assert _condition_fields(cols) == ['genotype', 'dose']
