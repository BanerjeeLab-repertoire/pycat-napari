"""**The decomposed UI builders construct at runtime (ui_builder_split).**

The largest builders (`_add_advanced_analysis`, `_add_condensate_physics`, …) were split into per-tab
builders + widget factories (returning a SimpleNamespace of handles) + run handlers that unpack the
namespace. The static contract (`test_ui_builder_split`) pins that no `ui_instance` attribute vanished,
but only a real construction exercises the runtime seam — the `SimpleNamespace` build/unpack and the
per-tab wiring. These build each widget headlessly against a stub UI and assert it comes up without
raising. Integration-marked (needs Qt).
"""
import types

import pytest


def _stub_ui():
    from PyQt5.QtWidgets import QComboBox

    class _Events:
        def connect(self, *a, **k):
            pass

    class _Layers(list):
        def __init__(self):
            super().__init__()
            self.events = types.SimpleNamespace(inserted=_Events(), removed=_Events())

    class _Viewer:
        layers = _Layers()

    class _DC:
        data_repository = {}

    class _CM:
        active_data_class = _DC()

    class _UI:
        viewer = _Viewer()
        central_manager = _CM()

        def add_text_label(self, layout, text, bold=False, **kwargs):
            pass

        def create_layer_dropdown(self, layer_type, *args, **kwargs):
            return QComboBox()

        def _add_widget_to_layout_or_dock(self, widget, layout, separate_widget, name):
            self._built = widget

        def _record(self, *a, **k):
            pass

    return _UI()


@pytest.mark.integration
def test_advanced_analysis_widget_constructs(qtbot):
    import pycat.toolbox.advanced_analysis_ui as m
    ui = _stub_ui()
    m._add_advanced_analysis(ui)
    assert getattr(ui, "_built", None) is not None


@pytest.mark.integration
def test_condensate_physics_widget_constructs(qtbot):
    import pycat.toolbox.condensate_physics_ui as m
    ui = _stub_ui()
    m._add_condensate_physics(ui)
    assert getattr(ui, "_built", None) is not None


@pytest.mark.integration
def test_timeseries_condensate_widget_constructs(qtbot):
    import pycat.toolbox.timeseries.ui as m
    ui = _stub_ui()
    m._add_run_timeseries_condensate_analysis(ui)
    assert getattr(ui, "_built", None) is not None


@pytest.mark.integration
def test_ts_cellpose_widget_constructs(qtbot):
    import pycat.toolbox.ts_cellpose_tools as m
    ui = _stub_ui()
    m._add_run_ts_cellpose(ui)
    assert getattr(ui, "_built", None) is not None


@pytest.mark.integration
def test_lazy_preprocess_widget_constructs(qtbot):
    import pycat.toolbox.timeseries.ui as m
    ui = _stub_ui()
    m._add_lazy_preprocess_stack(ui)
    assert getattr(ui, "_built", None) is not None
