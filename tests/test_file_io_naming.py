"""Direct tests for `file_io/naming.py` — the payoff of the decomposition.

These pixel-size / lazy-label helpers were buried in the 2800-line `file_io.py`, reachable only by
importing its heavy Qt/napari surface. Moved to a pure module, they are now testable headlessly, which
is the whole point of extracting them. (`derive_layer_name`/`_clean_filename_token` stayed in file_io.py
and keep their own tests in `test_channel_modality` / `test_loader_fixes`.)
"""
import numpy as np
import pytest

from pycat.file_io.naming import _tiff_pixel_size_um, _lazy_contrast_limits

pytestmark = pytest.mark.core


def test_tiff_pixel_size_from_baseline_resolution_tags(tmp_path):
    """The reason this helper exists: many TIFFs store µm/px ONLY in the baseline
    XResolution/ResolutionUnit tags, which the structured reader ignores. 1000 px/cm → 10 µm/px."""
    tifffile = pytest.importorskip("tifffile")
    p = tmp_path / "res.tif"
    tifffile.imwrite(str(p), np.zeros((4, 4), np.uint16),
                     resolution=(1000, 1000), resolutionunit='CENTIMETER')
    assert _tiff_pixel_size_um(str(p)) == pytest.approx(10.0)   # 10 000 µm/cm ÷ 1000 px/cm


def test_tiff_pixel_size_none_on_a_non_tiff(tmp_path):
    p = tmp_path / "nope.tif"
    p.write_bytes(b"definitely not a tiff")
    assert _tiff_pixel_size_um(str(p)) is None                  # unreadable → None, never a wrong 1.0


def test_lazy_contrast_limits_from_a_prefetched_plane():
    plane = np.array([[0, 5], [10, 20]], np.uint16)
    assert _lazy_contrast_limits(None, prefetched=plane) == (0.0, 20.0)


def test_lazy_contrast_limits_none_when_flat():
    """A flat plane has hi == lo — return None rather than a degenerate (v, v) window."""
    assert _lazy_contrast_limits(None, prefetched=np.zeros((3, 3), np.uint16)) is None
