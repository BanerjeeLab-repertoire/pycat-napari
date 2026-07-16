"""The status-bar coordinate readout: clean dual "px … | µm …", with µm precision fine enough that a
single-pixel move is visible (a fixed 1 decimal froze it on sub-0.1 µm pixels), and WITHOUT the
redundant layer-name/value clutter. Headless — fake viewer + layer, no napari/GL.
"""

from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.ui import coordinate_readout as cr


class _Layer:
    def __init__(self, px_um):
        self.name = "some_long_filename.ome C0-blue Stack"
        self.scale = np.array([1.0, px_um, px_um])
        self.data = np.zeros((10, 512, 512), np.uint16)

    def world_to_data(self, w):
        return np.asarray(w) / self.scale


def _status(px_um, x_px):
    layer = _Layer(px_um)

    class _V:
        cursor = SimpleNamespace(position=np.array([5.0, 100 * px_um, x_px * px_um]))
        layers = [layer]

    cr._top_data_layer = lambda v: layer
    return cr._coordinate_status(_V())


def test_dual_px_and_um_both_shown_when_calibrated():
    s = _status(0.0264, 137)
    assert s.startswith("px (r=100, c=137)")
    assert "µm (y=" in s and "x=" in s


def test_no_layer_name_or_value_in_readout():
    s = _status(0.0264, 137)
    assert "filename" not in s and "Stack" not in s and "=" not in s.split("µm")[0].replace("r=", "").replace("c=", "")
    # only two parts: px and µm
    assert s.count("|") == 1


def test_um_precision_makes_one_pixel_visible():
    # sub-0.1 µm pixel: consecutive pixels must render DIFFERENT µm (was static at 1 decimal).
    a = _status(0.0264, 100)
    b = _status(0.0264, 101)
    assert a != b, (a, b)


def test_precision_adapts_to_magnification():
    # coarse pixel (5 µm/px) → 1 decimal; fine pixel (0.0264) → 3 decimals.
    coarse = _status(5.0, 137)
    fine = _status(0.0264, 137)
    # count decimals in the x= µm field
    import re
    cd = len(re.search(r"x=(\d+)\.(\d+)\)", coarse).group(2))
    fd = len(re.search(r"x=(\d+)\.(\d+)\)", fine).group(2))
    assert cd == 1 and fd == 3, (cd, fd)


def test_uncalibrated_shows_px_only():
    layer = _Layer(1.0)
    layer.scale = np.array([1.0, 1.0, 1.0])   # unit scale = uncalibrated

    class _V:
        cursor = SimpleNamespace(position=np.array([5.0, 100.0, 137.0]))
        layers = [layer]

    cr._top_data_layer = lambda v: layer
    s = cr._coordinate_status(_V())
    assert s == "px (r=100, c=137)"    # no µm when there is no real scale
