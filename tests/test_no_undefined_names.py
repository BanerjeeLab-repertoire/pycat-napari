"""
Guard: no name is used where Python cannot resolve it.

Python does not catch this at import time. A ``NameError`` from a misplaced variable
only fires when that *line* runs — so it can sit in a button handler for months, and
when it does fire it is often swallowed by a nearby ``except Exception``, which turns a
crash into something worse: **a feature that silently does nothing**.

All three instances found in this codebase were exactly that shape:

* ``advanced_analysis_ui.py`` used ``progress_emit`` in the button handler, but
  ``progress_emit`` is a *parameter of a nested ``_task``* declared three lines further
  down. The Dynamic Spatial Analysis button raised ``NameError`` before the worker was
  even created — it could never have run.
* ``file_io.py`` had the body of a ``_has_structured_metadata`` method (docstring and
  all) accidentally merged into the tail of ``_apply_saved_tags_to_layer``. It
  referenced ``file_path``, which is not a parameter of that method, so it raised
  ``NameError`` on **every tagged layer load** and swallowed it in its own
  ``except Exception: return False``.
* ``timeseries_condensate_tools.py`` read ``mask_name`` inside ``_on_finished``, but
  ``mask_name`` is a local of ``_on_run`` — a **sibling** nested function. Siblings do
  not share locals (a closure sees the *enclosing* scope, not another nested frame).
  Wrapped in ``except Exception``, the effect was that ticking "Ripley's L / PCF"
  produced **no Ripley and no PCF results at all**: no crash, no warning, just missing
  output.

There is a **second** shape, which an earlier version of this guard missed and which is
just as fatal:

* ``intensity_profile_tools.py``, ``molecular_counting_tools.py`` and
  ``morphological_complexity_tools.py`` each imported ``QSizePolicy`` in a *later*
  ``else:`` branch of the same function, but used it many lines *earlier*. Because Python
  sees the name assigned **somewhere** in the function, it treats it as a function-**local**
  for the entire scope — so the earlier use raises ``UnboundLocalError`` (not
  ``NameError``), **unconditionally**. All three widgets were impossible to construct.

So the guard checks two things:

1. **Unbound** — the name is bound nowhere in the enclosing scope chain (``NameError``).
2. **Used before assignment** — the name IS a local of this scope, but every binding of it
   occurs *after* the use (``UnboundLocalError``). This is checked only for names bound
   exclusively by ``import`` statements, where the "assignment" is unambiguous and
   control-flow analysis is not required; that is where the real bugs were, and it keeps
   the check free of false positives.

Scoping is modelled properly — closures, comprehensions, lambdas, class bodies,
``global``/``nonlocal`` — so a legitimate closure variable is not flagged.
"""

import ast
import builtins
import pathlib

import pytest

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "_"}
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


class _Scope:
    __slots__ = ("parent", "bound")

    def __init__(self, parent=None):
        self.parent = parent
        self.bound = set()

    def binds(self, name):
        s = self
        while s is not None:
            if name in s.bound:
                return True
            s = s.parent
        return False


def _bind_names(node, scope):
    """Record every name bound in this scope's body, WITHOUT descending into nested
    scopes (a nested function's locals are not visible to its parent — or its
    siblings, which is exactly the bug this guards against)."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        scope.bound.add(node.name)
        return
    if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp,
                         ast.DictComp, ast.GeneratorExp)):
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        scope.bound.add(node.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for a in node.names:
            scope.bound.add((a.asname or a.name).split(".")[0])
    elif isinstance(node, ast.ExceptHandler) and node.name:
        scope.bound.add(node.name)
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        scope.bound.update(node.names)
    for child in ast.iter_child_nodes(node):
        _bind_names(child, scope)


def _enter(node, scope, out, path):
    for child in ast.iter_child_nodes(node):
        _bind_names(child, scope)
    body = node.body if isinstance(node.body, list) else [node.body]
    for stmt in body:
        _visit(stmt, scope, out, path)


def _fn_scope(node, scope):
    inner = _Scope(scope)
    a = node.args
    for arg in a.posonlyargs + a.args + a.kwonlyargs:
        inner.bound.add(arg.arg)
    if a.vararg:
        inner.bound.add(a.vararg.arg)
    if a.kwarg:
        inner.bound.add(a.kwarg.arg)
    return inner


def _visit(node, scope, out, path):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # decorators and defaults evaluate in the OUTER scope
        for d in node.decorator_list:
            _visit(d, scope, out, path)
        for d in node.args.defaults + [x for x in node.args.kw_defaults if x]:
            _visit(d, scope, out, path)
        _enter(node, _fn_scope(node, scope), out, path)
        return
    if isinstance(node, ast.Lambda):
        _visit(node.body, _fn_scope(node, scope), out, path)
        return
    if isinstance(node, ast.ClassDef):
        for d in node.decorator_list:
            _visit(d, scope, out, path)
        for b in node.bases:
            _visit(b, scope, out, path)
        _enter(node, _Scope(scope), out, path)
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        inner = _Scope(scope)
        for gen in node.generators:
            for t in ast.walk(gen.target):
                if isinstance(t, ast.Name):
                    inner.bound.add(t.id)
        for i, gen in enumerate(node.generators):
            _visit(gen.iter, scope if i == 0 else inner, out, path)
            for cond in gen.ifs:
                _visit(cond, inner, out, path)
        for field in ("elt", "key", "value"):
            v = getattr(node, field, None)
            if v is not None:
                _visit(v, inner, out, path)
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id not in _BUILTINS and not scope.binds(node.id):
            out.append((path, node.lineno, node.id))
        return
    for child in ast.iter_child_nodes(node):
        _visit(child, scope, out, path)


def _use_before_import(tree, path):
    """Names imported ONLY at a line AFTER they are used, within the same function.

    Python hoists the local-ness of a name to the whole scope, so an import in a later
    branch makes every earlier use an UnboundLocalError. Restricted to import-bound names:
    there the binding line is unambiguous, so this cannot false-positive the way a general
    control-flow analysis would.
    """
    out = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        own = {n for n in ast.walk(fn)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn}
        nested = [(f.lineno, f.end_lineno) for f in own]

        def in_nested(ln):
            return any(a <= ln <= b for a, b in nested)

        # import bindings made directly in THIS function (not in a nested one)
        imported = {}
        other_bind = set()
        for n in ast.walk(fn):
            if in_nested(getattr(n, "lineno", 0)):
                continue
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    imported.setdefault((a.asname or a.name).split(".")[0],
                                        []).append(n.lineno)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                other_bind.add(n.id)
        # a parameter is bound at entry
        a = fn.args
        for arg in a.posonlyargs + a.args + a.kwonlyargs:
            other_bind.add(arg.arg)

        for n in ast.walk(fn):
            if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
                continue
            if in_nested(n.lineno):
                continue
            name = n.id
            if name not in imported or name in other_bind:
                continue
            if n.lineno < min(imported[name]):
                out.append((path, n.lineno, name, min(imported[name])))
    return out


def _undefined_names(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    out = []
    _enter(tree, _Scope(None), out, path.name)
    return out


@pytest.mark.core
def test_no_undefined_names():
    """No module may reference a name that Python cannot resolve at run time."""
    findings = []
    for f in sorted(_SRC.rglob("*.py")):
        rel = f.relative_to(_SRC)
        for _, lineno, name in _undefined_names(f):
            findings.append(f"{rel}:{lineno}  ->  '{name}'  (bound nowhere: NameError)")
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for _, lineno, name, bind_ln in _use_before_import(tree, str(rel)):
            findings.append(
                f"{rel}:{lineno}  ->  '{name}'  (only imported later, at line {bind_ln}: "
                f"UnboundLocalError)")

    assert not findings, (
        "These names are bound NOWHERE in their enclosing scope chain and will raise "
        "NameError when the line executes:\n  "
        + "\n  ".join(findings)
        + "\n\nThe usual cause is reading a variable that belongs to a SIBLING nested "
          "function (siblings do not share locals), or using a name before the nested "
          "function that defines it. Pass the value explicitly — e.g. via a one-element "
          "list used as a mutable cell, the idiom already used in "
          "timeseries_condensate_tools.py."
    )
