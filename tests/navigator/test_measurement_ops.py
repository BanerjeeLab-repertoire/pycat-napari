"""**The catalog now contains the ANSWERS, not just the layers** (navigator wiring increment 1).

Before this, `operation_catalog.json` held only operations that produce an image or a label layer — the
planner could build a workflow's segmentation spine but had no operation whose output is a *measurement*,
so it could reach a mask and stop short of the number. This pins the measurement operations that close
that gap: each is bound to a REAL callable, produces the terminal `result` role, and declares only honest
requirements from the controlled vocabulary. (Gating on those requirements is increment 2; here they are
only declared.)
"""
import pytest

from pycat.navigator.operation_spec import iter_operation_specs, resolve_operation
from pycat.utils.tag_registry import REQUIREMENT_NAMES

pytestmark = pytest.mark.core

# The measurement operations this increment added, each with the module.function it MUST bind to. If a
# binding is renamed/moved, `resolve_operation` fails loudly here — the intended guard.
_EXPECTED = {
    'region_properties':     ('pycat.toolbox.feature_analysis_tools', 'run_cell_analysis_func'),
    'partition_coefficient': ('pycat.toolbox.invitro_tools', 'partition_coefficient_local'),
    'client_enrichment':     ('pycat.toolbox.partition_enrichment_tools', 'client_enrichment'),
    'size_distribution':     ('pycat.toolbox.invitro_tools', 'fit_size_distribution'),
    'spatial_statistics':    ('pycat.toolbox.spatial_metrology_tools', 'ripleys_l'),
    'colocalization':        ('pycat.toolbox.pixel_wise_corr_analysis_tools', 'pearsons_correlation'),
    'msd_diffusion':         ('pycat.toolbox.condensate_physics_tools', 'compute_msd'),
    'coarsening_fit':        ('pycat.toolbox.condensate_physics_tools', 'fit_coarsening'),
    'frap_recovery':         ('pycat.toolbox.frap_tools', 'fit_frap_recovery'),
    'viscosity':             ('pycat.toolbox.vpt_tools', 'viscosity_from_diffusion'),
}


def _measurement_specs():
    return {s.id: s for s in iter_operation_specs() if s.produces == 'result'}


def test_the_expected_measurement_ops_are_in_the_catalog():
    """This increment's 10 measurement ops are all present — a deliberate, reviewable delta (79 -> 89).
    (Two `result`-producing ops, `topology_envelope` and `optical_density`, pre-dated this increment; the
    subset check pins the ops THIS increment is responsible for without claiming the others.)"""
    got = set(_measurement_specs())
    missing = set(_EXPECTED) - got
    assert not missing, f"measurement ops missing from the catalog: {sorted(missing)}"

    total = len(iter_operation_specs())
    assert total == 89, (
        f"the catalog holds {total} ops, expected 89 (79 layer/ui + 10 measurements). If a layer op was "
        f"legitimately added/removed, update this count in the same commit that regenerates the catalog.")


def test_every_measurement_op_resolves_to_a_real_callable():
    """Bind to the real symbol: each measurement op's module.function must import to a callable, or the
    op is a dead entry in the catalog. `resolve_operation` is the loud failure that prevents that."""
    specs = _measurement_specs()
    for op, (module, function) in _EXPECTED.items():
        spec = specs[op]
        assert spec.module == module and spec.function == function, (
            f"{op} is bound to {spec.module}.{spec.function}, expected {module}.{function}")
        fn = resolve_operation(spec)      # raises OptionalDependencyError if it cannot import
        assert callable(fn), f"{op} did not resolve to a callable"


def test_measurements_are_terminal_products():
    """A measurement is a leaf — nothing in the catalog consumes a `result`, so the operation graph stays
    traversable with them added (the property `test_operation_graph` relies on)."""
    all_inputs = {i for s in iter_operation_specs() for i in s.inputs}
    assert 'result' not in all_inputs, (
        "an operation declares `result` as an input — a measurement is meant to be terminal; if a "
        "measurement genuinely feeds another op, the graph vocabulary needs a real edge, not a reused role")


def test_declared_requirements_are_in_the_controlled_vocabulary():
    """Honest gating later depends on honest requirements now: every requirement a measurement declares
    must be a controlled-vocabulary name, so a consumer can render its reason (increment 2)."""
    for op, spec in _measurement_specs().items():
        for req in spec.requirements:
            assert req in REQUIREMENT_NAMES, (
                f"{op} declares requirement {req!r}, not in the controlled vocabulary {REQUIREMENT_NAMES}")
    # and the ones that genuinely need a time series say so
    specs = _measurement_specs()
    for op in ('msd_diffusion', 'coarsening_fit', 'frap_recovery', 'viscosity'):
        assert 'time_axis' in specs[op].requirements, f"{op} needs a time axis but does not declare it"
    assert 'two_channels' in specs['colocalization'].requirements
    assert 'pixel_size' in specs['viscosity'].requirements
