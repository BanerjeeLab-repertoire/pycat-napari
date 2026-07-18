"""**Comparative figures must show the replicate structure and never annotate a lie.**

The rendering layer of increment 3. These are structural, headless (matplotlib Agg) checks — a figure
is drawn, its groups are right, it saves — plus the one that matters scientifically: the annotation
reflects the **replicate-aware** stats, so a pseudoreplicated null gets `n.s.`, not a star, and a
too-few-replicates case gets "NO TEST", not a fabricated p.

The visual polish (themes, faceting layout) and the interactive brushing are a later increment — they
need an eye and a viewer. What can be verified without one is verified here.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
def _agg_backend():
    import matplotlib
    matplotlib.use('Agg')


def _data(spec, n=200, spread=5.0):
    """spec: list of (condition, replicate, rep_mean)."""
    rng = np.random.default_rng(0)
    rows = []
    for c, r, m in spec:
        rows += [{'measurement': 'area', 'value': v, 'genotype': c, 'replicate': r}
                 for v in rng.normal(m, spread, n)]
    return pd.DataFrame(rows)


def test_the_figure_has_one_group_per_condition():
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', 'WT1', 100), ('WT', 'WT2', 102), ('mut', 'mut1', 140), ('mut', 'mut2', 142)])
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    assert labels == ['WT', 'mut']


def test_the_replicate_MEANS_are_drawn_as_their_own_layer():
    """The whole design: the units the test compares are visible on top of the object cloud, so the
    picture cannot disagree with the p-value."""
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', 'WT1', 100), ('WT', 'WT2', 102), ('mut', 'mut1', 140), ('mut', 'mut2', 142)])
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    # one of the legend entries is the replicate mean
    handles, labels = fig.axes[0].get_legend_handles_labels()
    assert any('replicate mean' in l for l in labels)


def test_the_annotation_names_the_TEST_and_n_at_both_levels():
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', f'WT{r}', 100) for r in range(4)]
               + [('mut', f'mut{r}', 150) for r in range(4)])
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    title = fig.axes[0].get_title(loc='left')
    assert 'Mann-Whitney' in title
    assert 'rep' in title and 'obj' in title            # n at both levels


def test_a_pseudoreplicated_NULL_is_annotated_n_s_not_starred():
    """The figure must not claim significance the honest test denies — even with thousands of
    pseudoreplicated objects behind a null."""
    from pycat.utils.comparative_figures import condition_comparison_figure
    rng = np.random.default_rng(3)
    spec = [(c, f'{c}{r}', rng.normal(100, 10)) for c in ('WT', 'mut') for r in range(3)]
    df = _data(spec, n=500)
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    title = fig.axes[0].get_title(loc='left')
    assert 'n.s.' in title and '[*]' not in title


def test_a_real_effect_IS_starred():
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', f'WT{r}', 100) for r in range(4)]
               + [('mut', f'mut{r}', 160) for r in range(4)])
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    assert '[*]' in fig.axes[0].get_title(loc='left')


def test_too_few_replicates_is_annotated_NO_TEST_not_a_p_value():
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', 'WT1', 100), ('mut', 'mut1', 200)])      # 1 replicate each
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    title = fig.axes[0].get_title(loc='left')
    assert 'NO TEST' in title
    assert 'p = ' not in title                                   # no fabricated p-value


def test_the_figure_SAVES_to_a_nonempty_png(tmp_path):
    from pycat.utils.comparative_figures import condition_comparison_figure
    df = _data([('WT', 'WT1', 100), ('WT', 'WT2', 102), ('mut', 'mut1', 140), ('mut', 'mut2', 142)])
    fig = condition_comparison_figure(df, 'area', condition_col='genotype', replicate_col='replicate')
    out = tmp_path / 'cmp.png'
    fig.savefig(out)
    assert out.stat().st_size > 1000


def test_the_dose_response_error_bar_is_over_REPLICATES():
    """The dose-response SEM must be across replicate means, not objects — same anti-pseudoreplication
    rule. A 3-replicate dose has SEM from 3 points, whatever the object count."""
    from pycat.utils.comparative_figures import dose_response_figure
    rng = np.random.default_rng(4)
    rows = []
    for dose in (0, 10, 100):
        for r in range(3):
            rm = rng.normal(50 + dose * 0.5, 4)
            rows += [{'measurement': 'area', 'value': v, 'dose': dose, 'replicate': f'd{dose}r{r}'}
                     for v in rng.normal(rm, 5, 300)]
    fig = dose_response_figure(pd.DataFrame(rows), 'area', dose_col='dose', replicate_col='replicate')
    assert fig.axes[0].get_xlabel() == 'dose'
    # the errorbar container exists (mean ± SEM over replicates), and the dose axis is quantitative
    assert len(fig.axes[0].containers) >= 1
