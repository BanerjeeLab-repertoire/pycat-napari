"""**The cellular time series measures what the opto movie does, on raw counts, the way 2D segments.**

Pinned on a synthetic dissolution movie with a KNOWN answer: one bright cell whose 12 condensates hand their
material back to the dilute phase with a 50 s half-time (5 frames x 10 s) -- total signal conserved -- next to
a dim, diffuse cell that never condenses (the transfection heterogeneity). Poisson noise on a 100-count camera
offset, as on the real ``dissolution.tif``.

What the time axis demands, each as a test:

* the dissolving cell responds, falls, and its half-time comes out near 50 s on BOTH measures (mask-based and
  mask-free);
* the diffuse cell responds on neither and grows no condensates;
* intensities stay connected in time: the dilute phase RISES as the condensates dissolve and the cell's total
  signal is conserved -- per-frame normalisation (the old "Dissolution" preset) would have flattened both;
* a condensate keeps ONE label value across frames (track_id + 1), so it can be followed in the viewer;
* the time series segments with exactly the 2D GUI's settings, by construction.
"""
import inspect

import numpy as np
import pytest

pytestmark = pytest.mark.base

HALF_TIME_S = 50.0
FRAME_S = 10.0


def _dissolving_movie(T=14, seed=0, half_frame=5.0, offset=100.0, peak=300.0):
    rng = np.random.default_rng(seed)
    H, W = 180, 240
    yy, xx = np.mgrid[:H, :W]
    c1 = ((yy - 90) / 60.0) ** 2 + ((xx - 70) / 55.0) ** 2 < 1
    c2 = ((yy - 90) / 55.0) ** 2 + ((xx - 180) / 45.0) ** 2 < 1
    cells = np.zeros((H, W), np.int32)
    cells[c1] = 1
    cells[c2] = 2
    pts = []
    while len(pts) < 12:
        y, x = rng.uniform(45, 135), rng.uniform(25, 115)
        if c1[int(y), int(x)] and all((y - a) ** 2 + (x - b) ** 2 > 14 ** 2 for a, b in pts):
            pts.append((y, x))
    blob = sum(np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 1.8 ** 2)) for y, x in pts)
    frac = np.exp(-np.log(2) / half_frame * np.arange(T))
    total_cond = peak * blob.sum()
    stack = np.zeros((T, H, W), np.float32)
    for t in range(T):
        cond = peak * frac[t] * blob
        dilute = 6.0 + (total_cond - cond.sum()) / c1.sum()      # what leaves the condensates joins the dilute
        img = offset + np.where(c1, dilute + cond, 0) + np.where(c2, 4.0, 0)
        stack[t] = rng.poisson(img).astype(np.float32)
    return stack, cells


@pytest.fixture(scope='module')
def result():
    from pycat.toolbox.timeseries.cell_pipeline import analyze_cell_timeseries
    stack, cells = _dissolving_movie()
    return analyze_cell_timeseries(stack, cells, 3, 40, frame_interval_s=FRAME_S, microns_per_pixel=0.1,
                                   n_workers=1)


def _trace(r, cell, col):
    d = r.per_cell[r.per_cell['cell'] == cell].sort_values('frame')
    return d[col].to_numpy(dtype=float)


@pytest.mark.parametrize('kin', ['kinetics', 'kinetics_mask_free'])
def test_the_dissolving_cell_falls_with_the_true_half_time(result, kin):
    row = getattr(result, kin).set_index('cell').loc[1]
    assert row['responded'] and row['direction'] == 'fall'
    assert 0.6 * HALF_TIME_S <= row['t_half_s'] <= 1.4 * HALF_TIME_S, (
        f"{kin}: half-time {row['t_half_s']:.0f} s, truth {HALF_TIME_S:.0f} s")


@pytest.mark.parametrize('kin', ['kinetics', 'kinetics_mask_free'])
def test_the_diffuse_low_expressing_cell_does_not_respond(result, kin):
    assert not getattr(result, kin).set_index('cell').loc[2, 'responded']
    assert _trace(result, 2, 'n_condensates').sum() == 0


def test_intensity_stays_connected_in_time(result):
    """Raw counts minus the camera background, never rescaled: the dilute phase gains what the condensates
    lose (6 -> ~12 counts here) while the cell's total stays put."""
    dilute = _trace(result, 1, 'dilute_net')
    assert dilute[-3:].mean() > 1.6 * dilute[:2].mean()
    retained = _trace(result, 1, 'signal_retained')
    assert np.all(np.abs(retained - 1) < 0.08), retained.round(3)
    frac = _trace(result, 1, 'condensed_fraction_mask_free')
    assert frac[0] > 0.25 and frac[-1] < 0.05


def test_expression_is_the_covariate_not_a_normaliser(result):
    k = result.kinetics.set_index('cell')
    assert k.loc[1, 'expression'] > 3 * k.loc[2, 'expression']


def test_a_condensate_keeps_one_label_value_through_time(result):
    pc = result.per_condensate
    long = pc.groupby('track_id')['frame'].nunique()
    tid = int(long.idxmax())
    assert long.max() >= 4
    rows = pc[pc['track_id'] == tid]
    for _, row in rows.iterrows():
        i = result.frames.index(int(row['frame']))
        y, x = int(round(row['y_px'])), int(round(row['x_px']))
        y0, _, x0, _ = result.roi
        assert result.condensate_labels[i, y - y0, x - x0] == tid + 1


def test_the_time_series_segments_with_the_2D_settings():
    """`CELLULAR_2D_SETTINGS` (what the time series uses) must equal the 2D GUI runner's defaults."""
    from pycat.toolbox.segmentation.subcellular import CELLULAR_2D_SETTINGS, run_segment_subcellular_objects
    defaults = {k: p.default for k, p in inspect.signature(run_segment_subcellular_objects).parameters.items()}
    for key, value in CELLULAR_2D_SETTINGS.items():
        assert defaults[key] == value, f"{key}: time series {value!r}, 2D GUI {defaults[key]!r}"


def test_cell_mask_stretching_on_a_crop_matches_the_whole_frame():
    """Each cell's CLAHE now runs on a crop (25x faster on a 13-cell 2048^2 frame); the pixels must be the
    ones the whole-frame CLAHE gave."""
    import math
    import scipy.ndimage as ndi
    import skimage as sk
    from pycat.toolbox.segmentation.morphology import cell_mask_stretching

    rng = np.random.default_rng(3)
    img = (rng.random((301, 257)) * 0.2).astype(np.float32)
    img[110:120, 130:140] += 0.8
    lab = np.zeros(img.shape, np.int32)
    lab[0:60, 0:50] = 1                       # touches two image edges
    lab[100:180, 120:200] = 2
    lab[250:301, 200:257] = 3                 # touches the other two
    lab[140:150, 10:30] = 4

    out = cell_mask_stretching(img, lab)
    for cell in (1, 2, 3, 4):                 # the pre-1.7.004 whole-frame computation, per cell
        cm = lab == cell
        dil = ndi.binary_dilation(cm, sk.morphology.disk(3))
        k = math.ceil(np.sqrt(cm.sum() / np.pi) / 4)
        ref = sk.exposure.equalize_adapthist(img * dil, kernel_size=k, clip_limit=0.0025)
        ero = ndi.binary_erosion(dil, sk.morphology.disk(3))
        inner = ndi.binary_erosion(ero, sk.morphology.disk(1))   # away from the final grey open's reach
        expect = ndi.grey_erosion(ndi.grey_dilation(np.where(ero, ref, 0), footprint=sk.morphology.disk(1)),
                                  footprint=sk.morphology.disk(1))
        assert np.allclose(out[inner], expect[inner], atol=1e-6), f"cell {cell}"


def test_kinetics_needs_a_real_change_not_just_a_quiet_trace():
    """A trace that sits at 0 and then picks up a little static texture (0.012 of the signal) has 'no
    noise' to compare with; it must not count as a response."""
    import pandas as pd
    from pycat.toolbox.timeseries.cell_kinetics import cell_kinetics
    y = np.r_[np.zeros(40), np.full(20, 0.012)]
    df = pd.DataFrame({'cell': 1, 'frame': np.arange(60), 'condensed_fraction': y, 'expression': 3.0})
    assert not cell_kinetics(df, frame_interval_s=10.0).loc[0, 'responded']


def test_batch_replay_writes_linked_outputs_every_movie_can_be_read_back(tmp_path):
    """The batch step writes the per-movie tables and images, and the batch results read them back as
    brushable tables whose ids match between a cell's summary row and its trace rows."""
    from types import SimpleNamespace
    import pandas as pd
    from pycat.batch.steps.timeseries_cell_steps import replay_cell_timeseries
    from pycat.batch_step_registry import _STEP_MAP
    from pycat.utils.batch_brushing import assemble_batch_object_tables, per_image_csvs
    from pycat.utils.entity_ref import ENTITY_ID_COLUMN

    assert _STEP_MAP['cell_timeseries'] is replay_cell_timeseries
    stack, cells = _dissolving_movie(T=8)
    src = tmp_path / 'movie.tif'
    out = tmp_path / 'out' / 'movie'
    out.mkdir(parents=True)
    state = {'image': stack, 'labeled_cells': cells,
             'data_instance': SimpleNamespace(data_repository={'microns_per_pixel_sq': 0.01, 'file_path': str(src)})}
    replay_cell_timeseries(state, src, {'ball_radius': 3, 'window_size': 40, 'frame_interval_s': 10.0,
                                        'n_workers': 1}, out)
    for suffix in ('ts_cells_df.csv', 'ts_condensate_tracks_df.csv', 'ts_cell_frames.csv', 'ts_condensates.csv',
                   'ts_summary.csv', 'measured_image.tiff', 'labeled_cells.tiff', 'condensate_tracks.tiff'):
        assert (out / f'movie_{suffix}').is_file(), suffix

    tables = assemble_batch_object_tables(tmp_path / 'out', [src])
    cells_df = tables['ts_cells']
    assert set(cells_df['cell']) == {1, 2} and cells_df['label'].tolist() == cells_df['cell'].tolist()
    frames = per_image_csvs(tmp_path / 'out', [src], 'ts_cell_frames')
    assert set(frames[ENTITY_ID_COLUMN]) == set(cells_df[ENTITY_ID_COLUMN])
    assert isinstance(pd.read_csv(out / 'movie_ts_summary.csv'), pd.DataFrame)


def test_default_workers_fit_in_memory_not_just_cores():
    """A 2048^2 frame job peaks near 0.9 GB; eight of them beside the viewer filled a 32 GB machine. The default
    is capped by the memory available, so a modest computer runs fewer workers instead of swapping."""
    from pycat.toolbox.timeseries.cell_pipeline import default_workers
    assert default_workers((2048, 2048), cpu_count=24, available_bytes=64e9) == 8
    assert default_workers((2048, 2048), cpu_count=24, available_bytes=8e9) == 4
    assert default_workers((2048, 2048), cpu_count=4, available_bytes=2e9) == 1
    assert default_workers((512, 512), cpu_count=4, available_bytes=2e9) == 3


def test_worker_initializer_does_not_import_torch():
    import sys
    import subprocess
    code = ("import sys; from pycat.toolbox.timeseries.analysis import _init_worker_threads; "
            "_init_worker_threads(); print('torch' in sys.modules)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=300)
    assert out.stdout.strip().splitlines()[-1] == 'False', out.stdout + out.stderr
