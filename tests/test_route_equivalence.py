"""**The cross-route equivalence matrix — three canonical workflows, asserted identical per route.**

Read `tests/route_equivalence.py` first: it explains why this exists (the same analysis must not yield
different numbers depending on how it was launched) and what a route / a documented gap is.

The matrix grew from three to **six** workflows (increment A), chosen for distinct data shapes and failure
modes — not the 15 the audit imagined at once, because three-per-increment that genuinely run will grow and
fifteen written at once are abandoned. Adding another is one `Workflow(...)` entry.

| workflow | headless | batch replay | session reload |
|---|---|---|---|
| rolling-ball background removal (the known-divergence scale-semantics path) | ✓ | ✓ | ✓ |
| puncta detection + measurement | ✓ | gap: cellpose | ✓ |
| VPT tracks → MSD → viscosity | ✓ | gap: skip-stub | ✓ |
| **cellpose segmentation** (most parameter surface) | ✓ | ✓ | ✓ |
| **colocalization** (two-channel; channel-assignment risk) | ✓ | gap: no coloc step | ✓ |
| **time-series condensate partition** (per-frame stack) | ✓ | gap: not a per-image step | ✓ |

The batch **gaps are declared, not skipped silently** — and the harness fails if a gap closes or a route
vanishes without the table being updated. They mark where the headless batch API stops: colocalization has
no replay step (an interactive two-channel analysis), and a time-series partition series is not a per-image
batch step (same class as the VPT/MSD skip-stub). Cellpose genuinely drives all three routes here (torch is
present); if cellpose is absent it is skipped via the optional-dependency pattern, not silently gapped.

**Beyond the arrays:** the two new DataFrame workflows also compare *metadata* (schema/column order, dtype,
NaN policy, and the units column) via `compare_metadata`, because two routes can produce numerically similar
tables while differing in scientifically important metadata. Layer-tag / provenance comparison on produced
layers is a cheap next addition, deferred here (the routes return arrays/frames, not tagged napari layers).

Every generator seeds its randomness; every comparison defaults to **exact** equality (a session round-trip
carries a justified 1-ULP CSV tolerance). A route that needed a loosened tolerance to pass would be a
finding to report, not a knob to turn. **No divergence was found in increment A.**
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.base

from tests.route_equivalence import (
    Workflow, run_all_routes, assert_routes_agree,
    compare_arrays, compare_dataframes, compare_frame_metadata,
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


# ── Workflow 4 — Cellpose segmentation (headless ≈ batch ≈ session) ──────────────────────────────
# Increment A, priority 1: the most-used path and the one with the most parameter surface (diameter,
# model, refine). The equivalence that matters is that the BATCH replay assembles the same parameters and
# resolves the same layer as a direct call — if it pulled the default diameter instead of the recorded
# one, or normalised differently, the masks would diverge. Gated on cellpose being importable (the
# optional-dependency skip pattern) rather than weakening the assertion.

_CELL_DIAMETER = 14


def _cellpose_workflow():
    pytest.importorskip('cellpose', reason='cellpose/torch not installed in this environment')
    from pycat.toolbox.segmentation_tools import cellpose_segmentation
    from pycat.batch.steps._common import _normalize_to_float

    raw = _synthetic_image(seed=5).astype(np.float64)          # raw counts, as the loader hands them

    def headless():
        norm = _normalize_to_float(raw)                        # the shared production normalisation
        masks = cellpose_segmentation(norm, _CELL_DIAMETER, postprocess=False)
        masks = masks[0] if isinstance(masks, (tuple, list)) else masks
        return np.asarray(masks).astype(np.float32)

    def batch():
        # The REAL replay step: it resolves the layer from state, normalises, reads cell_diameter from the
        # data_instance, and calls the same cellpose_segmentation. Seeding cell_diameter here proves the
        # batch route USES the recorded diameter — a route that fell back to the default (100) would diverge.
        state = {'image': raw, 'data_instance': _DataInstance({'cell_diameter': _CELL_DIAMETER})}
        batch_replay([{'step': 'cellpose_segmentation',
                       'params': {'method': 'cellpose', 'cellpose_refine': False}}], state)
        return np.asarray(state['cellpose_mask']).astype(np.float32)

    def session():
        return session_roundtrip_image(headless(), 'Cell Mask').astype(np.float32)

    return Workflow(
        'cellpose segmentation',
        routes={'headless': headless, 'batch': batch, 'session': session},
        # Exact: all three feed the same deterministic normalised image to the same cellpose call.
        compare=compare_arrays(rtol=0.0, atol=0.0))


# ── Workflow 5 — Colocalization (headless ≈ session; batch is a documented gap) ──────────────────
# Increment A, priority 2: two-channel input, exercising CHANNEL ASSIGNMENT — a distinct parameter-
# assembly risk from single-channel work. m1 (channel-1 over channel-2) ≠ m2 (channel-2 over channel-1),
# so the routes agreeing confirms the channel order is preserved through serialization, not just the values.

def _coloc_workflow():
    from pycat.toolbox.obj_based_coloc_analysis_tools import (
        manders_m1_calculation, manders_m2_calculation)
    from pycat.toolbox.pixel_wise_corr_analysis_tools import pearsons_correlation

    rng = np.random.default_rng(7)
    size = 96
    ch1 = rng.normal(200, 20, (size, size)).astype(np.float32)
    ch2 = (0.6 * ch1 + rng.normal(80, 20, (size, size))).astype(np.float32)   # partially correlated
    # A cell ROI (not the whole frame — Pearson over a whole frame measures the cell shape, not coloc).
    yy, xx = np.mgrid[0:size, 0:size]
    roi = ((yy - size / 2) ** 2 + (xx - size / 2) ** 2) < (size * 0.4) ** 2
    mask1 = (ch1 > np.percentile(ch1[roi], 70)) & roi
    mask2 = (ch2 > np.percentile(ch2[roi], 70)) & roi

    def headless():
        m1 = manders_m1_calculation(ch1 * mask1, mask2, roi)
        m2 = manders_m2_calculation(mask1, ch2 * mask2, roi)
        pcc = pearsons_correlation(ch1, ch2, roi)[0]
        return pd.DataFrame([{'coefficient': 'm1', 'value': float(m1), 'units': 'fraction'},
                             {'coefficient': 'm2', 'value': float(m2), 'units': 'fraction'},
                             {'coefficient': 'pearson', 'value': float(pcc), 'units': 'dimensionless'}])

    def session():
        return session_roundtrip_dataframe(headless(), 'coloc_df')

    return Workflow(
        'colocalization (two-channel)',
        routes={'headless': headless, 'session': session},
        # 1-ULP CSV decimal round-trip tolerance (see the puncta workflow).
        compare=compare_dataframes(['value'], rtol=1e-12, atol=1e-15),
        # Beyond the numbers: the schema, the dtype, the NaN policy, and the UNITS column must survive the
        # round-trip — a coloc coefficient with its units dropped means something different.
        compare_metadata=compare_frame_metadata(['coefficient', 'value', 'units'], units_column='units'),
        documented_gaps={
            'batch': "colocalization has no step in the batch replay registry — it is an interactive "
                     "two-channel analysis, not a per-image batch step (no `replay_coloc` exists)"})


# ── Workflow 6 — Time-series condensate partition (headless ≈ session; batch is a documented gap) ─
# Increment A, priority 3: a stack through the whole chain. The route most likely to diverge because batch
# and interactive materialize a stack differently — so the per-frame partition table must survive the
# save/reload round-trip with the frame axis and the NaN policy intact.

def _timeseries_condensate_workflow():
    from pycat.toolbox.partition_enrichment_tools import client_enrichment

    size, n_frames = 96, 8
    yy, xx = np.mgrid[0:size, 0:size]
    dense = ((yy - size / 2) ** 2 + (xx - size / 2) ** 2) < (size * 0.15) ** 2
    cell = np.ones((size, size), dtype=bool)

    def _frame(t):
        # A two-phase field with NO camera pedestal (background=0), so K = dense/dilute is finite; the
        # dense-phase intensity drifts frame to frame — a real per-frame series (K from ~3 to ~5). Each
        # frame is seeded by its index (not a shared advancing RNG) so `headless()` is identical whether
        # called for the reference route or again inside `session()`.
        rng = np.random.default_rng(1000 + t)
        dilute_val = 100.0
        dense_val = (3.0 + 0.3 * t) * dilute_val
        img = np.full((size, size), dilute_val, dtype=np.float32)
        img[dense] = dense_val
        return img + rng.normal(0, 1.0, (size, size)).astype(np.float32)

    def headless():
        rows = []
        for t in range(n_frames):
            out = client_enrichment(_frame(t), dense, cell_mask=cell, background=0.0)
            rows.append({'frame': t, 'enrichment': float(out['enrichment']), 'units': 'dimensionless'})
        return pd.DataFrame(rows)

    def session():
        return session_roundtrip_dataframe(headless(), 'condensate_ts_df')

    return Workflow(
        'time-series condensate partition',
        routes={'headless': headless, 'session': session},
        compare=compare_dataframes(['enrichment'], rtol=1e-12, atol=1e-15),
        compare_metadata=compare_frame_metadata(['frame', 'enrichment', 'units'], units_column='units'),
        documented_gaps={
            'batch': "a per-frame partition series over a time-series stack is not a per-image batch step "
                     "(the batch condensate replay is a single-frame step; the lazy stack is materialized "
                     "by the interactive/time-series path, not the batch recorder) — same class as the "
                     "VPT/MSD skip-stub gap"})


# ── The matrix ──────────────────────────────────────────────────────────────────────────────────

_WORKFLOW_BUILDERS = {
    'rolling_ball': _rolling_ball_workflow,
    'puncta': _puncta_workflow,
    'vpt_msd': lambda: _vpt_workflow()[0],
    'cellpose': _cellpose_workflow,
    'colocalization': _coloc_workflow,
    'time_series_condensate': _timeseries_condensate_workflow,
}


@pytest.mark.parametrize('name', list(_WORKFLOW_BUILDERS))
def test_the_workflow_gives_the_same_numbers_through_every_route(name):
    """Each canonical workflow yields the SAME result through every route that can run it — and the
    routes that cannot run are exactly the ones declared as gaps."""
    workflow = _WORKFLOW_BUILDERS[name]()
    results = run_all_routes(workflow)
    assert_routes_agree(workflow, results, reference='headless')


def test_cellpose_genuinely_runs_all_three_routes():
    """The cellpose row must exercise batch replay and session reload, not quietly become gaps — otherwise
    'the routes agree' is vacuous. (Skipped whole if cellpose is not installed.)"""
    from tests.route_equivalence import Unavailable
    results = run_all_routes(_cellpose_workflow())
    ran = {r for r, v in results.items() if not isinstance(v, Unavailable)}
    assert ran == {'headless', 'batch', 'session'}, f"cellpose only ran {sorted(ran)}"


def test_the_metadata_comparator_would_catch_a_units_or_nan_divergence():
    """Guard the guard: the new metadata comparison must actually fire. A frame with a units column
    dropped, or a NaN where the reference has a number, is a divergence the value comparator alone can
    miss — so prove `compare_frame_metadata` catches both."""
    ref = pd.DataFrame([{'v': 1.0, 'units': 'fraction'}, {'v': 2.0, 'units': 'fraction'}])
    cmp = compare_frame_metadata(['v', 'units'], units_column='units')
    agree, _ = cmp(ref, ref.copy())
    assert agree
    # units renamed → different meaning, same numbers
    bad_units = ref.copy(); bad_units['units'] = ['count', 'count']
    assert not cmp(ref, bad_units)[0]
    # a NaN where the reference has a number
    bad_nan = ref.copy(); bad_nan.loc[0, 'v'] = np.nan
    assert not cmp(ref, bad_nan)[0]


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
