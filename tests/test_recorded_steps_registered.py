"""**Every step the GUI records is registered for batch replay** (Completion Spec E2).

A recorded key with no registry entry replays as nothing. 27 such keys were recorded by the panels (every
brightfield result step, the z-stack metrics, the general stack tools) while batch printed "not registered" and
moved on. Each is now registered -- a real replay, or a declared ``_not_replayed`` entry the run report lists --
and this scan of the source keeps a new ``_record('key', ...)`` from arriving without one.
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.base

_SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'


def _recorded_keys():
    """String step keys passed to a ``record`` / ``_record`` call anywhere in the package."""
    keys = {}
    for path in _SRC.rglob('*.py'):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
            if name not in ('record', '_record'):
                continue
            for arg in node.args[:2]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.isidentifier():
                    keys.setdefault(arg.value, f'{path.relative_to(_SRC)}:{node.lineno}')
                    break
    return keys


def test_every_recorded_step_key_is_registered():
    from pycat.batch_step_registry import _STEP_MAP
    missing = {k: where for k, where in _recorded_keys().items() if k not in _STEP_MAP}
    assert not missing, ('recorded step keys with no batch registry entry (register a replay, or '
                         f'_not_replayed(key, reason)): {missing}')


def test_every_checklist_step_key_is_registered():
    from pycat.batch_step_registry import _STEP_MAP
    from pycat.ui.workflow_checklist import PIPELINE_DEFS
    missing = sorted({k for steps in PIPELINE_DEFS.values() for k, _ in steps if k and k not in _STEP_MAP})
    assert not missing, f'checklist steps batch does not know: {missing}'
