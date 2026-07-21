"""**Biological QC, Part B: the flags must SURFACE — in the consolidated table and the QC report.**

The object-level QC module (`biological_qc_tools`) shipped in 1.6.152 as a reusable core, but its flags
were computed nowhere a scientist would see them. Part B wires them into the two places that matter:

1. **The consolidated long table** gains a `qc_flags` column, so a condition comparison can be recomputed
   with and without flagged objects — *"the effect holds when edge-touching cells are excluded"* is a
   stronger claim than an unqualified one.
2. **The QC report** gains an object-level section stating counts per flag, in the same
   Assessment → Interpretation → Recommendation shape the imaging checks use.

The cardinal contract carries through both: **flag, never filter.** No row is ever dropped for tripping a
flag — the surfacing is additive. These tests pin exactly that, plus the cry-wolf property (a clean
population surfaces nothing) and the definitive-vs-hint wording of the flags.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core


# A population with ONE injected size outlier (object 10). n>=3 so robust MAD can act; the rest are a
# tight cluster so the one huge object is unambiguous, and intensity is constant so ONLY size trips.
def _seeded_population():
    return pd.DataFrame({
        'object_id':      list(range(1, 11)),
        'label':          list(range(1, 11)),
        'area':           [10, 11, 9, 10, 12, 10, 11, 9, 10, 1000.0],
        'intensity_mean': [100.0] * 10,
    })


def _clean_population():
    return pd.DataFrame({
        'object_id':      [1, 2, 3, 4, 5],
        'label':          [1, 2, 3, 4, 5],
        'area':           [10.0, 11.0, 9.0, 10.5, 10.2],
        'intensity_mean': [100.0, 101.0, 99.0, 100.5, 100.2],
    })


# ── The consolidated long table gains qc_flags ─────────────────────────────────────────────────

def test_the_consolidated_schema_gains_qc_flags_at_the_end():
    from pycat.utils.consolidated_table import consolidated_columns
    cols = consolidated_columns(['genotype'])
    assert cols[-1] == 'qc_flags', "qc_flags must ride at the END so the change is purely additive"
    # the pre-existing schema is untouched ahead of it
    assert 'measurement' in cols and 'value' in cols and 'units' in cols


def test_a_seeded_outlier_is_FLAGGED_in_the_consolidated_table():
    from pycat.utils.consolidated_table import build_image_long_table
    tbl = build_image_long_table([('cell', _seeded_population())], image_stem='x', condition_fields=[])
    flagged = tbl.loc[tbl['qc_flags'] != '', ['object_id', 'qc_flags']].drop_duplicates()
    assert list(flagged['object_id']) == [10], "exactly the injected outlier should be flagged"
    assert 'unusual size' in flagged['qc_flags'].iloc[0]


def test_a_CLEAN_population_surfaces_NO_flags_the_cry_wolf_test():
    from pycat.utils.consolidated_table import build_image_long_table
    tbl = build_image_long_table([('cell', _clean_population())], image_stem='x', condition_fields=[])
    assert set(tbl['qc_flags']) == {''}, "a clean population must trip nothing — false alarms erode trust"


def test_surfacing_flags_NEVER_drops_a_row():
    """The Part-B contract: flag, don't filter. The long table has exactly one row per object per
    numeric measurement, flagged objects included."""
    from pycat.utils.consolidated_table import build_image_long_table
    pop = _seeded_population()
    flagged = build_image_long_table([('cell', pop)], image_stem='x', condition_fields=[])
    unflagged = build_image_long_table([('cell', pop)], image_stem='x', condition_fields=[], qc=False)
    # Attaching flags must not change the row set: same length with QC on as with QC off.
    assert len(flagged) == len(unflagged), "surfacing a flag changed the row count — it filtered"
    assert 10 in set(flagged['object_id']), "the flagged object is present, not filtered out"


def test_an_upstream_qc_flags_column_is_CARRIED_THROUGH():
    """Mask-based flags (edge/containment) are computed upstream where the label image lives and stamped
    onto the table as qc_flags; the consolidated builder must carry that through, not overwrite it."""
    from pycat.utils.consolidated_table import build_image_long_table
    up = pd.DataFrame({'object_id': [1, 2], 'area': [10.0, 20.0],
                       'qc_flags': ['touches image border', '']})
    tbl = build_image_long_table([('cell', up)], image_stem='z', condition_fields=[])
    assert set(tbl.loc[tbl.object_id == 1, 'qc_flags']) == {'touches image border'}
    assert set(tbl.loc[tbl.object_id == 2, 'qc_flags']) == {''}


def test_qc_can_be_turned_OFF_and_the_column_is_blank():
    from pycat.utils.consolidated_table import build_image_long_table
    tbl = build_image_long_table([('cell', _seeded_population())], image_stem='x',
                                 condition_fields=[], qc=False)
    assert set(tbl['qc_flags']) == {''}, "qc=False must surface no flags (opt-out)"


def test_the_streaming_writer_carries_qc_flags_to_disk(tmp_path):
    from pycat.utils.consolidated_table import ConsolidatedLongWriter
    path = tmp_path / 'consolidated_long.csv'
    w = ConsolidatedLongWriter(path, condition_fields=['genotype'])
    w.add_image('imgA', [('cell', _seeded_population())])
    got = pd.read_csv(path, keep_default_na=False)
    assert 'qc_flags' in got.columns
    assert set(got.loc[got.object_id == 10, 'qc_flags']) == {'unusual size'}


# ── The QC report gains an object-level section ────────────────────────────────────────────────

def test_the_report_section_counts_each_flag():
    from pycat.toolbox.data_qc_tools import qc_biological_objects
    labels = np.zeros((50, 50), int)
    labels[0:5, 0:5] = 1        # object 1 touches the top-left edge
    labels[20:25, 20:25] = 2    # object 2 interior
    section = qc_biological_objects(_seeded_population().head(2).assign(label=[1, 2]), labels=labels)

    names = [r['name'] for r in section]
    assert names[0] == 'Object QC (biological)'
    # every result carries the report shape the imaging checks use
    for r in section:
        assert set(('name', 'status', 'headline', 'how', 'good')) <= set(r)
    edge = next(r for r in section if 'border' in r['name'])
    assert edge['headline'].startswith('1 of 2')


def test_edge_touching_is_DEFINITIVE_but_outliers_are_HINTS():
    """Wording contract: a truncated object is objectively wrong (can read 'bad'); a size/shape/intensity
    outlier is a review hint and never escalates past 'warn' — a mitotic cell is real data."""
    from pycat.toolbox.data_qc_tools import qc_biological_objects
    # a population where MANY objects are size outliers cannot push size past 'warn'
    pop = pd.DataFrame({'object_id': list(range(1, 7)), 'label': list(range(1, 7)),
                        'area': [10, 10, 10, 1000, 1000, 1000.0]})
    section = qc_biological_objects(pop)
    size = next((r for r in section if 'size' in r['name']), None)
    assert size is not None and size['status'] in ('good', 'warn'), "an outlier flag must not read 'bad'"


def test_an_empty_object_table_is_NA_not_a_clean_pass():
    from pycat.toolbox.data_qc_tools import qc_biological_objects
    section = qc_biological_objects(pd.DataFrame())
    assert len(section) == 1 and section[0]['status'] == 'na'


def test_run_full_qc_appends_the_section_ONLY_when_a_table_is_given():
    from pycat.toolbox.data_qc_tools import run_full_qc
    img = np.random.default_rng(0).normal(500, 20, (64, 64)).astype(np.float32)
    without = {r['name'] for r in run_full_qc(img)}
    with_tbl = {r['name'] for r in run_full_qc(img, object_table=_seeded_population())}
    assert 'Object QC (biological)' not in without, "no object table → no object section (additive)"
    assert 'Object QC (biological)' in with_tbl, "an object table → the section appears"


def test_the_QC_UI_WIRES_the_object_table_into_the_report():
    """AST: `data_qc_ui` (Qt-bound, not importable headless) must build the object table and pass it to
    `run_full_qc` — a report section that exists but is never fed live data is the gap this closes."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
           / 'toolbox' / 'data_qc_ui.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    # the run_full_qc(...) call must pass an `object_table=` keyword
    run_calls = [c for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and getattr(c.func, 'id', None) == 'run_full_qc']
    assert run_calls, "the QC UI never calls run_full_qc"
    assert any(kw.arg == 'object_table' for call in run_calls for kw in call.keywords), \
        "the QC UI does not pass object_table into run_full_qc — the object section stays empty"


def test_run_full_qc_never_raises_on_a_malformed_object_table():
    """The object-QC add-on must never break the imaging report."""
    from pycat.toolbox.data_qc_tools import run_full_qc
    img = np.random.default_rng(0).normal(500, 20, (64, 64)).astype(np.float32)
    # a table with no recognisable measurement columns must degrade gracefully, not crash the report
    results = run_full_qc(img, object_table=pd.DataFrame({'nonsense': [1, 2, 3]}))
    assert any(r['name'] == 'Saturation / clipping' for r in results), "imaging report survived"
