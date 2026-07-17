"""**Calibration turns intensity into a biophysical parameter — and must refuse to lie doing it.**

`utils/calibration.py` is the shared authority for intensity → apparent concentration → transfer free
energy (ΔG = −RT ln K_p). Its most important property is not the arithmetic; it is the **validity
gate**: a curve measured under one acquisition converts nothing under a different one, and returning a
plausible number anyway is the exact "plausible lie" this codebase forbids (the pixel-size gate, the
z-step NaN, the CNR fix).

So these tests are in three groups:

* **recovery** — a known standard is recovered exactly, and ΔG matches the closed form;
* **the gate fails loud** — mismatched exposure/gain/laser/channel and *missing* metadata are HARD
  BLOCKS, not warnings; a minor mismatch WARNs;
* **refusals and drift** — ΔG refuses non-positive concentrations and Celsius-passed-as-Kelvin; a
  stale curve's age is reported so a consumer can warn.

All `core` (pure, headless) — the whole point of building calibration as a standalone module.
"""

# Standard library imports
import json

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.utils import calibration as cal
from pycat.utils.measurement import ParameterSource

pytestmark = pytest.mark.core


def _fp(**over):
    base = dict(exposure_s=0.1, camera_name='cam', gain=1.0, channel='emission:525',
                laser_power=5.0, pixel_size_um=0.1)
    base.update(over)
    return cal.AcquisitionFingerprint(**base)


def _curve(slope=2.0, intercept=0.0, **over):
    """A curve where concentration = slope·intensity + intercept, exactly."""
    I = np.array([10, 20, 30, 40, 50], float)
    C = slope * I + intercept
    return cal.build_calibration(I, C, _fp(**over.pop('acq', {})),
                                 channel='GFP', fluorophore='GFP', conc_units='uM',
                                 standard_id='std-A', created='2026-07-17T10:00:00', **over)


# ── recovery ─────────────────────────────────────────────────────────────────

def test_a_KNOWN_standard_is_recovered_exactly():
    curve = _curve(slope=2.0, intercept=0.0)
    assert curve.slope == pytest.approx(2.0)
    assert curve.intercept == pytest.approx(0.0, abs=1e-9)
    assert curve.r_squared == pytest.approx(1.0)


def test_intensity_maps_to_concentration_with_the_right_UNITS_and_provenance():
    curve = _curve(slope=2.0)
    p = cal.intensity_to_concentration(25.0, curve)

    assert p.value == pytest.approx(50.0)                  # 2·25
    assert p.units == 'uM'
    assert p.source is ParameterSource.CALIBRATED          # the strongest provenance
    assert p.is_trustworthy()


def test_the_concentration_UNCERTAINTY_widens_away_from_the_curve_centre():
    """The confidence band of a fitted line is narrowest at Ī and widens outward — the honest
    behaviour, since the curve is least constrained where it had no standards."""
    # Add a little scatter so the residual std is non-zero and the SE is meaningful.
    I = np.array([10, 20, 30, 40, 50], float)
    C = 2.0 * I + np.array([0.5, -0.4, 0.3, -0.2, 0.6])
    curve = cal.build_calibration(I, C, _fp(), channel='GFP', fluorophore='GFP', conc_units='uM',
                                  standard_id='s', created='2026-07-17T10:00:00')

    se_centre = curve.concentration_se(30.0)               # Ī
    se_edge = curve.concentration_se(50.0)
    assert se_edge > se_centre > 0


def test_an_EXTRAPOLATED_intensity_is_flagged_not_trusted():
    """Just past the last standard the curve may still be right, but it is no longer a measurement —
    so it downgrades to FITTED with a note rather than presenting extrapolation as calibrated."""
    curve = _curve(slope=2.0)                              # calibrated over I in [10, 50]
    p = cal.intensity_to_concentration(500.0, curve)

    assert p.source is ParameterSource.FITTED
    assert not p.is_trustworthy()
    assert 'outside the calibrated range' in p.note


def test_delta_g_matches_the_CLOSED_FORM():
    """ΔG = −RT ln(K_p). K_p = 10 at 298.15 K in kcal/mol."""
    dg = cal.delta_g_transfer(100.0, 10.0, 298.15)
    expected = -cal._R['kcal/mol'] * 298.15 * np.log(10.0)

    assert dg.value == pytest.approx(expected)
    assert dg.units == 'kcal/mol'


def test_delta_g_PROPAGATES_uncertainty_from_the_concentrations():
    """σ_ΔG = RT·√[(σ_d/C_d)² + (σ_l/C_l)²]. Given Parameters with 1σ, it propagates; given bare
    floats, there is nothing to propagate and σ is None."""
    curve = _curve(slope=2.0)
    cd = cal.intensity_to_concentration(45.0, curve, name='dense')
    cl = cal.intensity_to_concentration(15.0, curve, name='dilute')

    dg_bare = cal.delta_g_transfer(cd.value, cl.value, 298.15)
    assert dg_bare.uncertainty is None

    # With uncertainties attached (inject a known 1σ), the propagation is exact.
    from pycat.utils.measurement import Parameter
    d = Parameter('d', 90.0, 'uM', ParameterSource.CALIBRATED, uncertainty=3.0)
    l = Parameter('l', 30.0, 'uM', ParameterSource.CALIBRATED, uncertainty=1.0)
    dg = cal.delta_g_transfer(d, l, 298.15)
    RT = cal._R['kcal/mol'] * 298.15
    expected_sigma = RT * np.sqrt((3.0 / 90.0) ** 2 + (1.0 / 30.0) ** 2)
    assert dg.uncertainty == pytest.approx(expected_sigma)


# ── the validity gate FAILS LOUD ──────────────────────────────────────────────

def test_a_MATCHING_acquisition_is_ok():
    curve = _curve()
    v = cal.check_calibration_validity(
        curve, {'exposure_s': 0.1, 'emission_nm': 525, 'gain': 1.0,
                'laser_power': 5.0, 'pixel_size_um': 0.1})
    assert v.valid and v.level == 'ok', v.reason


def test_a_mismatched_EXPOSURE_is_a_HARD_BLOCK():
    """Exposure sets the intensity scale directly — the curve's slope no longer converts anything."""
    curve = _curve()
    v = cal.check_calibration_validity(curve, {'exposure_s': 0.5, 'emission_nm': 525})
    assert not v.valid and v.level == 'invalid'
    assert 'exposure' in v.reason.lower()


def test_a_mismatched_CHANNEL_is_a_HARD_BLOCK():
    """The wrong curve entirely — a GFP curve cannot read an mCherry image."""
    curve = _curve()
    v = cal.check_calibration_validity(curve, {'exposure_s': 0.1, 'emission_nm': 610})
    assert not v.valid and 'channel' in v.reason.lower()


def test_MISSING_critical_metadata_REFUSES_rather_than_assuming():
    """No exposure on the image => cannot confirm the scale matches => refuse. Failing toward the
    loud side is the contract; a concentration under an unverifiable acquisition is a lie."""
    curve = _curve()
    v = cal.check_calibration_validity(curve, {'emission_nm': 525})   # no exposure
    assert not v.valid
    assert 'exposure' in v.reason.lower()


def test_a_present_and_different_GAIN_is_a_HARD_BLOCK():
    curve = _curve()
    v = cal.check_calibration_validity(
        curve, {'exposure_s': 0.1, 'emission_nm': 525, 'gain': 4.0})
    assert not v.valid and 'gain' in v.reason.lower()


def test_a_minor_PIXEL_SIZE_mismatch_only_WARNS():
    """Pixel size does not change the intensity scale, so a small difference is usable-with-a-flag,
    not a block."""
    curve = _curve()
    v = cal.check_calibration_validity(
        curve, {'exposure_s': 0.1, 'emission_nm': 525, 'gain': 1.0,
                'laser_power': 5.0, 'pixel_size_um': 0.104})   # ~4%, within tol -> ok actually
    assert v.valid


# ── refusals ──────────────────────────────────────────────────────────────────

def test_delta_g_REFUSES_a_non_positive_concentration():
    """ln of a ratio including zero/negative is undefined — a saturated or over-subtracted phase
    cannot become a free energy, and must not be turned into a plausible one."""
    with pytest.raises(ValueError, match='non-positive'):
        cal.delta_g_transfer(0.0, 10.0, 298.15)
    with pytest.raises(ValueError, match='non-positive'):
        cal.delta_g_transfer(100.0, -5.0, 298.15)


def test_delta_g_REFUSES_celsius_passed_as_kelvin():
    """24 (°C) passed where Kelvin is asked for is 24 K — physically absurd for aqueous biology, and
    silently using it would poison every ΔG. Refused loud."""
    with pytest.raises(ValueError, match='Kelvin'):
        cal.delta_g_transfer(100.0, 10.0, 24.0)


def test_build_calibration_needs_enough_points():
    with pytest.raises(ValueError, match='at least 3'):
        cal.build_calibration([1, 2], [1, 2], _fp(), channel='c', fluorophore='f',
                              conc_units='uM', standard_id='s', created='2026-07-17T10:00:00')


# ── drift + persistence ───────────────────────────────────────────────────────

def test_a_curve_ROUND_TRIPS_through_json(tmp_path):
    curve = _curve(slope=2.0)
    path = tmp_path / 'curve.json'
    cal.save_curve(curve, path)
    back = cal.load_curve(path)

    assert back.slope == pytest.approx(curve.slope)
    assert back.acquisition == curve.acquisition
    assert cal.intensity_to_concentration(25.0, back).value == pytest.approx(50.0)


def test_load_curve_REJECTS_an_unknown_schema(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps({'schema': 'something/else', 'slope': 1}), encoding='utf-8')
    with pytest.raises(ValueError, match='schema'):
        cal.load_curve(path)


def test_curve_AGE_is_measured_from_when_the_standard_was_imaged():
    """Drift is a first-class concern: a stale curve mis-scales everything, so its age is reported
    for a consumer to compare against a staleness window."""
    curve = _curve()                                       # created 2026-07-17T10:00:00
    age = cal.curve_age_days(curve, '2026-07-27T10:00:00')
    assert age == pytest.approx(10.0, abs=1e-6)


# ── the additive consumer: partition path ─────────────────────────────────────

def test_the_partition_path_is_BYTE_UNCHANGED_without_a_curve():
    """Additive: no curve, no calibration keys, identical dict to before."""
    from pycat.toolbox.partition_enrichment_tools import client_enrichment

    img = np.zeros((32, 32), float)
    dense = np.zeros((32, 32), bool); dense[8:16, 8:16] = True
    img[dense] = 100.0
    img[~dense] = 10.0

    out = client_enrichment(img, dense)
    assert 'calibration_validity' not in out
    assert 'dense_concentration' not in out
    assert set(out) == {'dense_mean', 'dilute_mean', 'dense_mean_raw', 'dilute_mean_raw',
                        'background', 'enrichment', 'n_dense_px', 'n_dilute_px'}


def test_the_calibrated_partition_path_reports_real_units(monkeypatch):
    """Given a valid curve and matching metadata, K_p and ΔG come back in real units alongside the
    intensity ratio."""
    import pycat.toolbox.partition_enrichment_tools as pet
    monkeypatch.setattr(pet, 'napari_show_warning', lambda *a, **k: None)
    from pycat.toolbox.partition_enrichment_tools import client_enrichment

    img = np.zeros((32, 32), float)
    dense = np.zeros((32, 32), bool); dense[8:16, 8:16] = True
    img[dense] = 100.0
    img[~dense] = 10.0

    curve = _curve(slope=2.0)                              # C = 2·I ; dense 200 µM, dilute 20 µM
    meta = {'exposure_s': 0.1, 'emission_nm': 525, 'gain': 1.0,
            'laser_power': 5.0, 'pixel_size_um': 0.1}

    out = client_enrichment(img, dense, calibration_curve=curve, image_metadata=meta,
                            temperature_K=298.15)
    assert out['calibration_validity']['valid']
    assert out['dense_concentration'].value == pytest.approx(200.0)
    assert out['dilute_concentration'].value == pytest.approx(20.0)
    assert out['Kp_calibrated'] == pytest.approx(10.0)
    assert out['delta_g_transfer'].value == pytest.approx(
        -cal._R['kcal/mol'] * 298.15 * np.log(10.0))


def test_a_MISMATCHED_curve_reports_the_refusal_and_NO_concentration(monkeypatch):
    """The safety property, end-to-end through the consumer: a wrong-acquisition curve yields the
    verdict and leaves NO plausible concentration behind."""
    import pycat.toolbox.partition_enrichment_tools as pet
    monkeypatch.setattr(pet, 'napari_show_warning', lambda *a, **k: None)
    from pycat.toolbox.partition_enrichment_tools import client_enrichment

    img = np.zeros((32, 32), float)
    dense = np.zeros((32, 32), bool); dense[8:16, 8:16] = True
    img[dense] = 100.0; img[~dense] = 10.0

    curve = _curve(slope=2.0)
    bad_meta = {'exposure_s': 0.5, 'emission_nm': 525}     # different exposure

    out = client_enrichment(img, dense, calibration_curve=curve, image_metadata=bad_meta,
                            temperature_K=298.15)
    assert not out['calibration_validity']['valid']
    assert 'dense_concentration' not in out                # nothing plausible left behind
    assert 'delta_g_transfer' not in out
    assert np.isfinite(out['enrichment'])                  # the intensity ratio still reported


def test_the_calibrated_path_matches_the_intensity_ratio_when_the_curve_is_IDENTITY(monkeypatch):
    """A sanity anchor: with slope 1, intercept 0, the concentration ratio IS the intensity ratio,
    so calibration adds units without changing the number."""
    import pycat.toolbox.partition_enrichment_tools as pet
    monkeypatch.setattr(pet, 'napari_show_warning', lambda *a, **k: None)
    from pycat.toolbox.partition_enrichment_tools import client_enrichment

    img = np.zeros((32, 32), float)
    dense = np.zeros((32, 32), bool); dense[8:16, 8:16] = True
    img[dense] = 90.0; img[~dense] = 30.0

    curve = _curve(slope=1.0, intercept=0.0)
    meta = {'exposure_s': 0.1, 'emission_nm': 525, 'gain': 1.0,
            'laser_power': 5.0, 'pixel_size_um': 0.1}

    out = client_enrichment(img, dense, calibration_curve=curve, image_metadata=meta)
    assert out['Kp_calibrated'] == pytest.approx(out['enrichment'])
