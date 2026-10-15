"""**Every pipeline's results are linked, or declared not-applicable with a reason** (Completion Spec Phase F).

Two of fourteen pipelines were connected to the selection machinery; ten displayed their tables through a
dialog that never joined the bus, even when the tables were stamped. ``pycat.ui.pipeline_linking`` declares each
pipeline; this holds each to its declaration.
"""
import pathlib
import re

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'


def _source_text():
    return '\n'.join(p.read_text(encoding='utf-8', errors='ignore') for p in _SRC.rglob('*.py'))


def _declarations():
    import ast
    tree = ast.parse((_SRC / 'ui' / 'pipeline_linking.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == 'PIPELINE_LINKING')
    out = {}
    for k, v in zip(node.value.keys, node.value.values):
        args = [ast.literal_eval(a) for a in v.args]
        out[k.value] = (v.func.id, args)
    return out


def _pipeline_keys():
    import ast
    tree = ast.parse((_SRC / 'ui' / 'workflow_checklist.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], 'id', None) == 'PIPELINE_DEFS')
    return {k.value for k in node.value.keys}


def test_every_pipeline_is_declared_linked_or_not_applicable():
    assert set(_declarations()) == _pipeline_keys()


def test_a_not_applicable_pipeline_says_why():
    for name, (kind, args) in _declarations().items():
        if kind == 'NotApplicable':
            assert args and len(args[0]) > 20, f'{name}: a not-applicable declaration needs a reason'


def test_every_linked_operation_is_actually_stamped_somewhere():
    """The operation id must reach a stamping call -- a declared spec used by finalize_entity_table, or an
    explicit ``operation_id=`` on stamp_entity_ids. A renamed op id fails here instead of silently unlinking."""
    text = _source_text()
    for name, (kind, args) in _declarations().items():
        if kind != 'Linked':
            continue
        for op in args[0]:
            call = rf"\b(finalize_entity_table|_stamped|_stamp)\(.{{0,240}}?['\"]{re.escape(op)}['\"]"
            stamped = re.search(call, text, re.S) or f"operation_id='{op}'" in text
            assert stamped, f'{name}: operation {op!r} is declared linked but nothing stamps a table with it'


def test_every_linked_display_puts_its_tables_on_the_selection_bus():
    marks = ('service=', 'BrushableWorkspace', 'build_workspace(', 'build_invitro_workspace(', 'make_brushable(')
    for name, (kind, args) in _declarations().items():
        if kind != 'Linked':
            continue
        for module in args[1]:
            text = (_SRC / module).read_text(encoding='utf-8', errors='ignore')
            assert any(m in text for m in marks), f'{name}: {module} shows its tables without the selection service'
