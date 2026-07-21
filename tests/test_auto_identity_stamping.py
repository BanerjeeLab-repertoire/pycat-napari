"""**Automatic entity-identity stamping at the result-finalization chokepoint.**

Manual `stamp_entity_ids` reached only 3 sites; every new analysis was one forgotten call away from silent
row-position linking. Identity is now applied by DECLARATION at a chokepoint: an operation declares an
`EntitySpec`, and `finalize_entity_table` (called automatically in `operation_runner`) stamps identity +
location together, with the operation's real id. These pin: a declared operation is stamped automatically;
`operation_id` comes from the declaration (not a literal); per-row frames work; identity and location are
co-generated; an undeclared operation is left untouched; the 3 migrated sites produce identical ids; a
previously-unstamped producer gains identity by declaring a spec; and the runner stamps at finalization.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.utils.entity_ref import (
    ENTITY_ID_COLUMN, LAYER_ID_COLUMN, EntitySpec, entity_spec_for, finalize_entity_table,
    register_entity_spec, stamp_entity_ids)

pytestmark = pytest.mark.core


def _cell_table():
    return pd.DataFrame({'label': [1, 2, 3], 'area': [100.0, 200.0, 150.0]})


# ── A declared operation is stamped automatically, with its real operation_id ───────────────────
def test_a_declared_operation_is_stamped_at_finalization():
    out = finalize_entity_table(_cell_table(), 'cell_analysis')
    assert ENTITY_ID_COLUMN in out.columns
    assert out[ENTITY_ID_COLUMN].notna().all() and (out[ENTITY_ID_COLUMN] != '').all()


def test_operation_id_comes_from_the_declaration_not_a_literal():
    """The same rows stamped under two different declared operations get DIFFERENT ids — proof the id
    carries the operation's real id from the declaration, not a hard-coded string."""
    as_cell = finalize_entity_table(_cell_table(), 'cell_analysis')[ENTITY_ID_COLUMN].tolist()
    as_region = finalize_entity_table(_cell_table(), 'measure_region_props')[ENTITY_ID_COLUMN].tolist()
    assert as_cell != as_region, "operation_id did not flow from the declaration into the id"


# ── Per-row frames: a multi-frame table stamps each row with its OWN frame ───────────────────────
def test_per_row_frames_stamp_each_row_with_its_own_frame():
    # vpt_tracks declares frame_column='frame' (a genuinely multi-frame table).
    assert entity_spec_for('vpt_tracks').frame_column == 'frame'
    tracks = pd.DataFrame({'track_id': [1, 1, 2], 'frame': [0, 1, 0], 'y_um': [1.0, 1.1, 5.0]})
    out = finalize_entity_table(tracks, 'vpt_tracks')
    ids = out[ENTITY_ID_COLUMN].tolist()
    assert ids[0] != ids[1], "the same track at two frames must get DIFFERENT ids (per-row frame)"
    assert ids[0] != ids[2] and ids[1] != ids[2]


# ── Identity and location are generated TOGETHER — no id-without-location row ────────────────────
def test_identity_and_location_are_co_generated():
    out = finalize_entity_table(_cell_table(), 'cell_analysis')
    assert ENTITY_ID_COLUMN in out.columns and LAYER_ID_COLUMN in out.columns   # both columns, one pass
    assert len(out) == 3 and out[ENTITY_ID_COLUMN].notna().all()


# ── An operation that declares nothing is left UNTOUCHED (honestly row-linked) ───────────────────
def test_an_undeclared_operation_is_left_untouched():
    out = finalize_entity_table(_cell_table(), 'some_operation_that_declares_nothing')
    assert ENTITY_ID_COLUMN not in out.columns


# ── The migrated sites produce IDENTICAL ids to the old manual stamp ─────────────────────────────
@pytest.mark.parametrize('operation_id,entity_type,frame', [
    ('cell_analysis', 'cell', 7),
    ('measure_region_props', 'mask_object', None),
])
def test_the_migrated_sites_produce_identical_ids(operation_id, entity_type, frame):
    """The migration changes the ROUTE (a declaration), not the RESULT — byte-identical ids."""
    via_finalize = finalize_entity_table(_cell_table(), operation_id, frame=frame)[ENTITY_ID_COLUMN].tolist()
    via_manual = stamp_entity_ids(_cell_table(), entity_type=entity_type, operation_id=operation_id,
                                  frame=frame)[ENTITY_ID_COLUMN].tolist()
    assert via_finalize == via_manual


def test_puncta_parent_is_auto_detected_identically():
    """Puncta emit a parent column; the migrated route auto-detects it exactly as the manual one did."""
    puncta = pd.DataFrame({'label': [1, 2, 1], 'cell_label': [1, 1, 2], 'intensity': [5.0, 6.0, 7.0]})
    via_finalize = finalize_entity_table(puncta.copy(), 'puncta_analysis', frame=0)[ENTITY_ID_COLUMN].tolist()
    via_manual = stamp_entity_ids(puncta.copy(), entity_type='punctum',
                                  operation_id='puncta_analysis', frame=0)[ENTITY_ID_COLUMN].tolist()
    assert via_finalize == via_manual
    assert via_finalize[0] != via_finalize[2], "label 1 in cell 1 vs cell 2 are DIFFERENT entities"


# ── A previously-unstamped producer gains identity by declaring a spec ───────────────────────────
def test_a_previously_unstamped_producer_gains_identity_by_declaration():
    condensates = pd.DataFrame({'label': [1, 2], 'area': [10.0, 20.0]})
    # 'condensate_analysis' is declared in the defaults — coverage grew by declaration, no new stamp call.
    out = finalize_entity_table(condensates, 'condensate_analysis')
    assert ENTITY_ID_COLUMN in out.columns and out[ENTITY_ID_COLUMN].notna().all()


# ── Idempotent: the runner path and a manual call never double-stamp ─────────────────────────────
def test_finalize_is_idempotent():
    once = finalize_entity_table(_cell_table(), 'cell_analysis')
    ids_once = once[ENTITY_ID_COLUMN].tolist()
    twice = finalize_entity_table(once, 'cell_analysis')       # already stamped → unchanged
    assert twice[ENTITY_ID_COLUMN].tolist() == ids_once


def test_a_newly_registered_spec_takes_effect():
    register_entity_spec('a_test_only_op', EntitySpec('test_thing', label_column='label'))
    try:
        out = finalize_entity_table(_cell_table(), 'a_test_only_op')
        assert ENTITY_ID_COLUMN in out.columns
    finally:
        from pycat.utils import entity_ref as er
        er._ENTITY_SPECS.pop('a_test_only_op', None)


# ── The runner stamps automatically at finalization, driven by the captured operation ───────────
def test_operation_runner_stamps_at_finalization():
    from pycat.utils.operation_runner import OperationRunner
    from pycat.utils.tag_registry import operation_context

    runner = OperationRunner()
    captured = {}

    def on_result(df):
        captured['df'] = df

    # The caller runs under the operation's context; the runner finalizes the DataFrame result there.
    with operation_context('cell_analysis'):
        runner.execute(lambda: _cell_table(), on_result=on_result)

    assert ENTITY_ID_COLUMN in captured['df'].columns, (
        "the runner did not stamp identity at finalization for a declared operation")


def test_the_runner_leaves_a_non_dataframe_and_undeclared_result_alone():
    from pycat.utils.operation_runner import OperationRunner
    from pycat.utils.tag_registry import operation_context
    runner = OperationRunner()

    got = {}
    with operation_context('cell_analysis'):
        runner.execute(lambda: np.array([1, 2, 3]), on_result=lambda r: got.update(r=r))  # not a DataFrame
    assert isinstance(got['r'], np.ndarray)                    # passed through untouched

    with operation_context('an_undeclared_op'):
        runner.execute(lambda: _cell_table(), on_result=lambda r: got.update(df=r))
    assert ENTITY_ID_COLUMN not in got['df'].columns           # undeclared → not stamped


# ── The registry is populated at the SAME chokepoint — one record → id + location ────────────────
def test_finalize_populates_the_registry_with_id_and_location():
    """The divergence closes at the source: stamping a table also registers each row's id → its CURRENT
    location, from one record, so a view holding only the id can resolve where to show it."""
    from pycat.utils.entity_registry import EntityRegistry
    from pycat.utils.entity_ref import populate_registry

    # A table carrying location columns (bbox, frame) — as a real object table does.
    table = pd.DataFrame({'label': [1, 2], 'frame': [3, 5],
                          'bbox': [(0, 0, 4, 4), (10, 10, 14, 14)], 'area': [16.0, 16.0]})
    stamped = finalize_entity_table(table, 'measure_region_props')
    reg = EntityRegistry()
    populate_registry(stamped, registry=reg, operation_id='measure_region_props')

    assert len(reg) == 2
    eid0 = stamped[ENTITY_ID_COLUMN].iloc[0]
    rec = reg.resolve(eid0)
    assert rec is not None and rec.provenance == 'measure_region_props'
    assert rec.location.bbox == (0, 0, 4, 4) and rec.location.frame == 3     # location co-generated with the id


def test_identity_and_location_do_not_diverge_per_row():
    """Per-row frames flow into the registry too: two same-track rows at different frames resolve to
    records with their OWN frame — identity and location generated together, never crossed."""
    from pycat.utils.entity_registry import EntityRegistry
    from pycat.utils.entity_ref import populate_registry

    tracks = pd.DataFrame({'track_id': [1, 1], 'frame': [0, 7], 'bbox': [(0, 0, 2, 2), (0, 0, 2, 2)]})
    stamped = finalize_entity_table(tracks, 'vpt_tracks')
    reg = EntityRegistry()
    populate_registry(stamped, registry=reg)
    r0 = reg.resolve(stamped[ENTITY_ID_COLUMN].iloc[0])
    r1 = reg.resolve(stamped[ENTITY_ID_COLUMN].iloc[1])
    assert r0.location.frame == 0 and r1.location.frame == 7


def test_an_unstamped_table_registers_nothing():
    from pycat.utils.entity_registry import EntityRegistry
    from pycat.utils.entity_ref import populate_registry
    reg = EntityRegistry()
    populate_registry(pd.DataFrame({'area': [1.0, 2.0]}), registry=reg)   # no _pycat_entity_id
    assert len(reg) == 0


def test_the_default_registry_resolves_a_chokepoint_stamped_row():
    from pycat.utils.entity_registry import default_registry
    stamped = finalize_entity_table(_cell_table(), 'cell_analysis', frame=2)
    eid = stamped[ENTITY_ID_COLUMN].iloc[0]
    assert default_registry().resolve(eid) is not None       # the chokepoint populated the shared authority
