"""**Data QC returns no verdict it cannot support** (Completion Spec Phase G, G2–G12).

Each test reproduces a measured failure on synthetic data with a known answer (``tests/synthetic``) and pins the
fix. The "before" numbers are the shipped behaviour the spec measured and this file re-measured; every assertion
fails on the old code.
"""
import json

import numpy as np
import pytest

from tests.synthetic import bleaching_stack, noise_stack

pytestmark = pytest.mark.base


@pytest.fixture(scope='module')
def qc():
    return pytest.importorskip('pycat.toolbox.data_qc_tools')


# ── G2: photobleaching tracks the SIGNAL, not the background ──────────────────────────────────────────────────

@pytest.mark.parametrize('pedestal', [0.0, 100.0, 1000.0])
def test_photobleaching_measures_the_objects_not_the_background(qc, pedestal):
    """Sparse objects decaying with tau = 8 to 2.7 % on a constant pedestal: the per-frame median (the background)
    reported 'good | 100 % remains'."""
    stack, truth = bleaching_stack(tau=8.0, pedestal=pedestal)
    r = qc.qc_photobleaching(stack)
    assert r['status'] == 'bad', r['headline']
    assert r['value'] == pytest.approx(truth * 100, abs=1.5), r['headline']
    assert r['diag']['basis'] == 'objects'


def test_a_non_bleaching_sparse_field_is_still_good(qc):
    stack, _ = bleaching_stack(tau=1e9)
    assert qc.qc_photobleaching(stack)['status'] == 'good'


# ── G3: SNR is per frame — the verdict must not depend on how many frames there are ──────────────────────────

def test_snr_does_not_rise_with_the_frame_count(qc):
    """Identical per-frame data (sigma 25): mean-frame SNR read 4.7 / 12.2 / 33 at 1 / 16 / 200 frames."""
    values, statuses = [], []
    for n in (1, 16, 200):
        st = noise_stack(sigma=25.0, n_frames=n)
        r = qc.qc_snr(st if n > 1 else st[0])
        values.append(r['value'])
        statuses.append(r['status'])
        assert r['diag']['noise'] == pytest.approx(25.0, rel=0.1), 'the reported noise is the per-frame noise'
    assert len(set(statuses)) == 1, statuses
    assert max(values) / min(values) < 1.05, values
    assert 'per-frame' in qc.qc_snr(noise_stack(n_frames=16))['how']


def test_a_flat_field_has_no_snr_to_be_good_about(qc):
    """A constant field returned SNR = inf, status 'good'."""
    assert qc.qc_snr(np.full((64, 64), 5.0))['status'] == 'na'


# ── G4: NaN pixel size refuses ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -0.1, 0.0, None])
def test_an_unusable_pixel_size_never_produces_a_verdict(qc, bad):
    """``float('nan')`` is truthy: qc_nyquist and qc_focus returned 'bad' with NaN in the value column."""
    assert qc.qc_nyquist(bad, 1.4, 520)['status'] not in ('good', 'warn', 'bad')
    assert qc.diffraction_limit_px(bad, 1.4, 520) is None
    scene = noise_stack(sigma=2.0, n_frames=1)[0]
    r = qc.qc_focus(scene, pixel_um=bad, na=1.4, wavelength_nm=520)
    assert r['status'] in ('info', 'na'), 'no absolute focus verdict without a usable pixel size'
    assert 'diffraction' not in r['unit']


def test_the_manuscript_qc_panel_does_not_pass_a_nan_pixel_size():
    from pycat.toolbox.manuscript.panels import _qc_kwarg_usable
    assert not _qc_kwarg_usable('pixel_um', float('nan'))
    assert _qc_kwarg_usable('pixel_um', 0.1) and _qc_kwarg_usable('axis', 't')


# ── G5: saturation does not depend on the dtype ───────────────────────────────────────────────────────────────

def _clipped_scene():
    rng = np.random.default_rng(3)
    img = rng.normal(150.0, 60.0, (256, 256)).clip(1, None)
    img[100:140, 100:140] = 500.0
    return np.round(img.clip(0, 300))


def test_float_and_integer_copies_of_one_image_get_the_same_saturation_verdict(qc):
    """float32 clipped at 300 rounded its ceiling up to 4095 and reported 0.00 %, good; uint16 reported bad."""
    img = _clipped_scene()
    as_float = qc.qc_saturation(img.astype(np.float32))
    as_uint = qc.qc_saturation(img.astype(np.uint16))
    assert as_float['status'] == as_uint['status'] == 'bad'
    assert as_float['value'] == pytest.approx(as_uint['value'])
    assert as_float['diag']['ceiling'] == pytest.approx(300.0)


def test_a_normalised_unclipped_float_image_is_not_called_clipped(qc):
    img = np.random.default_rng(0).random((128, 128)).astype(np.float32)
    assert qc.qc_saturation(img)['status'] == 'good'


# ── G6: a 3-D array of unknown meaning is refused, not assumed to be a movie ─────────────────────────────────

def _through_focus(n=21):
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[:128, :128]
    obj = np.zeros((128, 128), bool)
    for _ in range(15):
        y, x = rng.integers(10, 118, 2)
        obj |= (yy - y) ** 2 + (xx - x) ** 2 < 16
    return np.stack([gaussian_filter(np.where(obj, 1000.0, 100.0), abs(z - n // 2) * 0.6 + 0.5)
                     + rng.normal(0, 5, (128, 128)) for z in range(n)])


def test_a_zstack_with_unstated_axis_is_not_reported_as_drifting(qc):
    """Zero lateral drift, no axis given: reported 'Drift bad — 79 px' (the spec measured 101 px)."""
    by = {r['name']: r for r in qc.run_full_qc(_through_focus())}
    assert by['Drift']['status'] == 'na'
    assert by['Stack axis']['status'] == 'na'
    for name in ('Vibration', 'Photobleaching', 'Ghosting (double image)', 'Focus / sharpness'):
        assert by[name]['status'] == 'na', name


def test_stating_the_axis_runs_the_axis_checks(qc):
    st = noise_stack(sigma=5.0, n_frames=12)
    by_t = {r['name']: r for r in qc.run_full_qc(st, axis='t')}
    assert by_t['Drift']['status'] in ('good', 'warn', 'bad') and 'Stack axis' not in by_t
    by_z = {r['name']: r for r in qc.run_full_qc(_through_focus(), is_zstack=True)}
    assert by_z['Drift']['status'] == 'na' and by_z['Spherical aberration']['status'] != 'na'


def test_a_channel_stack_is_compared_as_channels(qc):
    """A (C, Y, X) image gave 'Drift: bad, 45 px'; there was no channel case at all."""
    rng = np.random.default_rng(1)
    base = _through_focus(1)[0]
    chans = np.stack([base + rng.normal(0, 5, base.shape) for _ in range(3)])
    by = {r['name']: r for r in qc.run_full_qc(chans, axis='c')}
    assert by['Drift']['status'] == 'na'
    assert by['Chromatic aberration']['status'] in ('good', 'warn', 'bad')
    assert by['Chromatic aberration']['value'] < 2.0


def test_a_4d_array_is_refused_with_a_message(qc):
    """It raised a bare 'ValueError: too many values to unpack' from deep inside the vignetting check."""
    from pycat.utils.errors import UnsupportedFormatError
    with pytest.raises(UnsupportedFormatError, match='4-D'):
        qc.run_full_qc(np.zeros((2, 3, 32, 32)))


# ── G7: noise is measured along both axes ─────────────────────────────────────────────────────────────────────

def test_rotating_the_camera_does_not_change_the_snr_verdict(qc):
    """Row-structured sigma-25 noise read SNR 1,880 'good' (x-differences cancel it); column noise read 5.7 'warn'."""
    row = qc.qc_snr(noise_stack(sigma=25.0, n_frames=1, axis='row')[0])
    col = qc.qc_snr(noise_stack(sigma=25.0, n_frames=1, axis='col')[0])
    assert row['status'] == col['status']
    assert row['value'] == pytest.approx(col['value'], rel=0.15)
    assert row['diag']['noise'] == pytest.approx(25.0, rel=0.25)


# ── G8: a blank noisy field is not "better than diffraction-limited" ─────────────────────────────────────────

def test_a_blank_noisy_field_gets_no_focus_verdict(qc):
    """N(1000, 10), no specimen: 'good | sharpest edge 0.7 px vs a diffraction limit of 1.2 px — 0.6x'."""
    blank = np.random.default_rng(0).normal(1000.0, 10.0, (256, 256))
    with_optics = qc.qc_focus(blank, pixel_um=0.065, na=1.4, wavelength_nm=520)
    assert with_optics['status'] == 'na'
    assert 'noise' in with_optics['how']
    assert qc.qc_focus(blank)['status'] == 'na'


# ── G9: a background-subtracted floor is declined ────────────────────────────────────────────────────────────

def test_a_subtracted_floor_is_reported_as_not_assessable(qc):
    """46.6 % of pixels at exactly 0 reported 'good | 0.00 % at floor'."""
    rng = np.random.default_rng(0)
    img = np.clip(rng.normal(0, 20, (256, 256)), 0, None)
    img[60:120, 60:120] += 1000
    r = qc.qc_saturation(img.astype(np.uint16))
    assert r['diag']['floor_assessable'] is False
    assert 'not assessable' in r['headline'] and '0.00% at floor' not in r['headline']


def test_a_raw_frame_with_a_pedestal_has_an_assessable_floor(qc):
    r = qc.qc_saturation((100 + np.random.default_rng(0).normal(0, 5, (64, 64))).astype(np.uint16))
    assert r['diag']['floor_assessable'] is True


# ── G10: a versioned, frozen, serialisable contract ──────────────────────────────────────────────────────────

def test_every_result_has_exactly_the_frozen_keys_and_enum_status(qc):
    results = qc.run_full_qc(noise_stack(sigma=5.0, n_frames=24), axis='t')
    for r in results:
        assert tuple(r) == qc.RESULT_KEYS, r['name']
        assert isinstance(r['status'], qc.QCStatus) and isinstance(r['tier'], qc.QCTier)
    drift = next(r for r in results if r['name'] == 'Drift')
    assert 'drift_in_features' in drift['diag'], 'the undeclared top-level keys moved into diag, not dropped'


def test_the_status_enum_still_behaves_like_the_strings_every_caller_uses(qc):
    s = qc.QCStatus('bad')
    assert s == 'bad' and {'bad': 0.2}[s] == 0.2 and f'{s}' == 'bad' and s in ('good', 'warn', 'bad')


def test_the_report_serialises_to_json_with_a_schema_version(qc):
    results = qc.run_full_qc(noise_stack(sigma=5.0, n_frames=24), axis='t', pixel_um=float('nan'))
    payload = json.loads(qc.results_to_json(results))
    assert payload['schema_version'] == qc.SCHEMA_VERSION
    assert len(payload['results']) == len(results)
    assert all(set(r) == set(qc.RESULT_KEYS) for r in payload['results'])


# ── G11 / G12: methods cited, heuristics labelled where the user reads them, every threshold provenanced ─────

def test_heuristics_are_labelled_in_the_rendered_how(qc):
    scene = noise_stack(sigma=2.0, n_frames=1)[0]
    assert 'PyCAT-defined heuristic' in qc.qc_focus(scene)['how']
    assert 'PyCAT-defined heuristic' in qc.qc_focus(noise_stack(sigma=2.0, n_frames=6))['how']
    drift = next(r for r in qc.run_full_qc(noise_stack(sigma=5.0, n_frames=6), axis='t') if r['name'] == 'Drift')
    assert 'heuristic' in drift['how'] and 'Guizar-Sicairos' in drift['how']


def test_the_methods_are_cited_in_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox' / 'data_qc'
    text = '\n'.join(p.read_text(encoding='utf-8') for p in root.glob('*.py'))
    for citation in ('Abbe', 'Nyquist', 'Guizar-Sicairos', 'Donoho', 'Bogert'):
        assert citation in text, citation


def test_every_threshold_carries_one_line_of_provenance():
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox' / 'data_qc'
           / 'thresholds.py').read_text(encoding='utf-8')
    constants = [ln for ln in src.splitlines() if re.match(r'^[A-Z][A-Z0-9_]+ = ', ln)]
    assert len(constants) >= 30
    missing = [ln for ln in constants if not re.search(r'# PROVENANCE: (measured|argued|none — chosen by inspection)', ln)]
    assert not missing, missing
