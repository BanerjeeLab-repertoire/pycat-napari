"""**VPT, FRAP, fusion, force-distance and temperature replay in batch** (2026-10-19).

Every analysis step of these five pipelines was registered as 'skipped in headless mode', so a recorded session
replayed nothing. Each now has a replay that runs the panel's operation with the recorded knobs; these tests pin that
a replay computes exactly what the toolbox computes from the same inputs (the real-data panel == batch comparison
lives in the private validation harness, validation/panel_vs_batch.py). They also pin the two loader fixes the
replays needed: an RGB time series and a one-page-per-frame TIFF read as movies, not as one frame.
"""
import datetime as _dt
import json
import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.base


class _DI:
    def __init__(self, repo=None):
        self.data_repository = dict(repo or {})

    def set_data(self, key, value):
        self.data_repository[key] = value


def _state(image=None, **repo):
    return {'image': image, 'data_instance': _DI(repo)}


# ── FRAP ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _recovery(n=30, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    stack = np.full((n, 40, 60), 100.0) + rng.normal(0, 1, (n, 40, 60))
    yy, xx = np.ogrid[:40, :60]
    spot = (yy - 20) ** 2 + (xx - 20) ** 2 < 36
    stack[:, spot] *= (0.3 + 0.6 * (1 - np.exp(-t / 6.0)))[:, None]
    return stack


def test_frap_replays_the_recorded_rois_and_equals_the_analysis(tmp_path):
    from pycat.batch.steps.frap_steps import replay_frap_analysis, replay_frap_define_roi
    from pycat.toolbox.frap_tools import masks_from_shapes, run_frap_analysis
    from types import SimpleNamespace
    stack = _recovery()
    shapes = [{'type': 'ellipse', 'vertices': [[14, 14], [14, 26], [26, 26], [26, 14]]},
              {'type': 'rectangle', 'vertices': [[2, 40], [2, 58], [12, 58], [12, 40]]}]
    st = _state(stack, timeseries_n_frames=30)
    replay_frap_define_roi(st, tmp_path / 'x.tif', {'roi_mode': 'draw', 'shapes': shapes}, tmp_path)
    params = {'frame_interval_s': 0.5, 'time_lag_s': 0.1, 'photofading': True, 'normalization': 'taylor',
              'fit_model': 'empirical', 'n_spline_points': 30, 'recovery_stack': 'r'}
    replay_frap_analysis(st, tmp_path / 'x.tif', params, tmp_path)
    layer = SimpleNamespace(data=[np.asarray(s['vertices'], float) for s in shapes],
                            shape_type=[s['type'] for s in shapes])
    bleach, ref = masks_from_shapes(layer, stack.shape[1:])
    direct = run_frap_analysis(stack, bleach, reference_mask=ref, frame_interval_s=0.5, time_lag_s=0.1,
                               n_spline_points=30, normalization='taylor', fit_model='empirical')
    got = st['data_instance'].data_repository['frap_result']
    pd.testing.assert_frame_equal(got['results_df'], direct['results_df'])
    assert (tmp_path / 'x_frap_curve.csv').is_file()


def test_frap_without_recorded_geometry_says_so_instead_of_guessing(tmp_path, capsys):
    from pycat.batch.steps.frap_steps import replay_frap_define_roi
    st = _state(_recovery(), timeseries_n_frames=30)
    replay_frap_define_roi(st, tmp_path / 'x.tif', {'roi_mode': 'draw'}, tmp_path)
    assert 'frap_masks' not in st and 'predates ROI geometry' in capsys.readouterr().out


def test_frap_fit_geometry_is_the_panels_rule():
    from pycat.toolbox.frap_tools import frap_fit_geometry
    m = np.zeros((20, 20), bool); m[5:10, 2:12] = True
    assert frap_fit_geometry(m, 'reaction_diffusion', 0.1, 1.0)[0] == pytest.approx((1.0, 0.5))
    assert frap_fit_geometry(m, 'circular', 0.1, 2.5) == (None, 2.5)                       # entered radius
    assert frap_fit_geometry(m, 'circular', 0.1, 1.0)[1] == pytest.approx((50 / np.pi) ** 0.5 * 0.1)
    assert frap_fit_geometry(m, 'empirical', 0.1, 1.0) == (None, None)


# ── fusion ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_fusion_image_mode_replays_and_resuggests_the_window_on_each_file(tmp_path):
    from pycat.batch.steps.fusion_steps import replay_fusion_build_signal, replay_fusion_fit
    from pycat.toolbox.fusion_tools import aspect_ratio_signal, fit_fusion_relaxation, suggest_fit_window
    yy, xx = np.mgrid[0:48, 0:48]
    frames = []
    for t in range(16):
        a = 1.0 + 0.8 * np.exp(-t / 3.0)
        frames.append((((yy - 24) * a) ** 2 / 80 + (xx - 24) ** 2 / (80 * a) <= 1) * 200.0 + 10.0)
    stack = np.stack(frames).astype(np.float32)
    st = _state(stack, timeseries_n_frames=16)
    replay_fusion_build_signal(st, tmp_path / 'f.tif', {'mode': 'image', 'threshold_method': 'otsu',
                                                         'frame_interval_s': 0.1}, tmp_path)
    replay_fusion_fit(st, tmp_path / 'f.tif', {'window_source': 'suggested', 'window_decimals': 4}, tmp_path)
    time, sig, _ = aspect_ratio_signal(stack, threshold_method='otsu', frame_interval_s=0.1, return_details=True)
    win = suggest_fit_window(time, sig)
    fit = fit_fusion_relaxation(time, sig, t_start=round(win['t_start'], 4), t_end=round(win['t_end'], 4))
    assert st['data_instance'].data_repository['fusion_fit']['tau_s'] == fit['tau_s']


def test_an_old_fusion_recording_without_the_source_is_refused(tmp_path, capsys):
    from pycat.batch.steps.fusion_steps import replay_fusion_build_signal
    st = {}
    replay_fusion_build_signal(st, tmp_path / 'f.h5', {'source': 'force F1x', 'n_samples': 10}, tmp_path)
    assert 'fusion_signal' not in st and 'predates the signal source' in capsys.readouterr().out


# ── force-distance (h5py: the loader now reads a Bluelake .h5 without pylake) ─────────────────────────────────

def _fd_h5(path):
    h5py = pytest.importorskip('h5py')
    n = 600
    t = 1_700_000_000_000_000_000 + np.arange(n, dtype=np.int64) * 10_000_000
    d = 2.0 + 0.5 * np.abs(((np.arange(n) / 100.0) % 2) - 1)            # three stretch/relax cycles
    rec = lambda v: np.array(list(zip(t, v)), dtype=[('Timestamp', '<i8'), ('Value', '<f8')])
    with h5py.File(path, 'w') as f:
        f.create_dataset('Distance/Distance 1', data=rec(d))
        f.create_dataset('Force LF/Force 1x', data=rec(np.random.default_rng(0).normal(0, 0.05, n)))
        f.create_dataset('Force LF/Force 2x', data=rec(40 * (d - 2.0) + np.random.default_rng(1).normal(0, 0.05, n)))
    return path


def test_fd_loads_without_pylake_and_picks_the_channel_that_tracks_distance(tmp_path):
    from pycat.file_io.frap_io import load_lumicks_fd
    data = load_lumicks_fd(str(_fd_h5(tmp_path / 'fd.h5')))
    assert data['force_channel'] == 'Force 2x' and data['distance_channel'] == 'Distance 1'
    assert data['time_s'][1] == pytest.approx(0.01, rel=1e-4)


def test_fd_replays_load_segment_rips(tmp_path):
    from pycat.batch.steps.fd_curve_steps import replay_fd_load, replay_fd_rips, replay_fd_segment
    from pycat.file_io.frap_io import load_lumicks_fd
    from pycat.toolbox.fd_curve_tools import segment_fd_cycles, summarise_cycles
    path = _fd_h5(tmp_path / 'fd.h5')
    st = {}
    replay_fd_load(st, path, {}, tmp_path)
    replay_fd_segment(st, path, {'min_segment': 20, 'distance_smooth_window': 11}, tmp_path)
    replay_fd_rips(st, path, {}, tmp_path)
    d = load_lumicks_fd(str(path))
    direct = segment_fd_cycles(np.asarray(d['force'], float), d['distance'], min_segment=20, smooth_window=11)
    pd.testing.assert_frame_equal(summarise_cycles(st['fd_segments']), summarise_cycles(direct))
    assert (tmp_path / 'fd_fd_rips.csv').is_file()


# ── temperature ──────────────────────────────────────────────────────────────────────────────────────────────

def _ramp_inputs(tmp_path):
    import tifffile
    t0 = _dt.datetime(2024, 12, 28, 12, 0, 0)
    tdir, cdir = tmp_path / 'tiffs', tmp_path / 'temps' / '20241228_daq'
    tdir.mkdir(parents=True); cdir.mkdir(parents=True)
    tiff = tdir / 'ramp.tif'
    with tifffile.TiffWriter(tiff) as tw:
        for t in range(10):
            frame = (np.random.default_rng(t).normal(100, 5 + 3 * t, (32, 32))).clip(0, 255).astype(np.uint8)
            md = json.dumps({'ElapsedTime-ms': 1000.0 * t, 'ReceivedTime': (t0 + _dt.timedelta(seconds=t)).strftime(
                '%Y-%m-%d %H:%M:%S') + '.000 -0800'})
            tw.write(frame, contiguous=False, extratags=[(51123, 's', 0, md, True)])
    os.utime(tiff, (t0.timestamp(), t0.timestamp()))
    rows = [f'Preamble {i},x' for i in range(6)] + ['Date/Time,AI0 (°C)']
    for k in range(-2, 24):
        when = t0 + _dt.timedelta(seconds=0.5 * k)
        rows.append(f"{when.strftime('%m/%d/%Y %I:%M:%S')}.{500 * (k % 2):03d} {when.strftime('%p')},"
                    f"{20 + 0.5 * max(k, 0):.2f}")
    (cdir / 'log.csv').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return tiff, tmp_path / 'temps'


def test_a_one_page_per_frame_tiff_reads_as_the_movie(tmp_path):
    pytest.importorskip('bioio')                 # the batch reads the movie through the image reader
    from pycat.batch.steps._common import _load_time_stack
    tiff, _ = _ramp_inputs(tmp_path)
    assert _load_time_stack(tiff).shape == (10, 32, 32)


def test_an_rgb_time_series_reads_one_colour_as_the_movie(tmp_path):
    pytest.importorskip('bioio')                 # the batch reads the movie through the image reader
    import tifffile
    from pycat.batch.steps._common import _session_time_stack
    movie = np.random.default_rng(0).integers(0, 255, (12, 8, 9, 3)).astype(np.uint8)
    path = tmp_path / 'rgb.tif'
    tifffile.imwrite(path, movie, photometric='rgb')
    st = _state(movie[0])                        # what open_image leaves: an (H, W, 3) frame, NOT a 3-frame movie
    np.testing.assert_array_equal(_session_time_stack(st, path, 1), movie[..., 1])


def test_temperature_replays_sync_and_turbidity(tmp_path):
    pytest.importorskip('bioio')                 # the batch reads the movie through the image reader
    from pycat.batch.steps.temperature_steps import replay_temperature_sync, replay_temperature_turbidity
    from pycat.toolbox.temperature_tools import entropy_turbidity_curve
    tiff, root = _ramp_inputs(tmp_path)
    st = {'data_instance': _DI()}
    replay_temperature_sync(st, tiff, {'temperature_root': str(root), 'temp_column': 'AI0 (°C)', 'csv_header': 6},
                            tmp_path)
    assert np.isfinite(st['temp_temperatures']).all() and st['temp_temperatures'][0] == pytest.approx(20.0)
    replay_temperature_turbidity(st, tiff, {'bins': 64}, tmp_path)
    from pycat.batch.steps._common import _load_time_stack
    direct = entropy_turbidity_curve(_load_time_stack(tiff), st['temp_temperatures'], subtract_first_frame=False,
                                     correct_focal_drift=False, bins=64, reference_frame_index=0)
    pd.testing.assert_frame_equal(st['data_instance'].data_repository['temp_turbidity_df'],
                                  direct.drop(columns=['focus_score'], errors='ignore'))


# ── VPT ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_vpt_link_replay_is_the_shared_link_step(tmp_path):
    from pycat.batch.steps.vpt_steps import replay_vpt_link_trajectories
    from pycat.toolbox.vpt.link_step import link_bead_populations
    rng = np.random.default_rng(0)
    rows = [{'frame': f, 'y_um': 2.0 + 3 * b + 0.02 * rng.normal(), 'x_um': 2.0 + 0.02 * rng.normal(),
             'bead_class': 'singlet'} for f in range(20) for b in range(4)]
    det = pd.DataFrame(rows)
    det['object_id'] = np.arange(len(det))
    st = _state(None, microns_per_pixel_sq=0.01, pixel_size_from_metadata=True)
    st['vpt_detections'] = det
    params = {'linker': 'greedy', 'max_linking_distance_um': 0.5, 'max_frame_gap': 2,
              'microrheology_population': 'singlet', 'promote_stable_to_singlet': True}
    replay_vpt_link_trajectories(st, tmp_path / 'v.tif', params, tmp_path)
    direct = link_bead_populations(det, 'greedy', 0.5, 2, 0.1, 'singlet', True)['primary']
    direct = direct[direct['track_id'] != -1]
    pd.testing.assert_frame_equal(st['vpt_tracks'].reset_index(drop=True), direct.reset_index(drop=True))
    assert st['vpt_tracks']['track_id'].nunique() == 4
