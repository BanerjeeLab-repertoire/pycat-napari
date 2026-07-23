"""**The easiest false result in imaging biology is pseudoreplication — these stats refuse it.**

Increment 3's honest-statistics core. Treat 5 000 puncta from 3 cells as 5 000 independent
observations and any trivial difference becomes p < 10⁻⁹; PyCAT's own doctrine
(`pixel_wise_corr_analysis_tools`) is that the inferential unit is the biological replicate, not the
object. So every comparison aggregates condition×replicate to one value first, then tests.

The roadmap's deliverable named three things these must do, and each is a test below:
* **recover a real effect** (aggregated);
* **not cry significance on a null** — even when the null carries thousands of pseudoreplicated
  objects, which is the case that catches a naive test;
* **aggregate to avoid pseudoreplication** — and, past that, *refuse to infer* when there are too few
  replicates rather than borrow significance from the objects.

All `core` — the statistics are pure and their correctness is exactly what must not be trusted to a
figure someone glances at.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest
from scipy import stats

# Local application imports
from pycat.utils.comparative_stats import (aggregate_to_replicate, compare_conditions,
                                           ComparisonResult)

pytestmark = pytest.mark.base


def _objects(condition, replicate, rep_mean, n=300, spread=5.0, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({'measurement': 'area',
                         'value': rng.normal(rep_mean, spread, n),
                         'genotype': condition, 'replicate': replicate})


def _dataset(spec):
    """spec: list of (condition, replicate, rep_mean). One block of objects each."""
    return pd.concat([_objects(c, r, m, seed=i) for i, (c, r, m) in enumerate(spec)],
                     ignore_index=True)


# ── the anti-pseudoreplication step ────────────────────────────────────────────

def test_aggregation_collapses_objects_to_ONE_value_per_replicate():
    df = _dataset([('WT', 'WT1', 100), ('WT', 'WT2', 105), ('mut', 'mut1', 140)])
    agg = aggregate_to_replicate(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert len(agg) == 3                       # 3 replicates, not 900 objects
    assert set(agg['replicate']) == {'WT1', 'WT2', 'mut1'}
    assert agg.loc[agg.replicate == 'WT1', 'value'].iloc[0] == pytest.approx(100, abs=2)


def test_aggregation_filters_to_the_named_measurement():
    df = pd.concat([_objects('WT', 'WT1', 100).assign(measurement='area'),
                    _objects('WT', 'WT1', 999).assign(measurement='perimeter')],
                   ignore_index=True)
    agg = aggregate_to_replicate(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert agg['value'].iloc[0] == pytest.approx(100, abs=2)   # not 999


# ── the three deliverables ─────────────────────────────────────────────────────

def test_it_does_NOT_cry_significance_on_a_pseudoreplicated_NULL():
    """**The headline.** Replicate means from the SAME distribution — no real difference — but 500
    objects per replicate. A naive test on the objects screams; the honest one does not."""
    rng = np.random.default_rng(1)
    spec = [(c, f'{c}{r}', rng.normal(100, 10)) for c in ('WT', 'mut') for r in range(3)]
    df = pd.concat([_objects(c, rp, m, n=500, seed=i) for i, (c, rp, m) in enumerate(spec)],
                   ignore_index=True)

    # the lie, for contrast: objects treated as independent
    wt = df[df.genotype == 'WT'].value
    mu = df[df.genotype == 'mut'].value
    _, p_pseudo = stats.mannwhitneyu(wt, mu)
    assert p_pseudo < 1e-10, "fixture check: the pseudoreplicated test should be spuriously tiny"

    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert res.inferential
    assert res.p_value > 0.05, (
        f"the replicate-aware test cried significance on a null (p={res.p_value:.3g}) — "
        f"pseudoreplication got through")


def test_it_RECOVERS_a_real_replicate_level_effect():
    rng = np.random.default_rng(2)
    spec = ([('WT', f'WT{r}', rng.normal(100, 6)) for r in range(4)]
            + [('mut', f'mut{r}', rng.normal(150, 6)) for r in range(4)])
    df = pd.concat([_objects(c, rp, m, seed=i) for i, (c, rp, m) in enumerate(spec)],
                   ignore_index=True)

    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert res.inferential and res.p_value < 0.05
    assert res.groups['mut']['mean'] > res.groups['WT']['mean']


def test_it_REFUSES_to_infer_with_too_few_replicates():
    """One replicate per condition: no within-condition variance at the biological level. A p-value
    here could only come from the pseudoreplicated objects, so none is produced."""
    df = _dataset([('WT', 'WT1', 100), ('mut', 'mut1', 140)])
    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')

    assert res.inferential is False
    assert res.p_value is None
    assert 'replicate' in res.note.lower() and 'pseudoreplicate' in res.note.lower()
    # the descriptive summaries still exist — refusing to TEST is not refusing to describe
    assert res.groups['WT']['mean'] == pytest.approx(100, abs=2)
    assert res.groups['WT']['n_objects'] == 300


# ── reporting honesty ───────────────────────────────────────────────────────────

def test_the_result_reports_n_at_BOTH_levels():
    df = _dataset([('WT', 'WT1', 100), ('WT', 'WT2', 102), ('WT', 'WT3', 98),
                   ('mut', 'mut1', 140), ('mut', 'mut2', 142), ('mut', 'mut3', 138)])
    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert res.groups['WT']['n_replicates'] == 3
    assert res.groups['WT']['n_objects'] == 900          # 3 × 300
    assert 'replicates' in res.summary() and 'objects' in res.summary()


def test_the_test_NAME_travels_with_the_result():
    df2 = _dataset([('WT', 'WT1', 100), ('WT', 'WT2', 101), ('mut', 'mut1', 140), ('mut', 'mut2', 141)])
    two = compare_conditions(df2, 'area', condition_col='genotype', replicate_col='replicate')
    assert 'Mann-Whitney' in two.test

    df3 = _dataset([(c, f'{c}{r}', m) for c, m in (('a', 100), ('b', 120), ('c', 140))
                    for r in (0, 1)])
    three = compare_conditions(df3, 'area', condition_col='genotype', replicate_col='replicate')
    assert 'Kruskal' in three.test                       # >2 groups -> Kruskal-Wallis


def test_parametric_switches_the_test_but_not_the_UNIT():
    """Parametric or not, the inferential unit is still the replicate — the choice is t/ANOVA vs
    Mann-Whitney/Kruskal, never object-level vs replicate-level."""
    df = _dataset([('WT', f'WT{r}', 100 + r) for r in range(4)]
                  + [('mut', f'mut{r}', 150 + r) for r in range(4)])
    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate',
                             parametric=True)
    assert "t-test" in res.test
    assert res.groups['WT']['n_replicates'] == 4          # still replicates, not objects


def test_an_underpowered_but_runnable_comparison_is_FLAGGED():
    """2 replicates per group: the test runs, but the result says report the effect size, not just p."""
    df = _dataset([('WT', 'WT1', 100), ('WT', 'WT2', 101),
                   ('mut', 'mut1', 200), ('mut', 'mut2', 201)])
    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert res.inferential
    assert 'underpowered' in res.note.lower()


def test_a_missing_column_is_a_clear_error():
    df = _dataset([('WT', 'WT1', 100)])
    with pytest.raises(KeyError, match='treatment'):
        compare_conditions(df, 'area', condition_col='treatment', replicate_col='replicate')


def test_a_single_condition_is_nothing_to_compare():
    df = _dataset([('WT', 'WT1', 100), ('WT', 'WT2', 102)])
    res = compare_conditions(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert res.inferential is False and 'nothing to compare' in res.note
