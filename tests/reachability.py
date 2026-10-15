"""Reachability scan (Completion Spec C4): public toolbox/utils functions that tests exercise but no production
code references. See ``tests/test_reachability.py``."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'pycat'
TESTS = ROOT / 'tests'
SCOPES = ('toolbox', 'utils')


def _public_functions():
    out = {}
    for scope in SCOPES:
        for path in sorted((SRC / scope).rglob('*.py')):
            try:
                tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_'):
                    out.setdefault(node.name, f'{path.relative_to(SRC).as_posix()}:{node.lineno}')
    return out


def _production_references():
    """Names used anywhere in src/pycat as a Name, an Attribute, or a word inside a string (a catalog entry, a
    getattr) -- excluding import statements, ``__all__`` and each function's own ``def``."""
    used = set()
    word = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
    for path in SRC.rglob('*.py'):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        skip = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == '__all__' for t in node.targets):
                skip.update(id(n) for n in ast.walk(node))
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.alias) and node.asname:
                used.add(node.name.split('.')[-1])        # imported under another name, and used by that name
            elif isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.update(word.findall(node.value))
    for path in SRC.rglob('*.json'):
        used.update(word.findall(path.read_text(encoding='utf-8', errors='ignore')))
    return used


def _tested():
    text = '\n'.join(p.read_text(encoding='utf-8', errors='ignore') for p in TESTS.rglob('*.py'))
    return set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', text))


def unreachable_tested_functions() -> dict:
    """{name: 'file:line'} -- public, referenced by a test, referenced by no production code."""
    used, tested = _production_references(), _tested()
    return {n: where for n, where in _public_functions().items() if n in tested and n not in used}
