"""**VPT drift is measured, tested, applied only when real — and never moves a bead off its centroid (1.6.476).**

The old correction chained the mean frame-to-frame step of whatever tracks spanned each frame pair and
was applied unconditionally, writing the corrected positions over the detected ones. Now:

* the drift is MEASURED (bead-anchored joint fit) and recovers a known drift;
* it is called REAL only when two independent halves of the beads agree on it and it exceeds the
  localization precision — no drift, or drift below the precision, is not applied;
* the stored tracks keep the detected centroids; the correction lives in a separate analysis copy;
* every display layer draws the centroid even when handed the analysis copy;
* the legacy ``drift_correct_com`` is idempotent (a second call no longer overwrites the centroids).
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.vpt.measured_drift import (
    measure_drift, attach_drift, drift_corrected_for_msd, centroid_offsets_px)

pytestmark = pytest.mark.base          # needs pandas / scipy (not the numpy-only core lane)

MPX = 0.1


def _beads(drift_px_per_frame=(0.0, 0.0), n_tracks=60, n_frames=150, step_px=0.3, noise_px=0.05, seed=0):
    """Brownian beads at fixed homes plus a COMMON linear drift (a moving stage)."""
    rng = np.random.default_rng(seed)
    rows = []
    for tid in range(n_tracks):
        y, x = rng.uniform(20, 480, 2)
        for f in range(n_frames):
            y += rng.normal(0, step_px)
            x += rng.normal(0, step_px)
            dy, dx = drift_px_per_frame[0] * f, drift_px_per_frame[1] * f
            rows.append(dict(track_id=tid, frame=f,
                             y_um=(y + dy + rng.normal(0, noise_px)) * MPX,
                             x_um=(x + dx + rng.normal(0, noise_px)) * MPX))
    return pd.DataFrame(rows)


def test_a_real_drift_is_measured_and_called_real():
    # 3 px / 6 px over the movie. Freely diffusing beads limit ANY drift estimate to ~ walk / sqrt(n beads),
    # so the movie has enough beads (200) for that limit to sit well under a pixel, and the error must also
    # be consistent with the noise the measurement itself reports (the split-half disagreement).
    tr = _beads(drift_px_per_frame=(0.02, -0.04), n_tracks=200, step_px=0.15)
    m = measure_drift(tr, MPX)
    truth_y = 0.02 * m['frames']; truth_x = -0.04 * m['frames']
    err = np.sqrt(np.mean((m['dy_px'] - truth_y) ** 2 + (m['dx_px'] - truth_x) ** 2))
    assert m['real'], m['verdict']
    assert err < 0.3, f"drift recovered to {err:.2f} px RMS — must be well under a pixel"
    assert err < 3 * m['noise_px'], f"error {err:.2f} px is not within the reported noise {m['noise_px']:.2f} px"


def test_no_drift_is_not_called_real():
    m = measure_drift(_beads(), MPX)
    assert not m['real'], m['verdict']


def test_a_drift_below_the_localization_precision_is_not_applied():
    # 0.03 px total over the movie against 0.5 px localization noise: consistent maybe, but meaningless
    tr = _beads(drift_px_per_frame=(0.0002, 0.0), noise_px=0.5, step_px=0.05)
    m = measure_drift(tr, MPX)
    assert not m['real'], m['verdict']


def test_stored_tracks_keep_the_detected_centroids():
    tr = _beads(drift_px_per_frame=(0.02, -0.04))
    m = measure_drift(tr, MPX)
    stored = attach_drift(tr, m, MPX)
    assert np.array_equal(stored['y_um'].to_numpy(), tr['y_um'].to_numpy())
    assert np.array_equal(stored['x_um'].to_numpy(), tr['x_um'].to_numpy())
    analysis = drift_corrected_for_msd(stored)
    assert np.array_equal(analysis['y_um_raw'].to_numpy(), tr['y_um'].to_numpy())
    assert not np.allclose(analysis['y_um'].to_numpy(), tr['y_um'].to_numpy())
    assert 'y_um_raw' not in stored, "the stored tracks must not carry a corrected/raw split"


def test_the_correction_recovers_the_true_msd():
    from pycat.toolbox.condensate_physics_tools import compute_msd, fit_anomalous_diffusion
    tr = _beads(drift_px_per_frame=(0.05, 0.05), n_frames=200)
    stored = attach_drift(tr, measure_drift(tr, MPX), MPX)
    fit = lambda t: fit_anomalous_diffusion(compute_msd(t, frame_interval_s=0.1, min_track_length=50),
                                            frame_interval_s=0.1, confine_to_defensible_bounds=False)
    raw_alpha = fit(stored)['alpha']
    cor_alpha = fit(drift_corrected_for_msd(stored))['alpha']
    assert raw_alpha > 1.2, "premise: uncorrected drift inflates alpha"
    assert abs(cor_alpha - 1.0) < 0.15, f"corrected alpha {cor_alpha:.2f} must come back to ~1"


def test_legacy_com_correction_is_idempotent():
    from pycat.toolbox.vpt.drift import drift_correct_com
    tr = _beads(drift_px_per_frame=(0.02, 0.0), n_tracks=10, n_frames=30)
    once = drift_correct_com(tr)
    twice = drift_correct_com(once)
    assert np.allclose(twice['y_um_raw'].to_numpy(), once['y_um_raw'].to_numpy())
    assert np.allclose(twice['y_um'].to_numpy(), once['y_um'].to_numpy())


def _gauss_image(centres, shape=(120, 120), sigma=1.5):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    img = np.full(shape, 10.0)
    for cy, cx in centres:
        img += 200 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sigma ** 2))
    return img


def test_centroid_offsets_detect_markers_off_the_beads():
    centres = np.array([[30.3, 40.7], [60.0, 80.2], [90.6, 25.1]])
    img = _gauss_image(centres)
    assert np.nanmax(centroid_offsets_px(img, centres)) < 0.2
    off = np.nanmedian(centroid_offsets_px(img, centres + [2.0, -2.0]))
    assert 2.0 < off < 3.5


def test_every_display_layer_draws_the_centroid_even_from_the_analysis_copy():
    """Hand the display code the drift-CORRECTED analysis copy: the Bead Picker and the trajectory layer
    must still draw the detected centroids."""
    pytest.importorskip('napari')                     # a napari ViewerModel and the VPT panel
    from napari.components import ViewerModel
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI
    tr = _beads(drift_px_per_frame=(0.05, 0.05), n_tracks=5, n_frames=40)
    analysis = drift_corrected_for_msd(attach_drift(tr, measure_drift(tr, MPX), MPX))
    ui = VideoParticleTrackingUI.__new__(VideoParticleTrackingUI)
    ui.viewer = ViewerModel()
    ui._install_viewer_bead_pick = lambda: None
    ui._mpx = lambda: MPX
    ui._add_pickable_bead_points(analysis, None, MPX)
    pts = np.asarray(ui.viewer.layers['Bead Picker'].data)
    want = np.column_stack([analysis['frame'], analysis['y_um_raw'] / MPX, analysis['x_um_raw'] / MPX])
    assert np.allclose(np.sort(pts, axis=0), np.sort(want, axis=0))
    ui._rebuild_track_layers(analysis)
    tl = np.asarray(ui.viewer.layers['Bead Trajectories'].data)
    assert np.allclose(np.sort(tl[:, 2]), np.sort(analysis['y_um_raw'].to_numpy() / MPX))


def test_a_track_too_short_for_an_msd_still_shows_in_the_msd_panel():
    """Most tracks in a fragmented movie are below the minimum length and have no per-track MSD. Clicking
    such a bead must still put a (labelled) curve in the MSD panel, and deselecting removes it."""
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    from matplotlib.figure import Figure
    from pycat.toolbox.analysis_plots import _msd_overlay_hooks

    fig = Figure(); ax = fig.add_subplot(111)
    ptc = pd.DataFrame({"track_id": [1, 1], "lag_s": [0.1, 0.2], "msd_um2": [1e-3, 2e-3]})
    reg = {}
    xy = np.array([[0.1, 5e-4], [0.2, 9e-4]])
    _msd_overlay_hooks(ax, fig, ptc, {}, {}, None, reg,
                       fallback_curve=lambda tid: (xy, f"track {tid}: too short") if tid == 7 else None)
    ln = reg["promote"](7)
    assert ln is not None and np.allclose(np.column_stack(ln.get_data()), xy)
    assert any("track 7" in t.get_text() for t in ax.texts)
    assert reg["demote_line"](ln)
    assert not any("track 7" in t.get_text() for t in ax.texts)
    assert reg["promote"](99) is None                  # no curve anywhere → clean None
