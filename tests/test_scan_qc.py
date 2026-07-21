"""**Scan-acquisition-artifact QC — per-object motion shear, bidirectional phase, disk pattern, crosstalk.**

Every artifact here is a property of HOW the pixels were collected, not the optics or sample, so each is
synthesized exactly as the physics describes and the check must recover the injected parameter. The
load-bearing assertions: the measured shear slope recovers the injected displacement-per-line; a stable
object beside a sheared one in the SAME frame is flagged — exactly one (the motivating case); uniform shear
across objects is called drift, not per-object motion; an elongated tilted STATIC object is never
confidently called motion; the disk-pattern check survives the vignetting trap (detrend first); and every
check is gated by modality — `na` with a reason when the mode is unknown, never guessed from pixels.
"""
import numpy as np
import pytest

from pycat.toolbox.scan_qc_tools import (
    qc_scan_shear, qc_bidirectional_phase, qc_disk_pattern, qc_pinhole_crosstalk,
    run_scan_qc, scan_shear_flags)

pytestmark = pytest.mark.core


# ── synthetic constructions (the artifacts, built as the physics defines them) ──────────────────
def _sheared_disk(shape, r0, c0, R, slope, value=1.0, into=None, label=1):
    """A disk whose per-row centre shifts by ``slope`` px per row — a motion shear. The per-row column
    centroid is exactly ``c0 + slope*(r-r0)``, so a fit recovers ``slope``."""
    img = np.zeros(shape, float) if into is None else into
    lab = np.zeros(shape, np.int32)
    for dr in range(-R, R + 1):
        r = r0 + dr
        if not (0 <= r < shape[0]):
            continue
        hw = int(np.sqrt(max(R * R - dr * dr, 0)))
        cc = c0 + slope * dr
        lo = max(0, int(round(cc - hw))); hi = min(shape[1] - 1, int(round(cc + hw)))
        img[r, lo:hi + 1] = value
        lab[r, lo:hi + 1] = label
    return img, lab


def _tilted_bar(shape, r0, c0, half_len, half_w, slope, value=1.0):
    """A thin, elongated, tilted STATIC bar. Its per-row centroid has a slope equal to its tilt — the
    slope is morphology, not motion; the check must not call it 'sheared'."""
    img = np.zeros(shape, float); lab = np.zeros(shape, np.int32)
    for dr in range(-half_len, half_len + 1):
        r = r0 + dr
        if not (0 <= r < shape[0]):
            continue
        cc = c0 + slope * dr
        lo = max(0, int(round(cc - half_w))); hi = min(shape[1] - 1, int(round(cc + half_w)))
        img[r, lo:hi + 1] = value; lab[r, lo:hi + 1] = 1
    return img, lab


# ── Scan shear: the measured slope recovers the injected displacement-per-line ───────────────────
def test_scan_shear_slope_recovers_the_injected_displacement():
    img, lab = _sheared_disk((120, 120), r0=60, c0=60, R=11, slope=0.3)
    res = qc_scan_shear(lab, img)
    obj = res['diag']['per_object'][0]
    assert abs(obj['slope_px_per_row'] - 0.3) < 0.05, (
        f"the measured shear slope should recover the injected 0.3 px/row, got {obj['slope_px_per_row']}")
    assert obj['status'] == 'sheared'


def test_an_immobile_object_reports_zero_shear():
    img, lab = _sheared_disk((120, 120), r0=60, c0=60, R=11, slope=0.0)
    res = qc_scan_shear(lab, img)
    obj = res['diag']['per_object'][0]
    assert abs(obj['slope_px_per_row']) < 0.05 and obj['status'] == 'stable'


# ── THE discriminating test: one stable + one sheared in a single frame → flag exactly one ───────
def test_a_stable_and_a_sheared_object_in_one_frame_flag_exactly_one():
    """The motivating case: a mobile condensate torn by the raster while a stable one beside it is clean.
    The check must flag exactly one — the whole point of the in-frame control."""
    img = np.zeros((140, 200), float)
    img, _ = _sheared_disk(img.shape, r0=60, c0=45, R=11, slope=0.0, into=img)      # stable (label 1)
    lab = np.zeros(img.shape, np.int32)
    lab[(img > 0)] = 1
    img2, lab2 = _sheared_disk(img.shape, r0=60, c0=150, R=11, slope=0.35)          # sheared (label 2)
    img = img + img2
    lab[lab2 > 0] = 2

    res = qc_scan_shear(lab, img)
    flags = res['diag']['flags']
    assert sum(bool(v) for v in flags.values()) == 1, f"exactly one object should be flagged, got {flags}"
    assert flags[2] is True and flags[1] is False


# ── Uniform shear across objects → drift/flow, not per-object motion ─────────────────────────────
def test_uniform_shear_is_reported_as_drift_not_per_object_motion():
    img = np.zeros((160, 240), float); lab = np.zeros(img.shape, np.int32)
    for i, cx in enumerate((40, 100, 160, 210), start=1):
        d, dl = _sheared_disk(img.shape, r0=80, c0=cx, R=10, slope=0.3, label=i)
        img += d; lab[dl > 0] = i
    res = qc_scan_shear(lab, img)
    assert res['diag']['uniform'] is True
    assert not any(res['diag']['flags'].values()), "uniform shear must not flag objects as individually mobile"
    assert 'drift' in res['headline'] or 'flow' in res['headline']


# ── Orientation vs shear: a tilted, elongated, STATIC object is never confidently called motion ──
def test_a_tilted_elongated_static_object_is_ambiguous_not_sheared():
    img, lab = _tilted_bar((120, 120), r0=60, c0=60, half_len=18, half_w=2, slope=0.6)
    res = qc_scan_shear(lab, img)
    obj = res['diag']['per_object'][0]
    assert obj['eccentricity'] >= 0.85
    assert obj['status'] in ('ambiguous', 'stable'), (
        f"an elongated tilted static object must not be confidently called motion, got {obj['status']}")


# ── Velocity only when the line time is known (the pixel-size-gate principle) ─────────────────────
def test_velocity_is_only_reported_with_a_line_time():
    img, lab = _sheared_disk((120, 120), r0=60, c0=60, R=11, slope=0.3)
    no_lt = qc_scan_shear(lab, img)['diag']['per_object'][0]
    assert no_lt['velocity'] is None and qc_scan_shear(lab, img)['diag']['velocity_unit'] is None

    with_lt = qc_scan_shear(lab, img, line_time_s=0.002, pixel_um=0.1)
    obj = with_lt['diag']['per_object'][0]
    assert obj['velocity'] == pytest.approx(0.3 / 0.002 * 0.1, rel=0.2)   # px/row → px/s → µm/s
    assert with_lt['diag']['velocity_unit'] == 'µm/s'


# ── Bidirectional phase: an injected odd/even offset is recovered; an aligned frame is ~0 ─────────
def test_bidirectional_phase_recovers_an_injected_offset():
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(0)
    base = gaussian_filter(rng.random((128, 128)), 3)
    aligned = qc_bidirectional_phase(base)
    assert abs(aligned['value']) < 0.5, f"an aligned frame should report ≈0, got {aligned['value']}"

    shifted = base.copy()
    shifted[1::2] = np.roll(base[1::2], 3, axis=1)          # offset odd rows by 3 px laterally
    res = qc_bidirectional_phase(shifted)
    assert abs(abs(res['value']) - 3) < 1.0, f"should recover a ~3 px offset, got {res['value']}"
    assert res['status'] == 'bad'


# ── Disk pattern: an injected periodic modulation is found; a smooth vignette is NOT (detrend) ───
def test_disk_pattern_detects_periodicity_and_not_vignetting():
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(1)
    H = W = 128
    x = np.arange(W)
    period = 8
    striped = 1.0 + 0.15 * np.sin(2 * np.pi * x / period)[None, :] * np.ones((H, 1)) \
        + 0.02 * rng.standard_normal((H, W))
    res_stripe = qc_disk_pattern(striped)
    assert res_stripe['status'] in ('warn', 'bad'), "a periodic disk pattern must be detected"
    assert abs(res_stripe['diag']['pitch_px'] - period) < 2.0, (
        f"the detected pitch should be ~{period} px, got {res_stripe['diag']['pitch_px']}")

    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt((yy - H / 2) ** 2 + (xx - W / 2) ** 2)
    vignette = (1.0 - 0.5 * (r / r.max())) + 0.02 * rng.standard_normal((H, W))
    res_vig = qc_disk_pattern(vignette)
    assert res_vig['status'] == 'good', (
        "a smooth vignetted field with no periodicity must NOT be flagged — detrend first (the "
        f"vignetting trap), got {res_vig['status']}")
    assert res_stripe['diag']['peak_over_background'] > 3 * res_vig['diag']['peak_over_background']


# ── Pinhole crosstalk: an injected halo raises the metric; a clean field does not ────────────────
def _disk_field(shape=(160, 160), centers=((40, 40), (40, 120), (120, 80)), R=8, bg=0.1, value=1.0):
    img = np.full(shape, bg, float); lab = np.zeros(shape, np.int32)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    for i, (cy, cx) in enumerate(centers, start=1):
        m = (yy - cy) ** 2 + (xx - cx) ** 2 <= R * R
        img[m] = value; lab[m] = i
    return img, lab


def test_pinhole_crosstalk_detects_a_halo_and_not_a_clean_field():
    from scipy.ndimage import gaussian_filter
    clean, lab = _disk_field()
    res_clean = qc_pinhole_crosstalk(lab, clean)
    assert res_clean['status'] == 'good', f"a clean field should not flag crosstalk, got {res_clean['status']}"

    halo = clean + gaussian_filter((lab > 0).astype(float), sigma=5) * 0.6   # light bleeding around objects
    res_halo = qc_pinhole_crosstalk(lab, halo)
    assert res_halo['status'] in ('warn', 'bad')
    assert res_halo['diag']['elevation'] > res_clean['diag']['elevation'] + 0.1


# ── Gating: unknown modality → na with a reason; wrong modality → na; right modality → runs ──────
def test_unknown_modality_reports_na_with_a_reason():
    img, lab = _disk_field()
    results = run_scan_qc(img, labels=lab, modality=None)
    assert len(results) == 4 and all(r['status'] == 'na' for r in results)
    assert all('unknown' in (r['how'] or '').lower() for r in results)


def test_modality_gating_runs_only_the_applicable_checks():
    img, lab = _sheared_disk((120, 120), r0=60, c0=60, R=11, slope=0.3)
    point = {r['name']: r for r in run_scan_qc(img, labels=lab, modality='point-scanning')}
    assert point['Scan shear (motion tearing)']['status'] != 'na'          # shear applies
    assert point['Disk-pattern residual']['status'] == 'na'                # disk checks do not

    spin = {r['name']: r for r in run_scan_qc(img, labels=lab, modality='spinning-disk')}
    assert spin['Disk-pattern residual']['status'] != 'na'                 # disk applies
    assert spin['Scan shear (motion tearing)']['status'] == 'na'           # shear does not (whole-field exposure)


# ── Composition with biological QC: the shear flag flows into the flag columns ───────────────────
def test_scan_shear_flags_compose_with_biological_qc():
    import pandas as pd
    from pycat.toolbox.biological_qc_tools import biological_qc

    img = np.zeros((140, 200), float)
    img, _ = _sheared_disk(img.shape, r0=60, c0=45, R=11, slope=0.0, into=img)
    lab = np.zeros(img.shape, np.int32); lab[img > 0] = 1
    img2, lab2 = _sheared_disk(img.shape, r0=60, c0=150, R=11, slope=0.35)
    img = img + img2; lab[lab2 > 0] = 2

    flags = scan_shear_flags(lab, img)
    assert isinstance(flags, pd.Series) and int(flags.sum()) == 1

    table = pd.DataFrame({'label': [1, 2], 'area': [300, 300]})
    out = biological_qc(table, lab, scan_shear_flags=flags)
    assert 'qc_scan_shear' in out.columns
    assert list(out['qc_scan_shear']) == [False, True]
    assert 'motion-sheared' in out.loc[out['label'] == 2, 'qc_flags'].iloc[0]
    assert out.attrs['qc_report'].get('scan_shear') == 1


# ── Metadata: scan fields are filled opportunistically from the raw block, never guessed ─────────
def test_metadata_fills_scan_fields_from_raw():
    from pycat.file_io.metadata_extract import _fill_scan_acquisition_fields, _empty_common
    common = _empty_common('x.czi')
    result = {'common': common, 'raw': {'Image/LineTime': '0.0021 s',
                                        'Microscope/Mode': 'Spinning Disk CSU-W1',
                                        'Channel/PinholeSize': '50 um'}}
    _fill_scan_acquisition_fields(result)
    assert result['common']['line_time_s'] == pytest.approx(0.0021)
    assert result['common']['pinhole_um'] == pytest.approx(50.0)
    assert result['common']['acquisition_mode'] == 'spinning-disk'

    # Nothing to go on → everything stays None (never guessed).
    empty = {'common': _empty_common('y.tif'), 'raw': {}}
    _fill_scan_acquisition_fields(empty)
    assert empty['common']['acquisition_mode'] is None and empty['common']['line_time_s'] is None
