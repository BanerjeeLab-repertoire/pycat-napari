"""**Loading a session REPLACES the workspace (clears first, guarded); clear signals cancel.**

Loading a session used to STACK onto the current workspace (`open_image_auto(clear_first=False)`), so old
layers/dataframes lingered and two sessions' identity references coexisted. Load is a document open, not an
overlay: it clears first — but never silently, because clearing discards possibly-unsaved work. These pin
the guard: `clear_all_without_saving` now returns True/False (cleared / user-cancelled) so the load can
abort on cancel, and the "Load Session" handler clears-before-loading (AST-verified — it is Qt-bound).
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core


def test_clear_returns_False_and_does_NOT_clear_when_the_user_cancels(monkeypatch):
    import pycat.file_io.session as sess
    import qtpy.QtWidgets as qtw
    cleared = []
    monkeypatch.setattr(sess, '_clear_everything', lambda v, cm: cleared.append(True))
    monkeypatch.setattr(qtw.QMessageBox, 'warning', lambda *a, **k: qtw.QMessageBox.No)
    assert sess.clear_all_without_saving(object(), object(), confirm=True) is False
    assert not cleared, "a cancelled confirm must NOT clear the workspace"


def test_clear_returns_True_and_clears_when_the_user_confirms(monkeypatch):
    import pycat.file_io.session as sess
    import qtpy.QtWidgets as qtw
    cleared = []
    monkeypatch.setattr(sess, '_clear_everything', lambda v, cm: cleared.append(True))
    monkeypatch.setattr(qtw.QMessageBox, 'warning', lambda *a, **k: qtw.QMessageBox.Yes)
    assert sess.clear_all_without_saving(object(), object(), confirm=True) is True
    assert cleared == [True]


def test_clear_with_confirm_False_clears_without_prompting(monkeypatch):
    import pycat.file_io.session as sess
    cleared = []
    monkeypatch.setattr(sess, '_clear_everything', lambda v, cm: cleared.append(True))
    assert sess.clear_all_without_saving(object(), object(), confirm=False) is True
    assert cleared == [True]


def test_EVERY_load_handler_CLEARS_before_loading_the_session():
    """AST: every function that calls `load_session` (the Qt-bound handlers) must first call
    `clear_all_without_saving` — otherwise a loaded session stacks onto the current workspace."""
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'ui'
           / 'menu_manager.py').read_text(encoding='utf-8')
    tree = ast.parse(src)

    # The guard may be invoked directly (clear_all_without_saving) or via the shared
    # clear_before_session_load helper — either satisfies "clears before loading".
    CLEARERS = ('clear_before_session_load', 'clear_all_without_saving')

    def _calls(fn):
        out = {}
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                nm = getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
                if nm in CLEARERS + ('load_session',):
                    out.setdefault(nm, c.lineno)     # first (earliest) call of each by source line
        return out

    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and 'load_session' in _calls(n)]
    assert handlers, "no load_session caller found — did the handler move?"
    for fn in handlers:
        c = _calls(fn)
        clears = {k: v for k, v in c.items() if k in CLEARERS}
        assert clears, f"{fn.name} calls load_session but never clears the workspace first"
        assert min(clears.values()) < c['load_session'], (
            f"{fn.name}: clear must happen BEFORE load_session, or the session stacks")
