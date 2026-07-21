"""**Explicit condensate modes — refuse a volume fraction in 2D, compute the true one in 3D, label everything.**

The load-bearing assertions: a 2D-mode volume-fraction request returns NaN *with a reason*, never a
fabricated estimate; a 3D z-stack of known spheres yields a true volume fraction that differs materially
from the projected area fraction (which is why the distinction matters); the mode is never silently inferred
for an ambiguous 3D array; the `condensate_mode` qualifier travels on every emitted table; the projection
caveat is retrievable from the ontology (data, not a UI string); and a time series declares itself one
biological unit so comparative aggregation does not pseudoreplicate it.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.condensate_modes import (
    CondensateMode, annotate_summary_table, attach_mode_column, is_pseudoreplicated,
    mark_timeseries_as_unit, projected_area_fraction, quantity_status, resolve_condensate_mode,
    volume_fraction)
from pycat.utils.errors import ScientificAssumptionError

pytestmark = pytest.mark.core


# ── Mode resolution: declared or derived, never guessed for an ambiguous 3D array ───────────────
def test_mode_is_derived_for_2d_and_refused_for_ambiguous_3d():
    assert resolve_condensate_mode(np.zeros((32, 32))) == CondensateMode.FIELD_2D
    assert resolve_condensate_mode(np.zeros((5, 32, 32)), axis_kind='z') == CondensateMode.ZSTACK_3D
    assert resolve_condensate_mode(np.zeros((5, 32, 32)), axis_kind='t') == CondensateMode.TIMESERIES
    assert resolve_condensate_mode(np.zeros((5, 32, 32)), declared='timeseries') == CondensateMode.TIMESERIES

    with pytest.raises(ScientificAssumptionError, match='ambiguous'):
        resolve_condensate_mode(np.zeros((5, 32, 32)))          # 3D, no axis_kind → refuse to guess z vs t


# ── THE refusal: volume fraction in 2D is NaN + a reason, never a number ─────────────────────────
def test_volume_fraction_is_refused_in_2d_with_a_reason():
    mask = np.zeros((32, 32), bool); mask[10:20, 10:20] = True
    value, reason = volume_fraction(mask, CondensateMode.FIELD_2D)
    assert np.isnan(value)
    assert 'not' in reason.lower() and 'assumptions' in reason.lower()
    assert quantity_status('volume_fraction', CondensateMode.FIELD_2D) == 'refused'


# ── 3D: the true volume fraction is recovered and DIFFERS from the projected area fraction ──────
def test_3d_volume_fraction_differs_from_the_projected_area_fraction():
    Z, H, W = 40, 40, 40
    zz, yy, xx = np.mgrid[0:Z, 0:H, 0:W]
    # Two solid spheres in the volume — a known geometry.
    stack = np.zeros((Z, H, W), bool)
    for cz, cy, cx, r in ((12, 12, 12, 7), (28, 26, 26, 6)):
        stack |= (zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2 <= r ** 2

    vf, reason = volume_fraction(stack, CondensateMode.ZSTACK_3D)
    assert reason == '' and 0 < vf < 1
    # Known: (4/3)πr³ voxels — recovered within a discretization tolerance.
    expected = sum((4 / 3) * np.pi * r ** 3 for r in (7, 6)) / (Z * H * W)
    assert abs(vf - expected) / expected < 0.1

    # The projected (max-projection) area fraction is materially LARGER — a sphere fills less of its
    # bounding cube than its shadow fills the bounding square, and axial gaps fill in on projection.
    paf = projected_area_fraction(stack.any(axis=0))
    assert paf > vf * 1.5, (
        f"projected area fraction {paf:.3f} should materially exceed the volume fraction {vf:.3f} — that "
        "difference is the whole reason a projected fraction must not be reported as a volume fraction")


# ── The condensate_mode qualifier travels on every emitted table ────────────────────────────────
def test_the_mode_column_is_attached_to_emitted_tables():
    table = pd.DataFrame({'label': [1, 2], 'projected_area_fraction': [0.1, 0.2]})
    out = attach_mode_column(table, CondensateMode.FIELD_2D)
    assert list(out['condensate_mode']) == ['2d', '2d']
    assert 'projected_area_fraction' in out.columns        # the number itself is unchanged (labelling, not recomputation)


# ── The projection caveat is DATA in the ontology, not a UI string ──────────────────────────────
def test_the_projection_caveat_is_retrievable_from_the_ontology():
    from pycat.utils.measurement_ontology import describe
    m = describe('projected_area_fraction')
    assert m is not None
    caveats = ' '.join(m.caveats)
    assert 'PROJECTION' in caveats and 'volume fraction' in caveats


# ── Time-series independence: one series aggregates as ONE unit, not N frames ────────────────────
def test_a_time_series_aggregates_as_one_biological_unit():
    from pycat.utils.comparative_figures import aggregate_to_unit

    assert is_pseudoreplicated(CondensateMode.TIMESERIES)
    assert not is_pseudoreplicated(CondensateMode.FIELD_2D)

    # A per-frame table for one droplet population over 20 frames — NOT 20 independent samples.
    frames = pd.DataFrame({
        'frame': range(20),
        'measurement': ['projected_area_fraction'] * 20,
        'value': np.linspace(0.10, 0.14, 20),      # a slow drift, one population
        'condition': ['ctrl'] * 20,
    })
    marked = mark_timeseries_as_unit(frames, series_id='movie_01')
    assert marked['pseudoreplicated'].all() and (marked['condensate_mode'] == 'timeseries').all()

    agg = aggregate_to_unit(marked, measurement='projected_area_fraction',
                            unit_cols=['biological_unit'], condition_cols=['condition'])
    assert len(agg) == 1, (
        f"the 20-frame series must collapse to ONE biological unit, got {len(agg)} — treating a drifting "
        "population's frames as independent replicates is the pseudoreplication this guards against")


# ── wire_orphans B2: the invitro field-summary tables carry the mode qualifier ───────────────────
def test_annotate_summary_table_labels_the_mode_and_notes_the_2d_refusal_additively():
    """The invitro whole-field summary is a 2D projection: annotating its table must add the mode and a
    volume-fraction refusal note WITHOUT touching the existing (byte-identical) columns."""
    mask2d = np.zeros((32, 32), dtype=int); mask2d[8:16, 8:16] = 1
    summ = pd.DataFrame([{'projected_area_fraction': 0.0625, 'volume_fraction': 0.0625, 'n_droplets': 1}])
    out = annotate_summary_table(summ, mask2d)
    # additive: originals untouched
    assert list(summ.columns) == ['projected_area_fraction', 'volume_fraction', 'n_droplets']
    assert out['projected_area_fraction'].iloc[0] == 0.0625
    # added: the qualifier now travels with the table
    assert out['condensate_mode'].iloc[0] == '2d'
    assert 'volume_fraction_note' in out.columns
    assert 'not' in out['volume_fraction_note'].iloc[0].lower() and \
           'volume fraction' in out['volume_fraction_note'].iloc[0].lower()


def test_a_true_3d_summary_gets_the_mode_but_NO_refusal_note():
    """In a z-stack the volume fraction is real, so no refusal note is added — only the mode label."""
    masks3d = np.zeros((4, 16, 16), dtype=int); masks3d[1:3, 4:12, 4:12] = 1
    summ = pd.DataFrame([{'projected_area_fraction': 0.25}])
    out = annotate_summary_table(summ, masks3d, axis_kind='z')
    assert out['condensate_mode'].iloc[0] == '3d'
    assert 'volume_fraction_note' not in out.columns


def test_both_invitro_field_summaries_wire_the_mode_annotation():
    """AST: the fluorescence AND brightfield in-vitro field-summary handlers (Qt-bound, not run in core)
    must call annotate_summary_table on their emitted table — else the orphan is unwired again."""
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox'
    for fname in ('invitro_fluor_ui.py', 'invitro_bf_ui.py'):
        src = (root / fname).read_text(encoding='utf-8')
        called = any(
            isinstance(c, ast.Call) and
            (getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)) == 'annotate_summary_table'
            for c in ast.walk(ast.parse(src)))
        assert called, f"{fname} no longer annotates its field-summary table with the condensate mode"
