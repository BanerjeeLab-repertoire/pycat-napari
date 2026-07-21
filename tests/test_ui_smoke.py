"""
Headless UI smoke tests.

These construct the real UI classes with an offscreen Qt platform (no display),
to catch failures a static parse can't: broken mixin composition / MRO errors,
missing ``self`` attributes referenced at construction, and import cycles. They
are the runtime companion to the static checks in ``test_ui_structure.py``.

Requires PyQt5 + napari; skipped automatically where those aren't installed
(e.g. minimal CI). Set QT_QPA_PLATFORM=offscreen is done here so no display is
needed.

Run: pytest tests/test_ui_smoke.py -v
"""

import os

import pytest

# Force offscreen Qt BEFORE any Qt import so this needs no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Skip the whole module cleanly if the GUI stack isn't present.
pytest.importorskip("PyQt5", reason="PyQt5 not installed")
napari = pytest.importorskip("napari", reason="napari not installed")


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def viewer(qapp):
    v = napari.Viewer(show=False)
    yield v
    try:
        v.close()
    except Exception:
        pass


def test_central_manager_constructs(viewer):
    """The CentralManager (which wires up the UI classes) must construct against
    a real viewer without raising. Catches import cycles and construction-time
    attribute errors that a static parse misses."""
    from pycat.central_manager import CentralManager
    cm = CentralManager(viewer)
    assert cm is not None


def test_toolbox_ui_present_and_has_core_methods(viewer):
    """After construction, the toolbox UI must exist and expose its core
    widget-builder methods. If a refactor drops one, this fails here rather than
    when a user opens that menu."""
    from pycat.central_manager import CentralManager
    cm = CentralManager(viewer)
    tui = getattr(cm, "toolbox_functions_ui", None)
    assert tui is not None, "central_manager.toolbox_functions_ui missing"
    # A representative sample of methods that menus/workflows depend on.
    for method in ("_add_pre_process", "_add_measure_line",
                   "_add_run_cellpose_segmentation",
                   "_add_run_segment_subcellular_objects",
                   "_add_chromatin_topology"):
        assert callable(getattr(tui, method, None)), (
            f"toolbox UI is missing expected method {method!r}")


def test_menu_manager_constructs(viewer):
    """The MenuManager builds the toolbox menus and registers every
    _add_* action; constructing it exercises those registrations. A dangling
    registration (method removed by a refactor) surfaces here."""
    from pycat.central_manager import CentralManager
    cm = CentralManager(viewer)
    # MenuManager is created by CentralManager; if it isn't exposed, at least
    # confirm the class imports and can be built against the manager.
    from pycat.ui.ui_modules import MenuManager
    mm = getattr(cm, "menu_manager", None)
    if mm is None:
        # Not stored on the manager in this version — construct directly.
        # MenuManager(viewer, central_manager).
        mm = MenuManager(viewer, cm)
    assert mm is not None


def _menu_manager(viewer):
    from pycat.central_manager import CentralManager
    from pycat.ui.ui_modules import MenuManager
    cm = CentralManager(viewer)
    return getattr(cm, "menu_manager", None) or MenuManager(viewer, cm)


def test_menu_guarded_installs_actually_RAN(viewer):
    """**The 1.5.509 bug class, guarded at runtime.** Several installs in `_setup_menu_bar` are wrapped
    in `try/except`, so a broken one no-ops SILENTLY. Assert each produced its result attribute — i.e.
    the guard ran to completion — rather than merely 'construction didn't raise'."""
    mm = _menu_manager(viewer)
    # The branded marker always resolves (it falls back to a disabled QAction inside its own guard).
    assert getattr(mm, "_pycat_marker_action", None) is not None
    # The command palette action + its registry (populated by every _add_actions_to_menu call).
    assert getattr(mm, "palette_action", None) is not None
    reg = getattr(mm, "_command_registry", None)
    assert reg and len(reg) >= 90, "the command registry is empty/short — actions were not wired"
    # The layer-event backstops installed (attributes set inside their guards).
    assert hasattr(mm, "_pycat_reroute_guard")
    assert callable(getattr(mm, "_autotag_user_layer", None))


def test_every_snapshot_label_became_a_registered_action(viewer):
    """Cross-check the static contract against reality: every action label the AST snapshot extracted
    must appear as a REAL registered action in `_command_registry` (which _add_actions_to_menu keys by
    label). Proves the headless snapshot is faithful AND that no action was dropped at runtime."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from test_menu_contract import extract_menu_contract
    labels = {lbl for entry in extract_menu_contract() for lbl in entry["actions"]}

    mm = _menu_manager(viewer)
    registered = set(getattr(mm, "_command_registry", {}) or {})
    missing = labels - registered
    assert not missing, (
        f"these menu labels are in the snapshot but were NOT registered at runtime: {sorted(missing)} "
        "— the static extractor and the real menu wiring have diverged (or an action silently failed).")


def test_scene_switcher_entry_point_does_not_raise(viewer):
    """The fully-guarded entry point must be safe to invoke (it swallows its own errors); a smoke that it
    is at least callable and does not blow up the menu on a bare viewer."""
    mm = _menu_manager(viewer)
    mm._open_scene_switcher()          # end-to-end try/except inside — must never raise
