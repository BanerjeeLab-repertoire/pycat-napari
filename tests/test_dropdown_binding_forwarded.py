"""**Every toolbox `create_layer_dropdown` forwards the full `(name_hint, binding)` signature** (1.6.376 S6).

There is one canonical `create_layer_dropdown` (`ui/base_ui.py`, tag-aware, with `name_hint` and `binding`);
the nine toolbox UIs each define a thin one-line delegator to it. Eight of the nine had truncated signatures
that dropped `binding=` (and several dropped `name_hint=` too), so those panels physically could not pass a
resolver binding through — which is why almost no toolbox dropdown was resolver-bound. These are the contract
tests: every delegator must accept AND forward both keyword arguments to the canonical implementation.
"""
import inspect
import types

import pytest

pytestmark = pytest.mark.base      # importing the toolbox UI classes pulls in the PyQt5/napari stack


def _toolbox_ui_classes():
    from pycat.toolbox.brightfield_ui import BrightfieldCondensateUI
    from pycat.toolbox.frap_ui import FRAPUI
    from pycat.toolbox.fusion_ui import DropletFusionUI
    from pycat.toolbox.invitro_bf_ui import InVitroBFUI
    from pycat.toolbox.invitro_fluor_ui import InVitroFluorUI
    from pycat.toolbox.temperature_ui import TemperatureDependentUI
    from pycat.toolbox.timeseries_invitro_fluor_ui import TimeSeriesInVitroFluorUI
    from pycat.toolbox.vpt_ui import VideoParticleTrackingUI
    from pycat.toolbox.zstack_segmentation_ui import ZStackSegmentationUI
    return [
        BrightfieldCondensateUI, FRAPUI, DropletFusionUI, InVitroBFUI, InVitroFluorUI,
        TemperatureDependentUI, TimeSeriesInVitroFluorUI, VideoParticleTrackingUI, ZStackSegmentationUI,
    ]


def test_every_delegator_accepts_name_hint_and_binding():
    for cls in _toolbox_ui_classes():
        params = inspect.signature(cls.create_layer_dropdown).parameters
        assert 'binding' in params, f"{cls.__name__}.create_layer_dropdown drops binding="
        assert 'name_hint' in params, f"{cls.__name__}.create_layer_dropdown drops name_hint="


def test_the_canonical_implementation_still_defines_both():
    from pycat.ui.base_ui import BaseUIClass
    params = inspect.signature(BaseUIClass.create_layer_dropdown).parameters
    assert 'binding' in params and 'name_hint' in params


def test_every_delegator_forwards_both_kwargs_to_the_canonical():
    # The delegator only touches self.central_manager.toolbox_functions_ui.create_layer_dropdown, so a fake
    # self with a recording stub proves the forwarding without constructing any Qt widget.
    for cls in _toolbox_ui_classes():
        captured = {}

        def _stub(layer_type, name_hint='', binding=''):
            captured.update(layer_type=layer_type, name_hint=name_hint, binding=binding)
            return 'sentinel'

        fake_self = types.SimpleNamespace(
            central_manager=types.SimpleNamespace(
                toolbox_functions_ui=types.SimpleNamespace(create_layer_dropdown=_stub)))

        out = cls.create_layer_dropdown(fake_self, 'ImageLayer', name_hint='hint-x', binding='bind-y')
        assert out == 'sentinel', f"{cls.__name__} does not return the canonical result"
        assert captured == {'layer_type': 'ImageLayer', 'name_hint': 'hint-x', 'binding': 'bind-y'}, (
            f"{cls.__name__} did not forward both kwargs: {captured}")
