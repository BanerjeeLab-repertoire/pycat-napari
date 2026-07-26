"""**Characterization pins for the mask-morphology functions — written BEFORE they move.**

`label_mask_split` Step 4 relocates the binary-morphology concern (`generate_cross_structuring_element`,
`extend_mask_to_edges`, `custom_binary_opening`/`custom_binary_closing`, `binary_morph_operation`,
`run_binary_morph_operation`, `opencv_contour_func`) out of `label_and_mask_tools.py` into
`toolbox/masks/morphology.py`. Coverage was thin, so per the spec's "characterization-test-first, no test no
move" discipline these pin the EXACT mask outputs on synthetic inputs — **exact array / pixel-count equality,
never approximate**, because a one-pixel difference propagates into every downstream object measurement.

They import through the public name (the re-export shim) and patch each function's own global namespace
(`__globals__`), so they pass **unchanged** before and after the move — the byte-identical proof.
(`split_touching_objects` moves too but is already pinned by `tests/test_group_c_geometry.py`.)
"""
import numpy as np
import pytest
from types import SimpleNamespace

import pycat.toolbox.label_and_mask_tools as L

pytestmark = pytest.mark.base      # imports label_and_mask_tools (cv2/scipy/skimage) → the fuller lane


def test_generate_cross_structuring_element_is_an_exact_cross():
    cross = L.generate_cross_structuring_element(2)
    expected = np.array([[0, 0, 1, 0, 0],
                         [0, 0, 1, 0, 0],
                         [1, 1, 1, 1, 1],
                         [0, 0, 1, 0, 0],
                         [0, 0, 1, 0, 0]])
    np.testing.assert_array_equal(cross, expected)


def test_extend_mask_to_edges_returns_a_copy_and_never_mutates_the_input():
    m = np.zeros((5, 5), dtype=int)
    m[2, 2] = 7
    before = m.copy()
    r = L.extend_mask_to_edges(m, 1)
    assert r is not m                                   # a fresh array — the documented aliasing bug is fixed
    np.testing.assert_array_equal(m, before)            # the caller's array is untouched
    # borders copied from one pixel in; the isolated centre pixel leaves the (zero) borders zero
    np.testing.assert_array_equal(r, before)


def test_extend_mask_to_edges_propagates_a_border_label_value():
    m = np.zeros((5, 5), dtype=int)
    m[1, :] = 3                                         # second row is all 3
    r = L.extend_mask_to_edges(m, 1)
    np.testing.assert_array_equal(r[0, :], np.full(5, 3))   # top border took row 1's values


def test_custom_binary_opening_and_closing_pin_exact_pixel_counts():
    speck = np.zeros((9, 9), dtype=bool)
    speck[3:6, 3:6] = True
    speck[0, 0] = True                                  # a corner speck opening should remove
    assert int(L.custom_binary_opening(speck).sum()) == 5      # speck gone, block eroded-then-dilated
    assert int(L.custom_binary_closing(speck).sum()) == 9


def test_binary_morph_operation_pins_every_mode():
    mask = np.zeros((9, 9), dtype=bool)
    mask[2:7, 2:7] = True
    mask[4, 4] = False                                  # a 5x5 block with a one-pixel hole
    expected_sum = {'Opening': 32, 'Closing': 81, 'Dilation': 81, 'Erosion': 4, 'Fill Holes': 25}
    for mode, want in expected_sum.items():
        out = L.binary_morph_operation(mask, iterations=1, element_size=1, element_shape='Disk', mode=mode)
        assert out.dtype == bool
        assert int(out.sum()) == want, f"{mode}: got {int(out.sum())}, want {want}"


def test_binary_morph_operation_fill_holes_fills_exactly_the_hole():
    mask = np.zeros((9, 9), dtype=bool)
    mask[2:7, 2:7] = True
    mask[4, 4] = False
    out = L.binary_morph_operation(mask, mode='Fill Holes')
    expected = np.zeros((9, 9), dtype=bool)
    expected[2:7, 2:7] = True                           # the hole is filled, nothing else changes
    np.testing.assert_array_equal(out, expected)


def test_opencv_contour_func_filters_by_true_filled_pixel_area():
    m = np.zeros((12, 12), dtype=bool)
    m[2:6, 2:6] = True                                  # 16 px object
    m[9, 9] = True                                      # 1 px speck
    filtered = L.opencv_contour_func(m, min_area=2, max_area=1000)
    assert filtered.dtype == np.uint8
    assert int(filtered.sum()) == 16                    # speck dropped, big object kept
    assert filtered[3, 3] == 1 and filtered[9, 9] == 0
    assert int(L.opencv_contour_func(m, min_area=1, max_area=1000).sum()) == 17   # no filter keeps both


def test_run_binary_morph_operation_computes_binary_morph_and_relabels(monkeypatch):
    """The GUI wrapper's science IS binary_morph_operation; pin that its refreshed output equals it, and that
    a LABELED input is re-labelled on the way out (its documented behaviour)."""
    import pycat.ui.ui_utils as U
    captured = {}
    monkeypatch.setattr(U, "refresh_viewer_with_new_data",
                        lambda viewer, layer, new_data: captured.__setitem__("data", new_data))

    class _Labels:      # stand-in for napari.layers.Labels
        def __init__(self, data): self.data = data

    g = L.run_binary_morph_operation.__globals__
    monkeypatch.setitem(g, "_napari",
                        lambda: SimpleNamespace(layers=SimpleNamespace(Labels=_Labels)))

    labels = np.zeros((9, 9), dtype=np.int32)
    labels[2:7, 2:7] = 5                                # one labelled block (id 5)
    active = _Labels(labels)
    viewer = SimpleNamespace(layers=SimpleNamespace(selection=SimpleNamespace(active=active)))
    txt = lambda v: SimpleNamespace(text=lambda: v)

    L.run_binary_morph_operation(None, txt("1"), txt("1"), "Disk", "Dilation", viewer)

    expected_bin = L.binary_morph_operation(labels > 0, iterations=1, element_size=1,
                                            element_shape="Disk", mode="Dilation")
    import skimage as sk
    expected = sk.measure.label(expected_bin).astype(labels.dtype)   # labelled input → relabelled output
    np.testing.assert_array_equal(captured["data"], expected)
