"""Native-menu manipulation — extracted from MenuManager (ui_decomposition Part 2).

Hides/reorders napari's built-in menus and disables its file-open actions (PyCAT owns loading). The
MenuManager methods are thin wrappers that call these; the menu structure is unchanged.
"""
from __future__ import annotations

from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame
from PyQt5.QtCore import Qt, QObject



def _hide_napari_native_menus(self, hidden_default=True):
    """Collapse napari's own top-level menus (File / View / Plugins / Window /
    Help / Layers) behind a single toggle, hidden by default.

    The PyCAT workflow never needs napari's native menus, and several test
    users lost their session by loading data through napari's File -> Open
    (which bypasses PyCAT's channel-assignment / metadata pipeline and crashes
    the workflow). So on startup only PyCAT's own controls are visible, with
    napari's menus tucked away -- but NOT removed: a leftmost toggle reveals /
    hides them on demand, because some of napari's layer operations are
    genuinely useful. napari's Open* actions stay disabled regardless, so even
    when the menus are revealed, data still loads through PyCAT.

    Fully defensive: identifies napari-native menus by title, never touches
    PyCAT's own menus, and never raises if napari changes its menu layout.
    """
    try:
        menubar = self.viewer.window._qt_window.menuBar()
    except Exception:
        return

    def _norm(t):
        return (t or '').replace('&', '').strip().lower()

    # PyCAT's own top-level titles -- never collapse these.
    pycat_titles = {
        _norm('\u25c6 PyCAT \u25b8'), _norm('Analysis Methods'), _norm('Toolbox'),
        _norm('\u2605 Open/Save File(s)'), _norm('\u2620 Clear'), _norm('\u2302 Home'),
        _norm('\u24d8 Metadata'), _norm('\u2630 Recorded Steps'),
        _norm('\u2630 napari'), _norm('\u2630 napari \u25be'),
    }
    # napari-native top-level menus to collapse.
    napari_titles = {'file', 'view', 'plugins', 'window', 'help', 'layers',
                     'acquisition'}

    # Collect the napari-native menu actions currently on the bar.
    self._napari_menu_actions = []
    try:
        for action in menubar.actions():
            menu = action.menu()
            if menu is None:
                continue
            title = _norm(action.text() or menu.title())
            if title in pycat_titles:
                continue
            if title in napari_titles:
                self._napari_menu_actions.append(action)
    except Exception:
        self._napari_menu_actions = []

    # Make PyCAT's Open/Save the first PyCAT menu (workflow entry point).
    self._reorder_pycat_menu_bar()

    # Build the leftmost toggle that shows / hides the napari menus.
    try:
        from PyQt5.QtGui import QFont as _QFont
        self._napari_toggle_action = QAction('\u2630 napari',
                                             self.viewer.window._qt_window)
        self._napari_toggle_action.setToolTip(
            'Show / hide napari\u2019s own menus (File, View, Layers, Window, '
            'Help). Hidden by default \u2014 the PyCAT workflow doesn\u2019t need '
            'them, but napari\u2019s layer operations are available here if you '
            'want them. (napari\u2019s Open stays disabled; load data via '
            '\u2605 Open/Save File(s).)')
        _tf = _QFont()
        _tf.setPointSize(max(1, _tf.pointSize() - 1))
        self._napari_toggle_action.setFont(_tf)
        self._napari_menus_visible = not hidden_default
        self._napari_toggle_action.triggered.connect(self._toggle_napari_menus)
        # Insert the toggle as the LEFTMOST item so that, with napari's menus
        # hidden, the bar reads: [napari] PyCAT | Open/Save | Analysis | ...
        _all = menubar.actions()
        if _all:
            menubar.insertAction(_all[0], self._napari_toggle_action)
        else:
            menubar.addAction(self._napari_toggle_action)
    except Exception:
        self._napari_toggle_action = None

    # Apply the default visibility (hidden).
    self._set_napari_menus_visible(not hidden_default)

    # Belt-and-suspenders: napari's Open* actions stay disabled even when the
    # menus are revealed, so data always loads through PyCAT.
    try:
        self._disable_napari_open_actions()
    except Exception:
        pass


def _disable_napari_open_actions(self):
    """Hard-disable every napari action that loads data, so a file can never
    enter the viewer through napari's own reader (which bypasses PyCAT's
    channel-assignment / data-repository pipeline and breaks downstream
    analysis). Loading must always go through PyCAT's ★ Open/Save File(s).

    Matching is primarily by the action's stable ``objectName`` (napari 0.7
    gives every action one, e.g. ``napari.window.file.open_files_dialog``),
    which is far more robust than display text (accelerators, '...' suffixes,
    version renames). A small text fallback covers older napari.

    napari builds some menus lazily (actions only exist once the menu is
    shown), so this is ALSO wired to each file-menu's ``aboutToShow`` to
    re-disable every time the menu opens — a one-shot startup pass alone
    misses lazily-created actions and anything napari re-enables.
    """
    try:
        window = self.viewer.window._qt_window
    except Exception:
        return

    # Stable objectName prefixes / exact ids for data-LOADING actions.
    # Anything whose objectName starts with one of these, OR is a sample
    # loader (napari.<sample> under the Open Sample menu), is disabled.
    _load_object_prefixes = (
        'napari.window.file.open_files_dialog',
        'napari.window.file.open_files_as_stack_dialog',
        'napari.window.file.open_folder_dialog',
        'napari.window.file._open_files_with_plugin',
        'napari.window.file._open_files_as_stack_with_plugin',
        'napari.window.file._open_folder_with_plugin',
        'napari.window.file._image_from_clipboard',
    )
    # Text fallback for older napari that may lack objectNames.
    _load_texts = {'open', 'open file...', 'open files...', 'open file(s)...',
                   'open folder...', 'open sample', 'open files as stack...',
                   'new image from clipboard'}

    _tip = ('Loading through napari is disabled \u2014 use PyCAT\u2019s '
            '\u2605 Open/Save File(s) menu so data enters PyCAT\u2019s '
            'pipeline (channel assignment + registration). napari\u2019s own '
            'reader would bypass this and break analysis.')

    def _is_load_action(act):
        on = act.objectName() or ''
        if any(on.startswith(p) for p in _load_object_prefixes):
            return True
        # Open Sample entries: objectName is 'napari.<sample>' and they live
        # under the Open Sample menu; disable all sample loaders.
        if on.startswith('napari.') and self._obj_is_sample_loader(on):
            return True
        txt = (act.text() or '').replace('&', '').strip().lower()
        return txt in _load_texts

    def _disable_in_menu(menu, depth=0):
        """Recursively disable+hide load actions within a QMenu tree. Walking
        the menu tree (rather than window.findChildren) is essential on napari
        0.7.1, where menu actions are provided by the app-model and may not be
        children of the QMainWindow — so findChildren misses them, but the
        menu that renders them always contains them."""
        if menu is None or depth > 4:
            return
        try:
            for act in menu.actions():
                sub = act.menu()
                if sub is not None:
                    _disable_in_menu(sub, depth + 1)
                    # Hide the submenu CONTAINER itself if, after processing,
                    # it has no usable content left: either every real action
                    # is now hidden (e.g. "Open with Plugin" — all its entries
                    # are load actions we hid) or it holds only napari's
                    # disabled "empty_dummy" placeholders ("IO Utilities",
                    # "Acquire"). Leaves genuinely-useful submenus alone.
                    try:
                        subacts = [a for a in sub.actions()
                                   if not a.isSeparator()]
                        def _dead(a):
                            on = a.objectName() or ''
                            return ((not a.isVisible()) or (not a.isEnabled())
                                    or on.endswith('empty_dummy'))
                        if subacts and all(_dead(a) for a in subacts):
                            act.setVisible(False)
                    except Exception:
                        pass
                    continue
                try:
                    if _is_load_action(act):
                        act.setEnabled(False)
                        act.setToolTip(_tip)
                        # Hiding removes it from the menu entirely — a hidden
                        # action can't be triggered even if napari re-enables
                        # it, and makes the lockdown visually obvious.
                        act.setVisible(False)
                except Exception:
                    continue
        except Exception:
            pass

    def _sweep():
        # Primary: walk the menu-bar tree (reaches app-model actions).
        try:
            menubar = window.menuBar()
            for action in menubar.actions():
                _disable_in_menu(action.menu())
        except Exception:
            pass
        # Secondary: also sweep any QActions parented under the window
        # (older napari where actions ARE window children).
        try:
            from PyQt5.QtGui import QAction as _QA
        except Exception:
            from PyQt5.QtWidgets import QAction as _QA
        try:
            for act in window.findChildren(_QA):
                try:
                    if _is_load_action(act):
                        act.setEnabled(False)
                        act.setToolTip(_tip)
                        act.setVisible(False)
                except Exception:
                    continue
        except Exception:
            pass

    # Initial sweep.
    _sweep()

    # Re-sweep whenever any top-level menu (or its submenus) is about to show
    # — covers lazily built/re-created actions. napari 0.7.1 may REBUILD menu
    # actions each time the menu opens, so a one-shot disable of the original
    # QAction objects is undone; re-running at aboutToShow catches the fresh
    # actions right before they're displayed. Connect once per menu.
    if not getattr(self, '_napari_load_guard_wired', False):
        try:
            menubar = window.menuBar()
            for action in menubar.actions():
                menu = action.menu()
                if menu is not None:
                    menu.aboutToShow.connect(_sweep)
                    for sub in menu.actions():
                        smenu = sub.menu()
                        if smenu is not None:
                            smenu.aboutToShow.connect(_sweep)
            self._napari_load_guard_wired = True
        except Exception:
            pass
