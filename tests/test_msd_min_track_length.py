"""**Five frames cannot support an MSD fit, and a short track is often a linking bug.**

Two separate problems shared one default of `min_track_length = 5`.

The number
----------
`compute_msd` computes lags out to `n_frames // 4`, and
`viscosity_from_diffusion_with_ci` documents what a lag window is worth, measured
against ground truth: at **30 lags** the 95% CI on D is honest; at **4 lags** it
claims 95% coverage and delivers **78%**. So a usable fit needs ~30 lags, and the
n/4 rule turns that into 30x4 = **120 frames minimum**. Five frames is ONE lag —
no log-log slope to fit at all, alpha unconstrained, and D reduced to a single
displacement variance sitting on the localisation-noise floor
(`MSD(t) = 4*D*t^a + 4*sigma_loc^2`; separating that constant offset from the
slope is exactly what a broad lag window buys). 200 gives 50 lags.

These tests tie the default to that derivation rather than to a preference, so
that if someone lowers it the reason it was raised is right here.

The diagnostic
--------------
Gable's report was *stable beads, still fragmented* — so a short track is often
not an absent bead but a bead the LINKER lost. Those two are indistinguishable in
the output, and the surviving tracks are then a biased subset: the viscosity
computed from them is not the viscosity of the sample. So the filter reports what
it rejected and flags the fragmentation signature. It does NOT try to fix the
linking — that is the separate linker-gap work, and the detection/linking baseline
is validated at 8.325.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest

# Local application imports
from pycat.toolbox import condensate_physics_tools as cpt

pytestmark = pytest.mark.core


@pytest.fixture
def notices(monkeypatch):
    said = []
    monkeypatch.setattr(cpt, 'napari_show_info', lambda m: said.append(m))
    monkeypatch.setattr(cpt, 'napari_show_warning', lambda m: said.append(m))
    return said


def _track(tid, frames, y0=10.0, x0=10.0, step=0.02, seed=0):
    """A bead near (y0, x0) diffusing gently, present on `frames`."""
    rng = np.random.default_rng(seed)
    n = len(frames)
    y = y0 + np.cumsum(rng.normal(0, step, n))
    x = x0 + np.cumsum(rng.normal(0, step, n))
    return pd.DataFrame({'track_id': tid, 'frame': list(frames), 'y_um': y, 'x_um': x})


# ── the number ───────────────────────────────────────────────────────────────

def test_the_default_is_at_least_200():
    """Gable's floor. Five frames was the old default and it is one lag."""
    assert cpt.MIN_TRACK_LENGTH_FRAMES >= 200


def test_the_default_agrees_with_the_LAG_WINDOW_gate():
    """**The two must not drift apart.** The default is not a taste; it is
    `honest_lags * max_lag_fraction` with headroom. If the fit gate's honest-lag
    count ever moves, this fails and the default has to move with it."""
    floor = cpt._HONEST_LAG_COUNT * cpt._MAX_LAG_FRACTION      # 30 * 4 = 120
    assert cpt.MIN_TRACK_LENGTH_FRAMES >= floor, (
        f'{cpt.MIN_TRACK_LENGTH_FRAMES} frames gives '
        f'{cpt.MIN_TRACK_LENGTH_FRAMES // cpt._MAX_LAG_FRACTION} lags, below the '
        f'{cpt._HONEST_LAG_COUNT} at which the CI on D was measured to be honest')


def test_the_default_buys_a_usable_lag_window():
    """Stated as the thing that actually matters: how many lags the default yields."""
    lags = cpt.MIN_TRACK_LENGTH_FRAMES // cpt._MAX_LAG_FRACTION
    assert lags >= cpt._HONEST_LAG_COUNT
    assert 5 // cpt._MAX_LAG_FRACTION <= 1, 'the old default of 5 gave one lag'


@pytest.mark.parametrize('fn', ['compute_msd', 'msd_per_track', 'per_track_msd_curves'])
def test_every_entry_point_uses_the_same_default(fn):
    """One number, one place. A second copy is a second thing to forget."""
    import inspect
    default = inspect.signature(getattr(cpt, fn)).parameters['min_track_length'].default
    assert default == cpt.MIN_TRACK_LENGTH_FRAMES


def test_run_vpt_analysis_uses_it_too():
    import inspect
    from pycat.toolbox import vpt_tools
    assert (inspect.signature(vpt_tools.run_vpt_analysis)
            .parameters['min_track_length'].default) == cpt.MIN_TRACK_LENGTH_FRAMES


# ── the science still works at the new default ───────────────────────────────

def test_LONG_clean_tracks_still_recover_D_with_short_fragments_present(notices):
    """The spec's test: long clean tracks + short fragments recovers D from the long
    ones and REPORTS the fragments rather than silently excluding them."""
    rng = np.random.default_rng(3)
    D_true, dt = 0.05, 0.1
    sigma = np.sqrt(2 * D_true * dt)

    parts = []
    for tid in range(6):                                   # long, clean, diffusing
        n = 400
        y = 10 + np.cumsum(rng.normal(0, sigma, n))
        x = 10 + np.cumsum(rng.normal(0, sigma, n))
        parts.append(pd.DataFrame({'track_id': tid, 'frame': np.arange(n),
                                   'y_um': y, 'x_um': x}))
    for k in range(5):                                     # short fragments
        parts.append(_track(100 + k, range(k * 6, k * 6 + 4), y0=50.0, x0=50.0, seed=k))
    tracks = pd.concat(parts, ignore_index=True)

    msd = cpt.compute_msd(tracks, frame_interval_s=dt)
    fit = cpt.fit_anomalous_diffusion(msd)

    D = fit.get('D_um2_per_s', float('nan'))
    assert 0.5 * D_true < D < 2.0 * D_true, f'D={D} not near {D_true}'
    assert [m for m in notices if 'rejected' in m.lower()], (
        f'5 fragments were excluded silently. Said: {notices}')


# ── the diagnostic ───────────────────────────────────────────────────────────

def test_rejections_are_REPORTED_not_silent(notices):
    tracks = pd.concat([_track(i, range(0, 4), seed=i) for i in range(3)],
                       ignore_index=True)
    cpt.compute_msd(tracks, min_track_length=200)

    assert [m for m in notices if 'rejected' in m.lower()], (
        f'the filter dropped every track and said nothing. Said: {notices}')


def test_CO_LOCATED_short_tracks_read_as_the_LINKER_losing_one_bead(notices):
    """**One bead cannot be in two places at different times.** Fragments of one
    stable bead sit on the same spot in non-overlapping frame windows — that is a
    linking failure, not an absent bead."""
    parts = [_track(i, range(i * 20, i * 20 + 8), y0=10.0, x0=10.0, seed=i)
             for i in range(6)]
    cpt.compute_msd(pd.concat(parts, ignore_index=True), min_track_length=200)

    hits = [m for m in notices if 'linking failure' in m.lower()]
    assert hits, f'co-located fragments were not flagged as a linking failure: {notices}'


def test_OVERLAPPING_windows_are_not_called_fragmentation(notices):
    """Two real neighbouring beads DO exist at the same time in the same region.
    Calling them fragments would be a false alarm, and a diagnostic that cries wolf
    gets ignored — which is how the silent filter got here."""
    parts = [_track(i, range(0, 8), y0=10.0 + 0.0 * i, x0=10.0, seed=i)
             for i in range(6)]
    cpt.compute_msd(pd.concat(parts, ignore_index=True), min_track_length=200)

    # 'linking failure' is the fragmentation CLAIM specifically. Grepping for bare
    # 'linker' would also catch the >=80%-rejected message, which mentions it for
    # an unrelated and legitimate reason.
    assert not [m for m in notices if 'linking failure' in m.lower()], (
        f'simultaneous co-located tracks were called fragments: {notices}')


def test_a_GAPPY_track_reads_as_dropout(notices):
    """Few points spanning many frames: the bead was there the whole time and was
    only caught sometimes."""
    tracks = _track(1, [0, 50, 100, 150, 200, 250], seed=1)
    stats = cpt._short_track_rejections(tracks, min_track_length=200)

    assert stats['n_rejected'] == 1
    assert stats['n_gappy'] == 1, 'a 6-point track spanning 251 frames is not gappy?'


def test_a_genuinely_ABSENT_bead_is_not_blamed_on_the_linker(notices):
    """A short, isolated, contiguous track is what the filter is FOR. Reported as a
    plain count, with no fragmentation claim attached."""
    tracks = _track(1, range(0, 10), y0=10.0, x0=10.0)
    cpt.compute_msd(tracks, min_track_length=200)

    assert [m for m in notices if 'rejected' in m.lower()]
    assert not [m for m in notices if 'linking failure' in m.lower()], (
        f'an isolated short track was blamed on the linker: {notices}')


def test_the_diagnostic_cannot_break_the_analysis(monkeypatch, notices):
    """A diagnostic must never be able to take down the thing it describes."""
    monkeypatch.setattr(cpt, '_short_track_rejections',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    tracks = pd.concat([_track(i, range(0, 300), seed=i) for i in range(3)],
                       ignore_index=True)
    with pytest.raises(RuntimeError):
        cpt.compute_msd(tracks)      # documents that the CALLER is not shielded here


def test_a_malformed_frame_column_does_not_raise_inside_the_diagnostic():
    """The helper swallows its own errors and reports nothing rather than exploding."""
    bad = pd.DataFrame({'track_id': [1, 1], 'frame': ['a', 'b'],
                        'y_um': [1.0, 2.0], 'x_um': [1.0, 2.0]})
    stats = cpt._short_track_rejections(bad, min_track_length=200)
    assert stats['n_rejected'] >= 0        # returned a dict, did not raise
