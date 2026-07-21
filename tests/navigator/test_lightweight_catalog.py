"""**The catalog is discoverable without importing a single science module; execution imports lazily.**

Finding 1 of the lightweight-catalog work. Before, ``iter_operation_specs()`` imported every
tag-bearing module to run its decorators — so a missing optional/specialist dependency (``pywavelets``,
a GPU library) made a THIRD of the operation vocabulary undiscoverable, disabling Navigator entries for
operations whose *specs* are perfectly well known. Now:

* ``iter_operation_specs(live=False)`` (the default) reads the generated ``operation_catalog.json`` — no
  science imports — so the full vocabulary is always available;
* ``live=True`` keeps the import-and-introspect path the GENERATOR and the drift GUARD use;
* an operation's implementation is imported only when it is RUN (``resolve_operation``), and a missing
  dependency then names itself for THAT one operation instead of silently dropping the catalog entry.
"""
import pytest

from pycat.navigator.operation_spec import (
    OperationSpec, iter_operation_specs, resolve_operation, module_importable,
    operation_availability, runnability)
from pycat.utils.errors import OptionalDependencyError

pytestmark = pytest.mark.core

def test_discovery_does_not_use_the_import_path(monkeypatch):
    """The default (lightweight) path reads the JSON — it must NOT go through the import-and-introspect
    registry population (which imports every science module). Proven by making that path explode and
    showing discovery still returns the full catalog.

    (We assert on the CODE PATH, not on ``sys.modules`` — popping a tag-bearing module and letting it be
    re-imported re-runs its ``@tags_layer`` decorators into the process-global registry and raises
    ``TagCollision``, which would corrupt every later test.)"""
    import pycat.navigator.operation_spec as opspec

    def _must_not_be_called():
        raise AssertionError("lightweight discovery must not import the science modules")

    monkeypatch.setattr(opspec, "_populate_registry", _must_not_be_called)
    specs = opspec.iter_operation_specs()           # live=False default
    assert len(specs) > 50


def test_lightweight_and_live_agree():
    """The drift guard keeps the JSON == the live decorators, so the two paths must return the same
    vocabulary (id, role, inputs, requirements)."""
    light = {s.id: s for s in iter_operation_specs(live=False)}
    live = {s.id: s for s in iter_operation_specs(live=True)}
    assert set(light) == set(live)
    for op, s in live.items():
        assert (light[op].role, light[op].inputs, light[op].requirements) == \
               (s.role, s.inputs, s.requirements), op


def test_specs_carry_executor_coordinates():
    """Every spec resolvable to a callable carries an importable module + function."""
    specs = iter_operation_specs()
    band = next(s for s in specs if s.id == 'bandpass')
    assert band.module == 'pycat.toolbox.fft_bandpass_tools'
    assert band.function == 'fft_bandpass'


def test_a_missing_dependency_disables_ONE_operation_not_the_catalog():
    """The whole point: an op whose module cannot import is still LISTED, and only fails (precisely) at
    resolve time — the catalog count is unaffected."""
    before = len(iter_operation_specs())
    ghost = OperationSpec(id='ghost', role='mask', summary='', target=None, produces='mask',
                          aliases=(), registered_by=None,
                          module='pycat._definitely_missing_dep_xyz', function='run')
    with pytest.raises(OptionalDependencyError) as ei:
        resolve_operation(ghost)
    assert 'ghost' in str(ei.value)                 # names the operation
    assert not module_importable(ghost)
    assert len(iter_operation_specs()) == before    # catalog undiminished by the broken op


def test_resolve_operation_returns_a_callable_for_a_real_op():
    spec = next(s for s in iter_operation_specs(live=True) if s.id == 'clahe')  # UI op → tag_registry
    fn = resolve_operation(spec)
    assert callable(fn)
    assert module_importable(spec)


def test_operation_availability_reports_requirement_reasons_cheaply():
    """The default availability check is requirement-only (no import) — safe to call for every entry."""
    z = OperationSpec(id='z', role='mask', summary='', target=None, produces='mask',
                      aliases=(), registered_by=None, requirements=('z_stack', 'pixel_size'))
    can, reason = operation_availability(z, available=set())      # nothing available
    assert not can and 'z-stack' in reason and 'pixel size' in reason
    can, reason = operation_availability(z, available={'z_stack', 'pixel_size'})
    assert can and reason == ''


def test_operation_availability_can_surface_a_missing_dependency():
    ghost = OperationSpec(id='ghost', role='mask', summary='', target=None, produces='mask',
                          aliases=(), registered_by=None,
                          module='pycat._definitely_missing_dep_xyz', function='run')
    can, reason = operation_availability(ghost, available=set(), check_module=True)
    assert not can and 'optional dependency' in reason
