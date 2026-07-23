"""**Characterization net for `partition_coefficient_local` — pins behaviour across every branch.**

`partition_coefficient_local` was a 394-line function; splitting it into phase helpers is only safe if
the split is **byte-identical**. This test captures the current outputs — the aggregate dict AND the
per-droplet values — on a synthetic droplet field across all five reporting branches (dark reference,
extracellular floor, in-vitro-no-reference, raw-ratio-allowed, empty), plus the invalid-sample-type
raise. The float values are pinned tight (rel=1e-9): a pure code-motion refactor reproduces them exactly,
so any drift here means the split changed the science.

The values were read off the pre-refactor implementation; they are the contract the decomposition must
preserve.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base

_H = _W = 200
_CENTRES = [(60, 60), (60, 140), (140, 60), (140, 140)]
_PED, _DIL, _DENSE = 500.0, 100.0, 3000.0


def _scene():
    yy, xx = np.mgrid[0:_H, 0:_W]
    rng = np.random.default_rng(0)
    img = np.full((_H, _W), _PED + _DIL)
    for cy, cx in _CENTRES:
        r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        img += (_DENSE - _DIL) * 0.5 * (1 - np.tanh((r - 16) / 2.5))
    img = img + rng.normal(0, 5, (_H, _W))
    dark = _PED + rng.normal(0, 5, (_H, _W))
    return img, dark


def _labels(radius=13):
    yy, xx = np.mgrid[0:_H, 0:_W]
    lab = np.zeros((_H, _W), np.int32)
    for i, (cy, cx) in enumerate(_CENTRES, start=1):
        lab[np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < radius] = i
    return lab


@pytest.fixture
def _silence(monkeypatch):
    from pycat.toolbox import invitro_tools as it
    monkeypatch.setattr(it, 'napari_show_warning', lambda *a, **k: None)
    monkeypatch.setattr(it, 'napari_show_info', lambda *a, **k: None)


_REL = 1e-9


def test_in_vitro_with_dark_reference_is_the_true_kp(_silence):
    from pycat.toolbox import invitro_tools as it
    img, dark = _scene()
    r = it.partition_coefficient_local(img, _labels(), sample_type='in_vitro', dark_reference=dark)

    assert r['partition_coefficient'] == pytest.approx(29.607976023896516, rel=_REL)
    assert r['floor_source'] == 'dark_reference' and r['is_true_kp'] is True
    assert r['camera_floor'] == pytest.approx(499.97701139195584, rel=_REL)
    assert r['contrast'] == pytest.approx(2861.47316796443, rel=_REL)
    assert r['n_saturated_droplets'] == 1 and len(r['per_droplet_df']) == 4
    row = r['per_droplet_df'].iloc[0]
    assert row['I_dense'] == pytest.approx(3461.4313355449567, rel=_REL)
    assert row['I_dilute_local'] == pytest.approx(599.905289428796, rel=_REL)
    assert row['gap_px'] == pytest.approx(21.0, rel=_REL)
    assert row['contrast'] == pytest.approx(2861.5260461161606, rel=_REL)
    assert row['partition_coefficient'] == pytest.approx(29.63579861809701, rel=_REL)
    assert row['raw_ratio'] == pytest.approx(5.769963020064021, rel=_REL)


def test_cellular_with_cell_mask_uses_the_extracellular_floor(_silence):
    from pycat.toolbox import invitro_tools as it
    img, _ = _scene()
    cell_mask = np.zeros((_H, _W), bool)
    cell_mask[30:170, 30:170] = True
    r = it.partition_coefficient_local(img, _labels(), sample_type='cellular', cell_mask=cell_mask)

    assert r['floor_source'] == 'extracellular' and r['is_true_kp'] is True
    assert r['camera_floor'] == pytest.approx(599.9641241929719, rel=_REL)
    assert r['contrast'] == pytest.approx(2861.47316796443, rel=_REL)


def test_in_vitro_without_reference_refuses_kp(_silence):
    from pycat.toolbox import invitro_tools as it
    img, _ = _scene()
    r = it.partition_coefficient_local(img, _labels(), sample_type='in_vitro')

    assert np.isnan(r['partition_coefficient'])
    assert r['floor_source'] == 'none' and r['is_true_kp'] is False
    assert r['contrast'] == pytest.approx(2861.47316796443, rel=_REL)
    assert 'IN VITRO' in r['verdict']


def test_allow_no_reference_returns_the_raw_ratio_labelled_not_kp(_silence):
    from pycat.toolbox import invitro_tools as it
    img, _ = _scene()
    r = it.partition_coefficient_local(img, _labels(), sample_type='cellular', allow_no_reference=True)

    assert r['partition_coefficient'] == pytest.approx(5.769151108598719, rel=_REL)
    assert r['floor_source'] == 'none' and r['is_true_kp'] is False   # a raw ratio, NOT Kp


def test_no_droplets_returns_the_minimal_refusal_dict(_silence):
    from pycat.toolbox import invitro_tools as it
    img, dark = _scene()
    r = it.partition_coefficient_local(img, np.zeros((_H, _W), int),
                                       sample_type='in_vitro', dark_reference=dark)
    assert np.isnan(r['partition_coefficient'])
    assert 'No droplets labelled' in r['verdict']
    assert len(r['per_droplet_df']) == 0


def test_an_invalid_sample_type_raises_rather_than_guesses(_silence):
    from pycat.toolbox import invitro_tools as it
    img, _ = _scene()
    with pytest.raises(ValueError, match='sample_type'):
        it.partition_coefficient_local(img, _labels(), sample_type='bogus')
