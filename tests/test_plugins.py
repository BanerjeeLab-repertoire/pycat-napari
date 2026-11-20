"""The plugin API (``pycat.plugins``): a fake plugin package, written to a temp dir and loaded through a real
``importlib.metadata.EntryPoint``, reaches every subsystem it registers into — the operation vocabulary and the
Navigator's registry, kernels, the batch step registry, step composition, executor adapters, the consolidated
table — while PyCAT's committed catalog stays PyCAT's alone. A plugin that fails leaves nothing behind.
"""
import sys
import textwrap
from importlib.metadata import EntryPoint

import pytest

pytestmark = pytest.mark.base

from pycat.plugins import loader, registry
from pycat.plugins.api import PLUGIN_API_VERSION, PluginAPI, PluginError

_GOOD = '''
PYCAT_PLUGIN_API = {major}

def register(api):
    api.add_target('bacterium')
    api.add_requirement('host_cells', 'human host cells in the field')
    from {pkg} import ops                      # decorators run now: after the target exists
    api.add_operation('adhesion_index', ops.adhesion_index, role='measurement',
                      summary='Adherent bacteria per host cell', inputs=('labels',))
    api.add_kernel('{name}.count_bacteria', '{pkg}.ops:count_bacteria', style='named')
    step = api.add_batch_step('count', ops.replay_count, operations=('{name}.count_bacteria',),
                              result_table=('bacterium', 'bacteria_df'))
    api.add_object_table('bacteria_df')
    api.add_navigator_adapter('{name}.count_bacteria', step)
    api.add_analysis_method('adhesion', 'Bacterial adhesion', '{pkg}.panel:AdhesionUI',
                            pipeline=(('open_image', 'Open'), (step, 'Count bacteria')))
    api.add_feature_card('adhesion', 'Bacterial adhesion', 'Count bacteria on host cells')
'''

_OPS = '''
from pycat.utils.tag_registry import tags_layer

@tags_layer('{name}.count_bacteria', role='measurement', summary='Count bacteria', target='bacterium',
            requirements=('host_cells',))
def count_bacteria(labels):
    return {{'n_bacteria': int(labels.max())}}

def adhesion_index(labels, image):
    return {{}}

def replay_count(state, image_path, params, output_dir):
    state['counted'] = params.get('n', 1)
'''


def _plugin(tmp_path, name, register_src=_GOOD, ops_src=_OPS, major=PLUGIN_API_VERSION[0]):
    """Write package ``<name>_pkg`` and return its entry point ``<name> = <name>_pkg:register``."""
    pkg = f"{name}_pkg"
    root = tmp_path / pkg
    root.mkdir()
    (root / '__init__.py').write_text(textwrap.dedent(register_src.format(major=major, pkg=pkg, name=name)))
    (root / 'ops.py').write_text(textwrap.dedent(ops_src.format(name=name)))
    if str(tmp_path) not in sys.path:
        sys.path.insert(0, str(tmp_path))
    return EntryPoint(name=name, value=f"{pkg}:register", group=loader.ENTRY_POINT_GROUP)


@pytest.fixture(autouse=True)
def _clean():
    loader._reset_for_tests()
    before = set(sys.modules)
    yield
    from pycat.ui.pipeline_steps import PIPELINE_DEFS, PIPELINE_DISPLAY_NAMES
    for key in [k for k in PIPELINE_DEFS if '.' in k]:
        PIPELINE_DEFS.pop(key)
        PIPELINE_DISPLAY_NAMES.pop(key, None)
    loader._reset_for_tests()
    for mod in set(sys.modules) - before:
        if '_pkg' in mod:
            sys.modules.pop(mod)


def _load(*eps):
    return {r.name: r for r in loader.load_plugins(entry_points=list(eps))}


def test_a_plugin_loads_and_says_what_it_added(tmp_path):
    rec = _load(_plugin(tmp_path, 'lab'))['lab']
    assert rec.status == 'loaded', rec.error
    kinds = {k for k, _ in rec.contributions}
    assert {'operation', 'kernel', 'batch step', 'object table', 'analysis method', 'feature card',
            'navigator adapter', 'target', 'requirement'} <= kinds


def test_plugin_operations_join_the_vocabulary_attributed_and_namespaced(tmp_path):
    from pycat.utils import tag_registry
    _load(_plugin(tmp_path, 'lab'))
    assert registry.plugin_of_operation('lab.count_bacteria') == 'lab'
    assert registry.plugin_of_operation('lab.adhesion_index') == 'lab'
    entry = tag_registry.get_operation('lab.adhesion_index')
    assert entry['registered_by'].endswith('lab_pkg.ops.adhesion_index')
    assert 'bacterium' in tag_registry.TARGETS and 'host_cells' in tag_registry.REQUIREMENTS


def test_plugin_ops_reach_the_navigator_but_not_pycats_committed_catalog(tmp_path):
    from pycat.navigator.op_catalog import build_catalog_document, build_operation_registry
    from pycat.navigator.operation_spec import iter_operation_specs
    _load(_plugin(tmp_path, 'lab'))
    own = {o['op'] for o in build_catalog_document()['operations']}
    assert 'lab.count_bacteria' not in own          # the snapshot + its regeneration guard stay PyCAT's alone
    full = {o['op']: o for o in build_catalog_document(include_plugins=True)['operations']}
    assert full['lab.count_bacteria']['kind'] == 'plugin'
    assert full['lab.count_bacteria']['source'] == 'plugin:lab'
    assert full['lab.count_bacteria']['target'] == 'bacterium'
    names = {c.name for c in build_operation_registry().all()}
    assert {'lab.count_bacteria', 'lab.adhesion_index'} <= names
    specs = {s.id: s for s in iter_operation_specs()}          # the import-free view lists them too
    assert specs['lab.count_bacteria'].module == 'lab_pkg.ops'
    assert specs['lab.count_bacteria'].requirements == ('host_cells',)


def test_plugin_kernel_runs_through_the_operation_service(tmp_path):
    import numpy as np
    from pycat.kernel.operation_service import OperationService
    _load(_plugin(tmp_path, 'lab'))
    out = OperationService.produce('lab.count_bacteria', {'labels': np.array([[0, 1], [2, 3]])})
    assert out == {'n_bacteria': 3}


def test_plugin_batch_steps_are_registered_composed_and_adapted(tmp_path):
    from pycat.batch.step_operations import all_step_operations, step_operations
    from pycat.batch_step_registry import register_all_steps
    from pycat.navigator.executor import _adapter_for
    _load(_plugin(tmp_path, 'lab'))

    steps = {}

    class _Recorder:
        def register_step(self, name, fn):
            steps[name] = fn
    register_all_steps(_Recorder())
    assert 'lab.count' in steps and 'open_image' in steps
    state = {}
    steps['lab.count'](state, None, {'n': 4}, None)
    assert state['counted'] == 4
    assert step_operations('lab.count') == ('lab.count_bacteria',)
    assert all_step_operations()['lab.count'] == ('lab.count_bacteria',)
    adapter = _adapter_for('lab.count_bacteria')
    assert adapter.batch_step == 'lab.count' and adapter.params_from(None, None, {}, {}) == {}


def test_plugin_object_tables_reach_the_consolidated_table(tmp_path):
    import pandas as pd
    from pycat.batch.steps._common import _ANALYSIS_STEP_TABLES
    from pycat.plugins.registry import plugin_step_tables
    from pycat.utils.consolidated_table import object_table_keys, records_from_output_dir
    _load(_plugin(tmp_path, 'lab'))
    assert 'bacteria_df' in object_table_keys()
    out = tmp_path / 'img'
    out.mkdir()
    pd.DataFrame({'area': [1.0, 2.0]}).to_csv(out / 'img_bacteria_df.csv', index=False)
    assert [t for t, _ in records_from_output_dir(out, 'img')] == ['bacteria']
    assert 'lab.count' not in _ANALYSIS_STEP_TABLES and plugin_step_tables()['lab.count'] == ('bacterium',
                                                                                             'bacteria_df')


def test_gui_wiring_registers_the_checklist_and_home_card(tmp_path):
    from pycat.ui.pipeline_steps import PIPELINE_DEFS
    from pycat.ui.plugins_menu import _installed_plugins_text, wire_plugin_contributions
    from pycat.utils.feature_registry import registry as feature_registry
    _load(_plugin(tmp_path, 'lab'))

    class _CM:
        analysis_methods_ui = type('AMU', (), {})()
    wire_plugin_contributions(_CM())
    assert PIPELINE_DEFS['lab.adhesion'] == [('open_image', 'Open'), ('lab.count', 'Count bacteria')]
    assert callable(getattr(_CM.analysis_methods_ui, '_switch_to_plugin__lab__adhesion'))
    assert 'lab.adhesion' in feature_registry()
    assert 'lab  [loaded]' in _installed_plugins_text()


def test_a_failing_plugin_is_skipped_and_leaves_nothing_behind(tmp_path):
    from pycat.utils import tag_registry
    broken = _GOOD.replace("    api.add_feature_card(", "    raise RuntimeError('boom')\n    api.add_feature_card(")
    recs = _load(_plugin(tmp_path, 'bad', register_src=broken), _plugin(tmp_path, 'lab'))
    assert recs['bad'].status == 'failed' and 'boom' in recs['bad'].error
    assert recs['lab'].status == 'loaded'           # one plugin's failure never stops another
    assert tag_registry.get_operation('bad.count_bacteria') is None
    assert 'bad.count' not in registry.plugin_batch_steps()
    assert 'host_cells' in tag_registry.REQUIREMENTS   # lab added it too, so it stays


def test_an_incompatible_plugin_is_refused_with_the_reason(tmp_path):
    rec = _load(_plugin(tmp_path, 'old', major=PLUGIN_API_VERSION[0] + 1))['old']
    assert rec.status == 'incompatible' and 'plugin API' in rec.error


def test_an_operation_outside_the_plugin_namespace_is_refused(tmp_path):
    rec = _load(_plugin(tmp_path, 'lab', ops_src=_OPS.replace("'{name}.count_bacteria'", "'count_bacteria'", 1)))
    assert rec['lab'].status == 'failed' and "must be 'lab.<operation>'" in rec['lab'].error


def test_cross_checks_name_what_is_wrong(tmp_path):
    rec = _load(_plugin(tmp_path, 'lab', register_src=_GOOD.replace(
        "operations=('{name}.count_bacteria',)", "operations=('{name}.nope',)")))['lab']
    assert rec.status == 'failed' and "unknown operation 'lab.nope'" in rec.error


def test_batch_step_signature_is_checked():
    api = PluginAPI('lab')
    with pytest.raises(PluginError, match='state, image_path, params, output_dir'):
        api.add_batch_step('x', lambda state, params: None)
    with pytest.raises(PluginError):
        PluginAPI('Bad-Name')


def test_disabled_plugins_load_nothing(monkeypatch):
    monkeypatch.setenv('PYCAT_DISABLE_PLUGINS', '1')
    assert loader.load_plugins() == []


def test_write_object_table_lands_where_the_consolidated_table_reads(tmp_path):
    import pandas as pd
    from pycat.plugins.batch import data_repository, write_object_table
    from pycat.utils.consolidated_table import records_from_output_dir
    state = {}
    path = write_object_table(state, pd.DataFrame({'area': [3.0]}), 'bacteria_df', tmp_path / 'img.tif', tmp_path)
    assert path.name == 'img_bacteria_df.csv'
    assert 'bacteria_df' in data_repository(state)
    assert [t for t, _ in records_from_output_dir(tmp_path, 'img', object_tables=('bacteria_df',))] == ['bacteria']


def test_pycats_own_ops_stay_pycats_when_a_plugin_import_registers_them_first(tmp_path):
    """In a fresh process the plugin's import is what first imports ``tag_registry`` (and any toolbox module it
    uses), so PyCAT's own decorators run while the plugin is loading. They must not become the plugin's."""
    import os
    import pathlib
    import subprocess
    ep = _plugin(tmp_path, 'lab')
    src = pathlib.Path(__file__).resolve().parents[1] / 'src'
    code = textwrap.dedent(f'''
        from importlib.metadata import EntryPoint
        from pycat.plugins import loader, registry
        (rec,) = loader.load_plugins(entry_points=[EntryPoint({ep.name!r}, {ep.value!r}, {ep.group!r})])
        assert rec.status == 'loaded', rec.error
        assert registry.plugin_of_operation('clahe') is None
        assert registry.plugin_of_operation('lab.count_bacteria') == 'lab'
        print('ok')
    ''')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(src)]))
    out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=300)
    assert out.stdout.strip().endswith('ok'), out.stderr[-2000:]
