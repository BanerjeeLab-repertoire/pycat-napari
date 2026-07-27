"""**Characterization pins for the label-operation functions — written BEFORE they move.**

`label_mask_split` Step 6 relocates the residual label-editing ops (`run_update_labels`,
`run_convert_labels_to_mask`, `run_label_binary_mask`, `run_expand_labels`, `run_mask_logic_merge`) out of
`label_and_mask_tools.py` into `toolbox/masks/labels.py`, after which the old file is a thin re-export shim.
Coverage was absent, so per the spec's "characterization-test-first, no test no move" discipline these pin the
EXACT label/mask outputs (array equality, never approximate) on synthetic inputs, plus the guard/reject paths.

They import through the public name (the re-export shim) and patch each function's own global namespace
(`__globals__`), so they pass **unchanged** before and after the move — the byte-identical proof.
"""
import numpy as np
import pytest
from types import SimpleNamespace

import pycat.toolbox.label_and_mask_tools as L

pytestmark = pytest.mark.base      # imports label_and_mask_tools (cv2/scipy/skimage) → the fuller lane


class _Viewer:
    """Captures add_labels(data, name) calls; that is the entire viewer contract these ops use."""
    def __init__(self):
        self.added = []

    def add_labels(self, data, name=None):
        self.added.append((name, np.asarray(data)))


def test_run_convert_labels_to_mask_binarizes_every_nonzero_label():
    v = _Viewer()
    labels = np.array([[0, 1, 1], [0, 2, 2], [3, 3, 0]])
    L.run_convert_labels_to_mask(SimpleNamespace(data=labels, name="L"), v)
    name, mask = v.added[0]
    assert name == "Mask from L"
    np.testing.assert_array_equal(mask, (labels > 0).astype(int))
    assert sorted(set(mask.ravel().tolist())) == [0, 1]


def test_run_label_binary_mask_labels_connected_components():
    v = _Viewer()
    m = np.zeros((7, 7), dtype=int)
    m[1:3, 1:3] = 1
    m[5, 5] = 1                                   # two separate components
    L.run_label_binary_mask(SimpleNamespace(data=m, name="M"), v)
    name, labeled = v.added[0]
    assert name == "Labeled M"
    import skimage as sk
    np.testing.assert_array_equal(labeled, sk.measure.label(m).astype(int))
    assert int(labeled.max()) == 2


def test_run_label_binary_mask_rejects_a_nonbinary_input():
    v = _Viewer()
    L.run_label_binary_mask(SimpleNamespace(data=np.array([[0, 2]]), name="X"), v)
    assert v.added == []                          # a non-binary mask adds nothing


def test_run_expand_labels_grows_a_label_by_the_distance():
    v = _Viewer()
    labels = np.zeros((7, 7), dtype=int)
    labels[3, 3] = 5
    L.run_expand_labels(SimpleNamespace(data=labels, name="L"), 1, v)
    name, expanded = v.added[0]
    assert name == "Expanded L"
    import skimage as sk
    np.testing.assert_array_equal(expanded, sk.segmentation.expand_labels(labels, distance=1.0).astype(int))
    assert int((expanded == 5).sum()) == 5        # a plus-shaped 1-pixel dilation


def test_run_expand_labels_rejects_a_nonpositive_distance():
    v = _Viewer()
    L.run_expand_labels(SimpleNamespace(data=np.zeros((3, 3), int), name="L"), 0, v)      # dist <= 0
    L.run_expand_labels(SimpleNamespace(data=np.zeros((3, 3), int), name="L"), "abc", v)  # non-numeric
    assert v.added == []


def test_run_mask_logic_merge_and_or_xor():
    a = np.array([[1, 1, 0], [0, 0, 0]])
    b = np.array([[0, 1, 1], [0, 0, 0]])
    expected = {'AND': np.logical_and(a != 0, b != 0),
                'OR': np.logical_or(a != 0, b != 0),
                'XOR': np.logical_xor(a != 0, b != 0)}
    for mode, want in expected.items():
        v = _Viewer()
        L.run_mask_logic_merge(SimpleNamespace(data=a, name="A"), SimpleNamespace(data=b, name="B"), mode, v)
        name, merged = v.added[0]
        assert name == f"{mode} (A · B)"
        np.testing.assert_array_equal(merged, want.astype(int))


def test_run_mask_logic_merge_rejects_shape_mismatch_and_unknown_mode():
    v = _Viewer()
    L.run_mask_logic_merge(SimpleNamespace(data=np.zeros((2, 2), int), name="A"),
                           SimpleNamespace(data=np.zeros((3, 3), int), name="B"), "AND", v)
    L.run_mask_logic_merge(SimpleNamespace(data=np.zeros((2, 2), int), name="A"),
                           SimpleNamespace(data=np.zeros((2, 2), int), name="B"), "NAND", v)
    assert v.added == []


def _stub_labels_layer(data):
    class _Labels:      # stand-in for napari.layers.Labels
        def __init__(self, d):
            self.data = d
            self.selected_label = 0
    return _Labels(data), _Labels


def test_run_update_labels_increment_mode_adds_to_every_label(monkeypatch):
    U = pytest.importorskip("pycat.ui.ui_utils")   # napari-bound; skip in a headless lane, don't error
    monkeypatch.setattr(U, "refresh_viewer_with_new_data", lambda *a, **k: None)

    active, Labels = _stub_labels_layer(np.array([[0, 1], [2, 3]]))
    monkeypatch.setitem(L.run_update_labels.__globals__, "_napari",
                        lambda: SimpleNamespace(layers=SimpleNamespace(Labels=Labels)))
    viewer = SimpleNamespace(layers=SimpleNamespace(selection=SimpleNamespace(active=active)))

    L.run_update_labels(SimpleNamespace(text=lambda: "10"),
                        SimpleNamespace(isChecked=lambda: True), viewer)
    np.testing.assert_array_equal(active.data, np.array([[10, 11], [12, 13]]))   # every label += 10


def test_run_update_labels_specific_mode_changes_one_label(monkeypatch):
    U = pytest.importorskip("pycat.ui.ui_utils")   # napari-bound; skip in a headless lane, don't error
    monkeypatch.setattr(U, "refresh_viewer_with_new_data", lambda *a, **k: None)

    active, Labels = _stub_labels_layer(np.array([[0, 1], [2, 2]]))
    active.selected_label = 2
    monkeypatch.setitem(L.run_update_labels.__globals__, "_napari",
                        lambda: SimpleNamespace(layers=SimpleNamespace(Labels=Labels)))
    viewer = SimpleNamespace(layers=SimpleNamespace(selection=SimpleNamespace(active=active)))

    L.run_update_labels(SimpleNamespace(text=lambda: "9"),
                        SimpleNamespace(isChecked=lambda: False), viewer)
    np.testing.assert_array_equal(active.data, np.array([[0, 1], [9, 9]]))       # label 2 → 9
