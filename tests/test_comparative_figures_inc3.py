"""**Comparative figures on the consolidated table — replicate-honest by construction (inc 3).**

The spec's contract for `comparative_figures`: three figure types, each returning `(Figure, summary_df)`
so the numbers are inspectable; every figure aggregates to a DECLARED biological unit and reports n at
each level; statistics are descriptive by default and, when asked for, run on the aggregated units and
refuse loudly below the minimum.

The headline test is the anti-pseudoreplication one: 450 objects from 3 replicates must report n=3 and
an error bar that is MATERIALLY WIDER than the (dishonest) object-level SEM — proven, not commented.
"""

import matplotlib
matplotlib.use('Agg')

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

from pycat.utils.comparative_figures import (
    aggregate_to_unit, condition_comparison, dose_response, measurement_matrix)


def _two_condition_long(effect=30.0, n_rep=3, n_obj=150, rep_sd=8.0, obj_sd=3.0, seed=0):
    """A tidy long table, two conditions × `n_rep` replicates × `n_obj` objects. `mut` is `effect`
    above `WT` at the replicate level; within-replicate object scatter is `obj_sd`."""
    rng = np.random.default_rng(seed)
    rows = []
    for cond, base in (('WT', 100.0), ('mut', 100.0 + effect)):
        for rep in range(n_rep):
            rep_mean = base + rng.normal(0, rep_sd)
            for obj in range(n_obj):
                rows.append(dict(image_stem=f'{cond}{rep}', genotype=cond, replicate=f'{cond}{rep}',
                                 measurement='area', object_id=obj,
                                 value=rep_mean + rng.normal(0, obj_sd)))
    return pd.DataFrame(rows)


def test_all_three_figures_return_a_Figure_AND_its_summary_frame():
    from matplotlib.figure import Figure
    long = _two_condition_long()
    for fig, summ in (
        condition_comparison(long, measurement='area', condition_cols=['genotype'],
                             unit_cols=['replicate']),
        dose_response(long.assign(genotype=long['replicate'].str[-1]),
                      measurement='area', dose_col='genotype', unit_cols=['replicate']),
        measurement_matrix(long, measurements=['area'], condition_cols=['genotype'],
                           unit_cols=['replicate']),
    ):
        assert isinstance(fig, Figure)
        assert isinstance(summ, pd.DataFrame) and not summ.empty, "the numbers must be inspectable"


def test_the_condition_effect_is_recovered_in_the_summary():
    long = _two_condition_long(effect=30.0)
    _fig, summ = condition_comparison(long, measurement='area', condition_cols=['genotype'],
                                      unit_cols=['replicate'])
    means = summ.set_index('condition')['mean']
    assert means['mut'] > means['WT'], "the known condition effect was not recovered"


def test_AGGREGATION_is_enforced_the_error_bar_is_replicate_wide_not_object_wide():
    """**The anti-pseudoreplication test.** 450 objects from 3 replicates → n_units=3, and the reported
    unit-level SEM is materially WIDER than the object-level SEM a pseudoreplicated analysis would use.
    A comment is not proof; this measures it."""
    long = _two_condition_long(n_rep=3, n_obj=150)
    _fig, summ = condition_comparison(long, measurement='area', condition_cols=['genotype'],
                                      unit_cols=['replicate'])
    wt = summ.set_index('condition').loc['WT']
    assert wt['n_objects'] == 450 and wt['n_units'] == 3, "n must be reported at both levels"

    objs = long[long.genotype == 'WT']['value']
    naive_object_sem = objs.std(ddof=1) / np.sqrt(len(objs))
    assert wt['sem_units'] > 3.0 * naive_object_sem, (
        f"the unit-level SEM ({wt['sem_units']:.3g}) is not materially wider than the object-level SEM "
        f"({naive_object_sem:.3g}) — the error bar is still pseudoreplicated")


def test_statistics_are_DESCRIPTIVE_by_default_no_p_value_unless_asked():
    long = _two_condition_long()
    _fig, summ = condition_comparison(long, measurement='area', condition_cols=['genotype'],
                                      unit_cols=['replicate'])                       # test defaults False
    assert summ.attrs['test'] is None and summ.attrs['p_value'] is None, (
        "a p-value appeared without being requested — descriptive is the default")


def test_a_requested_test_runs_on_the_UNITS_and_is_named():
    long = _two_condition_long(effect=40.0, rep_sd=3.0)
    _fig, summ = condition_comparison(long, measurement='area', condition_cols=['genotype'],
                                      unit_cols=['replicate'], test=True)
    assert summ.attrs['inferential'] is True
    assert 'replicate means' in (summ.attrs['test'] or '').lower(), "the test must name its unit"


def test_too_few_units_gets_a_STATED_REFUSAL_not_a_p_value():
    """A condition with one replicate has no replicate-level variance; the honest answer is a refusal
    naming pseudoreplication, never a p-value borrowed from the objects."""
    long = _two_condition_long(n_rep=1, n_obj=300)              # 1 replicate per condition
    _fig, summ = condition_comparison(long, measurement='area', condition_cols=['genotype'],
                                      unit_cols=['replicate'], test=True)
    assert summ.attrs['inferential'] is False
    assert summ.attrs['p_value'] is None
    assert 'pseudoreplicate' in (summ.attrs['note'] or '').lower()


def test_aggregate_to_unit_collapses_objects_to_one_value_per_unit():
    long = _two_condition_long(n_rep=3, n_obj=100)
    units = aggregate_to_unit(long, measurement='area', unit_cols=['replicate'],
                              condition_cols=['genotype'])
    assert len(units) == 6, "2 conditions × 3 replicates = 6 units, not 600 objects"
    assert set(units.columns) == {'condition', 'unit', 'unit_value'}


def test_measurement_matrix_stacks_per_measurement_rows():
    long = pd.concat([
        _two_condition_long().assign(measurement='area'),
        _two_condition_long(effect=-20.0, seed=1).assign(measurement='intensity'),
    ], ignore_index=True)
    _fig, summ = measurement_matrix(long, measurements=['area', 'intensity'],
                                    condition_cols=['genotype'], unit_cols=['replicate'])
    assert set(summ['measurement']) == {'area', 'intensity'}
    assert {'n_objects', 'n_units', 'mean', 'sem_units'}.issubset(summ.columns)
