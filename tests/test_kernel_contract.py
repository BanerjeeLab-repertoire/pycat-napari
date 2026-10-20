"""**One task, one place: the execution kernel's contract** (OperationService adoption, after 1.7.005).

1. Every kernel's op id is in the operation vocabulary the Navigator plans over (the catalog's layer ops and the
   curated measure ops), and the function the kernel wraps IS the catalog's function for that op. The old
   ``rolling_ball`` kernel ran the edge-enhanced removal while the catalog's ``rolling_ball`` named the plain one:
   one id, two computations.
2. **No launcher calls a kernel's science directly.** A launcher is a batch replay (``replay_*``), a panel function
   (takes ``viewer``), or a method of a UI class. Each must go through ``OperationService`` for an op that has a
   kernel, so the panel and the batch replay of the same task run the same code. In a UI module (``*_ui.py``,
   ``ui/``, ``navigator/``) every function is a launcher -- the panel builders are module-level. Toolbox algorithms that use a
   kernel's function as a building block (e.g. subcellular segmentation's own background removal) are not
   launchers and are not affected.

``_NOT_YET_MIGRATED`` is a ratchet: it lists the launcher call sites still to move. A new direct call fails; an
entry that no longer exists fails too (delete it) -- the list only shrinks.
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.base

SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'

#: (file relative to src/pycat, launcher qualname, wrapped function) -> why it has not moved yet.
_NOT_YET_MIGRATED: dict = {}


def _specs():
    from pycat.kernel.operation_service import OperationService
    return OperationService.specs()


def _vocabulary():
    from pycat.navigator.op_catalog import _measure_ops, load_operation_catalog
    return ({o['op']: o for o in load_operation_catalog()}, {o['id']: o for o in _measure_ops()})


def test_every_kernel_is_an_op_the_navigator_knows():
    """Every kernel is a catalog op, or a declared component (``part_of``) of one: a stage or variant the
    Navigator plans as that op (the Manders sub-coefficients, the per-frame properties trajectory linking reads)."""
    catalog, measure = _vocabulary()
    known = set(catalog) | set(measure)
    unknown = sorted(op for op, spec in _specs().items() if op not in known and spec.part_of is None)
    assert not unknown, f'kernels with no operation-catalog entry (and no part_of): {unknown}'
    orphans = sorted(f'{op} -> {spec.part_of}' for op, spec in _specs().items()
                     if spec.part_of is not None and spec.part_of not in known)
    assert not orphans, f'part_of names an op that is not in the catalog: {orphans}'
    both = sorted(op for op, spec in _specs().items() if spec.part_of is not None and op in known)
    assert not both, f'a component is planned as its parent, so it must not also be a catalog op: {both}'


def test_every_kernel_wraps_the_catalogs_function_for_its_op():
    from pycat.kernel.operation_service import resolve
    catalog, measure = _vocabulary()
    wrong = []
    for op, spec in _specs().items():
        fn = resolve(spec.wraps)
        tagged = getattr(fn, '__pycat_op__', None)
        if tagged is not None and tagged != op:
            wrong.append(f'{op}: wraps {spec.wraps}, which is tagged {tagged!r}')
        entry = catalog.get(op)
        if entry and entry['kind'] == 'toolbox' and entry['function'] != fn.__name__:
            wrong.append(f"{op}: wraps {fn.__name__}, the catalog's {op} is {entry['function']}")
        if op in measure and measure[op].get('api', '').rsplit('.', 1)[-1] != fn.__name__:
            wrong.append(f"{op}: wraps {fn.__name__}, the measure op's api is {measure[op].get('api')}")
    assert not wrong, '\n'.join(wrong)


# ── Launchers go through the kernel ─────────────────────────────────────────────────────────────────────────

def _is_ui_module(rel: str) -> bool:
    return rel.endswith('_ui.py') or rel.startswith(('ui/', 'navigator/'))


def _launchers(tree, rel):
    """(qualname, node) for every launcher function in a module (nested defs are covered by their parent)."""
    batch = rel.startswith('batch/') or rel == 'batch_step_registry.py'
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = {a.arg for a in node.args.args + node.args.kwonlyargs}
            if (batch and node.name.startswith('replay_')) or 'viewer' in params or _is_ui_module(rel):
                yield node.name, node
        elif isinstance(node, ast.ClassDef) and _is_ui_module(rel):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f'{node.name}.{item.name}', item


def direct_calls():
    """{(file, launcher, function)} for every launcher that calls a kernel-wrapped function directly."""
    wrapped = {spec.wraps.split(':', 1)[1] for spec in _specs().values()}
    found = set()
    for path in SRC.rglob('*.py'):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith('kernel/'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for qualname, fn in _launchers(tree, rel):
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, 'id', None) or getattr(call.func, 'attr', None)
                    if name in wrapped:
                        found.add((rel, qualname, name))
    return found


def test_no_launcher_calls_a_kernels_science_directly():
    found = direct_calls()
    new = sorted(found - set(_NOT_YET_MIGRATED))
    assert not new, ('these launchers call a kernel op\'s function directly; call OperationService.execute / '
                     '.produce with the op id instead:\n' + '\n'.join(map(str, new)))


def test_the_not_yet_migrated_list_only_shrinks():
    stale = sorted(set(_NOT_YET_MIGRATED) - direct_calls())
    assert not stale, f'migrated -- delete from _NOT_YET_MIGRATED: {stale}'


def _kernel_ops_called(fn):
    """The literal op ids a function sends to OperationService.execute / .produce."""
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {call.args[0].value for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in ('execute', 'produce') and getattr(call.func.value, 'id', None) == 'OperationService'
            and call.args and isinstance(call.args[0], ast.Constant)}


#: Steps that dispatch on a recorded ``method`` param: a fixed op tuple would be wrong for most methods, so they are
#: deliberately left undeclared (test_batch_step_composition's rationale for ivf_segmentation).
_METHOD_DISPATCH = {'ivf_preprocess', 'ivf_segmentation'}


def test_a_steps_declared_operations_include_every_op_its_replay_runs():
    """``_STEP_OPERATIONS`` was declared by hand and could not be checked; a replay that goes through the kernel
    names its ops in the call, so the declaration is now verified against the code."""
    from pycat.batch_step_registry import _STEP_MAP, step_operations
    specs = _specs()
    undeclared = []
    for step, fn in _STEP_MAP.items():
        if step in _METHOD_DISPATCH:
            continue
        # a component op is declared as the catalog op it is part of -- the vocabulary a step declares in
        called = {specs[op].part_of or op if op in specs else op for op in _kernel_ops_called(fn)}
        missing = called - set(step_operations(step))
        if missing:
            undeclared.append(f'{step}: runs {sorted(missing)} but declares {step_operations(step)}')
    assert not undeclared, '\n'.join(undeclared)


def test_OperationService_is_imported_once_at_module_level():
    """A local ``from pycat.kernel import OperationService`` makes the name local to the WHOLE function: a kernel
    call earlier in that function, or in a branch that skips the import, raises UnboundLocalError. That broke the
    in-vitro Dynamics panels' coarsening fit during the migration; the real-data verification caught it."""
    local = []
    for path in SRC.rglob('*.py'):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith('kernel/'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.ImportFrom) and node.module == 'pycat.kernel'
                            and any(a.name == 'OperationService' for a in node.names)):
                        local.append(f'{rel}:{node.lineno} (in {fn.name})')
    assert not local, 'import OperationService at module level instead:\n' + '\n'.join(sorted(set(local)))


def test_every_catalog_op_a_launcher_calls_has_a_kernel():
    """The rule above only polices functions that already HAVE a kernel. This closes the gap: a launcher calling
    any catalog operation's function (``@tags_layer`` toolbox ops, curated measure ops) must find a kernel for it,
    so a new panel cannot quietly go back to calling the science directly."""
    catalog, measure = _vocabulary()
    kernels = {spec.wraps.split(':', 1)[1] for spec in _specs().values()}
    op_functions = {o['function'] for o in catalog.values() if o['kind'] == 'toolbox'}
    op_functions |= {m['api'].rsplit('.', 1)[-1] for m in measure.values() if m.get('api')}
    missing = set()
    for path in SRC.rglob('*.py'):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith('kernel/'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for qualname, fn in _launchers(tree, rel):
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, 'id', None) or getattr(call.func, 'attr', None)
                    if name in op_functions and name not in kernels:
                        missing.add((rel, qualname, name))
    assert not missing, ('launchers call catalog operations that have no kernel -- register one in '
                         'pycat/kernel/ops.py and call it through OperationService:\n' + '\n'.join(map(str, sorted(missing))))


# ── Everything in the catalog ───────────────────────────────────────────────────────────────────────────────

def _is_science_module(module: str) -> bool:
    rel = module.split('pycat.', 1)[-1].replace('.', '/')
    return module.startswith('pycat.toolbox.') and not _is_ui_module(rel + '.py')


def science_calls():
    """{(file, launcher, 'module:function')} for every launcher call of a public toolbox function (resolved
    through the module's imports, or defined in the same module) that is not itself a launcher."""
    trees = {}
    for path in SRC.rglob('*.py'):
        rel = path.relative_to(SRC).as_posix()
        module = 'pycat.' + rel[:-3].replace('/', '.').removesuffix('.__init__')
        trees[module] = (ast.parse(path.read_text(encoding='utf-8')), rel)

    def definition(module, name):
        tree = trees.get(module, (None,))[0]
        return next((n for n in getattr(tree, 'body', ()) if isinstance(n, ast.FunctionDef) and n.name == name), None)

    found = set()
    for module, (tree, rel) in trees.items():
        if rel.startswith('kernel/'):
            continue
        imported = {a.asname or a.name: (n.module, a.name) for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module for a in n.names}
        for qualname, fn in _launchers(tree, rel):
            for call in ast.walk(fn):
                name = getattr(getattr(call, 'func', None), 'id', None) if isinstance(call, ast.Call) else None
                if not name:
                    continue
                target = imported.get(name) or ((module, name) if definition(module, name) else None)
                if target is None or not _is_science_module(target[0]) or target[1].startswith('_'):
                    continue
                d = definition(*target)
                if d is None or 'viewer' in {a.arg for a in d.args.args + d.args.kwonlyargs}:
                    continue
                found.add((rel, qualname, f'{target[0]}:{target[1]}'))
    return found


#: Toolbox functions a launcher calls that are NOT operations: they draw, format, read files or probe the
#: environment -- no measurement depends on them. 'module:function' -> why.
_NOT_OPERATIONS: dict = {
    'pycat.toolbox.analysis_plots:plot_coarsening': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_frap_recovery': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_fusion_relaxation': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_km_survival': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_msd_trajectories': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_phase_diagram': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_spatial_metrology': 'draws a figure from results already computed',
    'pycat.toolbox.analysis_plots:plot_vpt_panel': 'draws a figure from results already computed',
    'pycat.toolbox.coloc.analysis:cytofluorogram_plot': 'draws a figure from results already computed',
    'pycat.toolbox.coloc.analysis:li_ica_plot': 'draws a figure from results already computed',
    'pycat.toolbox.condensate_modes:annotate_summary_table': 'adds explanatory notes to a computed table',
    'pycat.toolbox.control_validation:control_report_figure': 'draws a figure from results already computed',
    'pycat.toolbox.fd_curve_tools:cycles_to_dataframe': 'formats cycles the FD-curve analysis computed',
    'pycat.toolbox.fd_curve_tools:wlc_extensible':
        'draws the worm-like-chain reference curve over the measured loops',
    'pycat.toolbox.frap_tools:circular_mask': 'builds a mask from a drawn circle (geometry the user drew)',
    'pycat.toolbox.frap_tools:masks_from_shapes': 'converts drawn shapes to masks (geometry the user drew, not a measurement)',
    'pycat.toolbox.frap_tools:masks_from_shapes_multi': 'converts drawn shapes to masks (geometry the user drew, not a measurement)',
    'pycat.toolbox.gpu_utils:gpu_available': 'probes the environment',
    'pycat.toolbox.invitro.partition:brush_ready_droplet_table': 'lays out computed tables for brushing; computes nothing new',
    'pycat.toolbox.invitro.timeseries_kinetics:brush_ready_kinetics': 'lays out computed tables for brushing; computes nothing new',
    'pycat.toolbox.invitro.timeseries_kinetics:channel_column': 'a column-name helper',
    'pycat.toolbox.invitro.timeseries_kinetics:short_channel_name': 'a column-name helper',
    'pycat.toolbox.pipeline_diagnostic_tools:bg_removal_steps_current':
        'shows the intermediate steps of a pipeline for comparison; no result is computed from them',
    'pycat.toolbox.pipeline_diagnostic_tools:bg_removal_steps_v100':
        'shows the intermediate steps of a pipeline for comparison; no result is computed from them',
    'pycat.toolbox.pipeline_diagnostic_tools:preprocess_steps_current':
        'shows the intermediate steps of a pipeline for comparison; no result is computed from them',
    'pycat.toolbox.pipeline_diagnostic_tools:preprocess_steps_v100':
        'shows the intermediate steps of a pipeline for comparison; no result is computed from them',
    'pycat.toolbox.qc_gallery:build_gallery': 'builds a QC image gallery for viewing',
    'pycat.toolbox.segmentation.contrast_floor:floor_counts':
        'the kept/below counts shown beside the slider; contrast_floor.apply is the operation',
    'pycat.toolbox.segmentation.local_thresholding:local_threshold_k':
        'maps a slider position to the k parameter; computes nothing from data',
    'pycat.toolbox.spatial_acf_tools:plot_sigma_vs_slice': 'draws a figure from results already computed',
    'pycat.toolbox.temperature_tools:build_temperature_labels': 'builds annotation strings for display',
    'pycat.toolbox.temperature_tools:elapsed_to_seconds': 'a unit conversion of timestamps',
    'pycat.toolbox.temperature_tools:locate_temperature_csv': 'finds a file',
    'pycat.toolbox.temperature_tools:parse_batch_filenames': 'parses file names',
    'pycat.toolbox.temperature_tools:plot_turbidity_transitions': 'draws a figure from results already computed',
    'pycat.toolbox.temperature_tools:read_micromanager_times': 'reads acquisition metadata',
    'pycat.toolbox.temperature_tools:render_annotated_mp4': 'renders a movie for viewing',
    'pycat.toolbox.timeseries.cell_pipeline:result_tables': 'formats the cellular time-series result',
    'pycat.toolbox.timeseries.results_view:droplet_trace_metrics': 'picks which measured columns to plot',
    'pycat.toolbox.timeseries_invitro_tools:make_frame_segmenter': 'builds the per-frame callable the ts_droplet_segment op runs; computes nothing itself',
    'pycat.toolbox.timeseries_invitro_tools:object_records_to_df': 'formats records the object_trajectories op computed',
    'pycat.toolbox.trackmate_bridge:trackmate_bridge_available': 'probes the environment',
    'pycat.toolbox.unmixing_tools:clip_for_display': 'display scaling only; the measured arrays are untouched',
}

#: The ratchet for "everything in the catalog": science a launcher still calls directly. Shrinks only -- and it is
#: empty: the last 88 (each called by one panel) went in on 2026-10-19. A new direct call fails the test above.
_NOT_YET_IN_CATALOG: set = set()     # empty since 2026-10-19: everything a launcher calls is in the catalog


def test_every_science_function_a_launcher_calls_is_in_the_catalog():
    """One task, one place, for ALL science: a panel, a batch replay and the Navigator reach an operation only
    through its catalog id. A launcher that calls a toolbox function directly must find a kernel for it."""
    kernels = {spec.wraps for spec in _specs().values()}
    direct = {fn for _, _, fn in science_calls()}
    missing = sorted(direct - kernels - set(_NOT_OPERATIONS) - _NOT_YET_IN_CATALOG)
    assert not missing, ('launchers call toolbox science that has no operation id -- give it a catalog entry and a '
                         'kernel (pycat/kernel/ops.py), or, if it computes nothing a result depends on, list it in '
                         '_NOT_OPERATIONS with the reason:\n' + '\n'.join(missing))


def test_the_not_yet_in_catalog_list_only_shrinks():
    kernels = {spec.wraps for spec in _specs().values()}
    direct = {fn for _, _, fn in science_calls()}
    stale = sorted(_NOT_YET_IN_CATALOG - (direct - kernels))
    assert not stale, f'in the catalog now (or no longer called) -- delete from _NOT_YET_IN_CATALOG: {stale}'
