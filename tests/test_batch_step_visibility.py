"""**A dropped batch item must be VISIBLE — a failed consolidated append is not a clean success.**

The `exception_context_classification` spec's batch_step rule: a broad handler around one batch item must
not silently drop it, because a 93-of-100 cohort that looks complete is a silent scientific corruption. The
concrete offender was `BatchWorker.run`: when a per-image consolidated-table append failed, it printed a
note but still recorded the image as `✓`, so its rows vanished from `consolidated_long.csv` while the batch
reported success. This AST-guards the fix (the loop is a QThread `run`, not unit-runnable headless): the
success mark is now GATED on the consolidated append succeeding, with a visible partial status otherwise.
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core


def _run_fn():
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'
           / 'batch_processor.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    worker = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.ClassDef) and n.name == 'BatchWorker')
    return next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == 'run'), src


def test_the_consolidated_append_failure_sets_a_flag_true_then_false():
    run, _ = _run_fn()
    consts = set()
    for n in ast.walk(run):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == '_consolidated_ok':
                    consts.add(n.value.value)
    assert consts == {True, False}, (
        "expected _consolidated_ok initialised True and set False on a failed consolidated append — the "
        "flag that makes the drop visible is missing")


def test_the_success_mark_is_GATED_on_the_consolidated_append_succeeding():
    run, _ = _run_fn()
    # find an `if _consolidated_ok:` whose success branch marks ✓ and whose else marks a visible partial
    gated = []
    for node in ast.walk(run):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) \
                and node.test.id == '_consolidated_ok':
            body_src = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            else_src = ast.dump(ast.Module(body=node.orelse, type_ignores=[]))
            if '✓' in body_src and node.orelse and '⚠' in else_src:
                gated.append(node)
    assert gated, (
        "the '✓ success' mark must be gated on `if _consolidated_ok:` with a visible '⚠ partial' else — "
        "otherwise a dropped consolidated row still reports the image as a clean success")


def test_the_partial_status_names_the_dropped_rows():
    _, src = _run_fn()
    assert 'NOT added to the consolidated table' in src and 'consolidated_long.csv' in src, (
        "the partial status must say the rows are missing from the consolidated table, so the drop is "
        "actionable, not just a bare warning")
