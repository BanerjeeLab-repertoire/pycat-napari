"""Regression tests for the panel bugs the kernel batch E scenarios found (2026-10-20).

Each was the same in the pre-migration tree, so the refactor did not cause them; the real-data scenarios
were the first thing to drive these panels end to end. One test per bug, on the input that exposed it.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base


# ── fusion: the threshold (fluorescence) mask kept only the largest object ───────────────────────────────
def _merging_disks():
    yy, xx = np.mgrid[0:64, 0:64]
    frames = []
    for t in range(20):
        f = np.zeros((64, 64), np.float32)
        if t < 5:
            f[(yy - 32) ** 2 + (xx - 22 - t) ** 2 <= 49] = 1
            f[(yy - 32) ** 2 + (xx - 42 + t) ** 2 <= 49] = 1
        else:
            a = 1.0 + 0.8 * np.exp(-(t - 5) / 3.0)
            f[((yy - 32) * a) ** 2 / 100 + (xx - 32) ** 2 / (100 * a) <= 1] = 1
        frames.append(f)
    return np.stack(frames)


def test_fusion_threshold_mode_sees_both_droplets_before_contact():
    pytest.importorskip('skimage')
    from pycat.toolbox.fusion_tools import aspect_ratio_signal, droplet_radii_um
    stack = _merging_disks() * 200.0 + 10.0
    _, ar, det = aspect_ratio_signal(stack, 'otsu', 1.0, return_details=True)
    assert (det['n_objects'][:3] == 2).all()          # was 1 in every frame: the second droplet was dropped
    assert ar[0] > 2.5                                 # the pair's union is elongated, not one round drop
    radii = droplet_radii_um(det, 5, 0.1)
    assert np.isfinite(radii['R1_um']) and np.isfinite(radii['R2_um'])


# ── contrast cascade: tied percentile edges collapsed the requested bands ─────────────────────────────────
def test_percentile_cascade_returns_the_requested_bands_on_a_zero_heavy_image():
    pytest.importorskip('skimage')
    from pycat.toolbox.contrast_cascade_tools import contrast_cascade_bands
    rng = np.random.default_rng(0)
    img = np.zeros((128, 128))
    img[32:96, 32:96] = rng.gamma(2.0, 50.0, (64, 64))   # 75 % of the field is the zero pedestal
    bands = contrast_cascade_bands(img, 4, 'percentile')
    assert len(bands) == 4                               # was 2
    assert bands[-1]['low'] == 0.0                       # the pedestal is the dimmest band


# ── spatial null: a single block silently became a PIXEL shuffle ──────────────────────────────────────────
def test_spatial_null_refuses_when_the_block_does_not_fit_twice():
    pytest.importorskip('scipy')
    from pycat.toolbox.coloc.nulls import spatial_null_test
    rng = np.random.default_rng(0)
    a = rng.normal(size=(128, 128))
    r = spatial_null_test(a, a + rng.normal(size=a.shape), n_permutations=20, block_size=128)
    assert np.isnan(r['p_value']) and r['n_blocks'] == 1 and not r['significant']
    assert 'no spatial-null p-value' in r['verdict']


def test_spatial_null_cautions_with_few_blocks():
    pytest.importorskip('scipy')
    from pycat.toolbox.coloc.nulls import spatial_null_test
    rng = np.random.default_rng(0)
    a = rng.normal(size=(64, 64))
    r = spatial_null_test(a, rng.normal(size=a.shape), n_permutations=20, block_size=16)
    assert r['n_blocks'] == 16 and 'only 16 blocks' in r['verdict']


# ── FRAP: an unphysical mobile fraction from the RD / circular fits was shown without comment ────────────
def test_frap_flags_a_mobile_fraction_outside_zero_one(monkeypatch):
    import pycat.toolbox.frap_tools as ft
    seen = []
    monkeypatch.setattr(ft, 'napari_show_warning', seen.append)
    assert ft._warn_mobile_out_of_range(3.0, 0.1, 'reaction-diffusion') and 'outside [0, 1]' in seen[-1]
    assert not ft._warn_mobile_out_of_range(1.02, 0.05, 'circular')   # within 2 sigma of 1: noise, no nag
    assert not ft._warn_mobile_out_of_range(float('nan'), 0.1, 'circular')


# ── SpIDA: viewer.metadata does not exist on napari 0.9.1 ─────────────────────────────────────────────────
class _Viewer:                 # deliberately has no .metadata, like napari's Viewer
    pass


class _Layer:
    def __init__(self, data):
        self.data = data


def test_spida_calibration_is_kept_and_used_by_auto_analysis(monkeypatch):
    import pycat.toolbox.spida_tools as st
    produce = st.OperationService.produce

    def _fake(op, inputs, params=None, **kw):
        if op == 'spida.fit':
            return dict(success=True, epsilon=40.0, N=2.0, r_squared=0.99)
        return produce(op, inputs, params, **kw) if params is not None else produce(op, inputs, **kw)

    monkeypatch.setattr(st.OperationService, 'produce', staticmethod(_fake))
    infos = []
    monkeypatch.setattr(st, 'napari_show_info', infos.append)
    monkeypatch.setattr(st, 'napari_show_warning', lambda *a, **k: None)
    viewer = _Viewer()
    img = _Layer(np.random.default_rng(0).poisson(30, (64, 64)).astype(np.uint16))
    assert st.run_spida_calibration(img, None, 50, 0.0, viewer) == 40.0    # raised AttributeError before
    assert st._stored_epsilon0(viewer) == 40.0
    st.run_spida_analysis(img, None, 50, 0.0, 0.0, viewer)                 # 0 = auto: raised before
    assert any('Monomer ref eps_0: 40.0' in m for m in infos)


# ── topology: a list-valued per-cell metric could not be written into cell_df ─────────────────────────────
def test_topology_writes_list_metrics_per_cell(monkeypatch):
    pytest.importorskip('napari')
    pd = pytest.importorskip('pandas')
    import pycat.toolbox.topology_tools as tt
    import pycat.ui.ui_utils as uu
    monkeypatch.setattr(uu, 'add_image_with_default_colormap', lambda *a, **k: None)

    labels = np.zeros((64, 64), np.int32)
    labels[8:30, 8:30] = 1
    labels[34:60, 34:60] = 2
    yy, xx = np.mgrid[0:64, 0:64]
    image = (100 + 50 * np.sin(yy / 3.0) * np.cos(xx / 4.0)).astype(np.float32) * (labels > 0)

    class _Layers(dict):
        pass

    class _V:
        layers = _Layers({'Labeled Cell Mask': _Layer(labels)})

    class _DI:
        data_repository = {'ball_radius': 5}

        def get_data(self, key, default=None):
            return self.data_repository.get(key, default)

    di = _DI()
    di.data_repository['cell_df'] = pd.DataFrame({'label': [1, 2]})
    lyr = _Layer(image)
    lyr.name = 'dapi'
    tt.run_chromatin_topology(lyr, di, _V())          # raised "Must have equal len keys and value" before
    df = di.data_repository['cell_df']
    assert df['topo_basin_persistences'].map(lambda v: isinstance(v, str)).all()
