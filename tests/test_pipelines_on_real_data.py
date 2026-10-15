"""**Every pipeline on real data, through every route** (Completion Spec Phase 0.3).

Skipped when the corpus is absent (``tests/data/fetch.py``); run on a schedule, not per commit. Each pipeline's
four route cells must hold a verdict, and none may be ``fail``: a batch table that lost a column or changed its
golden master, a headless computation that disagrees with batch, a session that does not restore the table.
``not-implemented`` and ``no-data`` are honest gaps, reported in ``docs/pipeline_route_matrix.md``; they turn
into passes as the gaps close (the matrix is how later phases are scored).
"""
import pytest

pytestmark = [pytest.mark.realdata, pytest.mark.integration, pytest.mark.slow]


def _cases():
    from tests.realdata_harness import CASES
    return CASES


@pytest.fixture(scope='module', autouse=True)
def _opt_in(request):
    """Minutes of Cellpose per run: only when asked for (``-m realdata`` or ``PYCAT_REALDATA=1``)."""
    import os
    if 'realdata' not in (request.config.option.markexpr or '') and not os.environ.get('PYCAT_REALDATA'):
        pytest.skip('real-data matrix: opt in with -m realdata or PYCAT_REALDATA=1')


@pytest.fixture(scope='module')
def golden():
    from tests.realdata_harness import load_golden
    return load_golden()


@pytest.mark.parametrize('case', _cases(), ids=lambda c: c.pipeline)
def test_the_pipeline_holds_on_real_data(case, golden):
    from tests.data.fetch import CorpusUnavailable, fetch
    from tests.realdata_harness import ROUTES, evaluate
    if case.corpus:
        try:
            fetch(case.corpus)
        except CorpusUnavailable as exc:
            pytest.skip(str(exc))
    verdicts = evaluate(case, golden)
    assert set(verdicts) == set(ROUTES), 'every route needs a verdict -- never blank'
    failed = {r: v.reason for r, v in verdicts.items() if v.verdict == 'fail'}
    assert not failed, f'{case.pipeline}: {failed}'
