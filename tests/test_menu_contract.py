"""**The menu contract — a net that fails when a menu action is dropped, renamed, or moved.**

`ui_modules.py` is the file the codebase deliberately left un-split, because *"a refactor whose only
verification is 'it still imports' is a refactor that ships bugs"* (`test_complexity_budget.py`'s own
warning). This is the verification that has to exist BEFORE any of it moves: a snapshot of the whole
menu tree — every top-level menu and submenu title, and every action label under it, in order.

It is a pure AST test (no `pycat.ui` import — conftest drops such modules headlessly, and constructing
`MenuManager` needs a real Qt menu bar + a fully-wired CentralManager anyway; the runtime side is the
Qt-smoke test in `test_ui_smoke.py`). A blind refactor that silently drops or reorders an action changes
the extracted contract and fails here — which is exactly the regression that is hardest to catch by hand.

**If you changed the menus on purpose,** regenerate the reference:
    python tests/test_menu_contract.py --regenerate
and commit `tests/menu_contract_snapshot.json` — the diff is the review of what you changed.
"""
import ast
import json
import pathlib

import pytest

pytestmark = pytest.mark.core

_UI = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'ui' / 'ui_modules.py'
_SNAPSHOT = pathlib.Path(__file__).resolve().parent / 'menu_contract_snapshot.json'

#: The MenuManager methods that build the menu bar. The contract lives entirely in these.
_BUILDERS = ('_setup_menu_bar', '_add_analysis_methods_to_menu',
             '_add_toolbox_to_menu', '_add_file_io_methods_to_menu')


def _var_key(target):
    """A stable key for a menu/dict variable: ``self.x`` for an attribute, the bare name otherwise."""
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
        return 'self.' + target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _is_add_menu(value):
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == 'addMenu' and value.args
            and isinstance(value.args[0], ast.Constant))


def extract_menu_contract():
    """The menu tree as a list of ``{'menu': title, 'dict': var, 'actions': [labels…]}`` — one entry per
    ``_add_actions_to_menu(<dict>, <menu>)`` call, in source order. Resolved purely from the AST:
    ``<menu>`` → its ``addMenu('title')`` title, ``<dict>`` → its literal string keys (the action labels)."""
    tree = ast.parse(_UI.read_text(encoding='utf-8'))
    mm = next(n for n in ast.walk(tree)
              if isinstance(n, ast.ClassDef) and n.name == 'MenuManager')

    titles = {}          # menu var-key -> title  (top-level menus + submenus)
    for node in ast.walk(mm):
        if isinstance(node, ast.Assign) and _is_add_menu(node.value):
            k = _var_key(node.targets[0])
            if k:
                titles[k] = node.value.args[0].value

    dicts = {}           # dict var-key -> [labels]
    calls = []           # (dict var-key, menu var-key) in source order
    for method in mm.body:
        if isinstance(method, ast.FunctionDef) and method.name in _BUILDERS:
            for node in ast.walk(method):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                    k = _var_key(node.targets[0])
                    labels = [kk.value for kk in node.value.keys
                              if isinstance(kk, ast.Constant) and isinstance(kk.value, str)]
                    if k and labels:
                        dicts[k] = labels
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == '_add_actions_to_menu' and len(node.args) == 2):
                    calls.append((_var_key(node.args[0]), _var_key(node.args[1])))

    return [{'menu': titles.get(mv, mv), 'dict': dv, 'actions': dicts.get(dv, [])}
            for dv, mv in calls]


def test_the_menu_tree_matches_the_committed_contract():
    """Every menu title and action label, snapshotted. A dropped / renamed / reordered / moved action
    changes the extracted contract and fails here — the single highest-value guard before any move."""
    current = extract_menu_contract()
    committed = json.loads(_SNAPSHOT.read_text(encoding='utf-8'))
    assert current == committed, (
        "the menu tree no longer matches tests/menu_contract_snapshot.json — an action was dropped, "
        "renamed, reordered, or moved to a different menu. If that was INTENTIONAL, regenerate the "
        "reference:\n    python tests/test_menu_contract.py --regenerate\nand commit the JSON (its diff "
        "is the review). If it was NOT intentional, a refactor broke the menu wiring.")


def test_the_contract_is_non_trivial():
    """Guard the guard: if the extractor silently returns nothing (e.g. a builder was renamed), the
    snapshot test would pass vacuously against an empty commit. Pin a floor."""
    contract = extract_menu_contract()
    n_actions = sum(len(e['actions']) for e in contract)
    assert len(contract) >= 20 and n_actions >= 90, (
        f"extracted only {len(contract)} menus / {n_actions} actions — the extractor likely stopped "
        "matching the builders (a method was renamed?). Fix the extractor, don't regenerate an empty snapshot.")


def test_setup_menu_bar_still_installs_its_guarded_setup():
    """The 1.5.509 bug class: a guarded install (`try: … except: pass`) whose result silently vanishes.
    The RUNTIME proof (the attribute is actually set) is the Qt-smoke test; here we statically pin that
    `_setup_menu_bar` still ASSIGNS each guard's result attribute, so deleting one is caught headlessly."""
    tree = ast.parse(_UI.read_text(encoding='utf-8'))
    setup = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == '_setup_menu_bar')
    assigned = {t.attr for node in ast.walk(setup) if isinstance(node, ast.Assign)
                for t in node.targets
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == 'self'}
    required = {'_pycat_marker_action', 'palette_action', '_pycat_drop_filter',
                '_pycat_reroute_guard', '_autotag_user_layer'}
    missing = required - assigned
    assert not missing, (
        f"_setup_menu_bar no longer assigns these guarded-install results: {sorted(missing)}. Each is set "
        "inside a try/except; losing the assignment is exactly the silent-no-op bug this file has seen before.")


if __name__ == '__main__':
    import sys
    if '--regenerate' in sys.argv:
        _SNAPSHOT.write_text(json.dumps(extract_menu_contract(), indent=1, ensure_ascii=False) + '\n',
                             encoding='utf-8')
        print(f"Regenerated {_SNAPSHOT} from {_UI.name}.")
    else:
        print("Run with --regenerate to rewrite the committed menu snapshot.")
