"""**The cross-route equivalence matrix — three canonical workflows, asserted identical per route.**

Read `tests/route_equivalence.py` first: it explains why this exists (the same analysis must not yield
different numbers depending on how it was launched) and what a route / a documented gap is.

The matrix starts at **three** workflows, chosen for distinct data shapes and failure modes — not the
15 the audit imagined, because three that genuinely run will grow and fifteen written at once are
abandoned. Adding a fourth is one `Workflow(...)` entry.

| workflow | headless | batch replay | session reload |
|---|---|---|---|
| rolling-ball background removal (the known-divergence scale-semantics path) | ✓ | ✓ | ✓ |
| puncta detection + measurement | ✓ | gap: cellpose | ✓ |
| VPT tracks → MSD → viscosity | ✓ | gap: skip-stub | ✓ |

The batch **gaps are declared, not skipped silently** — and the harness fails if a gap closes or a
route vanishes without the table being updated. They mark where the headless batch API stops: cell
segmentation's replay needs cellpose/torch (absent in the headless `core` env), and VPT/MSD replay
steps are deliberate skip-stubs in `batch_step_registry` (time-series, not per-image).

Every generator seeds its randomness; every comparison defaults to **exact** equality. A route that
needed a loosened tolerance to pass would be a finding to report, not a knob to turn.
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.core

from tests.route_equivalence import (
    Workflow, run_all_routes, assert_routes_agree,
    compare_arrays, compare_dataframes,
    session_roundtrip_dataframe, session_roundtrip_image, batch_replay)


# ── Fixtures (seeded, deterministic) ───────────────────────────────────────────────────────────

def _synthetic_image(seed):
    from tests.fixtures_synthetic import synthetic_puncta_image
    image, _labels = synthetic_puncta_image(shape=(128, 128), n_puncta=20, seed=seed)
    return np.asarray(image)


def _brownian_tracks(*, n_tracks, n_frames, D_um2_per_s, dt_s, seed):
    """2D Brownian walks with diffusion D (MSD(tau)=4*D*tau). Columns track_id, frame, y_um, x_um."""
    rng = np.random.default_rng(seed)
    step_std = np.sqrt(2.0 * D_um2_per_s * dt_s)
    rows = []
    for tid in range(n_tracks):
        y = np.cumsum(rng.normal(0.0, step_std, n_frames))
        x = np.cumsum(rng.normal(0.0, step_std, n_frames))
        y -= y[0]
        x -= x[0]
        for f in range(n_frames):
            rows.append({'track_id': tid, 'frame': f, 'y_um': float(y[f]), 'x_um': float(x[f])})
    return pd.DataFrame(rows)


class _DataInstance:
    """The slice of BaseDataClass the batch replay touches: a data_repository dict + get/set."""

    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value

    def get_data(self, key, default=None):
        return self.data_repository.get(key, default)


# ── Workflow 1 — rolling-ball background removal (headless ≈ batch ≈ session) ────────────────────
# This is the spec's known-divergence area made concrete. The interactive path hands the operation
# RAW COUNTS; batch once handed a normalised image, and the rolling-ball radius is not
# scale-invariant, so the two produced different backgrounds. Here both routes must reduce to the
# same toolbox call on the same raw image — bit for bit.

_BALL_RADIUS = 25


def _rolling_ball_workflow():
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement

    raw = _synthetic_image(seed=1).astype(np.float64)   # raw detector counts, as the GUI hands them

    def headless():
        # The operation the interactive runner applies to a raw (not-yet-enhanced) image.
        return rb_gaussian_bg_removal_with_edge_enhancement(raw, _BALL_RADIUS).astype(np.float32)

    def batch():
        # The recorded step, run through the real replay registry. `background_removal` re-derives
        # raw counts internally and takes the rolling-ball branch — so if it ever reverts to
        # normalising first, the result diverges and this names the batch route.
        state = {'image': raw, 'preprocessed': raw,
                 'data_instance': _DataInstance({'ball_radius': _BALL_RADIUS})}
        # `open_image` is bypassed (state pre-seeded) to keep the row on the operation under test.
        batch_replay([{'step': 'background_removal',
                       'params': {'ball_radius': _BALL_RADIUS, 'active_layer': 'segmentation image'}}],
                     state)
        return np.asarray(state['preprocessed']).astype(np.float32)

    def session():
        result = rb_gaussian_bg_removal_with_edge_enhancement(raw, _BALL_RADIUS).astype(np.float32)
        return session_roundtrip_image(result, 'Enhanced Background Removed').astype(np.float32)

    return Workflow(
        'rolling-ball background removal',
        routes={'headless': headless, 'batch': batch, 'session': session},
        # Exact: all three are the same deterministic float32 operation on the same raw input.
        compare=compare_arrays(rtol=0.0, atol=0.0))


# ── Workflow 2 — puncta detection + measurement (headless ≈ session; batch is a documented gap) ──

def _puncta_workflow():
    from pycat.toolbox.clean_spot_detection_tools import clean_detect

    image = _synthetic_image(seed=2).astype(np.float32)
    columns = ['y', 'x', 'intensity', 'n_hits']       # the per-object detection + measurement table

    def headless():
        return clean_detect(image, psf_sigma=2.5, psf_size=11)

    def session():
        return session_roundtrip_dataframe(headless(), 'puncta_df')

    return Workflow(
        'puncta detection + measurement',
        routes={'headless': headless, 'session': session},
        # A session round-trip is a DECIMAL (CSV) serialization, so a float64 can come back differing
        # in its last bit (~1 ULP, ~2e-16). That is the text format's precision, NOT a route computing
        # a different number — so the tolerance is 1 ULP-scale (rtol 1e-12, atol 1e-15), far below any
        # scientific significance and far above the round-trip error. Anything larger is a real finding.
        compare=compare_dataframes(columns, rtol=1e-12, atol=1e-15),
        documented_gaps={
            'batch': "batch cell/condensate replay needs a cellpose cell mask upstream "
                     "(cellpose_segmentation → torch), absent in the headless core env"})


# ── Workflow 3 — VPT tracks → MSD → viscosity (headless ≈ session; batch is a documented gap) ────
# The flagship numeric chain, with a validated reference: Stokes–Einstein eta = kT/(6 pi R D).

def _vpt_workflow():
    from pycat.toolbox.condensate_physics_tools import compute_msd

    D_true, dt_s, radius_um, temp_C = 0.05, 0.1, 0.1, 24.0
    tracks = _brownian_tracks(n_tracks=40, n_frames=60, D_um2_per_s=D_true, dt_s=dt_s, seed=0)
    columns = ['lag_frames', 'lag_s', 'msd_um2', 'msd_std', 'msd_sem', 'n_tracks', 'n_pairs']

    def headless():
        # min_track_length overridden to 5 for the short synthetic tracks (the default is 200 frames).
        return compute_msd(tracks, frame_interval_s=dt_s, min_track_length=5)

    def session():
        return session_roundtrip_dataframe(headless(), 'msd_df')

    return (Workflow(
        'VPT tracks -> MSD -> viscosity',
        routes={'headless': headless, 'session': session},
        # 1-ULP CSV decimal round-trip tolerance — see the puncta workflow for the justification.
        compare=compare_dataframes(columns, rtol=1e-12, atol=1e-15),
        documented_gaps={
            'batch': "VPT/MSD replay steps are deliberate skip-stubs in batch_step_registry "
                     "(time-series analysis, not a per-image batch step)"}),
        dict(D_true=D_true, dt_s=dt_s, radius_um=radius_um, temp_C=temp_C, tracks=tracks))


# ── The matrix ──────────────────────────────────────────────────────────────────────────────────

_WORKFLOW_BUILDERS = {
    'rolling_ball': _rolling_ball_workflow,
    'puncta': _puncta_workflow,
    'vpt_msd': lambda: _vpt_workflow()[0],
}


@pytest.mark.parametrize('name', list(_WORKFLOW_BUILDERS))
def test_the_workflow_gives_the_same_numbers_through_every_route(name):
    """Each canonical workflow yields the SAME result through every route that can run it — and the
    routes that cannot run are exactly the ones declared as gaps."""
    workflow = _WORKFLOW_BUILDERS[name]()
    results = run_all_routes(workflow)
    assert_routes_agree(workflow, results, reference='headless')


def test_rolling_ball_runs_all_three_routes_not_just_headless():
    """The flagship row must genuinely exercise batch replay and session reload — a matrix where
    every non-headless route quietly became a gap would pass vacuously."""
    results = run_all_routes(_rolling_ball_workflow())
    from tests.route_equivalence import Unavailable
    ran = {r for r, v in results.items() if not isinstance(v, Unavailable)}
    assert ran == {'headless', 'batch', 'session'}, f"rolling-ball only ran {sorted(ran)}"


def test_the_VPT_chain_recovers_the_KNOWN_viscosity_end_to_end():
    """The MSD table agreeing across routes is necessary but not sufficient — the chain must land on
    the physically correct number. Stokes–Einstein with the true D is the reference (arithmetic
    checked to the constant elsewhere); the full noisy chain must recover eta within a stated band.
    """
    from pycat.toolbox.condensate_physics_tools import compute_msd, fit_anomalous_diffusion
    from pycat.toolbox.vpt_tools import viscosity_from_diffusion

    _wf, ctx = _vpt_workflow()
    msd = compute_msd(ctx['tracks'], frame_interval_s=ctx['dt_s'], min_track_length=5)
    fit = fit_anomalous_diffusion(msd, frame_interval_s=ctx['dt_s'])
    eta = viscosity_from_diffusion(fit['D_um2_per_s'], ctx['radius_um'], ctx['temp_C'])

    kB = 1.380649e-23
    eta_true = kB * (ctx['temp_C'] + 273.15) / (
        6.0 * np.pi * (ctx['radius_um'] * 1e-6) * (ctx['D_true'] * 1e-12))

    # The recovered D is within ~1% on this seed; the viscosity band is the propagated tolerance,
    # not a tuned one — a stated 15% covers the finite-track statistical error of the noisy chain.
    assert abs(fit['D_um2_per_s'] - ctx['D_true']) / ctx['D_true'] < 0.15, (
        f"recovered D {fit['D_um2_per_s']:.4f} is not within 15% of the true {ctx['D_true']}")
    assert abs(eta - eta_true) / eta_true < 0.15, (
        f"recovered viscosity {eta:.4f} Pa*s is not within 15% of Stokes-Einstein {eta_true:.4f}")
