"""Per-droplet boundaries for in-vitro fluorescence: one object per droplet, edge at half its height."""
import numpy as np
import pytest
import scipy.ndimage as ndi

from pycat.toolbox.invitro.droplets import segment_droplets_by_peak
from pycat.toolbox.invitro.segmentation import segment_ivf_droplets

pytestmark = pytest.mark.base

SHAPE = (160, 160)
YY, XX = np.mgrid[:SHAPE[0], :SHAPE[1]]


def _field(droplets, seed=0, noise=0.004, streak=None):
    """Uniform disks blurred by a PSF, plus noise. ``droplets`` = [(cy, cx, radius, amplitude)]."""
    rng = np.random.default_rng(seed)
    truth = np.zeros(SHAPE, np.int32)
    img = np.zeros(SHAPE)
    for i, (cy, cx, r, amp) in enumerate(droplets, start=1):
        disk = (YY - cy) ** 2 + (XX - cx) ** 2 <= r * r
        img += amp * disk
        truth[disk] = i
    img = ndi.gaussian_filter(img, 2.0)
    if streak is not None:
        y, x0, x1, amp = streak
        img[y:y + 2, x0:x1] += amp
    img = 0.05 + img + rng.normal(0, noise, SHAPE)
    return (img * 4000).astype(np.uint16), truth


@pytest.mark.parametrize('seed', [0, 1])
def test_a_bright_droplet_does_not_swallow_its_dim_touching_neighbour(seed):
    img, truth = _field([(80, 60, 22, 1.0), (80, 94, 12, 0.3)], seed=seed)    # edges touch
    lab = segment_droplets_by_peak(img)
    assert lab.max() == 2
    assert lab[80, 60] and lab[80, 96] and lab[80, 60] != lab[80, 96]
    assert (lab == lab[80, 96]).sum() > 0.6 * (truth == 2).sum()
    # a whole-field multi-Otsu cut fuses the same pair
    multi, _ = segment_ivf_droplets(img.astype(float), img, method='multiotsu', multiotsu_classes=3)
    assert multi[80, 60] == multi[80, 96] != 0


@pytest.mark.parametrize('radius', [6, 12, 25])
def test_the_edge_sits_at_the_droplet_edge_at_every_size(radius):
    img, truth = _field([(80, 80, radius, 0.8)])
    lab = segment_droplets_by_peak(img)
    assert lab.max() == 1
    ratio = (lab > 0).sum() / (truth > 0).sum()
    assert 0.8 < ratio < 1.2


@pytest.mark.parametrize('seed', [0, 1, 2])
def test_a_bright_droplets_glow_is_not_a_second_object(seed):
    img, truth = _field([(80, 80, 25, 1.0)], seed=seed)
    glow = 0.25 * np.exp(-((YY - 80) ** 2 + (XX - 80) ** 2) / (2 * 30.0 ** 2))
    img = img + (glow * 4000).astype(np.uint16)
    lab = segment_droplets_by_peak(img)
    assert lab.max() == 1


def test_the_same_droplet_has_the_same_size_at_any_brightness():
    areas = [(segment_droplets_by_peak(_field([(80, 80, 15, amp)])[0]) > 0).sum() for amp in (0.2, 0.5, 1.0)]
    assert max(areas) / min(areas) < 1.1


def test_a_faint_droplet_beside_bright_ones_is_counted():
    img, _ = _field([(50, 50, 18, 1.0), (50, 110, 18, 1.0), (120, 80, 6, 0.06)])
    lab = segment_droplets_by_peak(img)
    assert lab.max() == 3 and lab[120, 80]


@pytest.mark.parametrize('seed', [0, 1, 2])
def test_noise_alone_gives_nothing(seed):
    img, _ = _field([], seed=seed)
    assert segment_droplets_by_peak(img).max() == 0


def test_a_scan_line_streak_is_not_a_droplet():
    img, _ = _field([(100, 80, 12, 0.6)], streak=(40, 40, 90, 0.3))
    lab = segment_droplets_by_peak(img)
    assert lab.max() == 1 and lab[100, 80] and not lab[40:42, 40:90].any()


def test_droplets_never_touch():
    img, _ = _field([(80, 50, 20, 1.0), (80, 92, 20, 0.9), (40, 80, 10, 0.5)])
    lab = segment_droplets_by_peak(img)
    grown = ndi.grey_dilation(lab, size=3)
    assert not ((lab > 0) & (grown != lab)).any()


def test_the_ivf_entry_point_runs_it_on_the_raw_image():
    img, _ = _field([(80, 60, 22, 1.0), (80, 104, 12, 0.3)])
    pre = ndi.gaussian_filter(img.astype(float), 1.0)
    lab, fg = segment_ivf_droplets(pre, img, method='droplet')
    np.testing.assert_array_equal(lab, segment_droplets_by_peak(img))
    assert lab.dtype == np.int32 and np.array_equal(fg, lab > 0)


def test_droplet_is_the_default_in_the_panel_and_the_navigator():
    import pathlib
    from pycat.navigator.executor import _IVF_SEG_DEFAULTS
    from pycat.navigator.parameters import _MATERIAL
    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'src/pycat/toolbox/invitro_fluor_ui.py').read_text(encoding='utf-8')
    assert 'rb_drop.setChecked(True)' in src and "method = 'droplet'" in src
    assert _IVF_SEG_DEFAULTS['method'] == 'droplet'
    method = [p for p in _MATERIAL['ivf_droplet_segment'] if p.name == 'method'][0]
    assert method.default == 'droplet'
