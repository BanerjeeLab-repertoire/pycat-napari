"""**Pooled molecule counting applies the same camera corrections as the single-trace path** (1.6.376 S2).

`count_molecules_pooled` — the docstring-"preferred" estimator — omitted the three corrections
`count_molecules_single` performs: it fed the RAW trace to the variance-pair regression (no pedestal
subtraction, so ν was corrupted), took the numerator `y[fast]` with the pedestal still in, and fitted ν
through the origin with no read-noise floor. An uncorrected pedestal inflated the pooled N up to ~2.5×. These
pin the fix: the pooled count is now pedestal-invariant and its ν tracks the single-trace fitter on the same
data (they share `_fit_counting_nu`).
"""
import numpy as np
import pytest

from pycat.toolbox.molecular_counting_tools import count_molecules_pooled, count_molecules_single

pytestmark = pytest.mark.base      # scipy/pandas stack


def _trace(seed, true_N=20, nu=100.0, n_frames=200, survival=0.97, read_sd=0.0, pedestal=0.0):
    """A binomial-thinning bleaching trace with a known ``true_N`` (each fluorophore survives independently),
    plus an optional camera pedestal and read noise."""
    rng = np.random.default_rng(seed)
    alive, tr = true_N, []
    for _ in range(n_frames):
        tr.append(alive * nu)
        alive = rng.binomial(alive, survival)
    return np.asarray(tr, float) + pedestal + rng.normal(0, read_sd, n_frames)


def _traces(n=40, **kw):
    return [_trace(seed=s, **kw) for s in range(n)]


def test_pooled_count_is_pedestal_invariant():
    true_N = 20
    traces = _traces(n=40, true_N=true_N)                 # read_sd=0 → deterministic; isolates the pedestal
    m_clean = count_molecules_pooled(traces, fast=4)['per_trace']['N'].median()
    m_ped = count_molecules_pooled([t + 800.0 for t in traces], fast=4)['per_trace']['N'].median()
    # The old code kept the pedestal in the numerator AND the ν regression → the pedestal inflated the pooled
    # N by ~2.5× (a factor of ~1.5 error). The corrected path recovers the SAME count with or without it.
    assert abs(m_clean - m_ped) / true_N < 0.10


def test_pooled_nu_tracks_the_single_trace_estimator_on_the_same_data():
    ped = [t + 800.0 for t in _traces(n=40, true_N=20, read_sd=5.0)]
    r_pooled = count_molecules_pooled(ped, fast=4)
    r_single = count_molecules_single(ped[0], fast=4)
    # Both now run the same pedestal subtraction + free-intercept ν fit, so the shared brightness ν agrees.
    assert abs(r_pooled['nu'] - r_single['nu']) / r_pooled['nu'] < 0.2


def test_pooled_median_recovers_the_true_count_with_a_pedestal():
    r = count_molecules_pooled([t + 800.0 for t in _traces(n=40, true_N=20, read_sd=5.0)], fast=4)
    med = r['per_trace']['N'].median()
    assert 0.6 * 20 < med < 1.4 * 20                      # the pedestal no longer inflates the population N


def test_pooled_reports_the_per_trace_pedestal():
    r = count_molecules_pooled([t + 800.0 for t in _traces(n=6, true_N=20)], fast=4)
    peds = r['per_trace']['pedestal']
    assert (peds > 700).all()                            # each trace's ~800 dark reference is recovered + recorded
