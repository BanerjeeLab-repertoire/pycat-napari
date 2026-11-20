"""The GUI half of the plugin API: load the installed plugins and put what they registered where a user finds it.

Installed from ``CentralManager`` (not the line-capped menu god-file), like the Custom Methods submenu. It adds a
top-level **✦ Plugins** menu (one submenu per loaded plugin: its analysis methods, toolbox tools and actions, plus
'Installed plugins…', which lists every discovered plugin and why any did not load) and wires each analysis method
into the workflow checklist, session restore and the Home dock.

The headless half — discovery, validation, batch steps, operations — is :mod:`pycat.plugins`.
"""
from __future__ import annotations

_MENU_TITLE = '✦ Plugins'      # not 'Plugins': napari's own menu of that name is collapsed at startup


def _switch_attr(key: str) -> str:
    return '_switch_to_plugin__' + key.replace('.', '__')


def _class_name(ref) -> str:
    return ref.rpartition(':')[2].rpartition('.')[2] if isinstance(ref, str) else getattr(ref, '__name__', '')


def _run_safely(what: str, fn):
    """Run a plugin's GUI code; a failure is the plugin's, so it is reported and PyCAT carries on."""
    try:
        return fn()
    except Exception as exc:     # broad-ok: ui_cleanup — a plugin's panel/action raising must not take PyCAT down
        import traceback
        from pycat.utils.notify import show_warning
        traceback.print_exc()
        show_warning(f"Plugin {what} failed: {type(exc).__name__}: {exc}")
        return None


def open_analysis_method(cm, method):
    """Open a plugin's analysis method the way PyCAT opens its own (``AnalysisMethodsUI._switch_analysis``)."""
    from pycat.plugins.api import resolve_ref

    def _open():
        cm.analysis_methods_ui._switch_analysis(None, resolve_ref(method.ui_class))
    return _run_safely(f"'{method.label}'", _open)


def run_menu_action(cm, action):
    from pycat.plugins.api import resolve_ref

    def _run():
        fn = resolve_ref(action.callback)
        if action.kind == 'toolbox':
            return fn(cm.toolbox_functions_ui, layout=None, separate_widget=True)
        return fn(cm)
    return _run_safely(f"'{action.label}'", _run)


def wire_plugin_contributions(cm) -> None:
    """Everything except the menu: checklist pipelines, session restore, Home-dock cards. Qt-free."""
    from pycat.plugins import registry
    from pycat.plugins.api import resolve_ref
    from pycat.ui.pipeline_steps import PIPELINE_DEFS, PIPELINE_DISPLAY_NAMES

    for method in registry.analysis_methods():
        if method.pipeline:
            PIPELINE_DEFS.setdefault(method.key, list(method.pipeline))
            PIPELINE_DISPLAY_NAMES.setdefault(method.key, method.label)
        attr = _switch_attr(method.key)
        setattr(cm.analysis_methods_ui, attr, lambda m=method: open_analysis_method(cm, m))
        try:
            from pycat.ui.session_loader import _SESSION_METHOD_SWITCH
            _SESSION_METHOD_SWITCH.setdefault(_class_name(method.ui_class), attr)   # PyCAT's own names win
        except ImportError as exc:
            from pycat.utils.general_utils import debug_log
            debug_log('plugins: session restore map unavailable', exc)

    from pycat.utils.feature_registry import FeatureCard, register_feature, registry as feature_registry
    for card in registry.feature_cards():
        if card.key in feature_registry():      # re-installing (a second CentralManager) keeps the first
            continue
        entry = (None if card.entry is None else
                 (lambda c=card: _run_safely(f"'{c.title}'", lambda: resolve_ref(c.entry)(cm))))
        register_feature(FeatureCard(key=card.key, title=card.title, summary=card.summary,
                                     category=card.category, entry=entry, docs_anchor=card.docs_anchor))


def _installed_plugins_text() -> str:
    from pycat.plugins import registry
    from pycat.plugins.api import PLUGIN_API_VERSION
    from pycat.plugins.loader import ENTRY_POINT_GROUP, plugins_disabled
    lines = [f"PyCAT plugin API {PLUGIN_API_VERSION[0]}.{PLUGIN_API_VERSION[1]} "
             f"(entry-point group '{ENTRY_POINT_GROUP}')", '']
    if plugins_disabled():
        lines.append('Plugins are disabled (PYCAT_DISABLE_PLUGINS is set).')
    records = registry.plugins()
    if not records:
        lines.append('No plugins are installed. A plugin is a pip package that declares a '
                     f"'{ENTRY_POINT_GROUP}' entry point; see the PyCAT docs, Development > Plugins.")
    for r in records:
        lines.append(f"{r.name}  [{r.status}]  {r.distribution} {r.version}".rstrip())
        lines.append(f"    entry point: {r.target}")
        if r.error:
            lines.append(f"    reason: {r.error}")
        for kind, value in r.contributions:
            if kind == 'traceback':
                lines += ['    ' + ln for ln in value.rstrip().splitlines()]
            else:
                lines.append(f"    {kind}: {value}")
        lines.append('')
    return '\n'.join(lines)


def _show_installed_plugins(parent=None):
    from qtpy.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout
    dlg = QDialog(parent)
    dlg.setWindowTitle('Installed plugins')
    dlg.resize(720, 480)
    layout = QVBoxLayout(dlg)
    text = QPlainTextEdit(_installed_plugins_text())
    text.setReadOnly(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    dlg.exec_() if hasattr(dlg, 'exec_') else dlg.exec()


def install_plugins(cm):
    """Load the installed plugins and add the ✦ Plugins menu. Returns the menu (``None`` headless)."""
    from pycat.plugins import load_plugins, registry
    load_plugins()
    wire_plugin_contributions(cm)

    mm = getattr(cm, 'menu_manager', None)
    qt_window = getattr(getattr(cm.viewer, 'window', None), '_qt_window', None)
    if mm is None or qt_window is None:
        return None
    menu = qt_window.menuBar().addMenu(_MENU_TITLE)

    by_plugin: dict = {}
    for method in registry.analysis_methods():
        by_plugin.setdefault(method.plugin, {})[method.label] = (
            lambda *_, m=method: open_analysis_method(cm, m), {})
    for action in registry.menu_actions():
        by_plugin.setdefault(action.plugin, {})[action.label] = (
            lambda *_, a=action: run_menu_action(cm, a), {})
    for name, actions in by_plugin.items():
        record = next((r for r in registry.plugins() if r.name == name), None)
        submenu = menu.addMenu(record.distribution or name if record else name)
        mm._add_actions_to_menu(actions, submenu)    # also registers them in the command palette
    if by_plugin:
        menu.addSeparator()
    menu.addAction('Installed plugins…').triggered.connect(lambda *_: _show_installed_plugins(qt_window))
    return menu
