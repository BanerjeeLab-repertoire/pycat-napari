"""**The local-cache cleanup no longer gates startup: it is offered non-blockingly and reachable on demand.**

The cleanup dialog used to appear modally over the still-assembling window at launch. Now the startup path only
posts a non-blocking notification (`offer_cache_cleanup`), and the grouped keep/clear dialog is opened on
demand (`open_cache_manager`, wired to a File-menu action). These pin the contract: the startup offer never
opens the dialog (no `exec_` on the launch path); an empty cache is silent (first-run sees nothing); the
on-demand manager still shows the SAME dialog and applies the choice through the unchanged `_apply`; and the
launch scheduling calls the offer AFTER the window is built, guarded so a failure cannot crash launch.

NOTE: `local_cache` is imported INSIDE each test (the `lc` fixture), never at module scope — the headless core
collector (`conftest.pytest_ignore_collect`) skips any module that imports `pycat.file_io` at module scope, and
`local_cache` is headless-safe, so a lazy import keeps these tests actually collectable in the core lane.
"""
import pytest


@pytest.fixture
def lc():
    from pycat.file_io import local_cache
    return local_cache


_ITEMS = [
    {'path': '/c/a.tif', 'basename': 'a.tif', 'source': '/src/a.tif', 'size_bytes': 1_000_000},
    {'path': '/c/b.tif', 'basename': 'b.tif', 'source': '/src/b.tif', 'size_bytes': 1_000_000},
]


def _no_dialog(*_a, **_k):
    raise AssertionError("the dialog (exec_) must not be reached on this path")


# ── the startup offer is non-blocking (napari notification → integration) ────────────────────────────

@pytest.mark.integration
def test_the_startup_offer_notifies_and_never_opens_the_dialog(lc, monkeypatch):
    monkeypatch.setattr(lc, '_scan_cache', lambda: list(_ITEMS))
    monkeypatch.setattr(lc, '_is_protected', lambda *_a, **_k: False)
    monkeypatch.setattr(lc, '_show_dialog', _no_dialog)             # would raise if the offer opened it
    shown = []
    import napari.utils.notifications as _notif
    monkeypatch.setattr(_notif, 'show_info', lambda msg: shown.append(msg))

    lc.offer_cache_cleanup(viewer=None)                            # must return without blocking

    assert len(shown) == 1
    assert 'cached copies' in shown[0] and 'Manage local cache' in shown[0]
    assert '2' in shown[0]                                          # count of cached files reported


# ── empty-cache silence + never-crash (napari-free → core) ───────────────────────────────────────────

@pytest.mark.core
def test_an_empty_cache_is_silent_no_summary_no_offer(lc, monkeypatch):
    monkeypatch.setattr(lc, '_scan_cache', lambda: [])
    monkeypatch.setattr(lc, '_show_dialog', _no_dialog)
    assert lc.cached_summary() is None                             # nothing to report
    lc.offer_cache_cleanup(viewer=None)                            # returns before touching notifications


@pytest.mark.core
def test_the_offer_never_raises_even_if_scanning_blows_up(lc, monkeypatch):
    def _boom():
        raise RuntimeError("disk gone")
    monkeypatch.setattr(lc, '_scan_cache', _boom)
    lc.offer_cache_cleanup(viewer=None)                            # swallowed — launch must not crash


# ── the on-demand manager still shows the real dialog and applies the choice (napari-free → core) ─────

@pytest.mark.core
def test_open_cache_manager_shows_the_dialog_and_applies_the_choice(lc, monkeypatch):
    monkeypatch.setattr(lc, '_scan_cache', lambda: list(_ITEMS))
    monkeypatch.setattr(lc, '_is_protected', lambda *_a, **_k: False)
    seen = {}
    monkeypatch.setattr(lc, '_show_dialog',
                        lambda items, prot, now, total: (seen.update(items=items, total=total), [items[0]])[1])
    applied = {}
    monkeypatch.setattr(lc, '_apply', lambda items, chosen: applied.update(chosen=chosen))

    lc.open_cache_manager()
    assert seen['total'] == 2_000_000 and len(seen['items']) == 2  # same grouped list built as before
    assert applied['chosen'] == [_ITEMS[0]]                        # the user's choice flows to _apply unchanged


@pytest.mark.core
def test_open_cache_manager_on_empty_cache_does_nothing(lc, monkeypatch):
    monkeypatch.setattr(lc, '_scan_cache', lambda: [])
    monkeypatch.setattr(lc, '_show_dialog', _no_dialog)
    lc.open_cache_manager()                                        # no dialog, no crash


@pytest.mark.core
def test_a_cancelled_dialog_clears_nothing(lc, monkeypatch):
    monkeypatch.setattr(lc, '_scan_cache', lambda: list(_ITEMS))
    monkeypatch.setattr(lc, '_is_protected', lambda *_a, **_k: False)
    monkeypatch.setattr(lc, '_show_dialog', lambda *a: None)       # cancelled
    monkeypatch.setattr(lc, '_apply', _no_dialog)                 # _apply must not run
    lc.open_cache_manager()                                       # nothing deleted


# ── launch wiring: the offer is deferred to the end and never called immediately (core) ───────────────

@pytest.mark.core
def test_run_pycat_defers_the_offer_and_no_longer_clears_at_startup(lc):
    import ast
    import pathlib
    src = pathlib.Path(lc.__file__).resolve().parents[1] / 'run_pycat.py'
    tree = ast.parse(src.read_text(encoding='utf-8'))
    names = {n.attr if isinstance(n, ast.Attribute) else n.id
             for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    # the blocking startup clear is gone; the non-blocking offer is what runs now
    assert 'clear_cache_on_startup' not in names
    assert 'offer_cache_cleanup' in names
    # and it is scheduled via a timer (deferred), not called inline at viewer construction
    assert 'singleShot' in names
