"""**The Data QC dashboard runs** (Completion Spec G1).

`_on_run` imported ``QC_MAX_FRAMES`` from the ``data_qc_tools`` shim; the 1.6.350 decomposition dropped the
name, so every click on Run died with ``ImportError`` -- above the handler's ``try``. 38 QC-UI tests passed
throughout, because every one of them greps the source. These drive the real handler: build the panel on a
stub UI, click Run on a 2-D image and on a time series, and require a report.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.integration


class _Layer:
    def __init__(self, name, data):
        self.name, self.data = name, data
        self.metadata = {}


class _Layers(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next(layer for layer in self if layer.name == key)
        return list.__getitem__(self, key)


def _dashboard(layers, repo=None):
    from types import SimpleNamespace
    from qtpy.QtWidgets import QComboBox, QLabel
    from pycat.toolbox.data_qc_ui import _add_data_qc

    class _Stub:
        def __init__(self):
            self.docked = []
            self.viewer = SimpleNamespace(
                layers=_Layers(layers),
                window=SimpleNamespace(add_dock_widget=lambda w, **kw: self.docked.append((w, kw))))
            self.central_manager = SimpleNamespace(
                active_data_class=SimpleNamespace(data_repository=dict(repo or {})))
            self.widget = None

        def add_text_label(self, lay, text, bold=False):
            lay.addWidget(QLabel(text))

        def create_layer_dropdown(self, _type):
            dd = QComboBox()
            dd.addItems([layer.name for layer in layers])
            return dd

        def _add_widget_to_layout_or_dock(self, widget, layout, separate, name):
            self.widget = widget

    stub = _Stub()
    _add_data_qc(stub, separate_widget=True)
    return stub


def _click_run(stub, monkeypatch):
    import pycat.toolbox.data_qc_ui as mod
    from qtpy.QtWidgets import QPushButton
    shown = []
    monkeypatch.setattr(mod, 'napari_show_warning', lambda m: shown.append(('warning', m)))
    monkeypatch.setattr(mod, 'napari_show_info', lambda m: shown.append(('info', m)))
    monkeypatch.setattr('pycat.toolbox.data_qc_tools.plot_qc_report', lambda *a, **k: None)
    run = next(b for b in stub.widget.findChildren(QPushButton) if 'Run Quality Report' in b.text())
    run.click()
    return shown


def test_the_dashboard_runs_on_a_2d_image(qtbot, monkeypatch):
    rng = np.random.default_rng(0)
    img = (100 + rng.normal(0, 5, (128, 128))).astype(np.uint16)
    stub = _dashboard([_Layer('img', img)])
    qtbot.addWidget(stub.widget)
    shown = _click_run(stub, monkeypatch)
    results = stub.central_manager.active_data_class.data_repository.get('data_qc_results')
    assert results, f'the report never ran: {shown}'
    assert not any('QC failed' in m for _, m in shown), shown


def test_the_dashboard_runs_on_a_time_series_and_caps_the_frames(qtbot, monkeypatch):
    from pycat.toolbox.data_qc_tools import QC_MAX_FRAMES
    rng = np.random.default_rng(1)
    movie = (100 + rng.normal(0, 5, (QC_MAX_FRAMES + 16, 64, 64))).astype(np.uint16)
    stub = _dashboard([_Layer('movie', movie)])
    qtbot.addWidget(stub.widget)
    shown = _click_run(stub, monkeypatch)
    results = stub.central_manager.active_data_class.data_repository.get('data_qc_results')
    assert results, f'the report never ran: {shown}'
    assert not any('QC failed' in m for _, m in shown), shown


# ── The static contract tests (tests/test_qc_ui_contract.py), each backed by one that EXECUTES the handler ────

class _LazyStack:
    """A PyCAT lazy wrapper as far as QC sees it: `__array__` returns FRAME 0 ONLY (the 1.5.273 trap); frames are
    reachable only by index."""

    def __init__(self, frames):
        self._f = np.asarray(frames)
        self.shape, self.ndim, self.dtype = self._f.shape, 3, self._f.dtype

    def __getitem__(self, i):
        return self._f[i]

    def __array__(self, dtype=None, copy=None):
        return self._f[0] if dtype is None else self._f[0].astype(dtype)


def _results(stub):
    return {r['name']: r for r in stub.central_manager.active_data_class.data_repository['data_qc_results']}


def _set_axis(stub, axis):
    from qtpy.QtWidgets import QComboBox
    dd = next(c for c in stub.widget.findChildren(QComboBox) if c.findData(axis) >= 0 and c.count() == 4)
    dd.setCurrentIndex(dd.findData(axis))


def test_a_lazy_movie_is_qcd_as_a_movie_not_its_first_frame(qtbot, monkeypatch):
    rng = np.random.default_rng(2)
    frames = (100 + rng.normal(0, 5, (24, 64, 64))).astype(np.float32)
    stub = _dashboard([_Layer('movie', _LazyStack(frames))])
    qtbot.addWidget(stub.widget)
    _set_axis(stub, 't')
    _click_run(stub, monkeypatch)
    r = _results(stub)
    assert r['Drift']['status'] != 'na' and r['Photobleaching']['status'] != 'na', \
        'the drift / bleaching checks saw one frame, not the movie'


def test_a_report_where_checks_could_not_run_is_not_called_clean(qtbot, monkeypatch):
    img = (100 + np.random.default_rng(0).normal(0, 5, (128, 128))).astype(np.uint16)
    img[40:80, 40:80] += 400
    stub = _dashboard([_Layer('img', img)])
    qtbot.addWidget(stub.widget)
    shown = _click_run(stub, monkeypatch)
    messages = ' '.join(m for _, m in shown)
    assert 'could NOT run' in messages or 'POOR' in messages or 'CHECK' in messages, messages
    assert 'all assessed metrics look good' not in messages.lower()


def test_the_channel_images_reach_the_chromatic_check(qtbot, monkeypatch):
    rng = np.random.default_rng(4)
    base = np.zeros((128, 128))
    base[30:50, 30:50] = base[80:95, 60:90] = 500
    c1 = (100 + base + rng.normal(0, 3, base.shape)).astype(np.float32)
    c2 = (100 + base + rng.normal(0, 3, base.shape)).astype(np.float32)
    stub = _dashboard([_Layer('c1', c1), _Layer('c2', c2)])
    qtbot.addWidget(stub.widget)
    _click_run(stub, monkeypatch)
    chrom = _results(stub)['Chromatic aberration']
    assert chrom['status'] in ('good', 'warn', 'bad'), chrom['headline']


def test_the_exemplar_gallery_opens_from_the_dashboard(qtbot):
    from qtpy.QtWidgets import QPushButton
    img = np.zeros((32, 32), np.uint16)
    stub = _dashboard([_Layer('img', img)])
    qtbot.addWidget(stub.widget)
    next(b for b in stub.widget.findChildren(QPushButton) if 'quality problem' in b.text()).click()
    assert stub.docked, 'the gallery button did not dock the gallery'
    qtbot.addWidget(stub.docked[0][0])


# ── G4 / G6 / G10 in the dashboard itself ────────────────────────────────────────────────────────────────────

def test_the_placeholder_pixel_size_is_not_prefilled(qtbot):
    """Uncalibrated loads write microns_per_pixel_sq = 1; pre-filling it fabricated a Nyquist verdict (G4)."""
    from qtpy.QtWidgets import QDoubleSpinBox
    stub = _dashboard([_Layer('img', np.zeros((32, 32), np.uint16))], repo={'microns_per_pixel_sq': 1})
    qtbot.addWidget(stub.widget)
    px = next(s for s in stub.widget.findChildren(QDoubleSpinBox) if s.suffix() == ' µm')
    assert px.value() == 0.0


def test_an_unstated_stack_axis_refuses_the_axis_checks_and_z_runs_the_focal_series(qtbot, monkeypatch):
    rng = np.random.default_rng(5)
    st = (100 + rng.normal(0, 5, (12, 64, 64))).astype(np.float32)
    stub = _dashboard([_Layer('stack', st)])
    qtbot.addWidget(stub.widget)
    _click_run(stub, monkeypatch)
    assert _results(stub)['Stack axis']['status'] == 'na'
    _set_axis(stub, 'z')
    _click_run(stub, monkeypatch)
    r = _results(stub)
    assert 'Stack axis' not in r and r['Drift']['status'] == 'na' and 'focal series' in r['Drift']['how']


def test_the_axis_is_suggested_from_the_recorded_frame_count(qtbot):
    from qtpy.QtWidgets import QComboBox
    st = np.zeros((12, 32, 32), np.float32)
    stub = _dashboard([_Layer('movie', st)], repo={'n_t': 12})
    qtbot.addWidget(stub.widget)
    dd = next(c for c in stub.widget.findChildren(QComboBox) if c.count() == 4)
    assert dd.currentData() == 't'


def test_saving_writes_the_versioned_json(qtbot, monkeypatch, tmp_path):
    import json
    from qtpy.QtWidgets import QFileDialog, QPushButton
    img = (100 + np.random.default_rng(0).normal(0, 5, (64, 64))).astype(np.uint16)
    stub = _dashboard([_Layer('img', img)])
    qtbot.addWidget(stub.widget)
    _click_run(stub, monkeypatch)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        staticmethod(lambda *a, **k: (str(tmp_path / 'report.png'), '')))
    next(b for b in stub.widget.findChildren(QPushButton) if 'Save Report' in b.text()).click()
    payload = json.loads((tmp_path / 'report_metrics.json').read_text(encoding='utf-8'))
    assert payload['schema_version'] and payload['results']
