"""**Clearing the workspace resets every open method widget's fields — via one central hub.**

`session_clear_reset` Bug 2: clearing left the previous workflow's spin boxes, dropdowns and status circles
populated, because each toolbox builder's `FieldRegistry` was an island the clear path could not find. The
hub is the missing central handle — every registry registers on construction (weakly), and
`_clear_everything` resets them all. These pin the Qt-free mechanism: register/reset/return-count; weak
references drop a closed widget's registry; one registry's failure does not block the rest; and
`_clear_everything` actually calls the reset.
"""
import gc

import pytest

from pycat.utils.field_registry_hub import (
    FieldRegistryHub, active_field_registries, register_field_registry)

pytestmark = pytest.mark.core


class _FakeRegistry:
    def __init__(self, boom=False):
        self.reset_count = 0
        self._boom = boom

    def reset_all(self):
        if self._boom:
            raise RuntimeError('this widget refused to reset')
        self.reset_count += 1


def test_reset_all_resets_every_registered_registry():
    hub = FieldRegistryHub()
    a, b = _FakeRegistry(), _FakeRegistry()
    hub.register(a)
    hub.register(b)
    assert hub.reset_all() == 2
    assert a.reset_count == 1 and b.reset_count == 1


def test_registration_is_idempotent():
    hub = FieldRegistryHub()
    a = _FakeRegistry()
    hub.register(a)
    hub.register(a)                          # same object again
    assert len(hub) == 1 and hub.reset_all() == 1


def test_a_closed_widgets_registry_drops_out_weakly():
    hub = FieldRegistryHub()
    keep = _FakeRegistry()
    transient = _FakeRegistry()
    hub.register(keep)
    hub.register(transient)
    assert len(hub) == 2

    del transient
    gc.collect()
    assert len(hub) == 1, "a GC'd registry must drop out of the hub — no leak, no resetting a dead widget"
    assert hub.reset_all() == 1 and keep.reset_count == 1


def test_one_registrys_failure_does_not_block_the_others():
    hub = FieldRegistryHub()
    good1, bad, good2 = _FakeRegistry(), _FakeRegistry(boom=True), _FakeRegistry()
    for r in (good1, bad, good2):
        hub.register(r)
    reset = hub.reset_all()
    assert good1.reset_count == 1 and good2.reset_count == 1, "a raising registry blocked clearing the rest"
    assert reset == 2                        # the boom one is not counted, but did not stop the clear


def test_the_active_hub_is_a_singleton_and_registers():
    r = _FakeRegistry()
    before = len(active_field_registries())
    register_field_registry(r)
    assert len(active_field_registries()) == before + 1
    del r
    gc.collect()
    assert len(active_field_registries()) == before      # dropped out weakly


# ── _clear_everything actually calls the hub reset ───────────────────────────────────────────────
class _FakeLayers:
    def select_all(self): pass
    def remove_selected(self): pass


class _FakeViewer:
    def __init__(self): self.layers = _FakeLayers()


class _FakeDataClass:
    def __init__(self): self.data_repository = {}
    def get_dataframes(self): return {}
    def reset_values(self, **kwargs): pass


class _FakeCM:
    def __init__(self):
        self.active_data_class = _FakeDataClass()
        self.persist_measurements = False


def test_clear_everything_resets_the_active_field_registries():
    from pycat.file_io.session import _clear_everything
    spy = _FakeRegistry()
    register_field_registry(spy)
    try:
        _clear_everything(_FakeViewer(), _FakeCM())
        assert spy.reset_count == 1, "clearing the workspace did not reset the open method widget's fields"
    finally:
        del spy
        gc.collect()


def test_field_registry_auto_registers_on_construction():
    """The wiring in `FieldRegistry.__init__` (needs Qt — skipped headlessly)."""
    pytest.importorskip('PyQt5')
    from pycat.ui.field_status import FieldRegistry
    before = len(active_field_registries())
    reg = FieldRegistry()
    assert len(active_field_registries()) == before + 1
    del reg
    gc.collect()
    assert len(active_field_registries()) == before
