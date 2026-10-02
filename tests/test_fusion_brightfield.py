"""Brightfield droplet fusion: the droplet outline from its halo, the fit window from the inflection."""
import numpy as np
import pytest
import scipy.ndimage as ndi

pytestmark = pytest.mark.base

H, W = 120, 240
YY, XX = np.mgrid[:H, :W]


@pytest.fixture
def _quiet(monkeypatch):
    import pycat.toolbox.fusion_tools as ft
    seen = []
    monkeypatch.setattr(ft, 'napari_show_warning', lambda m, *a, **k: seen.append(m))
    monkeypatch.setattr(ft, 'napari_show_info', lambda *a, **k: None)
    return seen


def _bf_frame(shape_mask, rng, bg=160.0):
    """A brightfield droplet: interior equal to the background, a dark rim just inside the edge and a
    bright halo just outside it, on a textured background."""
    d = ndi.distance_transform_edt(~shape_mask) - ndi.distance_transform_edt(shape_mask)
    img = (bg + 45 * np.exp(-(d - 2.5) ** 2 / (2 * 1.5 ** 2))
           - 35 * np.exp(-(d + 2.0) ** 2 / (2 * 1.5 ** 2)))
    tex = ndi.gaussian_filter(rng.normal(0, 1, (H, W)), 4)
    img += 8 * tex / tex.std() + rng.normal(0, 2, (H, W))      # ~ the C-Trap movie (7.6)
    return np.clip(img, 0, 255).astype(np.uint8)


def _fusion_movie(tau_frames=6.0, n_before=15, n_after=60, r=26, seed=0):
    """Two touching droplets, then one droplet whose aspect ratio relaxes as 1 + (2-1)·exp(-t/tau)
    at constant area (volume ~ area in projection)."""
    rng = np.random.default_rng(seed)
    cy, cx = H / 2, W / 2
    frames = []
    pair = ((YY - cy) ** 2 + (XX - cx + r) ** 2 <= r * r) | ((YY - cy) ** 2 + (XX - cx - r) ** 2 <= r * r)
    for _ in range(n_before):
        frames.append(_bf_frame(pair, rng))
    area = pair.sum()
    for k in range(n_after):
        ar = 1 + 1.0 * np.exp(-k / tau_frames)
        b = np.sqrt(area / (np.pi * ar)); a = ar * b
        frames.append(_bf_frame(((YY - cy) / b) ** 2 + ((XX - cx) / a) ** 2 <= 1, rng))
    return np.stack(frames), n_before


def _fluor_movie(seed=0):
    rng = np.random.default_rng(seed)
    disk = (YY - 60) ** 2 + (XX - 120) ** 2 <= 30 ** 2
    return np.stack([(10 + 200 * disk + rng.normal(0, 3, (H, W))).clip(0, 255).astype(np.uint8)
                     for _ in range(4)])


def test_brightfield_is_recognised_and_fluorescence_is_not():
    from pycat.toolbox.fusion_tools import looks_brightfield
    movie, _ = _fusion_movie(n_after=5)
    assert looks_brightfield(movie)
    assert not looks_brightfield(_fluor_movie())


def test_the_halo_outline_tracks_the_fusion_where_a_threshold_cannot():
    from pycat.toolbox.fusion_tools import aspect_ratio_signal
    movie, n0 = _fusion_movie()
    t, ar, det = aspect_ratio_signal(movie, threshold_method='auto', return_details=True)
    assert det['method'] == 'brightfield'
    assert 1.8 < np.nanmedian(ar[:n0]) < 2.2              # the touching pair
    assert np.nanmedian(ar[-10:]) < 1.08                   # one round droplet
    assert (det['n_objects'][n0:] == 1).all()
    _, ar_otsu = aspect_ratio_signal(movie, threshold_method='otsu')
    assert abs(np.nanmedian(ar_otsu[:n0]) - np.nanmedian(ar_otsu[-10:])) < 0.5   # Otsu sees no fusion


@pytest.mark.parametrize('tau_frames', [4.0, 8.0])
def test_tau_is_recovered_from_a_brightfield_movie(_quiet, tau_frames):
    from pycat.toolbox.fusion_tools import aspect_ratio_signal, fit_fusion_relaxation, suggest_fit_window
    movie, _ = _fusion_movie(tau_frames=tau_frames)
    dt = 1 / 15.0
    t, ar = aspect_ratio_signal(movie, threshold_method='brightfield', frame_interval_s=dt)
    w = suggest_fit_window(t, ar)
    fit = fit_fusion_relaxation(t, ar, t_start=w['t_start'], t_end=w['t_end'])
    assert fit['tau_s'] == pytest.approx(tau_frames * dt, rel=0.15)


def _neck_then_relax(tau=0.25, lag=0.15, n=4000, t_end=4.0, onset=1.0, seed=0):
    """A force-like fusion trace: flat, an accelerating neck-growth phase, then an exponential
    approach to the plateau (continuous value and slope at the join)."""
    t = np.linspace(0, t_end, n)
    y = np.zeros(n)
    s = t - onset
    grow = (s > 0) & (s <= lag)
    y0 = 0.5 * (lag / tau) * lag                       # quadratic ramp y = s²/(2·tau) up to the lag
    y[grow] = s[grow] ** 2 / (2 * tau)
    rest = s > lag
    plateau = y0 + lag                                 # slope at the join is lag/tau; exp carries it on
    y[rest] = plateau - lag * np.exp(-(s[rest] - lag) / tau)
    return t, y + np.random.default_rng(seed).normal(0, 0.002, n)


def test_the_window_starts_at_the_inflection_not_at_contact(_quiet):
    from pycat.toolbox.fusion_tools import suggest_fit_window, fit_fusion_relaxation
    t, y = _neck_then_relax()
    w = suggest_fit_window(t, y)
    assert 1.1 < w['t_start'] < 1.25                     # the end of the neck-growth phase (1.15 s)
    good = fit_fusion_relaxation(t, y, t_start=w['t_start'])
    assert good['tau_s'] == pytest.approx(0.25, rel=0.1)
    assert good['start_sensitivity']['stable']


def test_a_tau_that_moves_with_the_fit_start_is_flagged(_quiet):
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    t, y = _neck_then_relax(lag=0.4)
    fit = fit_fusion_relaxation(t, y, t_start=0.95)        # from before contact: lag + exponential
    assert fit['start_sensitivity']['stable'] is False
    assert any('depends on where the fit starts' in m for m in _quiet)


def test_bluelake_tiff_metadata_gives_frame_interval_and_pixel_size(tmp_path):
    import json
    import tifffile
    from pycat.file_io.metadata_extract import extract_tiff_metadata
    desc = json.dumps({"Camera": "Bright-field", "Framerate (Hz)": 15.000960061443932,
                       "Pixel calibration (nm/pix)": 85.696, "Exposure time (ms)": 10.0})
    p = tmp_path / 'bf.tiff'
    tifffile.imwrite(p, np.zeros((3, 8, 8), np.uint8), description=desc, software='Bluelake 2.3.2')
    common = extract_tiff_metadata(str(p))['common']
    assert common['frame_interval_s'] == pytest.approx(1 / 15.000960061443932)
    assert common['pixel_size_um'] == pytest.approx(0.085696)
    assert common['frame_interval_source'] == 'bluelake_framerate'


def test_force_traces_load_without_pylake(tmp_path):
    import h5py
    from pycat.file_io.frap_io import _load_lumicks_fusion_h5py
    p = tmp_path / 'fusion.h5'
    with h5py.File(p, 'w') as f:
        for ch in ('Force 1x', 'Force 2x'):
            ds = f.create_dataset(f'Force HF/{ch}', data=np.arange(10, dtype=float))
            ds.attrs['Sample rate (Hz)'] = 78125.0
            ds.attrs['Start time (ns)'] = np.int64(1698097070002208200)
    out = _load_lumicks_fusion_h5py(str(p))
    assert set(out['forces']) == {'F1x', 'F2x'} and out['sample_rate_hz'] == 78125.0
    assert out['n_samples'] == 10 and out['start_time_ns'] == 1698097070002208200


def test_the_verdict_does_not_depend_on_when_the_recording_started(_quiet):
    """A fusion 30 s into a movie is the same fusion: tau and the two-mode verdict must not move."""
    from pycat.toolbox.fusion_tools import fit_fusion_relaxation
    t = np.linspace(0, 3, 45)
    y = 1 + np.exp(-t / 0.25) + np.random.default_rng(0).normal(0, 0.005, t.size)
    at0 = fit_fusion_relaxation(t, y)
    at30 = fit_fusion_relaxation(t + 30.0, y)
    assert at30['tau_s'] == pytest.approx(at0['tau_s'], rel=1e-6)
    assert at0['is_two_mode'] is False and at30['is_two_mode'] is False
