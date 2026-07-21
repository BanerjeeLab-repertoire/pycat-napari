"""**Contract net for the UI-builder split — every `ui_instance` attribute a builder sets must survive.**

The five largest functions in the tree are UI builders. Splitting them along widget-block boundaries is
mechanical, but the realistic failure mode is a **dropped `ui_instance` attribute** — a worker handle or a
widget the run method later reads that a careless extraction leaves unset. An import-only test misses it.

So this pins, for each builder, the set of `ui_instance.<attr> =` assignments it makes TODAY (captured
before the split), and asserts every one is still assigned SOMEWHERE in the builder's module after the
split — i.e. it moved into a `_build_*` helper rather than vanishing. Static (AST) so it runs headless,
where the pure-Qt builders cannot be constructed.

**This test is written before the split; do not regenerate the reference sets from post-split code.**
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'toolbox'

#: (module, builder) -> the ui_instance attributes it sets. Captured from the 1.6.182 tree, before the
#: split. Each must remain assigned somewhere in the module afterward.
_BUILDER_ATTRS = {
    ('advanced_analysis_ui.py', '_add_advanced_analysis'): {'_morph_worker', '_org_worker'},
    ('condensate_physics_ui.py', '_add_condensate_physics'): {'_hist_worker', '_msd_worker', '_qc_worker'},
    ('ts_cellpose_tools.py', '_add_run_ts_cellpose'): {'_ts_cellpose_worker'},
    ('timeseries_condensate_tools.py', '_add_lazy_preprocess_stack'):
        {'_ts_workers', '_ts_zarr_bgrem', '_ts_zarr_preproc'},
    ('timeseries_condensate_tools.py', '_add_run_timeseries_condensate_analysis'): set(),
}


def _module_attribute_assignments(module):
    """Every ``<name>.<attr> =`` attribute target assigned anywhere in the module — the pool an extracted
    ``_build_*`` helper's assignments land in."""
    tree = ast.parse((_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore'))
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute):
                    assigned.add(t.attr)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
            assigned.add(node.target.attr)
    return assigned


def test_every_builder_attribute_survives_the_split():
    missing = []
    for (module, builder), attrs in _BUILDER_ATTRS.items():
        assigned = _module_attribute_assignments(module)
        for a in sorted(attrs):
            if a not in assigned:
                missing.append(f"{module}::{builder} no longer sets `ui_instance.{a}`")
    assert not missing, (
        "a UI-builder split dropped an attribute the run method depends on:\n  " + "\n  ".join(missing))


def test_the_builders_still_exist_by_name():
    """A builder that vanished (renamed/removed by an over-eager split) is caught here — the menu wiring
    calls these by name."""
    for module, builder in {(m, b) for (m, b) in _BUILDER_ATTRS}:
        tree = ast.parse((_TOOLBOX / module).read_text(encoding='utf-8', errors='ignore'))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert builder in names, f"{module}::{builder}() no longer exists"
