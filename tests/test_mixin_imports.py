"""
Static import-resolution test for the UI mixins.

This guards the `ui_modules.py` mixin split against the class of bug that surfaced
during the refactor: a widget-builder method moved into a mixin references a
module-level name (e.g. `math`, `guard_wheel`, `QSizePolicy`) that lived in
ui_modules.py and wasn't carried over — which parses fine but raises NameError /
UnboundLocalError at runtime when that widget is opened.

The check walks every top-level method in each mixin and confirms every loaded
name resolves from: module imports, local assignments/imports, parameters,
comprehension/with/except targets, sibling class methods, builtins, or `self`.
It is intentionally strict for mixin *methods* (which are top-level, not closures).

No Qt/napari needed — pure ast. Runs anywhere.

Run: pytest tests/test_mixin_imports.py -v
"""

import ast
import builtins
import glob
import os

import pytest

_BUILTINS = set(dir(builtins))
_MIXIN_GLOB = os.path.join(
    os.path.dirname(__file__), "..", "src", "pycat", "ui", "ui_*_mixin.py")


def _unresolved_names(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)

    mod_names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                mod_names.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                mod_names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            mod_names.add(n.name)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                for nm in ast.walk(t):
                    if isinstance(nm, ast.Name):
                        mod_names.add(nm.id)

    def _all_bound(method):
        """Every name bound anywhere in the method tree (all nested scopes pooled).
        Pooling nested scopes means legit closures resolve (no false positives); the
        tradeoff is we won't flag name-before-assignment within a method, which is
        not the bug class this guards (moved-method-lost-its-module-import)."""
        bound = set()
        for n in ast.walk(method):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(n.name)
                a = n.args
                for arg in list(a.args) + list(a.kwonlyargs) + list(getattr(a, "posonlyargs", [])):
                    bound.add(arg.arg)
                if a.vararg:
                    bound.add(a.vararg.arg)
                if a.kwarg:
                    bound.add(a.kwarg.arg)
            elif isinstance(n, ast.Lambda):
                for arg in list(n.args.args):
                    bound.add(arg.arg)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for nm in ast.walk(t):
                        if isinstance(nm, ast.Name):
                            bound.add(nm.id)
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                if isinstance(n.target, ast.Name):
                    bound.add(n.target.id)
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    bound.add(a.asname or a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                for nm in ast.walk(n.target):
                    if isinstance(nm, ast.Name):
                        bound.add(nm.id)
            elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for g in n.generators:
                    for nm in ast.walk(g.target):
                        if isinstance(nm, ast.Name):
                            bound.add(nm.id)
            elif isinstance(n, ast.withitem) and n.optional_vars:
                for nm in ast.walk(n.optional_vars):
                    if isinstance(nm, ast.Name):
                        bound.add(nm.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
        return bound

    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = set(m.name for m in node.body
                          if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)))
            for m in node.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                avail = (set(mod_names) | methods | _BUILTINS
                         | {"self", "cls", "__class__"} | _all_bound(m))
                for n in ast.walk(m):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                        if n.id not in avail:
                            problems.append((m.name, n.id, n.lineno))
    return problems


def _mixin_files():
    return sorted(glob.glob(_MIXIN_GLOB))


def test_mixin_files_exist():
    assert _mixin_files(), "no ui_*_mixin.py files found — glob or path wrong"


@pytest.mark.parametrize("path", _mixin_files(),
                         ids=lambda p: os.path.basename(p))
def test_mixin_names_resolve(path):
    """Every name used in a mixin method must resolve — catches the moved-method-
    lost-its-import bug (math / guard_wheel / QSizePolicy) at test time."""
    problems = _unresolved_names(path)
    assert not problems, (
        f"{os.path.basename(path)} has unresolved names (runtime NameError risk): "
        + "; ".join(f"{fn}() uses {name!r} @L{ln}" for fn, name, ln in problems))
