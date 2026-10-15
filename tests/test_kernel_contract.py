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

#: Kernel ops that are deliberately not plannable Navigator operations: sub-coefficients of the object
#: colocalization workflow, which the Navigator plans as one op.
_NOT_PLANNABLE = {'coloc.manders_m1', 'coloc.manders_m2'}

#: (file relative to src/pycat, launcher qualname, wrapped function) -> why it has not moved yet.
_NOT_YET_MIGRATED: dict = {}


def _specs():
    from pycat.kernel.operation_service import OperationService
    return OperationService.specs()


def _vocabulary():
    from pycat.navigator.op_catalog import _measure_ops, load_operation_catalog
    return ({o['op']: o for o in load_operation_catalog()}, {o['id']: o for o in _measure_ops()})


def test_every_kernel_is_an_op_the_navigator_knows():
    catalog, measure = _vocabulary()
    unknown = sorted(op for op in _specs() if op not in catalog and op not in measure and op not in _NOT_PLANNABLE)
    assert not unknown, f'kernels with no operation-catalog entry: {unknown}'


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
    undeclared = []
    for step, fn in _STEP_MAP.items():
        if step in _METHOD_DISPATCH:
            continue
        missing = _kernel_ops_called(fn) - set(step_operations(step))
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
