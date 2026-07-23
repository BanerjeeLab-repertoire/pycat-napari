"""`load_into_viewer` must normalise images the SAME way as every lazy stack wrapper: DTYPE-MAX
(divide by the dtype ceiling), never per-frame MIN-MAX (contrast-stretch each frame's own min..max).

Min-max is the normalization `utils/intensity_semantics` classifies DESTROYED: it forces every
frame's brightest pixel to 1.0 (false-tripping saturation ceilings), injects the frame's dark floor
as an offset (corrupting partition-coefficient ratios — `partition_coefficient_local` refuses a
min-max layer), and is frame-dependent (breaking intensity time-courses). The audit
(claude_code_spec_fileio_cleanup) confirmed no scientific module depends on min-max. Headless: a fake
viewer captures the array actually handed to napari.
"""

import types

import numpy as np
import pytest

pytestmark = pytest.mark.base


class _FakeViewer:
    layers = []


def _capture_load(monkeypatch, data):
    """Run load_into_viewer on `data` and return the array it hands to napari."""
    from pycat.file_io import viewer_load as vl
    captured = {}
    monkeypatch.setattr(vl, 'add_image_with_default_colormap',
                        lambda arr, viewer, name=None: captured.__setitem__('data', arr))
    monkeypatch.setattr(vl, '_tag_loaded_layer', lambda *a, **k: None)
    monkeypatch.setattr(vl, '_enable_auto_scale_bar', lambda *a, **k: None)
    cm = types.SimpleNamespace(
        active_data_class=types.SimpleNamespace(data_repository={}))
    vl.load_into_viewer(_FakeViewer(), cm, data, name='x')
    return captured['data']


def test_uint16_frame_is_dtype_max_not_minmax(monkeypatch):
    # A frame whose real range is 1000..40000 — it does NOT span 0..65535, so dtype-max and min-max
    # give very different answers.
    frame = np.array([[1000, 40000], [13000, 20000]], np.uint16)
    out = _capture_load(monkeypatch, frame)

    assert out.dtype == np.float32
    # DTYPE-MAX: 40000 / 65535 ≈ 0.6104 (NOT 1.0, which is what min-max would give).
    assert float(out.max()) == pytest.approx(40000 / 65535, rel=1e-4)
    assert float(out.min()) == pytest.approx(1000 / 65535, rel=1e-4)
    assert float(out.max()) < 0.99, "brightest pixel hit 1.0 — that is min-max, not dtype-max"


def test_unit_float_frame_passes_through_unchanged(monkeypatch):
    # Already in [0, 1] (dtype-max output of a loader): must NOT be stretched again.
    frame = np.array([[0.25, 0.5, 0.75]], np.float32)
    out = _capture_load(monkeypatch, frame)
    assert np.allclose(out, [[0.25, 0.5, 0.75]]), (
        "a [0,1] float was rescaled — min-max would map 0.25→0 and 0.75→1")


def test_load_into_viewer_does_not_call_minmax(monkeypatch):
    # A source-level guard: the load path must not reach for apply_rescale_intensity (min-max).
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "pycat" / "file_io" / "viewer_load.py").read_text(encoding='utf-8')
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, 'id', None) == 'apply_rescale_intensity']
    assert not calls, "viewer_load still min-maxes on load (apply_rescale_intensity call present)"
