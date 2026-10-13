"""**Time-series in-vitro: multi-channel measurement, per-droplet kinetics, raw field trajectories (1.7.002).**

Synthetic droplet movies with KNOWN answers: a camera offset and dilute-phase signal that the local background
must cancel, droplets that light up at known times, and a field whose brightness changes over time (which the
old per-frame [0, 1] normalisation divided out).
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.base

OFFSET = 500.0          # camera offset, in every pixel


def _disk_labels(shape=(120, 120), centres=((30, 30), (30, 75), (80, 40), (85, 90)), r=9):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    lab = np.zeros(shape, np.int32)
    for i, (cy, cx) in enumerate(centres, 1):
        lab[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = i
    return lab


def _movie(T=60, onsets=(10, 20, 30, 40), dilute=50.0, amp=1000.0, seed=0):
    """Droplet k turns on (sigmoid) at frame onsets[k]; the dilute phase sits at OFFSET + dilute."""
    rng = np.random.default_rng(seed)
    lab = _disk_labels()
    labels = np.repeat(lab[None], T, axis=0)
    movie = np.full((T,) + lab.shape, OFFSET + dilute)
    for t in range(T):
        for k, on in enumerate(onsets, 1):
            movie[t][lab == k] += amp / (1 + np.exp(-(t - on) / 1.5))
    movie += rng.normal(0, 5, movie.shape)
    return labels, movie


def test_net_signal_cancels_the_camera_offset_and_dilute_phase():
    from pycat.toolbox.invitro.timeseries_kinetics import measure_droplets_multichannel
    labels, movie = _movie()
    df, dilute = measure_droplets_multichannel(labels, {'C0-488': movie}, microns_per_pixel=0.1)
    last = df[df.frame == 59]
    assert np.allclose(last['net_C0_488'], 1000, atol=15), last['net_C0_488'].tolist()
    assert np.allclose(last['bg_C0_488'], OFFSET + 50, atol=5)
    assert np.allclose(dilute['dilute_C0_488'], OFFSET + 50, atol=5)
    assert {'frame', 'object_id', 'y_um', 'x_um', 'area_um2', 'equiv_radius_um'} <= set(df.columns)


def test_the_background_ring_never_enters_a_neighbouring_droplet():
    """Two touching droplets, one bright: the dim one's background must still be the dilute phase."""
    from pycat.toolbox.invitro.timeseries_kinetics import measure_droplets_multichannel
    lab = _disk_labels(centres=((60, 40), (60, 59)), r=9)          # 1 px apart
    img = np.full(lab.shape, OFFSET)
    img[lab == 1] += 5000.0
    img[lab == 2] += 100.0
    df, _ = measure_droplets_multichannel(lab[None], {'ch': img[None]}, 1.0)
    dim = df[df.object_id == 2].iloc[0]
    assert abs(dim['bg_ch'] - OFFSET) < 1 and abs(dim['net_ch'] - 100) < 1


def test_kinetics_recover_the_known_onset_and_half_times():
    from pycat.toolbox.invitro.timeseries_kinetics import measure_droplets_multichannel, droplet_kinetics
    labels, movie = _movie()
    df, _ = measure_droplets_multichannel(labels, {'C0-488': movie}, 0.1)
    df['track_id'] = df['object_id']                                   # static droplets: identity is the label
    kin = droplet_kinetics(df, 'C0-488', frame_interval_s=60.0, smooth_frames=1).set_index('track_id')
    assert kin['responded'].all()
    for k, on in enumerate((10, 20, 30, 40), 1):
        assert abs(kin.loc[k, 't_half_s'] / 60 - on) < 0.6, (k, kin.loc[k, 't_half_s'] / 60)
        assert kin.loc[k, 't_onset_s'] < kin.loc[k, 't_half_s'] < kin.loc[k, 't_90_s']
        assert abs(kin.loc[k, 'amplitude'] - 1000) < 30


def test_a_droplet_that_never_changes_does_not_respond():
    from pycat.toolbox.invitro.timeseries_kinetics import measure_droplets_multichannel, droplet_kinetics
    labels, movie = _movie(onsets=(10, 1000, 30, 40))                # droplet 2 never turns on
    df, _ = measure_droplets_multichannel(labels, {'c': movie}, 0.1)
    df['track_id'] = df['object_id']
    kin = droplet_kinetics(df, 'c', 60.0).set_index('track_id')
    assert not kin.loc[2, 'responded'] and np.isnan(kin.loc[2, 't_half_s'])


def test_heterogeneity_rises_then_collapses_and_the_summary_counts_it():
    from pycat.toolbox.invitro.timeseries_kinetics import (measure_droplets_multichannel, droplet_kinetics,
                                                            kinetics_heterogeneity, heterogeneity_over_time)
    labels, movie = _movie(onsets=(10, 20, 30, 40))
    df, _ = measure_droplets_multichannel(labels, {'c': movie}, 0.1)
    df['track_id'] = df['object_id']
    kin = droplet_kinetics(df, 'c', 60.0)
    curve = heterogeneity_over_time(df, kin, 'c').set_index('frame')
    assert curve.loc[2, 'progress_iqr'] < 0.1 and curve.loc[58, 'progress_iqr'] < 0.1
    assert curve.loc[25, 'progress_iqr'] > 0.4                          # mid-movie: half the droplets are on
    assert curve.loc[25, 'fraction_activated'] == pytest.approx(0.5)
    s = kinetics_heterogeneity(kin)
    assert s['n_responded'] == 4 and s['fraction_responded'] == 1.0
    assert abs(s['t_half_s_median'] / 60 - 25) < 1


def test_field_trajectories_no_longer_normalise_each_frame():
    """The reaction raises the WHOLE field 5x over the movie; per-frame [0, 1] scaling made every frame look the
    same. Raw counts keep the rise in the dense and dilute intensities."""
    from pycat.toolbox.timeseries_invitro_tools import field_trajectories
    lab = _disk_labels()
    T = 6
    labels = np.repeat(lab[None], T, axis=0)
    movie = np.stack([np.where(lab > 0, 1000.0 * (1 + t), 100.0 * (1 + t)) + OFFSET for t in range(T)])
    movie = movie.astype(np.uint16)
    out = field_trajectories(labels, movie, microns_per_pixel=0.1, frame_interval_s=60.0)
    assert out['c_dense_proxy'].iloc[-1] > 3 * out['c_dense_proxy'].iloc[0]
    assert out['c_sat_proxy'].iloc[-1] > out['c_sat_proxy'].iloc[0]


def test_threaded_stack_segmentation_matches_serial():
    from pycat.toolbox.timeseries_invitro_tools import segment_stack_per_frame
    from skimage import measure
    _, movie = _movie(T=8)
    fn = lambda f, t: measure.label(f > OFFSET + 400)
    assert np.array_equal(segment_stack_per_frame(movie, fn), segment_stack_per_frame(movie, fn, n_workers=4))


def test_the_ims_frame_interval_is_read_from_the_per_timepoint_stamps(tmp_path):
    h5py = pytest.importorskip("h5py")
    from pycat.file_io.metadata_extract import extract_ims_metadata
    p = tmp_path / "t.ims"
    with h5py.File(p, "w") as f:
        g = f.create_group("DataSetInfo/TimeInfo")
        for i, s in enumerate(("2026-04-11 17:45:42.569", "2026-04-11 17:46:42.700", "2026-04-11 17:47:42.500",
                               "2026-04-11 17:48:42.650"), 1):
            g.attrs[f"TimePoint{i}"] = np.frombuffer(s.encode(), dtype="S1")
    c = extract_ims_metadata(str(p))["common"]
    assert c["frame_interval_s"] == pytest.approx(60.0, abs=0.2) and c["frame_interval_source"] == "ims_timeinfo"
    assert c["n_timepoints"] == 4


def test_lazy_ims_frames_are_never_read_concurrently():
    """imaris_ims_file_reader is not thread-safe: concurrent reads of a lazy IMS layer killed the process with no
    traceback (the time-series measurement read frames from 8 threads). Every read now holds one lock."""
    import threading, time
    from concurrent.futures import ThreadPoolExecutor
    from pycat.file_io.readers.ims_reader import _ImsReaderTYX

    class _Reader:
        shape = (16, 1, 1, 4, 4)
        active, overlaps = 0, 0
        requested = []
        guard = threading.Lock()

        def __getitem__(self, idx):
            with self.guard:
                type(self).requested.append(int(idx[0]))
                type(self).active += 1
                if type(self).active > 1:
                    type(self).overlaps += 1
            time.sleep(0.005)
            with self.guard:
                type(self).active -= 1
            return np.full((4, 4), idx[0], np.uint16)

    lazy = _ImsReaderTYX(_Reader(), 0)
    with ThreadPoolExecutor(8) as ex:
        frames = list(ex.map(lambda t: lazy[t], range(16)))
    assert _Reader.overlaps == 0
    assert sorted(_Reader.requested) == list(range(16)) and len(frames) == 16
