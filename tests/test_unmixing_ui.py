"""**The spectral-unmixing toolbox step builds and unmixes end-to-end.**

Integration-marked (needs a real QApplication via ``qtbot``), so it skips in a headless core run. Drives the
actual Qt handler — builds the widget, points its dropdowns at synthetic control + mixed layers, clicks Run,
and asserts the recovered abundances are added as layers. The unmixing math itself is pinned Qt-free in
``test_unmixing``; this pins that the UI is WIRED to it (backlog C1 UI).
"""
import numpy as np
import pytest

_M = np.array([[1.00, 0.15], [0.08, 1.00]])          # 8 % / 15 % crosstalk


@pytest.mark.integration
def test_the_unmixing_step_builds_and_recovers_the_true_channels(qtbot):
    from PyQt5.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QLabel
    import napari
    from pycat.ui.ui_imageops_mixin import _ImageOpsWidgetsMixin

    a_true = np.stack([np.full((8, 8), 800.0), np.full((8, 8), 300.0)])      # (2,H,W) true abundances
    mixed = np.einsum('ij,jhw->ihw', _M, a_true)                             # observed = M · a
    ctrl0 = np.stack([np.full((8, 8), 500.0), np.full((8, 8), 40.0)])        # only fluor 0 → col [1, .08]
    ctrl1 = np.stack([np.full((8, 8), 45.0), np.full((8, 8), 300.0)])        # only fluor 1 → col [.15, 1]
    layers = {'mixed': type('L', (), {'data': mixed})(),
              'c0': type('L', (), {'data': ctrl0})(),
              'c1': type('L', (), {'data': ctrl1})()}

    added = []

    class _Viewer:
        def __init__(self): self.layers = layers
        def add_image(self, data, name=None): added.append((name, np.asarray(data)))

    class _Stub(_ImageOpsWidgetsMixin):
        def __init__(self): self.viewer = _Viewer(); self._w = None
        def add_text_label(self, lay, text, bold=False): lay.addWidget(QLabel(text))
        def create_layer_dropdown(self, ltype):
            dd = QComboBox(); dd.addItems(['(select)', 'mixed', 'c0', 'c1']); return dd
        def _add_widget_to_layout_or_dock(self, w, layout, sep, name): self._w = w

    stub = _Stub()
    stub._add_run_spectral_unmixing(layout=QVBoxLayout(), separate_widget=True)

    combos = stub._w.findChildren(QComboBox)          # order: mixed, ctrl0, ctrl1, ctrl2, ctrl3
    combos[0].setCurrentText('mixed')
    combos[1].setCurrentText('c0')
    combos[2].setCurrentText('c1')
    run = next(b for b in stub._w.findChildren(QPushButton) if 'Unmix' in b.text())
    run.click()

    assert {n for n, _ in added} == {'Unmixed C0', 'Unmixed C1'}, "the unmixed channels were not emitted"
    by_name = dict(added)
    assert np.allclose(by_name['Unmixed C0'], 800.0, atol=1e-6)   # recovered true abundances
    assert np.allclose(by_name['Unmixed C1'], 300.0, atol=1e-6)


@pytest.mark.integration
def test_the_step_warns_and_does_not_emit_when_controls_are_missing(qtbot, monkeypatch):
    from PyQt5.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QLabel
    import napari
    import pycat.ui.ui_imageops_mixin as mod
    from pycat.ui.ui_imageops_mixin import _ImageOpsWidgetsMixin

    warnings = []
    # the handler imports show_warning locally from napari.utils.notifications
    import napari.utils.notifications as noti
    monkeypatch.setattr(noti, 'show_warning', lambda m: warnings.append(m))

    added = []

    class _Viewer:
        layers = {'mixed': type('L', (), {'data': np.zeros((2, 4, 4))})()}
        def add_image(self, data, name=None): added.append(name)

    class _Stub(_ImageOpsWidgetsMixin):
        def __init__(self): self.viewer = _Viewer(); self._w = None
        def add_text_label(self, lay, text, bold=False): lay.addWidget(QLabel(text))
        def create_layer_dropdown(self, ltype):
            dd = QComboBox(); dd.addItems(['(select)', 'mixed']); return dd
        def _add_widget_to_layout_or_dock(self, w, layout, sep, name): self._w = w

    stub = _Stub()
    stub._add_run_spectral_unmixing(layout=QVBoxLayout(), separate_widget=True)
    stub._w.findChildren(QComboBox)[0].setCurrentText('mixed')     # mixed set, but NO controls
    next(b for b in stub._w.findChildren(QPushButton) if 'Unmix' in b.text()).click()

    assert added == [] and warnings, "with no controls it must warn and emit nothing"
