"""**Phase A of the Completion Spec, pinned against known truth -- as SWEEPS over noise and pedestal.**

Each measured bias in Phase A (SpIDA read noise, the SpIDA pedestal gate, the field partition coefficient, texture
entropy / LBP, the truncated size distribution) is reproduced here on the ``tests.synthetic`` generators, and the
fixed code is held to a stated tolerance across a noise / pedestal sweep. Two of these biases survived because the
old golden masters generated noise-free, pedestal-free data on which the biased branch could not engage; a single
point would not have caught them, so nothing here is a single point.

Where a test asserts the OLD behaviour it is labelled PREMISE: it shows the bias is real on this generator (the
spec's [executed] number), so the fix test beside it is not vacuous.
"""
import numpy as np
import pytest

from tests.synthetic import (lognormal_sizes, partition_field, spida_field, textured_object)

pytestmark = pytest.mark.base


# ── A1: SpIDA read noise ───────────────────────────────────────────────────────────────────────────────────────

def _spida_fit(pixels, read_sd=0.0, corrected=True):
    from pycat.toolbox import spida_tools as sp
    s = read_sd if corrected else 0.0
    x, y = sp.build_intensity_histogram(pixels, n_bins=256, read_noise_sd=s)
    return sp.fit_spida_histogram(x, y, read_noise_var=s * s)


def test_PREMISE_uncorrected_read_noise_halves_N_and_doubles_epsilon():
    """Spec A1 [executed]: N = 8, eps = 25, read sd 80 -> N 3.98 (-50 %), eps 53 (+113 %), no warning."""
    fit = _spida_fit(spida_field(8, 25, read_sigma=80), read_sd=80, corrected=False)
    assert fit['N'] < 0.6 * 8 and fit['epsilon'] > 1.8 * 25


@pytest.mark.parametrize('read_sd', [0, 40, 80, 160])
@pytest.mark.parametrize('n_true', [8, 30])
def test_spida_recovers_N_and_epsilon_across_a_read_noise_sweep(read_sd, n_true):
    fit = _spida_fit(spida_field(n_true, 25, read_sigma=read_sd, seed=1), read_sd=read_sd)
    assert fit['success'] and fit['read_noise_corrected'] == (read_sd > 0)
    assert fit['N'] == pytest.approx(n_true, rel=0.15), f"N = {fit['N']:.2f} against {n_true} at read sd {read_sd}"
    assert fit['epsilon'] == pytest.approx(25, rel=0.15)


@pytest.mark.parametrize('read_sd', [40, 80])
def test_spida_low_density_with_read_noise_reports_the_unbiased_moment_anchor(read_sd):
    fit = _spida_fit(spida_field(2, 25, read_sigma=read_sd, seed=1), read_sd=read_sd)
    assert fit['N'] == pytest.approx(2, rel=0.1) and fit['low_density_regime']


def test_spida_refuses_when_the_variance_does_not_exceed_the_read_noise():
    from pycat.toolbox import spida_tools as sp
    x, y = sp.build_intensity_histogram(spida_field(8, 25, read_sigma=10), 256, read_noise_sd=10)
    fit = sp.fit_spida_histogram(x, y, read_noise_var=1e6)
    assert not fit['success'] and np.isnan(fit['N']) and 'read-noise' in fit['reason']


# ── A1'': the SpIDA pedestal gate must not grow with N ─────────────────────────────────────────────────────────

def _pedestal_flagged(pixels, read_sd=0.0):
    from pycat.toolbox import spida_tools as sp
    return any('CAMERA PEDESTAL' in w for w in sp.check_assumptions(pixels, 65535, read_noise_sd=read_sd))


def test_PREMISE_the_old_floor_over_spread_statistic_grows_with_N():
    """Spec A1'' [executed]: on pedestal-free fields floor/spread = 0.14 (N 8) ... 1.47 (N 100) -- the old gate
    (> 0.17) fired on every ROI with N >~ 10."""
    def floor_over_spread(p):
        lo = np.percentile(p, 0.5)
        return lo / (np.percentile(p, 99.5) - lo)
    assert floor_over_spread(spida_field(8, 25)) < 0.17 < floor_over_spread(spida_field(20, 25))
    assert floor_over_spread(spida_field(100, 25)) > 1.0


@pytest.mark.parametrize('n_true', [2, 8, 10, 20, 100, 300])
def test_no_pedestal_accusation_on_pedestal_free_fields_at_any_density(n_true):
    flagged = [s for s in range(5) if _pedestal_flagged(spida_field(n_true, 25, seed=s))]
    assert not flagged, f"N = {n_true}: a pedestal-free field was accused of a pedestal (seeds {flagged})"


@pytest.mark.parametrize('pedestal', [25, 200, 800])
@pytest.mark.parametrize('read_sd', [0, 40])
def test_a_damaging_pedestal_is_still_caught(pedestal, read_sd):
    """25 counts at N = 8 already inflates N by 23 %. Read noise (declared) does not hide it."""
    assert _pedestal_flagged(spida_field(8, 25, read_sigma=read_sd, pedestal=pedestal), read_sd=read_sd)


def test_where_the_gate_has_no_power_it_says_the_pedestal_cannot_be_ruled_out():
    from pycat.toolbox import spida_tools as sp
    w = sp.check_assumptions(spida_field(100, 25, pedestal=200), 65535)
    assert not any('CAMERA PEDESTAL**' in s for s in w)
    assert any('CANNOT BE RULED OUT' in s for s in w)


# ── A2: the field partition coefficient ───────────────────────────────────────────────────────────────────────

def _kp(img, lbl, **kw):
    from pycat.toolbox.invitro.partition import partition_coefficient_field
    return partition_coefficient_field(img, lbl, **kw)


@pytest.mark.parametrize('K', [100, 1000, 10000])
def test_A2a_a_sub_1e6_dilute_phase_no_longer_collapses_K_to_6(K):
    """Spec A2a [executed]: dilute 1e-7 (a background-subtracted float image) -> K = 5.66 / 5.92 / 5.94 for true
    100 / 1000 / 10000: not even monotonic. The dilute level is now used as reported."""
    img, lbl = partition_field(K, dilute=1e-7, dilute_cv=0.05)
    res = _kp(img, lbl)
    assert res['partition_coeff'] == pytest.approx(K, rel=0.01)
    assert res['dilute_denominator'] == res['c_sat_proxy'] and not res['dilute_fallback_used']


def test_A2a_an_unusable_dilute_phase_is_NaN_with_the_reason():
    img, lbl = partition_field(100, dilute=0.0)
    res = _kp(img, lbl)
    assert np.isnan(res['partition_coeff']) and res['dilute_fallback_used'] and res['dilute_floor_reason']


def test_PREMISE_the_10th_percentile_biased_K_up_with_a_discontinuity():
    """Spec A2b [executed]: +34 % at CV 0.2, +105 % at CV 0.4 -- then ~0 at CV 0.78 when a hidden guard engaged.
    Reproduced with the old estimator applied to the same fields."""
    def old_estimate(img, lbl):
        bg = img[lbl == 0].astype(float)
        pct, mean = np.percentile(bg, 10), bg.mean()
        bulk = mean if pct <= 0 or pct < 0.05 * mean else pct
        return img[lbl > 0].mean() / bulk
    kp = {cv: old_estimate(*partition_field(100, dilute=100, dilute_cv=cv, dtype=np.uint16)) for cv in (0.2, 0.4, 0.78)}
    assert kp[0.2] > 125 and kp[0.4] > 190 and kp[0.78] < 110


@pytest.mark.parametrize('cv', [0.05, 0.1, 0.2, 0.4, 0.6, 0.78, 0.9])
@pytest.mark.parametrize('pedestal', [0, 500])
@pytest.mark.parametrize('dtype', [np.float64, np.uint16])
def test_A2b_K_is_unbiased_across_a_dilute_noise_and_pedestal_sweep(cv, pedestal, dtype):
    img, lbl = partition_field(100, dilute=100, dilute_cv=cv, pedestal=pedestal, dtype=dtype)
    res = _kp(img, lbl, dark_level=pedestal if pedestal else None)
    assert res['partition_coeff'] == pytest.approx(100, rel=0.02), f"CV {cv}, pedestal {pedestal}, {dtype}"


@pytest.mark.parametrize('kind', ['uint16', 'float64', 'float01'])
def test_A2c_a_float_image_is_not_saturated_by_its_own_maximum(kind):
    """Spec A2c [executed]: uint16 -> 100, float64 -> NaN, float [0, 1] -> NaN."""
    img, lbl = partition_field(100, dilute=100, dense_cv=0.02, dtype=np.uint16 if kind == 'uint16' else np.float64)
    if kind == 'float01':
        img = img / img.max()
    res = _kp(img, lbl)
    assert res['partition_coeff'] == pytest.approx(100, rel=0.01)
    assert res['saturation_checked'] == (kind == 'uint16')


def test_A2c_an_explicit_ceiling_is_still_honoured_on_a_float_image():
    img, lbl = partition_field(100, dilute=100)                   # flat-topped dense phase == the stated ceiling
    assert np.isnan(_kp(img / img.max(), lbl, saturation_level=1.0)['partition_coeff'])


def test_without_a_dark_level_a_pedestal_drags_K_toward_1_and_says_so():
    """Spec [executed]: pedestal 500, true K 10000 -> 100. The dark level removes it."""
    img, lbl = partition_field(10000, dilute=5, pedestal=500, dtype=np.uint16)
    raw, fixed = _kp(img, lbl), _kp(img, lbl, dark_level=500)
    assert raw['partition_coeff'] == pytest.approx(100, rel=0.02) and not raw['background_subtracted']
    assert fixed['partition_coeff'] == pytest.approx(10000, rel=0.01) and fixed['background_subtracted']


def test_Kp_of_t_uses_the_local_estimator_so_frame_noise_is_not_kinetics():
    """Constant K = 30 while the dilute-phase noise changes frame to frame: with a dark reference Kp(t) is flat;
    without one it is NaN (in vitro the pedestal is inseparable) and the exact contrast carries the time course."""
    from pycat.toolbox.timeseries_invitro_tools import field_trajectories
    frames, labels = [], []
    for t, cv in enumerate((0.02, 0.2, 0.4, 0.1)):
        img, lbl = partition_field(30, dilute=100, dilute_cv=cv, pedestal=500, dtype=np.uint16, seed=t)
        frames.append(img)
        labels.append(lbl)
    movie, lab = np.stack(frames), np.stack(labels)
    with_dark = field_trajectories(lab, movie, microns_per_pixel=0.1, frame_interval_s=1.0, dark_reference=500)
    assert np.allclose(with_dark['partition_coefficient'], 30, rtol=0.03), with_dark['partition_coefficient']
    assert with_dark['kp_is_true'].all()
    without = field_trajectories(lab, movie, microns_per_pixel=0.1, frame_interval_s=1.0)
    assert without['partition_coefficient'].isna().all() and (without['contrast'] > 0).all()


# ── A3: texture features ───────────────────────────────────────────────────────────────────────────────────────

_ASPECTS = (1, 4, 10, 16)


def _entropy(img, mask):
    from pycat.toolbox.feature_analysis_tools import calculate_image_entropy
    return calculate_image_entropy(img, 3, mask).iloc[0]


def test_PREMISE_the_test_shapes_span_the_spec_bbox_fill_fractions():
    fills = []
    for a in _ASPECTS:
        _, m = textured_object(a)
        ys, xs = np.where(m)
        fills.append(m.sum() / ((np.ptp(ys) + 1) * (np.ptp(xs) + 1)))
    assert np.allclose(fills, [0.792, 0.363, 0.154, 0.097], atol=0.01)


@pytest.mark.parametrize('background,pedestal,noise', [(0, 0, 0), (120, 0, 8), (0, 500, 0), (120, 500, 8)])
def test_A3_entropy_is_shape_invariant_for_an_identical_texture(background, pedestal, noise):
    """Spec A3' [executed]: 8_bit_entropy fell 81.5 % from a circle to a 16:1 ellipse with the within-mask
    multiset byte-identical -- the zeroed bbox, not the texture. Masked, it must not move."""
    rows = [_entropy(*textured_object(a, background=background, pedestal=pedestal, noise_sigma=noise))
            for a in _ASPECTS]
    for col in ('8_bit_entropy', '32_bit_entropy'):
        vals = np.array([r[col] for r in rows])
        assert np.ptp(vals) / vals.mean() < 0.01, f"{col} varies with shape: {vals}"


def _lbp(img, mask):
    from pycat.toolbox.feature_analysis_tools import calculate_lbp_features
    return calculate_lbp_features(img, mask, 3).iloc[0]


def test_A3_lbp_mean_is_no_longer_the_pixel_count_over_256():
    """Spec A3'' [executed]: lbp_mean == n_pixels / 256 exactly, for ANY texture (a flat constant included)."""
    img, m = textured_object(1, area=4000)
    assert _lbp(img, m)['lbp_mean'] != pytest.approx(4000 / 256)


def test_A3_lbp_responds_to_texture_and_not_to_area():
    rough, flat = _lbp(*textured_object(1)), _lbp(*textured_object(1, texture='flat'))
    assert abs(rough['lbp_mean'] - flat['lbp_mean']) > 50 and abs(rough['lbp_entropy'] - flat['lbp_entropy']) > 2
    small, large = _lbp(*textured_object(1, area=2000)), _lbp(*textured_object(1, area=8000))
    assert large['lbp_std'] == pytest.approx(small['lbp_std'], rel=0.1)      # was linear in area


def test_A3_lbp_shape_dependence_is_only_the_boundary_residual():
    """Codes at the mask edge see the zeroed outside, so a thin shape (more edge) shifts lbp_mean slightly -- the
    same geometric residual as the masked GLCM (6.85 %), not an area statistic."""
    means = np.array([_lbp(*textured_object(a))['lbp_mean'] for a in _ASPECTS])
    assert np.ptp(means) / means.mean() < 0.06


# ── A4: the truncated size distribution ────────────────────────────────────────────────────────────────────────

_MU, _SIGMA = np.log(0.5), 0.6


@pytest.mark.parametrize('d_min,mu_err,sig_err', [(0.2, 0.115, -0.119), (0.3, 0.299, -0.236), (0.4, 0.501, -0.331)])
def test_PREMISE_the_untruncated_fit_reproduces_the_spec_bias(d_min, mu_err, sig_err):
    from pycat.toolbox.invitro.size_distribution import fit_size_distribution
    res = fit_size_distribution(lognormal_sizes(_MU, _SIGMA, d_min))
    assert (res['lognormal_mu'] - _MU) / abs(_MU) == pytest.approx(mu_err, abs=0.01)
    assert (res['lognormal_sigma'] - _SIGMA) / _SIGMA == pytest.approx(sig_err, abs=0.01)
    assert np.isnan(res['detection_limit_xmin']) and not res['sample_statistics_truncated']


@pytest.mark.parametrize('d_min', [0.2, 0.3, 0.4])
@pytest.mark.parametrize('seed', [0, 1])
def test_A4_the_detection_limit_removes_the_truncation_bias(d_min, seed):
    from pycat.toolbox.invitro.size_distribution import fit_size_distribution
    res = fit_size_distribution(lognormal_sizes(_MU, _SIGMA, d_min, seed=seed), xmin=d_min)
    assert abs((res['lognormal_mu'] - _MU) / _MU) < 0.035
    assert abs((res['lognormal_sigma'] - _SIGMA) / _SIGMA) < 0.025
    assert res['preferred_model'] == 'lognormal'
    assert res['detection_limit_xmin'] == d_min and res['sample_statistics_truncated']


def test_A4_the_limit_comes_from_the_recorded_min_area_and_is_refused_for_a_foreign_mask():
    from pycat.toolbox.invitro.size_distribution import detection_limit_for_radii, detection_limit_radius_um
    xmin = detection_limit_radius_um(6, 0.1)
    assert xmin == pytest.approx(np.sqrt(6 * 0.01 / np.pi))
    assert detection_limit_for_radii([xmin, 2 * xmin], 6, 0.1) == pytest.approx(xmin)
    assert detection_limit_for_radii([0.5 * xmin, 2 * xmin], 6, 0.1) is None     # smaller objects: not that mask
    assert detection_limit_radius_um(None, 0.1) is None and detection_limit_radius_um(6, float('nan')) is None


def test_A4_batch_replay_fits_with_the_segmentation_detection_limit(tmp_path):
    from types import SimpleNamespace
    from skimage.draw import disk
    from pycat.batch.steps.invitro_steps import replay_ivf_size_distribution
    rng = np.random.default_rng(0)
    mask = np.zeros((600, 600), np.int32)
    k = 1
    for cy in range(20, 600, 40):
        for cx in range(20, 600, 40):
            r = max(2.0, min(15.0, rng.lognormal(np.log(6), 0.4)))
            rr, cc = disk((cy, cx), r, shape=mask.shape)
            mask[rr, cc] = k
            k += 1
    state = {'ivf_droplet_mask': mask, 'ivf_min_area_px': 12,
             'data_instance': SimpleNamespace(data_repository={'microns_per_pixel_sq': 0.01,
                                                               'pixel_size_from_metadata': True})}
    replay_ivf_size_distribution(state, tmp_path / 'img.tif', {}, tmp_path)
    res = state['ivf_size_distribution']
    assert res['detection_limit_xmin'] == pytest.approx(np.sqrt(12 * 0.01 / np.pi))
    import pandas as pd
    row = pd.read_csv(tmp_path / 'img_ivf_size_distribution.csv')
    assert 'detection_limit_xmin' in row.columns
