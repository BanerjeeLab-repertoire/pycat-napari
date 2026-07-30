"""Custom Methods submenu (Method-Widget Spec 2).

Kept OUT of ``menu_manager`` (a complexity-ratchet concentration point) so adding this feature does not grow that
god-file. The submenu lists the user's saved guided methods (``navigator.templates``) and, on selection, rebuilds
each into a generated method panel against the CURRENT data — the saved ANSWERS are recompiled and every quality
gate re-evaluates (verdicts were never stored). It is the analysis tree's first DYNAMICALLY populated submenu:
repopulated on ``aboutToShow``, so saving or deleting a method is reflected without a restart.

GUI-bound (it wires Qt menu actions); the rebuild logic it calls — ``plan_from_saved_method`` — is tested
headlessly in ``tests/navigator/test_navigator_templates.py``.
"""
from __future__ import annotations


def install_custom_methods_submenu(mm):
    """Attach a dynamic 'Custom Methods' submenu to ``mm.analysis_methods_menu``. ``mm`` is the MenuManager,
    used for its ``analysis_methods_menu``, ``make_lambda``, and ``central_manager``."""
    submenu = mm.analysis_methods_menu.addMenu('Custom Methods')

    def _open(name):
        from pycat.navigator.templates import load_template
        from pycat.navigator.session import plan_from_saved_method
        from pycat.ui.navigator_dock import build_method_panel_via_central_manager
        tmpl = load_template(name)
        if tmpl is None:
            return
        # Recompile the saved answers against live data, then dock the generated panel. The panel carries its own
        # pixel gate + per-section status, so opening one on unsuitable data shows the problem rather than a wrong
        # number.
        plan = plan_from_saved_method(tmpl, mm.central_manager)
        build_method_panel_via_central_manager(mm.central_manager, plan,
                                               review=dict(tmpl.parameters or {}), intent=plan.intent)

    def _populate():
        submenu.clear()
        try:
            from pycat.navigator.templates import list_templates
            methods = list_templates()
        except Exception:  # broad-ok: ui_cleanup — a bad settings store must not break the menu bar
            methods = []
        if not methods:
            empty = submenu.addAction('(no saved methods yet — build one in the Navigator, then "Save as template")')
            empty.setEnabled(False)
            return
        for tmpl in methods:
            submenu.addAction(tmpl.name).triggered.connect(mm.make_lambda(_open, {'name': tmpl.name}))

    submenu.aboutToShow.connect(_populate)
    return submenu
