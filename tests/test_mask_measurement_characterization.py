"""**Characterization pins for the mask-measurement functions — written BEFORE they move.**

`label_mask_split` Step 3 relocates the measurement concern (`measure_region_props`,
`run_measure_binary_mask`, `run_measure_region_props`, `MeasurementDialog`) out of
`label_and_mask_tools.py` into `toolbox/masks/measurement.py`. Coverage here was thin, so per the spec's
"characterization-test-first, no test no move" discipline these pin the EXACT measured numbers on synthetic
inputs at `rtol=1e-9`. They import through the public name (the re-export shim), and patch each function's own
global namespace (`__globals__`) rather than a fixed module, so they pass **unchanged** before and after the
move — the proof that the relocation is byte-identical.
"""
import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace

import pycat.toolbox.label_and_mask_tools as L

pytestmark = pytest.mark.base      # imports label_and_mask_tools (cv2/scipy/skimage) → the fuller lane, not core


def _two_region_labels():
    """A labelled mask with two known regions: label 1 = 9 px @ intensity 5, label 2 = 16 px @ intensity 8."""
    lab = np.zeros((10, 10), dtype=np.int32)
    lab[1:4, 1:4] = 1                 # 3x3 = 9 px
    lab[6:10, 6:10] = 2               # 4x4 = 16 px
    img = np.zeros((10, 10), dtype=np.float64)
    img[lab == 1] = 5.0
    img[lab == 2] = 8.0
    return lab, img


def test_measure_region_props_pins_area_intensity_and_identity():
    lab, img = _two_region_labels()
    df = L.measure_region_props(lab, img, [('label', ''), ('area', ''), ('intensity_mean', '')])

    assert list(df['label']) == [1, 2]
    np.testing.assert_allclose(df['area'].to_numpy(), [9.0, 16.0], rtol=1e-9)
    np.testing.assert_allclose(df['intensity_mean'].to_numpy(), [5.0, 8.0], rtol=1e-9)
    # identity stamping is deterministic with no source path
    assert df['_pycat_entity_id'].iloc[0] == 'unknown/measure_region_props/mask_object/-/1'
    assert df['_pycat_entity_id'].iloc[1] == 'unknown/measure_region_props/mask_object/-/2'


def test_measure_region_props_custom_names_rename_columns():
    lab, img = _two_region_labels()
    df = L.measure_region_props(lab, img, [('area', 'MyArea'), ('intensity_mean', 'MyMean')])
    assert 'MyArea' in df.columns and 'MyMean' in df.columns
    np.testing.assert_allclose(df['MyArea'].to_numpy(), [9.0, 16.0], rtol=1e-9)
    np.testing.assert_allclose(df['MyMean'].to_numpy(), [5.0, 8.0], rtol=1e-9)


def test_run_measure_binary_mask_pins_the_full_stats_row(monkeypatch):
    U = pytest.importorskip("pycat.ui.ui_utils")   # napari-bound; skip in a headless lane, don't error
    monkeypatch.setattr(U, "show_dataframes_dialog", lambda *a, **k: None)   # lazy import target — move-stable

    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True             # 9 px
    img = np.zeros((10, 10), dtype=np.float64)
    img[mask] = 4.0
    img[0, 0] = 2.0                   # one pixel outside the mask, so Relative Intensity < 1
    di = SimpleNamespace(data_repository={'microns_per_pixel_sq': 0.25})

    L.run_measure_binary_mask(SimpleNamespace(data=mask), SimpleNamespace(data=img), di)
    row = di.data_repository['binary_mask_stats_df'].iloc[0]

    assert int(row['Area']) == 9
    np.testing.assert_allclose(float(row['Intensity_Mean']), 4.0, rtol=1e-9)
    np.testing.assert_allclose(float(row['Intensity_Total']), 36.0, rtol=1e-9)
    np.testing.assert_allclose(float(row['Micron Area']), 2.25, rtol=1e-9)     # 9 * 0.25
    np.testing.assert_allclose(float(row['Relative Area']), 0.09, rtol=1e-9)   # 9 / 100
    np.testing.assert_allclose(float(row['Relative Intensity']), 0.9474, rtol=1e-9)  # round(36/38, 4)


def test_run_measure_binary_mask_rejects_a_shape_mismatch():
    di = SimpleNamespace(data_repository={'microns_per_pixel_sq': 1.0})
    with pytest.raises(ValueError):
        L.run_measure_binary_mask(SimpleNamespace(data=np.zeros((4, 4), bool)),
                                  SimpleNamespace(data=np.zeros((5, 5))), di)


def test_run_measure_region_props_appends_the_measured_numbers(monkeypatch):
    U = pytest.importorskip("pycat.ui.ui_utils")   # napari-bound; skip in a headless lane, don't error
    monkeypatch.setattr(U, "show_dataframes_dialog", lambda *a, **k: None)

    class _FakeDialog:
        Accepted = 1
        def __init__(self, props): pass
        def exec_(self): return 1
        def get_selected_props(self): return [('label', ''), ('area', ''), ('intensity_mean', '')]

    # Patch the function's OWN globals so this survives the module move unchanged.
    g = L.run_measure_region_props.__globals__
    monkeypatch.setitem(g, "MeasurementDialog", _FakeDialog)
    monkeypatch.setitem(g, "QDialog", SimpleNamespace(Accepted=1, Rejected=0))
    monkeypatch.setitem(g, "attach_layer_id", lambda df, layer: df)   # pin numbers, not the layer-id plumbing

    lab, img = _two_region_labels()
    di = SimpleNamespace(data_repository={'generic_df': pd.DataFrame()})
    L.run_measure_region_props(SimpleNamespace(data=lab), SimpleNamespace(data=img), di)
    g_df = di.data_repository['generic_df']

    assert list(g_df['label']) == [1, 2]
    np.testing.assert_allclose(g_df['area'].to_numpy(), [9.0, 16.0], rtol=1e-9)
    np.testing.assert_allclose(g_df['intensity_mean'].to_numpy(), [5.0, 8.0], rtol=1e-9)


def test_run_measure_region_props_cancel_does_nothing(monkeypatch):
    class _CancelDialog:
        def __init__(self, props): pass
        def exec_(self): return 0
        def get_selected_props(self): return []

    g = L.run_measure_region_props.__globals__
    monkeypatch.setitem(g, "MeasurementDialog", _CancelDialog)
    monkeypatch.setitem(g, "QDialog", SimpleNamespace(Accepted=1, Rejected=0))

    lab, img = _two_region_labels()
    di = SimpleNamespace(data_repository={'generic_df': pd.DataFrame()})
    L.run_measure_region_props(SimpleNamespace(data=lab), SimpleNamespace(data=img), di)
    assert di.data_repository['generic_df'].empty       # a cancelled dialog measures nothing
