"""
**A name imported inside ONE branch and used in ANOTHER is a runtime crash waiting for the right
user.**

``_build_cellpose_model`` had::

    if _cellpose_major_version() >= 4:
        from cellpose import models              # <-- imported HERE
        model = models.CellposeModel(...)
    else:
        model = models.CellposeModel(...)        # <-- used HERE. Never imported.

**Every Cellpose 3.x install** — which is most of them — took the ``else`` branch and died::

    UnboundLocalError: cannot access local variable 'models'

**Cell segmentation was completely dead**, and nothing caught it.

Why `test_no_undefined_names` cannot catch this
-----------------------------------------------
``models`` **IS** bound — just in a branch that may not run. A scope-chain checker sees a binding
and stops. *It is not a scoping bug; it is a **control-flow** bug, and it needs its own guard.*

This test walks each function's AST and asks: **is any name imported inside a conditional branch,
and then used somewhere that branch does not dominate?**
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


def _conditionally_imported_names(function_node):
    """Names imported inside an ``if``/``try`` branch, and where."""
    inside_branch = {}

    for node in ast.walk(function_node):
        if not isinstance(node, (ast.If, ast.Try)):
            continue

        # Every import in ONE arm of the branch (not the whole function body).
        arms = []
        if isinstance(node, ast.If):
            arms = [node.body, node.orelse]
        else:
            arms = [node.body] + [h.body for h in node.handlers] + [node.orelse, node.finalbody]

        for arm_index, arm in enumerate(arms):
            for statement in arm:
                for inner in ast.walk(statement):
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        for alias in inner.names:
                            name = alias.asname or alias.name.split('.')[0]
                            inside_branch.setdefault(name, []).append((id(node), arm_index))

    return inside_branch


def _used_names(function_node):
    """Every name LOADED in the function, and the branch it sits in (or None for the top level)."""
    uses = []

    def _walk(node, branch):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.Try)):
                arms = ([child.body, child.orelse] if isinstance(child, ast.If)
                        else [child.body] + [h.body for h in child.handlers]
                        + [child.orelse, child.finalbody])
                for arm_index, arm in enumerate(arms):
                    for statement in arm:
                        _walk(statement, (id(child), arm_index))
                # The `if` TEST itself is outside both arms.
                if isinstance(child, ast.If):
                    _walk_expr(child.test, branch)
                continue

            _walk_expr(child, branch)
            _walk(child, branch)

    def _walk_expr(node, branch):
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                uses.append((inner.id, branch, inner.lineno))

    _walk(function_node, None)
    return uses


@pytest.mark.core
def test_no_name_is_imported_in_one_branch_and_used_in_another():
    """**The bug that killed Cellpose segmentation for every 3.x user.**

    A name imported inside one arm of an ``if`` and used inside the *other* arm is an
    ``UnboundLocalError`` for exactly the users who take the other path — and it looks perfectly
    fine to a scope checker, because the name **is** bound somewhere.
    """
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            imported = _conditionally_imported_names(node)
            if not imported:
                continue

            for name, locations in imported.items():
                arms_importing = set(locations)

                for used_name, branch, lineno in _used_names(node):
                    if used_name != name:
                        continue
                    if branch is None:
                        continue          # used at the function's top level — a different problem
                    if branch in arms_importing:
                        continue          # used in the same arm that imported it. Fine.

                    # Used in a DIFFERENT arm of a branch that also imports it elsewhere.
                    same_if = any(b[0] == branch[0] for b in arms_importing)
                    if same_if:
                        offenders.append(
                            f"{path.relative_to(_SOURCE)}:{lineno} in `{node.name}`: "
                            f"`{name}` is imported in one arm and used in another")

    assert not offenders, (
        "these names are imported inside one branch and used inside a different one:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\n\n**That is an UnboundLocalError for exactly the users who take the other path.** "
          "`test_no_undefined_names` cannot see it — the name IS bound, just not on every route "
          "through the function. Move the import ABOVE the branch."
    )


@pytest.mark.core
def test_the_cellpose_model_builds_on_BOTH_major_versions():
    """**The regression itself.** Cellpose 3.x → ``model_type``; Cellpose 4.x → ``pretrained_model``.

    Both APIs must work, and the import must be visible to both.
    """
    # _build_cellpose_model moved to the segmentation/cellpose.py family module (1.6.241).
    source = (_SOURCE / "toolbox" / "segmentation" / "cellpose.py").read_text(
        encoding='utf-8', errors='ignore')

    tree = ast.parse(source)
    builder = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == '_build_cellpose_model'), None)
    assert builder is not None, "_build_cellpose_model is gone"

    # The `from cellpose import models` must NOT be nested inside the version branch.
    for node in ast.walk(builder):
        if not isinstance(node, ast.If):
            continue
        for arm in (node.body, node.orelse):
            for statement in arm:
                for inner in ast.walk(statement):
                    if isinstance(inner, ast.ImportFrom) and inner.module == 'cellpose':
                        for alias in inner.names:
                            assert alias.name != 'models', (
                                "`from cellpose import models` is inside a version branch. The "
                                "OTHER branch then uses `models` with nothing having imported it "
                                "— which killed segmentation for every Cellpose 3.x user."
                            )
