"""``MenuManager`` — the PyCAT menu bar, extracted from ui_modules.py (decomposition Phase 2, 1.6.149).

Moved VERBATIM (the menu-contract snapshot test, `tests/test_menu_contract.py`, guards that not one
action changed). The two session-restore method maps and the file-drop event filter it owns came with it.
`ui_modules.py` re-exports `MenuManager`, so `from pycat.ui.ui_modules import MenuManager` (CentralManager,
the smoke tests) keeps working. This module imports nothing from `ui_modules`, so there is no cycle.
"""
from __future__ import annotations

import math
import napari 
from pycat.utils.general_utils import debug_log
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, 
    QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction,
    QTabWidget, QToolButton, QFrame)
from PyQt5.QtCore import Qt, QObject
from pycat.toolbox.invitro_fluor_ui import InVitroFluorUI
from pycat.toolbox.timeseries_invitro_fluor_ui import TimeSeriesInVitroFluorUI
from pycat.toolbox.vpt_ui import VideoParticleTrackingUI
from pycat.toolbox.frap_ui import FRAPUI
from pycat.toolbox.fusion_ui import DropletFusionUI
from pycat.toolbox.temperature_ui import TemperatureDependentUI
from pycat.toolbox.fd_curve_ui import FDCurveUI
from pycat.toolbox.invitro_bf_ui import InVitroBFUI
from pycat.toolbox.zstack_segmentation_ui import ZStackSegmentationUI


class _FileDropFilter(QObject):
    """
    Application-level event filter that routes files dropped anywhere on the
    napari window into PyCAT's own openers (channel assignment + data-repository
    registration), instead of napari's default reader which bypasses the PyCAT
    pipeline. Also accepts the drag-enter so the drop actually fires.

    Text/number input widgets are left alone so path drops into fields still work.
    """
    def __init__(self, file_io):
        super().__init__()
        self._file_io = file_io

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        from PyQt5.QtWidgets import QLineEdit, QTextEdit, QAbstractSpinBox
        et = event.type()
        if et not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if isinstance(obj, (QLineEdit, QTextEdit, QAbstractSpinBox)):
            return False   # let input fields handle their own drops
        md = event.mimeData() if hasattr(event, 'mimeData') else None
        if md is None or not md.hasUrls():
            return False
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        paths = [p for p in paths if p]
        if not paths:
            return False
        if et in (QEvent.DragEnter, QEvent.DragMove):
            event.acceptProposedAction()
            return True
        # Drop
        event.acceptProposedAction()
        self._route(paths)
        return True

    def _route(self, paths):
        """Route dropped files.

        Type: a dropped file loads as an IMAGE unless it carries a PyCAT
        signifier saying it is a mask (then it loads as a Labels layer). There is
        NO image-vs-mask prompt — PyCAT isn't intended to ingest foreign masks, so
        an unsignified file is treated as an image. Structure is still
        auto-detected (IMS/TIFF/CZI stacks load lazily; 2D through the channel
        pipeline).

        Session: if an image layer is already loaded, the user is asked ONCE
        whether to CLEAR the current session and load the dropped file(s), or ADD
        them to it — this applies to the whole dropped batch. If nothing is loaded
        yet, the file(s) load with no prompt.
        """
        import os
        paths = [p for p in (paths or []) if p and os.path.exists(p)]
        if not paths:
            return

        # Decide clear-vs-add once, up front, based on whether an image is present.
        clear_session = False
        if self._image_layer_present():
            choice = self._ask_clear_or_add(len(paths))
            if choice == 'cancel':
                return
            clear_session = (choice == 'clear')

        try:
            for _i, p in enumerate(paths):
                # Only the FIRST file clears (when the user chose clear); the rest
                # always add so the whole batch ends up loaded together.
                first = (_i == 0)
                self._route_one(p, clear_first=(clear_session and first))
        except Exception as e:
            try:
                from napari.utils.notifications import show_warning
                show_warning(f"PyCAT could not open dropped file(s): {e}")
            except Exception:
                print(f"[PyCAT] Drop-open error: {e}")

    def _route_one(self, file_path, clear_first):
        """Load a single dropped file: mask only if PyCAT-signified, else image
        (structure auto-detected). No image-vs-mask prompt."""
        fio = self._file_io
        try:
            sig = fio._read_pycat_signifier(file_path)
        except Exception:
            sig = None
        if sig == 'mask':
            fio.open_2d_mask(file_paths=[file_path], clear_first=clear_first)
        else:
            fio._open_image_auto_single(file_path, clear_first=clear_first)

    def _image_layer_present(self):
        """True if the viewer currently holds at least one Image layer."""
        try:
            viewer = getattr(self._file_io, 'viewer', None)
            if viewer is None:
                return False
            for lyr in viewer.layers:
                if lyr.__class__.__name__ == 'Image':
                    return True
        except Exception:
            pass
        return False

    def _ask_clear_or_add(self, n_files):
        """Ask whether to clear the current session or add the dropped file(s).
        Returns 'clear', 'add', or 'cancel'. Defaults to 'add' if the dialog
        can't be shown (safer — never discards the user's current work silently)."""
        try:
            from qtpy.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setWindowTitle("Clear or add?")
            what = "this file" if n_files == 1 else f"these {n_files} files"
            box.setText(
                f"An image is already loaded.\n\nClear the current session and "
                f"load {what}, or add to what's already open?")
            clear_btn = box.addButton("Clear && load", QMessageBox.DestructiveRole)
            add_btn = box.addButton("Add", QMessageBox.AcceptRole)
            cancel_btn = box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(add_btn)
            box.exec_()
            clicked = box.clickedButton()
            if clicked is clear_btn:
                return 'clear'
            if clicked is cancel_btn:
                return 'cancel'
            return 'add'
        except Exception:
            return 'add'


_SESSION_METHOD_SWITCH = {
    'VideoParticleTrackingUI': '_switch_to_vpt_analysis',
    'CondensateAnalysisUI': '_switch_to_condensate_analysis',
    'InVitroFluorUI': '_switch_to_invitro_fluor_analysis',
    'TimeSeriesInVitroFluorUI': '_switch_to_ts_invitro_fluor_analysis',
    'FRAPUI': '_switch_to_frap_analysis',
    'DropletFusionUI': '_switch_to_fusion_analysis',
    'TemperatureDependentUI': '_switch_to_temperature_analysis',
    'FDCurveUI': '_switch_to_fd_curve_analysis',
    'InVitroBFUI': '_switch_to_invitro_bf_analysis',
    'ZStackSegmentationUI': '_switch_to_zstack_analysis',
}


_SESSION_METHOD_BY_DATA = {
    'vpt_tracks': 'VideoParticleTrackingUI',
}


class MenuManager:
    """
    Manages the setup and addition of menu items to a napari viewer instance. This class
    integrates a variety of analysis, file I/O, and toolbox functions into the viewer's
    menu bar, allowing for easy access to different functionalities within the application.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari Viewer instance to which the menus will be added.
    central_manager : CentralManager
        An instance of a custom class managing central functionalities, including
        file I/O operations, analysis methods, and toolbox functions.

    Methods
    -------
    _setup_menu_bar():
        Sets up the main menu bar with specific menu items and their associated actions.
    make_lambda(action_method, kwargs):
        Creates a lambda function for triggering actions with arguments.
    _add_actions_to_menu(actions_dict, menu):
        Adds actions to a given menu based on a dictionary of action names and methods.
    _add_file_io_methods_to_menu():
        Adds file I/O methods as menu items under the file menu.
    _add_analysis_methods_to_menu():
        Adds analysis methods as menu items under the analysis methods menu.
    _add_toolbox_to_menu():
        Adds toolbox functions as menu items under the toolbox menu.
    """

    def __init__(self, viewer, central_manager):
        """
        Initializes the MenuManager with a viewer and a central_manager instance,
        and sets up the menu bar.

        Parameters
        ----------
        viewer : Viewer
            The napari Viewer instance to which the menus will be added.
        central_manager : CentralManager
            An instance managing central functionalities, like file I/O and analysis methods.
        """

        self.viewer = viewer
        self.central_manager = central_manager
        self._setup_menu_bar()

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

    def _set_napari_menus_visible(self, visible):
        """Show or hide the collected napari-native menu actions and update the
        toggle label to reflect state."""
        for action in getattr(self, '_napari_menu_actions', []):
            try:
                action.setVisible(visible)
            except Exception:
                pass
        self._napari_menus_visible = visible
        tog = getattr(self, '_napari_toggle_action', None)
        if tog is not None:
            try:
                tog.setText('\u2630 napari \u25be' if visible else '\u2630 napari')
            except Exception:
                pass

    def _toggle_napari_menus(self, *_):
        """Flip napari-native menu visibility (the un-hide control)."""
        self._set_napari_menus_visible(
            not getattr(self, '_napari_menus_visible', False))

    def _reorder_pycat_menu_bar(self):
        """Move PyCAT's ★ Open/Save File(s) ahead of Analysis Methods / Toolbox,
        so loading data (the workflow entry point) is the first PyCAT menu.
        Defensive: no-op if the expected actions aren't present."""
        try:
            menubar = self.viewer.window._qt_window.menuBar()
            file_action = self.file_menu.menuAction()
            anchor = self.analysis_methods_menu.menuAction()
            if file_action is not None and anchor is not None:
                menubar.removeAction(file_action)
                menubar.insertAction(anchor, file_action)
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

    def _obj_is_sample_loader(self, object_name):
        """True for napari 'Open Sample' loader actions. These have objectNames
        like 'napari.astronaut' / 'napari.cells3d' (a sample id) rather than the
        'napari.window.*' / 'napari.viewer.*' / 'napari.layer.*' namespaces used
        by UI/toggle actions. Heuristic: 'napari.<single_token>' with no further
        dotted namespace, and not one of the known non-loader singletons."""
        parts = object_name.split('.')
        if len(parts) != 2 or parts[0] != 'napari':
            return False
        # Known non-loader 'napari.<x>' actions to leave alone (none currently,
        # but guard against false positives on UI singletons).
        _not_loaders = {'napari.new_layer'}
        return object_name not in _not_loaders


    def _home_fit_view(self):
        """
        Fit the camera to the selected Image / Labels / Shapes (ROI) layer.
        For an arbitrary Points/line selection (or nothing selected), show a
        brief notice and do nothing.
        """
        import numpy as np
        from napari.utils.notifications import show_info as _info
        layer = self.viewer.layers.selection.active
        if layer is None:
            _info("Select an image or ROI layer, then press Home.")
            return
        fittable = isinstance(
            layer, (napari.layers.Image, napari.layers.Labels, napari.layers.Shapes))
        if not fittable:
            _info(f"'{layer.name}' isn't an image or ROI — nothing to fit to.")
            return
        try:
            ext = np.asarray(layer.extent.world)      # (2, ndim): [mins, maxs]
            mins, maxs = ext[0], ext[1]
            nd = self.viewer.dims.ndisplay
            dims = list(self.viewer.dims.displayed)[-nd:]
            center = (mins + maxs) / 2.0
            self.viewer.camera.center = tuple(float(center[d]) for d in dims)

            # Zoom to fit: need the canvas size in pixels. Prefer the private
            # `_qt_viewer` attribute — the public `window.qt_viewer` property is
            # deprecated (napari ≤0.8) and emits a FutureWarning on access, so we
            # try the private one first and only fall back with the warning muted.
            cw = ch = None
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore', FutureWarning)
                for accessor in ('_qt_viewer', 'qt_viewer'):
                    try:
                        sz = getattr(self.viewer.window, accessor).canvas.size
                        cw, ch = float(sz[0]), float(sz[1])
                        break
                    except Exception:
                        continue
            sizes = [float(maxs[d] - mins[d]) for d in dims]
            if nd == 2 and cw and ch and all(s > 0 for s in sizes):
                # displayed dims are [y, x]; canvas is (width=x, height=y)
                zoom = min(ch / sizes[0], cw / sizes[1]) * 0.9   # 10% margin
                self.viewer.camera.zoom = zoom
            else:
                # Couldn't compute a fit zoom — at least re-center via reset.
                self.viewer.reset_view()
        except Exception:
            try:
                self.viewer.reset_view()
            except Exception:
                pass

    def _setup_menu_bar(self):
        """
        Set up the main menu bar with specific menu items and their associated actions.
        This method initializes and configures menus for analysis methods, toolbox functions,
        and file I/O operations, populating them with the relevant actions.
        """
        # Setup and populate the "Analysis Methods" menu
        # ── PyCAT section marker ─────────────────────────────────────────────
        # PyCAT's menus are appended to napari's own menu bar (File/View/Plugins/
        # Window/Help). Without a visual break, users can't tell where napari ends
        # and PyCAT begins. Insert a bold, non-clickable marker as an obvious
        # divider so everything to its right reads as "PyCAT".
        from PyQt5.QtGui import QFont as _QFont, QColor as _QColor, QIcon as _QIcon
        _menubar = self.viewer.window._qt_window.menuBar()
        # Branded section marker: the reduced PyCAT logo mark (snake/helix roundel,
        # no wordmark) followed by the "PyCAT" wordmark.
        #
        # NOTE: a plain QAction with BOTH an icon and text renders ICON-ONLY on a
        # QMenuBar (Qt drops the text), which is why the earlier version showed the
        # roundel but no "PyCAT". A QWidgetAction wrapping a real QLabel paints
        # exactly what we put in it, so the icon and the wordmark both appear.
        self._pycat_marker_action = None
        try:
            from PyQt5.QtWidgets import QWidgetAction, QLabel as _QLabel
            from PyQt5.QtGui import QPixmap as _QPixmap
            import importlib.resources as _res

            _pm = None
            try:
                # Build the pixmap INSIDE the as_file() block: on zipped/bundled
                # installs the extracted temp file is removed when the block exits.
                _mark_res = _res.files('pycat') / 'icons' / 'pycat_mark.png'
                with _res.as_file(_mark_res) as _mp:
                    _cand = _QPixmap(str(_mp))
                if not _cand.isNull():
                    _pm = _cand
            except Exception:
                _pm = None

            _lbl = _QLabel()
            _lbl.setTextFormat(Qt.RichText)
            _lbl.setStyleSheet(
                "QLabel { color: #6495ED; font-weight: bold; padding: 0px 6px; "
                "background: transparent; }")
            _lf = _QFont()
            _lf.setBold(True)
            _lf.setPointSize(_lf.pointSize() + 1)
            _lbl.setFont(_lf)

            if _pm is not None:
                # Scale the mark to menu-bar height and lay it out beside the text.
                _icon_px = 18
                _scaled = _pm.scaled(_icon_px, _icon_px, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
                _holder = QWidget()
                _hl = QHBoxLayout(_holder)
                _hl.setContentsMargins(6, 0, 6, 0)
                _hl.setSpacing(5)
                _pic = _QLabel()
                _pic.setPixmap(_scaled)
                _pic.setStyleSheet("background: transparent;")
                _txt = _QLabel("PyCAT \u25b8")
                _txt.setFont(_lf)
                _txt.setStyleSheet(
                    "QLabel { color: #6495ED; font-weight: bold; "
                    "background: transparent; }")
                _hl.addWidget(_pic)
                _hl.addWidget(_txt)
                _holder.setStyleSheet("background: transparent;")
                _wa = QWidgetAction(self.viewer.window._qt_window)
                _wa.setDefaultWidget(_holder)
                self._pycat_marker_action = _wa
            else:
                # No icon available — fall back to the original diamond + text.
                _lbl.setText("\u25c6 PyCAT \u25b8")
                _wa = QWidgetAction(self.viewer.window._qt_window)
                _wa.setDefaultWidget(_lbl)
                self._pycat_marker_action = _wa
        except Exception:
            self._pycat_marker_action = None

        if self._pycat_marker_action is None:
            # Last-resort fallback: plain (text-only) disabled action.
            self._pycat_marker_action = QAction('\u25c6 PyCAT \u25b8',
                                                self.viewer.window._qt_window)
            self._pycat_marker_action.setEnabled(False)
            _mfont = _QFont()
            _mfont.setBold(True)
            _mfont.setPointSize(_mfont.pointSize() + 1)
            self._pycat_marker_action.setFont(_mfont)
        # Accent colour for the text-only fallback marker (the QWidgetAction path
        # above styles its own labels directly).
        try:
            _menubar.setStyleSheet(
                _menubar.styleSheet() +
                "\nQMenuBar::item:disabled { color: #6495ED; font-weight: bold; }")
        except Exception:
            pass
        _menubar.addAction(self._pycat_marker_action)

        self.analysis_methods_menu = self.viewer.window._qt_window.menuBar().addMenu('Analysis Methods')
        self._add_analysis_methods_to_menu()

        # Setup and populate the "Toolbox" menu with various tools and utilities
        self.toolbox_menu = self.viewer.window._qt_window.menuBar().addMenu('Toolbox')
        self._add_toolbox_to_menu()
    
        # Setup and populate the "Open File(s)" menu with file I/O actions
        self.file_menu = self.viewer.window._qt_window.menuBar().addMenu('★ Open/Save File(s)')
        self._add_file_io_methods_to_menu()

        # NOTE: the action buttons that used to sit here on the menu bar (Clear,
        # Home, Metadata, Recorded Steps, Tags) now live in the PyCAT bar (the
        # gray "Batch:/Layers:/Information:" toolbar) so the top menu bar stays
        # mostly menus. They're created in add_batch_toolbar_button()
        # (batch_processor.py) and call back into these same MenuManager methods
        # (_home_fit_view, _show_metadata_dialog, _show_recorded_steps_dialog,
        # open_tag_inspector, and file_io.clear_all_without_saving).

        # Command palette: fuzzy-search to open any analysis method / toolbox
        # function or select a layer by name. Menu-bar button + Ctrl+Shift+P.
        try:
            from PyQt5.QtGui import QKeySequence
            self.palette_action = QAction('\u2315 Search',
                                          self.viewer.window._qt_window)
            self.palette_action.setToolTip(
                'Command palette — search methods, toolbox functions, and layers '
                'by name (Ctrl+Shift+P).')
            self.palette_action.setShortcut(QKeySequence('Ctrl+Shift+P'))
            self.palette_action.triggered.connect(self.open_command_palette)
            self.viewer.window._qt_window.menuBar().addAction(self.palette_action)
        except Exception:
            pass

        # Route files dropped onto the napari window through PyCAT's openers
        # (napari's default drop bypasses PyCAT's channel-assignment pipeline).
        try:
            from PyQt5.QtWidgets import QApplication
            self._pycat_drop_filter = _FileDropFilter(self.central_manager.file_io)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self._pycat_drop_filter)
            self.viewer.window._qt_window.setAcceptDrops(True)
            # An app-level filter usually sees events first, but a file dropped
            # directly on the napari CANVAS is handled by napari's QtViewer. The
            # "no-drop" cursor over the canvas means the canvas widget has
            # acceptDrops=False, so Qt never even generates DragEnter/Drop events
            # there for our filter to catch. Fix: force acceptDrops=True on the
            # QtViewer + its canvas widget, and install our event filter on each
            # so it intercepts and routes the drop. (PyQt does not honour
            # instance-level dropEvent reassignment — Qt calls the C++ virtual —
            # so an installed event filter is the correct mechanism, and it only
            # works once acceptDrops is enabled on the target widget.)
            _qtv = None
            for _acc in ('_qt_viewer', 'qt_viewer'):
                try:
                    _qtv = getattr(self.viewer.window, _acc, None)
                    if _qtv is not None:
                        break
                except Exception:
                    continue

            def _enable_drops(widget):
                if widget is None:
                    return
                try:
                    if hasattr(widget, 'setAcceptDrops'):
                        widget.setAcceptDrops(True)
                    if hasattr(widget, 'installEventFilter'):
                        widget.installEventFilter(self._pycat_drop_filter)
                except Exception:
                    pass

            if _qtv is not None:
                _enable_drops(_qtv)
                for _wattr in ('canvas', '_canvas'):
                    try:
                        _w = getattr(_qtv, _wattr, None)
                        _qw = getattr(_w, 'native', _w)
                        _enable_drops(_qw)
                        # vispy's native widget may itself wrap a viewport/child
                        # that receives the events; enable on its children too.
                        if _qw is not None and hasattr(_qw, 'children'):
                            try:
                                for _child in _qw.children():
                                    if hasattr(_child, 'setAcceptDrops'):
                                        _enable_drops(_child)
                            except Exception:
                                pass
                    except Exception:
                        continue

                # DEFERRED RE-ASSERTION (the actual fix for the red-slash cursor).
                # The drop target is the vispy CanvasBackendDesktop widget
                # (qtv.canvas.native) — confirmed by dnd_diag: it sits on top of
                # the QtViewer and has acceptDrops=False. Setting it once at init
                # (above) does not stick because vispy initialises/refreshes that
                # widget AFTER PyCAT's setup runs and resets acceptDrops to False.
                # Re-assert it on short deferred timers, once vispy has settled, so
                # the flag is True at drag time and Qt actually delivers the
                # DragEnter/Drop to our filter.
                def _reassert_canvas_drops():
                    try:
                        for _wattr in ('canvas', '_canvas'):
                            _w = getattr(_qtv, _wattr, None)
                            _qw = getattr(_w, 'native', _w)
                            if _qw is not None and hasattr(_qw, 'setAcceptDrops'):
                                _qw.setAcceptDrops(True)
                                _qw.installEventFilter(self._pycat_drop_filter)
                    except Exception:
                        pass
                try:
                    from PyQt5.QtCore import QTimer as _QTimer
                    # A couple of delays to beat whenever vispy finishes its init.
                    for _delay in (300, 900, 2000):
                        _QTimer.singleShot(_delay, _reassert_canvas_drops)
                except Exception:
                    _reassert_canvas_drops()
        except Exception as _e:
            print(f"[PyCAT] Could not install file-drop handler: {_e}")

        # LAYER-INSERTION BACKSTOP for drag-and-drop onto the canvas.
        # On napari 0.7.1 the canvas refuses the drag before any event filter can
        # catch it (the "no-drop" cursor), so the filter approach above cannot
        # intercept a canvas drop. This backstop takes the opposite tack: let
        # napari's own reader load the file (producing a layer), then detect that
        # layer as FOREIGN (napari sets layer.source.path on reader-loaded layers;
        # PyCAT's programmatic add_image leaves it None), remove the raw napari
        # layer(s), and re-open the SAME path through PyCAT's context-aware opener
        # so it enters the channel-assignment / metadata pipeline. This catches a
        # load no matter how it was triggered (canvas drop, or any path we can't
        # otherwise block), without depending on reaching napari's canvas widget.
        try:
            self._pycat_reroute_guard = False

            def _on_foreign_layer_inserted(event):
                # Re-entrancy guard: PyCAT's own opener inserts layers too.
                if getattr(self, '_pycat_reroute_guard', False):
                    return
                try:
                    layer = event.value
                except Exception:
                    layer = getattr(event, 'source', None)
                if layer is None:
                    return
                # Foreign = has a reader source path PyCAT didn't set.
                src_path = None
                try:
                    src = getattr(layer, 'source', None)
                    src_path = getattr(src, 'path', None) if src is not None else None
                except Exception:
                    src_path = None
                if not src_path:
                    return  # programmatic PyCAT layer — leave it alone
                # Defer the reroute: several layers can be inserted from one drop
                # (multi-channel), and we must not mutate viewers inside the
                # inserted callback. Collect the path and process once via a timer.
                try:
                    if not hasattr(self, '_pending_foreign_paths'):
                        self._pending_foreign_paths = []
                    if src_path not in self._pending_foreign_paths:
                        self._pending_foreign_paths.append(src_path)
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(0, self._process_foreign_layers)
                except Exception:
                    pass

            self._on_foreign_layer_inserted = _on_foreign_layer_inserted
            self.viewer.layers.events.inserted.connect(_on_foreign_layer_inserted)
        except Exception as _e:
            print(f"[PyCAT] Could not install layer-insertion backstop: {_e}")

        # Auto-tag USER-CREATED layers (made via napari's own "new points / shapes
        # / labels layer" menu buttons). Such a layer has no PyCAT tags and no
        # reader source path, so neither PyCAT's load-time tagging nor the
        # foreign-layer reroute above touches it — leaving it invisible to the tag
        # system. Stamp a light, low-confidence default role from the layer TYPE so
        # it is at least visible/queryable; the user can refine it in the Tag
        # Inspector, and that refinement (user_set) locks over this default.
        try:
            def _autotag_user_layer(event):
                try:
                    layer = event.value
                except Exception:
                    layer = getattr(event, 'source', None)
                if layer is None:
                    return
                try:
                    from pycat.utils import layer_tags as _LT
                    # Already tagged (PyCAT-created, or restored from a saved file)?
                    if _LT.get_tag(layer, 'role') is not None:
                        return
                    # Reader-loaded foreign file layers are handled/rerouted by the
                    # backstop above and re-tagged when re-opened through PyCAT;
                    # skip them here so we don't tag a layer that's about to be
                    # removed and replaced.
                    try:
                        src = getattr(layer, 'source', None)
                        if src is not None and getattr(src, 'path', None):
                            return
                    except Exception:
                        pass
                    # Default role by layer type.
                    cls = layer.__class__.__name__
                    role = {'Shapes': 'annotation', 'Points': 'annotation',
                            'Labels': 'mask', 'Image': 'image'}.get(cls)
                    if role is None:
                        return
                    _LT.tag_layer(layer, 'role', role, source='inferred',
                                  confidence=0.4)
                    _LT.tag_layer(layer, 'provenance', 'user-created',
                                  source='inferred', confidence=0.4)
                except Exception:
                    pass

            self._autotag_user_layer = _autotag_user_layer
            self.viewer.layers.events.inserted.connect(_autotag_user_layer)
        except Exception as _e:
            print(f"[PyCAT] Could not install user-layer auto-tagger: {_e}")

        # Hide napari's native File menu (and disable its Open* actions) so users
        # can't accidentally load data through napari's reader, which routes
        # around PyCAT's channel-assignment / metadata pipeline and crashes the
        # downstream workflow. Data must load via PyCAT's ★ Open/Save File(s).
        self._hide_napari_native_menus()

    def _process_foreign_layers(self):
        """Remove napari-reader-loaded (foreign) layers and re-open their source
        files through PyCAT's opener. Runs deferred (QTimer) so it doesn't mutate
        the layer list from inside the inserted-event callback. Handles the
        multi-layer case (one dropped multi-channel file → several foreign
        layers sharing a path)."""
        paths = getattr(self, '_pending_foreign_paths', [])
        self._pending_foreign_paths = []
        if not paths:
            return
        # Collect and remove every foreign layer whose source path is in our set.
        try:
            to_remove = []
            for layer in list(self.viewer.layers):
                try:
                    src = getattr(layer, 'source', None)
                    sp = getattr(src, 'path', None) if src is not None else None
                except Exception:
                    sp = None
                if sp and sp in paths:
                    to_remove.append(layer)
            for layer in to_remove:
                try:
                    self.viewer.layers.remove(layer)
                except Exception:
                    pass
        except Exception:
            pass
        # Re-open each unique path through PyCAT's context-aware opener, guarding
        # against the backstop re-triggering on PyCAT's own inserts.
        import os as _os
        self._pycat_reroute_guard = True
        try:
            from napari.utils.notifications import show_info as _info
            for i, p in enumerate(paths):
                try:
                    # First dropped file replaces the session (normal open);
                    # additional files add without clearing (comparison).
                    self.central_manager.file_io.open_image_auto(
                        file_path=p, clear_first=(i == 0))
                except Exception as _e:
                    print(f"[PyCAT] Could not re-open dropped file '{p}': {_e}")
            try:
                _info("Loaded dropped file(s) through PyCAT.")
            except Exception:
                pass
        finally:
            self._pycat_reroute_guard = False

    def _show_metadata_dialog(self):
        """Show acquisition metadata for the loaded file.

        Displays the curated 'common' fields by default, with a checkbox that
        reveals the full raw metadata dump. Also offers a JSON export button.
        """
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QPushButton, QCheckBox,
                                      QTableWidget, QTableWidgetItem, QHeaderView,
                                      QFileDialog)
        from napari.utils.notifications import (show_info as _info,
                                                show_warning as _warn)
        dr = self.central_manager.active_data_class.data_repository
        md = dr.get('file_metadata')
        if not md or not isinstance(md, dict):
            _warn("No metadata available — open an image first.")
            return

        common = md.get('common', {}) or {}
        raw = md.get('raw', {}) or {}

        dialog = QDialog(self.viewer.window._qt_window)
        dialog.setWindowTitle("File Metadata")
        dialog.resize(560, 620)
        layout = QVBoxLayout(dialog)

        fname = common.get('file_name') or 'Unknown file'
        header = QLabel(f"<b>{fname}</b>")
        layout.addWidget(header)

        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(['Field', 'Value'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(table)

        # Curated-field display order and labels.
        _labels = [
            ('file_type', 'File type'),
            ('dimensions', 'Dimensions (T,C,Z,Y,X)'),
            ('pixel_size_um', 'Pixel size (µm/px)'),
            ('pixel_size_source', 'Pixel size source'),
            ('bit_depth', 'Bit depth'),
            ('n_channels', 'Channels'),
            ('n_timepoints', 'Timepoints'),
            ('n_z', 'Z slices'),
            ('objective', 'Objective'),
            ('numerical_aperture', 'Numerical aperture'),
            ('modality', 'Modality'),
            ('excitation_nm', 'Excitation (nm)'),
            ('emission_nm', 'Emission (nm)'),
            ('acquisition_date', 'Acquisition date'),
            ('software', 'Software'),
            ('camera_name', 'Camera'),
            ('exposure_s', 'Exposure (s)'),
            ('frame_interval_s', 'Frame interval (s)'),
            ('frame_interval_source', 'Frame interval source'),
            ('z_step_um', 'Z step (µm)'),
        ]

        def _fmt(v):
            if v is None:
                return '—'
            if isinstance(v, dict):
                return ', '.join(f"{k.upper()}={v.get(k)}" for k in ('t', 'c', 'z', 'y', 'x')
                                 if v.get(k) is not None)
            if isinstance(v, float):
                return f"{v:.6g}"
            return str(v)

        def _fmt_interval(c):
            """Frame interval with IQR appended when measured per-frame."""
            fi = c.get('frame_interval_s')
            if fi is None:
                return '—'
            txt = f"{float(fi):.6g}"
            iqr = c.get('frame_interval_iqr_s')
            if iqr is not None:
                txt += f"  (IQR {float(iqr):.4g})"
            return txt

        def _populate(show_raw):
            rows = []
            for key, lbl in _labels:
                if key == 'frame_interval_s':
                    rows.append((lbl, _fmt_interval(common)))
                else:
                    rows.append((lbl, _fmt(common.get(key))))
            if show_raw:
                # Full per-frame timing (the measured deltas) live in the
                # expanded view so the curated panel stays compact.
                deltas = common.get('frame_deltas_s')
                if deltas:
                    rows.append(('— frame timing (measured) —', ''))
                    rows.append(('n frames', _fmt(common.get('n_frames'))))
                    rows.append(('acquisition start', _fmt(common.get('acquisition_start_time'))))
                    rows.append(('frame deltas (s)',
                                 ', '.join(f"{float(d):.5g}" for d in deltas)))
                if raw:
                    rows.append(('— raw metadata —', ''))
                    for k in sorted(raw.keys()):
                        rows.append((k, _fmt(raw.get(k))))
            table.setRowCount(len(rows))
            for i, (k, v) in enumerate(rows):
                table.setItem(i, 0, QTableWidgetItem(str(k)))
                table.setItem(i, 1, QTableWidgetItem(str(v)))

        _populate(False)

        controls = QHBoxLayout()
        raw_check = QCheckBox("Show all raw metadata")
        raw_check.toggled.connect(_populate)
        controls.addWidget(raw_check)
        controls.addStretch(1)

        export_btn = QPushButton("Export JSON…")

        def _export():
            import json
            path, _ = QFileDialog.getSaveFileName(
                dialog, "Export metadata as JSON",
                (common.get('file_name') or 'metadata') + '_metadata.json',
                "JSON Files (*.json)")
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(md, f, indent=2, default=str)
                    _info(f"Metadata exported to {path}")
                except Exception as e:
                    _warn(f"Export failed: {e}")

        export_btn.clicked.connect(_export)
        controls.addWidget(export_btn)

        compare_btn = QPushButton("Compare loaded images…")
        compare_btn.setToolTip(
            "Diff acquisition settings across the currently visible images and "
            "flag differences (exposure, laser, objective, filters, etc.) that "
            "can make a quantitative comparison untrustworthy.")
        compare_btn.clicked.connect(lambda: self._show_metadata_comparison())
        controls.addWidget(compare_btn)
        layout.addLayout(controls)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec_()

    def _load_discovered_session(self, folder, sessions):
        """Pick ONE session and load it. **A session picker, not a file multi-select.**

        PyCAT knows what a session needs — its manifest records exactly that — so there is nothing
        for the user to curate here. The old dialog asked which *files* to load, `selectAll()`d them,
        and then ignored the answer anyway. The only question worth asking is *which session*, and
        only when there is more than one.
        """
        from PyQt5.QtWidgets import QInputDialog
        from napari.utils.notifications import show_info as napari_show_info
        from pycat.file_io.session_loader import session_picker_labels, load_session

        chosen = sessions[0]
        if len(sessions) > 1:
            labels = session_picker_labels(sessions)
            label, ok = QInputDialog.getItem(
                None, "Load Session",
                f"{len(sessions)} sessions found in {folder.name}. Which one?",
                labels, 0, False)
            if not ok:
                return
            chosen = sessions[labels.index(label)]

        # A session REPLACES the workspace (clears first, guarded — see clear_before_session_load); abort
        # the load if the user declines to discard existing work.
        from pycat.file_io.session import clear_before_session_load
        if not clear_before_session_load(self.central_manager.viewer, self.central_manager):
            return
        result = load_session(
            chosen['dir'], self.central_manager.viewer,
            self.central_manager.active_data_class,
            central_manager=self.central_manager, use_worker=True,
        )
        napari_show_info(
            f"Restored session '{chosen['name']}': "
            f"{len(result['loaded_layers'])} layer(s), {len(result['loaded_dfs'])} table(s)."
        )

    def _open_session_loader(self):
        """Open a folder browser to select a PyCAT output directory and reload."""
        from PyQt5.QtWidgets import (QFileDialog, QDialog, QVBoxLayout,
                                      QListWidget, QPushButton, QLabel,
                                      QHBoxLayout, QCheckBox,
                                      QAbstractItemView)
        from pathlib import Path
        from napari.utils.notifications import (
            show_info as napari_show_info,
            show_warning as napari_show_warning,
        )
        from pycat.file_io.session_loader import (
            scan_output_folder, load_session, session_load_messages)
        from pycat.file_io.session_manifest import discover_sessions

        folder = QFileDialog.getExistingDirectory(
            None, "Select PyCAT Output Folder", "",
            QFileDialog.ShowDirsOnly
        )
        if not folder:
            return
        folder = Path(folder)

        groups = scan_output_folder(folder)
        if not groups:
            # ── The sessions are in SUBFOLDERS, and nothing looked there ──────────────────
            #
            # Saving always creates its own `session_<stem>_<timestamp>/`. The scan above is
            # `folder.iterdir()` — one level, files only — so pointing at the parent directory the
            # sessions were saved into (the obvious thing to do) reported "no outputs found" with
            # every session sitting in plain view underneath it.
            sessions = discover_sessions(folder)
            if sessions:
                self._load_discovered_session(folder, sessions)
                return
            napari_show_warning(
                f"No recognised PyCAT outputs found in {folder.name}.\n"
                "Expected files like *_preprocessed.tiff, *_cell_df.csv, etc."
            )
            return

        dlg = QDialog()
        dlg.setWindowTitle(f"Load Session — {folder.name}")
        dlg.setMinimumWidth(520)
        dlg.setMinimumHeight(480)
        vl = QVBoxLayout(dlg)

        n_files = sum(len(v) for v in groups.values())
        vl.addWidget(QLabel(
            f"Found {n_files} PyCAT output file(s) from "
            f"{len(groups)} image stem(s) in:\n{folder}"
        ))

        group_list = QListWidget()
        group_list.setSelectionMode(QAbstractItemView.MultiSelection)
        for stem, files in sorted(groups.items()):
            n_img = sum(1 for f in files if f["layer_type"] == "image")
            n_lbl = sum(1 for f in files if f["layer_type"] == "labels")
            n_df  = sum(1 for f in files if f["layer_type"] == "dataframe")
            group_list.addItem(
                f"{stem}  —  {n_img} image(s), {n_lbl} label(s), {n_df} table(s)"
            )
        group_list.selectAll()
        vl.addWidget(group_list)

        status_lbl  = QLabel("")
        status_lbl.setWordWrap(True)

        status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        vl.addWidget(status_lbl)

        btn_row    = QHBoxLayout()
        load_btn   = QPushButton("Load Selected")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(load_btn); btn_row.addWidget(cancel_btn)
        vl.addLayout(btn_row)

        cancel_btn.clicked.connect(dlg.reject)

        def _on_load():
            selected_stems = {
                item.text().split("  —  ")[0].strip()
                for item in group_list.selectedItems()
            }
            if not selected_stems:
                napari_show_warning("No images selected.")
                return

            # A session REPLACES the workspace (clears first, guarded — see clear_before_session_load).
            from pycat.file_io.session import clear_before_session_load
            if not clear_before_session_load(self.central_manager.viewer, self.central_manager):
                status_lbl.setText("Load cancelled — current workspace kept.")
                return

            load_btn.setEnabled(False)
            status_lbl.setText("Loading session…")

            data_instance = self.central_manager.active_data_class

            # ── Off the Qt thread, behind a modal progress dialog ──────────────────────────────
            #
            # `use_worker=True` runs the read/decode on a QThread while a modal QProgressDialog keeps
            # the window painting — the "Python is not responding" freeze otherwise (the 1.6.81/82
            # bars made the wait visible, not shorter). The worker owns that dialog, so the inline
            # `prog_bar` is retired here: two bars for one operation is the UX trap the roadmap
            # flagged. `stems=selected_stems` loads exactly the user's selection (the folder re-scan
            # used to ignore it and load all eight of eight).
            result = load_session(
                folder, self.central_manager.viewer,
                data_instance, stems=selected_stems,
                central_manager=self.central_manager, use_worker=True,
            )

            load_btn.setEnabled(True)
            _status_text, _info_text = session_load_messages(result)
            status_lbl.setText(_status_text)
            napari_show_info(_info_text)
            for p, reason in result["skipped"]:
                print(f"[PyCAT Session] Skipped {p.name}: {reason}")

            # ── Reopen the analysis method and rebuild its VIEW, not just the data ──────────
            #
            # Restoring the dataframes into the repository is not "restoring the session": the user
            # expects the method they were in to reopen with its plots/tables/layers, not an empty
            # panel. So reopen the recorded method (or, for a session saved before the method was
            # recorded, infer it from the restored data) and ask it to rebuild its view. Switching
            # methods PRESERVES the data repository, so the reopened method sees the restored data.
            try:
                _active = result.get('active_method')
                if not _active:
                    for _dkey, _cls in _SESSION_METHOD_BY_DATA.items():
                        if _dkey in result["loaded_dfs"]:
                            _active = _cls
                            break
                _switch = _SESSION_METHOD_SWITCH.get(_active)
                if _switch is not None:
                    getattr(self.central_manager.analysis_methods_ui, _switch)()
                    _cur = getattr(self.central_manager.analysis_methods_ui,
                                   'current_analysis_ui', None)
                    if _cur is not None and hasattr(_cur, 'restore_session_view') \
                            and _cur.restore_session_view():
                        napari_show_info(f"Session restored — the analysis view was rebuilt.")
                    else:
                        napari_show_info("Session data restored; reopen the analysis "
                                         "method to rebuild its view.")
                elif _active:
                    napari_show_info(f"Session data restored. Reopen '{_active}' to rebuild its view.")
            except Exception as _ve:
                print(f"[PyCAT Session] method reopen/restore failed: {_ve}")

            # Clicking Load LOADS and then CLOSES — the completion is reported by the toast above, so
            # the dialog has done its job. Cancel is the only way to dismiss WITHOUT loading. (Before,
            # Load left the dialog open and the user had to click Cancel to get rid of it, which reads
            # as "did it even work?".)
            dlg.accept()

        load_btn.clicked.connect(_on_load)
        dlg.exec_()

    def _show_recorded_steps_dialog(self):
        """Show the batch workflow recorded so far.

        Top-level rows are the recorded steps (number, name, timestamp). Each
        step expands to reveal the layers/parameters it captured, so the user
        can review exactly what will be replayed.
        """
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                      QPushButton, QTreeWidget, QTreeWidgetItem,
                                      QHeaderView)
        from napari.utils.notifications import show_info as _info

        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        steps = (bp.config.get('steps', []) if bp and getattr(bp, 'config', None)
                 else [])
        rec_on = bool(getattr(bp, 'recording_enabled', False)) if bp else False

        dialog = QDialog(self.viewer.window._qt_window)
        dialog.setWindowTitle("Recorded Batch Steps")
        dialog.resize(620, 640)
        layout = QVBoxLayout(dialog)

        status = ("<span style='color:#5cb85c;'>● Recording ON</span>" if rec_on
                  else "<span style='color:#aaa;'>○ Recording off</span>")
        header = QLabel(f"<b>{len(steps)} step(s) recorded</b> &nbsp; {status}")
        layout.addWidget(header)

        if not steps:
            layout.addWidget(QLabel(
                "<span style='color:#aaa;'>No steps recorded yet. Turn on "
                "recording in the Batch dialog, then run your workflow.</span>"))

        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(['Step', 'Value'])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(tree)

        # Parameter keys that are internal debugging snapshots — shown last and
        # de-emphasised rather than as primary parameters.
        _debug_keys = {'_active_layer_at_record', '_all_layers_at_record'}

        def _fmt(v):
            if v is None:
                return '—'
            if isinstance(v, (list, tuple)):
                return ', '.join(str(x) for x in v) if v else '(none)'
            if isinstance(v, float):
                return f"{v:.4g}"
            return str(v)

        from pycat.batch_step_registry import step_operations
        for i, step in enumerate(steps, 1):
            name = step.get('step', '?')
            ts = step.get('timestamp', '')
            params = step.get('params', {}) or {}
            top = QTreeWidgetItem([f"{i}.  {name}", ts])
            tree.addTopLevelItem(top)
            _ops = step_operations(name)   # the step's declared op composition — auditable replay in the UI
            if _ops:
                top.addChild(QTreeWidgetItem(["operations", ", ".join(_ops)]))
            # Primary params first, debug snapshots last.
            primary = [(k, v) for k, v in params.items() if k not in _debug_keys]
            debug   = [(k, v) for k, v in params.items() if k in _debug_keys]
            for k, v in primary:
                top.addChild(QTreeWidgetItem([str(k), _fmt(v)]))
            for k, v in debug:
                child = QTreeWidgetItem([f"{k}  (snapshot)", _fmt(v)])
                top.addChild(child)
        tree.expandToDepth(0)  # show steps collapsed; user expands to see params

        btn_row = QHBoxLayout()
        for _lbl, _fn in [("Expand all", tree.expandAll), ("Collapse all", tree.collapseAll)]:
            _b = QPushButton(_lbl); _b.clicked.connect(_fn); btn_row.addWidget(_b)
        btn_row.addStretch(1)
        _close = QPushButton("Close"); _close.clicked.connect(dialog.accept); btn_row.addWidget(_close)
        layout.addLayout(btn_row)

        dialog.exec_()

    def make_lambda(self, action_method, kwargs):
        """
        Creates a lambda function for triggering actions with arguments. This allows
        for the dynamic execution of methods with specific parameters directly from
        menu action triggers.

        Parameters
        ----------
        action_method : callable
            The method to be executed when the action is triggered.
        kwargs : dict
            A dictionary of keyword arguments to be passed to the action method.

        Returns
        -------
        function
            A lambda function that calls `action_method` with `kwargs` when triggered.
        """
        return lambda: action_method(**kwargs)

    def _add_actions_to_menu(self, actions_dict, menu):
        """
        Add actions to a given menu based on the provided dictionary of action names
        and methods. This allows for a dynamic and flexible addition of actions to menus,
        facilitating customization and extension.

        Parameters
        ----------
        actions_dict : dict
            A dictionary where keys are action names (str) and values are tuples.
            Each tuple contains the method to connect to the action and an optional
            dictionary of arguments for that method.
        menu : QMenu
            The menu to which the actions will be added.
        """

        for action_name, (action_method, kwargs) in actions_dict.items():
            action = QAction(action_name, self.viewer.window._qt_window)
            if kwargs:
                # Connect the action to a lambda function for methods requiring arguments
                action.triggered.connect(self.make_lambda(action_method, kwargs))
            else:
                # Connect the action directly to the method if no arguments are needed
                action.triggered.connect(action_method)
            menu.addAction(action)
            # Accumulate a flat, searchable registry of every menu command so the
            # command palette (Ctrl+Shift+P) can fuzzy-find and launch any method
            # or toolbox function by name. Records the same callable/kwargs the
            # menu uses, so the palette invokes exactly what the menu would.
            try:
                if not hasattr(self, '_command_registry'):
                    self._command_registry = {}
                menu_title = menu.title().replace('&', '') if hasattr(menu, 'title') else ''
                self._command_registry[action_name] = (
                    action_method, kwargs, menu_title)
            except Exception:
                pass

    # The following methods implement specific functionality additions to their respective menus.
    # These methods organize the addition of various analysis, file I/O, and toolbox
    # actions into structured menus and submenus, providing a user-friendly interface for
    # accessing different functionalities within the napari application.

    # Each method utilizes _add_actions_to_menu to dynamically add actions based on a predefined
    # dictionary of action names and associated methods. These dictionaries define the structure
    # and content of the menus, facilitating easy modifications and extensions to the menu system.
            
    # Add specific file I/O methods as actions to the file I/O menu.
    def _add_file_io_methods_to_menu(self):
            """
            Add specific file I/O methods as actions to the file I/O menu.
            """
            file_io_methods_dict = {
                'Open Image (auto-detect 2D / stack)': (self.central_manager.file_io.open_image_auto, {}),
                'Add Image / Mask (keep current)': (self.central_manager.file_io.add_image_or_mask, {}),
                'Switch Position / Scene (multi-position files)': (self._open_scene_switcher, {}),
                'Load Previous Session Results': (self._open_session_loader, {}),
                'Save and Clear': (self.central_manager.file_io.save_and_clear_all, {'viewer': self.viewer})
            }
            self._add_actions_to_menu(file_io_methods_dict, self.file_menu)

    def _open_scene_switcher(self, *args, **kwargs):
        """Open (or re-focus) the multi-position scene switcher dock.

        A multi-position acquisition loads ONE position at a time; this dock changes which one in
        place. Held on the instance so re-opening re-uses the one dock rather than stacking another.
        """
        try:
            dock = getattr(self, '_scene_switcher_dock', None)
            if dock is None:
                from pycat.ui.scene_switcher import SceneSwitcherDock
                dock = SceneSwitcherDock(self.viewer, self.central_manager)
                self._scene_switcher_dock = dock
            dock.show()
        except Exception as exc:
            from pycat.utils.general_utils import debug_log
            debug_log('ui_modules: could not open the scene switcher', exc)

    def _open_image_add(self, *args, **kwargs):
        """Open an image WITHOUT clearing the current session — adds its layers
        alongside the existing ones (for side-by-side comparison or loading a
        missing channel of a split-file image). Uses the context-aware router."""
        self.central_manager.file_io.open_image_auto(clear_first=False)

    def _toggle_grid_view(self, *args, **kwargs):
        """Toggle a PyCAT-managed side-by-side grid for comparing images.

        napari's raw grid mode tiles EVERY layer — including PyCAT's annotation
        Shapes layers (Cell/Object Diameter) and any drawing layers, which then
        get their own empty tiles instead of overlaying the images. It also grids
        by layer count regardless of the visibility eyeball. This managed version:
          - tiles only IMAGE layers (annotations/shapes/points stay overlaid,
            hidden behind the scenes while comparing — they can't be tiled
            meaningfully since an annotation belongs to one image),
          - respects the visibility eyeball: hidden image layers are dropped from
            the grid and it reflows,
          - recomputes automatically when layer visibility changes while grid is
            on, and restores the normal overlaid view when toggled off.
        """
        try:
            self._pycat_grid_on = not getattr(self, '_pycat_grid_on', False)
        except Exception:
            self._pycat_grid_on = True

        from napari.utils.notifications import show_info as _info
        if self._pycat_grid_on:
            # Snapshot the CANONICAL order of tileable layers at the moment grid
            # is turned on. Every reflow arranges visible layers against THIS
            # fixed anchor (not the transient list order), so toggling visibility
            # — including "show/hide all" — can never shuffle the grid: a layer
            # always returns to the same relative slot. Layers added later append
            # to the anchor in arrival order.
            self._grid_canonical_order = [
                l for l in self.viewer.layers
                if isinstance(l, (napari.layers.Image, napari.layers.Labels))]
            self._apply_managed_grid()
            # Recompute the grid whenever any layer's visibility toggles.
            if not getattr(self, '_grid_vis_wired', False):
                try:
                    for lyr in self.viewer.layers:
                        try:
                            lyr.events.visible.connect(self._on_grid_layer_vis_changed)
                        except Exception:
                            pass
                    # New layers added while grid is on should also be watched.
                    self.viewer.layers.events.inserted.connect(
                        self._on_grid_layers_changed)
                    self.viewer.layers.events.removed.connect(
                        self._on_grid_layers_changed)
                    self._grid_vis_wired = True
                except Exception:
                    pass
            # If any non-image (annotation / drawing) layers were pulled out to
            # keep them from claiming empty grid tiles, tell the user they're
            # temporarily set aside and will come back when grid is turned off —
            # so a drawing layer vanishing from the list isn't alarming.
            n_removed = len(getattr(self, '_grid_removed_nonimage', []))
            if n_removed:
                _info(f"Side-by-side grid view ON. {n_removed} annotation/"
                      f"drawing layer(s) temporarily set aside (with their "
                      f"contents) and will return when you toggle grid off.")
            else:
                _info("Side-by-side grid view ON (image layers only).")
            # Surface an acquisition-metadata comparison so the user knows
            # whether the images being compared were acquired under the same
            # settings (different exposure / laser / objective / filters make a
            # quantitative comparison untrustworthy — independent of the grid).
            try:
                self._maybe_warn_metadata_diff()
            except Exception:
                pass
        else:
            try:
                self.viewer.grid.enabled = False
            except Exception:
                pass
            # Re-insert the non-image layers removed for grid mode.
            n_restored = len(getattr(self, '_grid_removed_nonimage', []))
            self._restore_grid_removed_layers()
            # Clear the canonical order anchor so a fresh snapshot is taken next
            # time grid is enabled.
            self._grid_canonical_order = []
            if n_restored:
                _info(f"Side-by-side grid view OFF. {n_restored} annotation/"
                      f"drawing layer(s) restored.")
            else:
                _info("Side-by-side grid view OFF.")

    def _gather_compared_metadata(self):
        """Collect per-layer acquisition metadata for the currently VISIBLE image
        layers (the ones being compared in grid mode). Returns (names, metas).
        Reads the metadata stashed on each layer at load time."""
        names, metas = [], []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, napari.layers.Image) and bool(getattr(lyr, 'visible', True)):
                    md = None
                    try:
                        full = lyr.metadata.get('pycat_file_metadata')
                        if isinstance(full, dict):
                            md = full.get('common', full)
                    except Exception:
                        md = None
                    names.append(lyr.name)
                    metas.append(md or {})
        except Exception:
            pass
        return names, metas

    def _maybe_warn_metadata_diff(self):
        """When grid comparison starts with 2+ images, run the acquisition-
        metadata diff and, if critical settings differ, pop the comparison table
        so the user knows the comparison may be untrustworthy. If everything
        matches (or metadata is absent), stay quiet."""
        names, metas = self._gather_compared_metadata()
        if len(names) < 2:
            return
        # Only show automatically when there's something worth warning about.
        try:
            from pycat.file_io.metadata_extract import compare_acquisition_metadata
            result = compare_acquisition_metadata(metas, names=names)
        except Exception:
            return
        if result['n_critical_diff'] > 0:
            self._show_metadata_comparison(result)

    def _show_metadata_comparison(self, result=None):
        """Show a table diffing acquisition metadata across the compared images,
        highlighting settings that differ. Can be called standalone; if no
        result is passed it gathers the current visible-image metadata."""
        from qtpy.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget,
                                    QTableWidgetItem, QPushButton)
        from qtpy.QtGui import QColor
        if result is None:
            names, metas = self._gather_compared_metadata()
            if len(names) < 2:
                from napari.utils.notifications import show_info as _info
                _info("Load/show at least two images to compare their metadata.")
                return
            from pycat.file_io.metadata_extract import compare_acquisition_metadata
            result = compare_acquisition_metadata(metas, names=names)

        names = result['names']
        rows = result['rows']
        dlg = QDialog()
        dlg.setWindowTitle("Acquisition Metadata Comparison")
        lay = QVBoxLayout(dlg)

        verdict = QLabel(result['summary'])
        verdict.setWordWrap(True)
        if result['n_critical_diff'] > 0:
            verdict.setStyleSheet("color:#c0392b; font-weight:bold;")
        elif result['any_diff']:
            verdict.setStyleSheet("color:#b8860b;")
        else:
            verdict.setStyleSheet("color:#2e7d32;")
        lay.addWidget(verdict)

        table = QTableWidget(len(rows), len(names) + 1)
        table.setHorizontalHeaderLabels(['Setting'] + list(names))
        for r, row in enumerate(rows):
            lbl = QTableWidgetItem(row['label']
                                   + ('  \u26a0' if row['differs'] and
                                      row['severity'] == 'critical' else ''))
            table.setItem(r, 0, lbl)
            for c, val in enumerate(row['values']):
                item = QTableWidgetItem('—' if val is None else str(val))
                if row['differs']:
                    # Highlight differing rows: red for critical, amber for info.
                    item.setBackground(QColor('#f9d6d5') if row['severity'] ==
                                       'critical' else QColor('#fdf1cf'))
                table.setItem(r, c + 1, item)
        table.resizeColumnsToContents()
        lay.addWidget(table)

        note = QLabel("Rows highlighted red are acquisition settings that can "
                      "make a quantitative comparison untrustworthy; amber rows "
                      "differ but are less critical. '—' means the value wasn't "
                      "recorded in that file's metadata.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:9pt;")
        lay.addWidget(note)

        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close)
        dlg.resize(600, 400)
        dlg.exec_()

    def _annotation_layers(self):
        """Layers that are pure annotation/drawing (Shapes/Points) — these are
        removed from the grid (they can't tile meaningfully). Mask (Labels)
        layers are NOT included here: they overlay their image and are controlled
        by their own visibility eyeball."""
        out = []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, (napari.layers.Shapes, napari.layers.Points)):
                    out.append(lyr)
        except Exception:
            pass
        return out

    def _grid_tileable_visible(self):
        """Visible layers that should occupy grid cells: Image and Labels (mask)
        layers that are currently visible, in layer order."""
        out = []
        try:
            for lyr in self.viewer.layers:
                if isinstance(lyr, (napari.layers.Image, napari.layers.Labels)):
                    if bool(getattr(lyr, 'visible', True)):
                        out.append(lyr)
        except Exception:
            pass
        return out

    def _apply_managed_grid(self):
        """Enable napari grid, reflowed to only the VISIBLE tileable layers.

        The diagnostic on napari 0.7.1 established two facts that drive this:
          (1) napari's grid tiles by TOTAL layer count and ignores visibility, so
              hidden layers otherwise leave empty black tiles (grid does NOT
              reflow on its own, and shape=(-1,-1) auto-recomputes to the full
              count) — but
          (2) setting grid.shape EXPLICITLY to fit the visible count DOES reflow
              the canvas, and napari fills cells by LAYER INDEX.
        So: remove pure annotation/drawing layers; arrange the visible tileable
        layers (images + visible masks) into the front cells ORDERED BY A
        CANONICAL ANCHOR snapshotted when grid was enabled — so visibility
        toggles (including show/hide-all) reflow the grid without ever shuffling
        which layer sits where — and set grid.shape to fit the visible count.
        Hidden tileable layers sort after the visible ones; masks overlay their
        image via z-order and are governed by their own eyeball.

        Idempotent and re-entrancy-safe.
        """
        import math
        if getattr(self, '_grid_applying', False):
            return
        self._grid_applying = True
        try:
            g = self.viewer.grid
            # 1. Remove pure annotation/drawing layers (recorded for restore).
            if not hasattr(self, '_grid_removed_nonimage'):
                self._grid_removed_nonimage = []
            for idx in range(len(self.viewer.layers) - 1, -1, -1):
                lyr = self.viewer.layers[idx]
                if isinstance(lyr, (napari.layers.Shapes, napari.layers.Points)):
                    if not any(l is lyr for _, l in self._grid_removed_nonimage):
                        self._grid_removed_nonimage.append((idx, lyr))
                    try:
                        self.viewer.layers.remove(lyr)
                    except Exception:
                        pass
            # 2. Count visible tileable layers and set an explicit grid shape.
            vis = self._grid_tileable_visible()
            n = len(vis)
            if n <= 1:
                g.enabled = False
                return
            # 3. Arrange visible tileable layers into the front cells, ordered by
            #    the CANONICAL anchor captured at grid-on (not by transient list
            #    order) so visibility toggles never shuffle the grid. Any layer
            #    not in the anchor (added after grid-on) is appended in arrival
            #    order. Hidden tileable layers go after the visible ones.
            anchor = getattr(self, '_grid_canonical_order', None) or []

            def _anchor_key(layer):
                try:
                    return anchor.index(layer)
                except ValueError:
                    return len(anchor) + list(self.viewer.layers).index(layer)

            vis_sorted = sorted(vis, key=_anchor_key)
            hidden_tileable = [
                l for l in self.viewer.layers
                if isinstance(l, (napari.layers.Image, napari.layers.Labels))
                and l not in vis]
            hidden_sorted = sorted(hidden_tileable, key=_anchor_key)
            target = vis_sorted + hidden_sorted + [
                l for l in self.viewer.layers
                if l not in vis_sorted and l not in hidden_sorted]
            try:
                for dst, lyr in enumerate(target):
                    src = list(self.viewer.layers).index(lyr)
                    if src != dst:
                        self.viewer.layers.move(src, dst)
            except Exception:
                pass
            cols = int(math.ceil(math.sqrt(n)))
            rows = int(math.ceil(n / cols))
            g.enabled = True
            try:
                g.stride = 1
                g.shape = (rows, cols)   # EXPLICIT shape → reflows (proven)
            except Exception:
                pass
        except Exception as _e:
            print(f"[PyCAT] managed grid failed: {_e}")
        finally:
            self._grid_applying = False

    def _restore_grid_removed_layers(self):
        """Re-insert the annotation/drawing layers removed for grid mode, at their
        original positions (best-effort), preserving their data."""
        removed = getattr(self, '_grid_removed_nonimage', [])
        for idx, lyr in sorted(removed, key=lambda t: t[0]):
            try:
                if lyr not in list(self.viewer.layers):
                    insert_at = min(idx, len(self.viewer.layers))
                    self.viewer.layers.insert(insert_at, lyr)
            except Exception:
                try:
                    self.viewer.layers.append(lyr)
                except Exception:
                    pass
        self._grid_removed_nonimage = []

    def _on_grid_layer_vis_changed(self, *args):
        if getattr(self, '_pycat_grid_on', False):
            self._apply_managed_grid()

    def _on_grid_layers_changed(self, *args):
        if getattr(self, '_pycat_grid_on', False):
            # Wire visibility watcher on any new layer, then recompute.
            try:
                for lyr in self.viewer.layers:
                    try:
                        lyr.events.visible.connect(self._on_grid_layer_vis_changed)
                    except Exception:
                        pass
            except Exception:
                pass
            self._apply_managed_grid()

    # Add specific analysis methods as actions to the analysis methods menu.
    def open_tag_inspector(self, *_):
        """Open the Layer Tag Inspector — shows each layer's tags with their
        source and confidence, its lineage edges, and lets the user override any
        tag (an override locks against re-inference). This is the trust layer for
        the tagging system: you can always see *why* a tag is set and correct it.
        """
        try:
            from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                QComboBox, QTableWidget, QTableWidgetItem, QLabel, QPushButton,
                QLineEdit, QHeaderView, QWidget)
            from PyQt5.QtCore import Qt
            from pycat.utils import layer_tags as _LT
        except Exception as _e:
            print(f"[PyCAT tags] inspector unavailable: {_e}")
            return

        dlg = QDialog(self.viewer.window._qt_window)
        dlg.setWindowTitle("Layer Tag Inspector")
        dlg.setMinimumWidth(560); dlg.setMinimumHeight(420)
        v = QVBoxLayout(dlg)

        # Layer picker.
        row = QHBoxLayout()
        row.addWidget(QLabel("Layer:"))
        picker = QComboBox()
        layer_names = [l.name for l in self.viewer.layers]
        picker.addItems(layer_names)
        row.addWidget(picker, 1)
        v.addLayout(row)

        # Tags table: key | value | source | confidence.
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["key", "value", "source", "confidence"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(table, 1)

        # Lineage summary.
        lineage_lbl = QLabel("")
        lineage_lbl.setWordWrap(True)
        lineage_lbl.setStyleSheet("QLabel { color:#888; font-size:11px; }")
        v.addWidget(lineage_lbl)

        # Override row: key + value -> set as user_set.
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Override:"))
        key_edit = QComboBox(); key_edit.setEditable(True)
        key_edit.addItems(sorted(_LT.CORE_KEYS))
        val_edit = QLineEdit(); val_edit.setPlaceholderText("value")
        set_btn = QPushButton("Set (locks)")
        orow.addWidget(key_edit); orow.addWidget(val_edit, 1); orow.addWidget(set_btn)
        v.addLayout(orow)
        hint = QLabel("Core values: role∈{image,mask,bead_stack,host_mask,roi,"
                      "annotation,result}, modality∈{fluorescence,brightfield}, "
                      "dimensionality∈{2d,2d+t,z-stack,multi-position}, "
                      "scale∈{calibrated,uncalibrated}. Free keys allowed as "
                      "'user:name'.")
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color:#999; font-size:10px; }")
        v.addWidget(hint)

        def _current_layer():
            nm = picker.currentText()
            return self.viewer.layers[nm] if nm in self.viewer.layers else None

        def _refresh_table():
            lyr = _current_layer()
            table.setRowCount(0)
            if lyr is None:
                lineage_lbl.setText(""); return
            tags = _LT.get_tags(lyr)
            table.setRowCount(len(tags))
            for i, t in enumerate(tags):
                src = t.get('source', '')
                for j, key in enumerate(('key', 'value', 'source', 'confidence')):
                    val = t.get(key, '')
                    if key == 'confidence' and isinstance(val, (int, float)):
                        val = f"{val:.2f}"
                    it = QTableWidgetItem(str(val))
                    # Colour user_set rows so overrides are obvious.
                    if src == 'user_set':
                        from PyQt5.QtGui import QColor
                        it.setForeground(QColor('#c8102e'))
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    table.setItem(i, j, it)
            # Lineage.
            edges = _LT.get_edges(lyr)
            if edges:
                # map target ids -> names via the session index
                idx = _LT.rebuild_index(self.viewer)
                def _nm(tid):
                    e = idx.get(tid)
                    return (e.get('name') if e else tid) or tid
                parts = [f"{e['relation']} → {_nm(e['target'])}"
                         + (f" (via {e['via']})" if e.get('via') else "")
                         for e in edges]
                lineage_lbl.setText("Lineage: " + "; ".join(parts))
            else:
                lineage_lbl.setText("Lineage: (none — this layer is not derived "
                                    "from another)")

        def _apply_override():
            lyr = _current_layer()
            if lyr is None:
                return
            k = key_edit.currentText().strip()
            val = val_edit.text().strip()
            if not k or not val:
                return
            ok = _LT.set_user_tag(lyr, k, val)
            if not ok:
                from napari.utils.notifications import show_warning
                show_warning(f"Could not set {k}={val} (not a valid controlled "
                             f"value for core key '{k}').")
            val_edit.clear()
            _refresh_table()

        picker.currentIndexChanged.connect(lambda *_: _refresh_table())
        set_btn.clicked.connect(_apply_override)
        _refresh_table()
        dlg.exec_()

    def open_command_palette(self, *_):
        """Open a fuzzy-search command palette (Ctrl+Shift+P).

        Phase 1: find and launch any analysis method / toolbox function by name
        (over the flat registry accumulated in _add_actions_to_menu).
        Phase 2: find and select a layer by name (over viewer.layers).

        A single filterable list mixes both; picking a command runs it, picking a
        layer selects + reveals it. Deliberately scoped to these two easy wins;
        finding a step *within* a widget (phase 3) needs step-addressing infra
        and is left for later.
        """
        try:
            from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLineEdit,
                                         QListWidget, QListWidgetItem)
            from PyQt5.QtCore import Qt
        except Exception:
            return

        registry = getattr(self, '_command_registry', {}) or {}

        # Build the combined entry list: (label, kind, payload).
        entries = []
        for name, (method, kwargs, group) in registry.items():
            label = f"{name}"
            if group:
                label = f"{name}   ·   {group}"
            entries.append((label, 'command', (method, kwargs)))
        try:
            for lyr in self.viewer.layers:
                entries.append((f"{lyr.name}   ·   layer", 'layer', lyr.name))
        except Exception:
            pass

        dlg = QDialog(self.viewer.window._qt_window)
        dlg.setWindowTitle("Command Palette")
        dlg.setMinimumWidth(480)
        v = QVBoxLayout(dlg)
        box = QLineEdit(); box.setPlaceholderText(
            "Type to search methods, toolbox functions, and layers…")
        lst = QListWidget()
        v.addWidget(box); v.addWidget(lst)

        def _score(query, label):
            # Rank match QUALITY, not just yes/no, so the best match sorts first.
            q = query.lower().replace(' ', ''); s = label.lower()
            if not q:
                return 0.0
            # Strong signal: contiguous substring — score high, higher the earlier
            # it appears (a layer named "Bead Detections" should beat a command
            # that merely contains b-e-a-d scattered through it).
            idx = s.find(q)
            if idx >= 0:
                return 1000.0 - idx - 0.1 * len(s)
            # Fallback: subsequence match (all query chars in order). Lower band,
            # rewarding an early start and few gaps.
            i = 0; first = -1; last = -1; gaps = 0
            for pos, ch in enumerate(s):
                if i < len(q) and ch == q[i]:
                    if first < 0:
                        first = pos
                    if last >= 0 and pos - last > 1:
                        gaps += 1
                    last = pos; i += 1
            if i != len(q):
                return -1.0
            return 500.0 - first - 5.0 * gaps - 0.1 * len(s)

        def _refresh():
            query = box.text()
            lst.clear()
            scored = []
            for label, kind, payload in entries:
                sc = _score(query, label)
                if sc >= 0:
                    scored.append((sc, label, kind, payload))
            # Best match first (score desc); commands before layers on ties, then
            # alphabetical for a stable feel.
            scored.sort(key=lambda x: (-x[0], x[2] == 'layer', x[1].lower()))
            for _sc, label, kind, payload in scored:
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, (kind, payload))
                lst.addItem(it)
            if lst.count():
                lst.setCurrentRow(0)

        def _activate(item=None):
            item = item or lst.currentItem()
            if item is None:
                return
            kind, payload = item.data(Qt.UserRole)
            dlg.accept()
            try:
                if kind == 'command':
                    method, kwargs = payload
                    if kwargs:
                        method(**kwargs)
                    else:
                        method()
                elif kind == 'layer':
                    lyr = self.viewer.layers[payload]
                    self.viewer.layers.selection = {lyr}
                    try:
                        self.viewer.layers.move(
                            self.viewer.layers.index(lyr),
                            len(self.viewer.layers) - 1)
                    except Exception:
                        pass
            except Exception as _e:
                try:
                    from napari.utils.notifications import show_warning
                    show_warning(f"Command palette: could not run '{payload}': {_e}")
                except Exception:
                    print(f"[PyCAT palette] failed: {_e}")

        box.textChanged.connect(_refresh)
        lst.itemActivated.connect(_activate)

        # Enter activates the highlighted row; Down from the search box moves into
        # the list. Keep it keyboard-first like a real command palette.
        def _key(ev):
            from PyQt5.QtCore import Qt as _Qt
            if ev.key() in (_Qt.Key_Return, _Qt.Key_Enter):
                _activate(); return
            if ev.key() == _Qt.Key_Down and lst.count():
                lst.setFocus(); lst.setCurrentRow(min(1, lst.count() - 1)); return
            QLineEdit.keyPressEvent(box, ev)
        box.keyPressEvent = _key

        _refresh()
        box.setFocus()
        dlg.exec_()

    def _add_analysis_methods_to_menu(self):
        """
        Add specific analysis methods as actions to the analysis methods menu. 
        """
        # Imaging/morphometric pipelines — agnostic to whether the system has a
        # membrane (cellular or in vitro), hence "Condensate & Cell Analysis".
        condensate_cell_analysis_submenu = self.analysis_methods_menu.addMenu('Cell and Object Analyses')
        condensate_cell_analysis_dict = {
            'Cellular Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_condensate_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'In Vitro Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_invitro_fluor_analysis, {}),
            'In Vitro Object Analysis (Brightfield)': (self.central_manager.analysis_methods_ui._switch_to_invitro_bf_analysis, {}),
            'Time Series Cellular Object Analysis': (self.central_manager.analysis_methods_ui._switch_to_timeseries_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Time Series In Vitro Object Analysis (Fluorescence)': (self.central_manager.analysis_methods_ui._switch_to_ts_invitro_fluor_analysis, {}),
            'Z-Stack (3D) Object Analysis': (self.central_manager.analysis_methods_ui._switch_to_zstack_analysis, {}),
            'Cellular Fibril Analysis': (self.central_manager.analysis_methods_ui._switch_to_fibril_analysis_cellulo, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'In Vitro Fibril Analysis': (self.central_manager.analysis_methods_ui._switch_to_fibril_analysis_vitro, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
        }
        self._add_actions_to_menu(condensate_cell_analysis_dict, condensate_cell_analysis_submenu)

        # Biophysics pipelines — dynamics, material properties, and single-tether
        # force measurements.
        biophysics_submenu = self.analysis_methods_menu.addMenu('Biophysics')
        biophysics_dict = {
            'Video Particle Tracking (Microrheology)': (self.central_manager.analysis_methods_ui._switch_to_vpt_analysis, {}),
            'FRAP (Fluorescence Recovery)': (self.central_manager.analysis_methods_ui._switch_to_frap_analysis, {}),
            'Droplet Fusion (C-Trap)': (self.central_manager.analysis_methods_ui._switch_to_fusion_analysis, {}),
            'Temperature-Dependent Microscopy': (self.central_manager.analysis_methods_ui._switch_to_temperature_analysis, {}),
            'Force-Distance Curve (DNA Tethering)': (self.central_manager.analysis_methods_ui._switch_to_fd_curve_analysis, {}),
        }
        self._add_actions_to_menu(biophysics_dict, biophysics_submenu)

        coloc_analysis_submenu = self.analysis_methods_menu.addMenu('Colocalization Analysis')
        coloc_analysis_actions = {
            'Colocalization Analysis (Pixel-wise + Object-based)': (self.central_manager.analysis_methods_ui._switch_to_coloc_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Colocalization Over Time (time-series)': (self.central_manager.toolbox_functions_ui._add_run_pwcca, {'separate_widget': True}),
        }
        self._add_actions_to_menu(coloc_analysis_actions, coloc_analysis_submenu)

        analysis_methods_dict = {
            # Data QC is the FIRST thing you do to a dataset — is it in focus, drifting, bleaching,
            # a real time series? It belongs at the top level of Analysis Methods, not tucked inside
            # Toolbox → Data Visualization where it was hard to find and conceptually misfiled.
            'Data Quality Control': (self.central_manager.toolbox_functions_ui._add_data_qc, {'separate_widget': True}),
            'Exploratory Analysis': (self.central_manager.analysis_methods_ui._switch_to_general_analysis, {'base_data_repository': self.central_manager.active_data_class.data_repository}),
            'Comparative Figures (batch consolidated table)': (lambda: __import__('pycat.ui.comparative_figures_ui', fromlist=['f']).open_comparative_figures_dialog(self.central_manager, self.viewer), {}),
        }
        self._add_actions_to_menu(analysis_methods_dict, self.analysis_methods_menu)

    # Add specific toolbox functions as actions to the toolbox menu.
    def _add_toolbox_to_menu(self):
        """
        Add indiviudal toolbox functions as actions to the toolbox functions menu. They are organized into sub-menus based on their functionality.
        """
        # Add functions to the main toolbox menu
        toolbox_actions = {
            'Measure Object Diameters': (self.central_manager.toolbox_functions_ui._add_measure_line, {'separate_widget': True})
        }
        self._add_actions_to_menu(toolbox_actions, self.toolbox_menu)

        # Create sub-menu for image processing functions
        image_processing_submenu = self.toolbox_menu.addMenu('Image Processing')
        image_processing_actions = {
            'Pre-Process Image': (self.central_manager.toolbox_functions_ui._add_pre_process, {'separate_widget': True}),
            'Reference / Background Subtraction': (self.central_manager.toolbox_functions_ui._add_run_reference_subtraction, {'separate_widget': True}),
            'Image Registration (subpixel)': (self.central_manager.toolbox_functions_ui._add_image_registration, {'separate_widget': True}),  # general techniques promoted out of single-method pipelines
            'Photobleach Correction': (self.central_manager.toolbox_functions_ui._add_bleach_correction, {'separate_widget': True}),
            'Detrend Stack (drift / bleaching)': (self.central_manager.toolbox_functions_ui._add_detrend_stack, {'separate_widget': True}),
            'Pipeline Step Diagnostics': (self.central_manager.toolbox_functions_ui._add_pipeline_diagnostics, {'separate_widget': True}),
            'Pipeline SNR Analysis': (self.central_manager.toolbox_functions_ui._add_pipeline_snr_analysis, {'separate_widget': True}),
            'Foreground Suppression Tuner': (self.central_manager.toolbox_functions_ui._add_foreground_suppression_tuner, {'separate_widget': True}),
            'Temporal Enhancement Optimizer': (self.central_manager.toolbox_functions_ui._add_temporal_enhancement_optimizer, {'separate_widget': True}),
            'Segmentation Benchmark': (self.central_manager.toolbox_functions_ui._add_segmentation_benchmark, {'separate_widget': True}),
            'Control Validation (positive/negative)': (self.central_manager.toolbox_functions_ui._add_control_validation, {'separate_widget': True}),
            'Segmentation Speed Comparison': (self.central_manager.toolbox_functions_ui._add_segmentation_speed_comparison, {'separate_widget': True}),
            'Chromatin Topology Map': (self.central_manager.toolbox_functions_ui._add_chromatin_topology, {'separate_widget': True}),
            'Nucleolus / Void Estimator': (self.central_manager.toolbox_functions_ui._add_nucleolus_void_estimator, {'separate_widget': True}),
            'Display Diagnostics': (self.central_manager.toolbox_functions_ui._add_display_diagnostics, {'separate_widget': True}),
        }
        self._add_actions_to_menu(image_processing_actions, image_processing_submenu)

        # Create sub-sub-menu for image adjustment functions
        image_adjustments_sub_submenu = image_processing_submenu.addMenu('Image Adjustments')
        image_adjustment_actions = {
            'Rescale Intensity': (self.central_manager.toolbox_functions_ui._add_run_apply_rescale_intensity, {'separate_widget': True}),
            'Invert Image': (self.central_manager.toolbox_functions_ui._add_run_invert_image, {'separate_widget': True}),
            'Upscale Image': (self.central_manager.toolbox_functions_ui._add_run_upscaling, {'separate_widget': True})
        }
        self._add_actions_to_menu(image_adjustment_actions, image_adjustments_sub_submenu)

        # Stack / time-series variants of the image-processing tools. These
        # operate on a whole (T, H, W) stack rather than a single 2-D frame. They
        # previously existed only inside the Time-Series Condensate pipeline even
        # though they're general-purpose, so they're surfaced here too.
        stack_tools_submenu = image_processing_submenu.addMenu('Stack / Time-Series Tools')
        stack_tools_actions = {
            'Upscale Stack': (self.central_manager.toolbox_functions_ui._add_ts_upscale_stack, {'separate_widget': True}),
            'Pre-Process Stack (lazy)': (self.central_manager.toolbox_functions_ui._add_lazy_preprocess_stack, {'separate_widget': True}),
            'Cellpose Segmentation (stack)': (self.central_manager.toolbox_functions_ui._add_run_ts_cellpose, {'separate_widget': True}),
        }
        self._add_actions_to_menu(stack_tools_actions, stack_tools_submenu)
        # Create sub-sub-menu for background and noise correction functions
        background_noise_correction_submenu = image_processing_submenu.addMenu('Background and Noise Correction')
        background_noise_correction_actions = {
            'Spectral / Bleed-through Unmixing (2–4 channels)': (self.central_manager.toolbox_functions_ui._add_run_spectral_unmixing, {'separate_widget': True}),
            'Rolling-Ball Gaussian Background Removal': (self.central_manager.toolbox_functions_ui._add_run_rb_gaussian_background_removal, {'separate_widget': True}),
            'Background Removal w/ Edge Enhancement': (self.central_manager.toolbox_functions_ui._add_run_enhanced_rb_gaussian_bg_removal, {'separate_widget': True}),
            'Calibration Correction (flat-field / clear-frame)': (self.central_manager.toolbox_functions_ui._add_run_calibration_correction, {'separate_widget': True}),
            'Wavelet BG and Noise Subtraction': (self.central_manager.toolbox_functions_ui._add_run_wbns, {'separate_widget': True}),
            'Wavelet Noise Reduction': (self.central_manager.toolbox_functions_ui._add_run_wavelet_noise_subtraction, {'separate_widget': True}), 
            'Bilateral Noise Reduction': (self.central_manager.toolbox_functions_ui._add_run_apply_bilateral_filter, {'separate_widget': True}),
        }
        self._add_actions_to_menu(background_noise_correction_actions, background_noise_correction_submenu)

        # Create sub-sub-menu for image enhancement and filter functions
        enhancements_and_filters_submenu = image_processing_submenu.addMenu('Enhancements and Filters')
        enhancements_and_filters_actions = {
            'CLAHE': (self.central_manager.toolbox_functions_ui._add_run_clahe, {'separate_widget': True}),
            'Peak and Edge Enhancement': (self.central_manager.toolbox_functions_ui._add_run_peak_and_edge_enhancement, {'separate_widget': True}),
            'Morphological Gaussian Filter': (self.central_manager.toolbox_functions_ui._add_run_morphological_gaussian_filter, {'separate_widget': True}),
            'LoG Filter': (self.central_manager.toolbox_functions_ui._add_run_apply_laplace_of_gauss_filter, {'separate_widget': True}),            
            'Deblur by Pixel Reassignment': (self.central_manager.toolbox_functions_ui._add_run_dpr, {'separate_widget': True}),
            'FFT Bandpass Filter': (self.central_manager.toolbox_functions_ui._add_run_fft_bandpass, {'separate_widget': True}),
        }
        self._add_actions_to_menu(enhancements_and_filters_actions, enhancements_and_filters_submenu)

        # Create a sub-menu for segmentation functions
        image_segmentation_submenu = self.toolbox_menu.addMenu('Image Segmentation')
        image_segmentation_actions = {
            'Local Thresholding': (self.central_manager.toolbox_functions_ui._add_run_local_thresholding, {'separate_widget': True}),
            'Manual Threshold (im2bw)': (self.central_manager.toolbox_functions_ui._add_run_im2bw, {'separate_widget': True}),
            'Cellpose Segmentation': (self.central_manager.toolbox_functions_ui._add_run_cellpose_segmentation, {'separate_widget': True}),
            'Felzenszwalb Segmentation and Region Merging': (self.central_manager.toolbox_functions_ui._add_run_fz_segmentation_and_merging, {'separate_widget': True}),
            'Gaussian Spot Localization': (self.central_manager.toolbox_functions_ui._add_gaussian_localization, {'separate_widget': True}),
            'Contrast Cascade (bright body + dim fibers)': (self.central_manager.toolbox_functions_ui._add_contrast_cascade, {'separate_widget': True})
        }
        self._add_actions_to_menu(image_segmentation_actions, image_segmentation_submenu)

        # Create a sub-menu for Label and Mask Tools
        label_and_mask_tools_submenu = self.toolbox_menu.addMenu('Label and Mask Tools')

        # Create a sub-sub-menu for binary mask tools
        mask_tools_sub_submenu = label_and_mask_tools_submenu.addMenu('Binary Mask Tools')
        mask_tools_actions = {
            'Binary Morphological Operations': (self.central_manager.toolbox_functions_ui._add_run_binary_morph_operation, {'separate_widget': True}),
            'Measure Binary Mask': (self.central_manager.toolbox_functions_ui._add_run_measure_binary_mask, {'separate_widget': True}),
            'Label Binary Mask': (self.central_manager.toolbox_functions_ui._add_run_label_binary_mask, {'separate_widget': True})
        }
        self._add_actions_to_menu(mask_tools_actions, mask_tools_sub_submenu)
        
        # Create a sub-sub-menu for labeled mask tools
        label_tools_sub_submenu = label_and_mask_tools_submenu.addMenu('Labeled Mask Tools')   
        label_tools_actions = {
            'Label Updater': (self.central_manager.toolbox_functions_ui._add_run_update_labels, {'separate_widget': True}),
            'Convert Labels to Mask': (self.central_manager.toolbox_functions_ui._add_run_convert_labels_to_mask, {'separate_widget': True}),
            'Expand Labels': (self.central_manager.toolbox_functions_ui._add_run_expand_labels, {'separate_widget': True}),
            'Measure Region Properties': (self.central_manager.toolbox_functions_ui._add_run_measure_region_props, {'separate_widget': True})
        }
        self._add_actions_to_menu(label_tools_actions, label_tools_sub_submenu)

        # Create a sub-menu for layer operations    
        layer_operations_submenu = self.toolbox_menu.addMenu('Layer Operations')
        layer_operations_actions = {
            'Simple Multi-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_simple_multi_merge, {'separate_widget': True}),
            'Advanced 2-Layer Merge': (self.central_manager.toolbox_functions_ui._add_run_advanced_two_layer_merge, {'separate_widget': True}),
            'Mask Operations (AND/OR/XOR)': (self.central_manager.toolbox_functions_ui._add_run_mask_logic_merge, {'separate_widget': True})
        }
        self._add_actions_to_menu(layer_operations_actions, layer_operations_submenu)

        # Create a sub-menu for colocalization tools
        colocalization_tools_submenu = self.toolbox_menu.addMenu('Colocalization/Correlation')
        autocorrelation_actions = {
            'Auto-Correlation Function Analysis': (self.central_manager.toolbox_functions_ui._add_run_autocorrelation_analysis, {'separate_widget': True}),
            'Client Partition / Enrichment': (self.central_manager.toolbox_functions_ui._add_client_enrichment, {'separate_widget': True})
        }
        
        self._add_actions_to_menu(autocorrelation_actions, colocalization_tools_submenu)

        # Create a sub-sub-menu for pixel wise correlation analysis tools
        pixel_coloc_tools_sub_submenu = colocalization_tools_submenu.addMenu('Pixel-Wise Correlation Analysis')
        pixel_coloc_tools_actions = {
            'Pixel-Wise Correlation Coefficient Analysis': (self.central_manager.toolbox_functions_ui._add_run_pwcca, {'separate_widget': True}),
            'Cross-Correlation Function Analysis': (self.central_manager.toolbox_functions_ui._add_run_ccf_analysis, {'separate_widget': True})
        }
        self._add_actions_to_menu(pixel_coloc_tools_actions, pixel_coloc_tools_sub_submenu)

        # Create a sub-sub-menu for object based colocalization analysis tools
        obj_coloc_tools_sub_submenu = colocalization_tools_submenu.addMenu('Object-Based Colocalization Analysis')
        obj_coloc_tools_actions = {
            'Object Based Colocalization Analysis': (self.central_manager.toolbox_functions_ui._add_run_obca, {'separate_widget': True}),
            'Manders Colocalization Coefficient': (self.central_manager.toolbox_functions_ui._add_run_manders_coloc, {'separate_widget': True}),
            # Two-channel condensate coloc was only reachable from inside the
            # Colocalization Analysis pipeline, even though its siblings (OBCA,
            # Manders) are standalone tools here. Surfaced for consistency.
            'Two-Channel Condensate Colocalization': (self.central_manager.toolbox_functions_ui._add_run_two_channel_coloc, {'separate_widget': True}),
        }
        self._add_actions_to_menu(obj_coloc_tools_actions, obj_coloc_tools_sub_submenu)

        # ── Cell and Object Analyses ───────────────────────────────────────────
        condensate_analysis_submenu = self.toolbox_menu.addMenu('Cell and Object Analyses')
        condensate_analysis_actions = {
            'Cell Analyzer': (self.central_manager.toolbox_functions_ui._add_run_cell_analysis_func, {'separate_widget': True}),
            'Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_run_segment_subcellular_objects, {'separate_widget': True}),
            'Condensate Analyzer': (self.central_manager.toolbox_functions_ui._add_run_puncta_analysis_func, {'separate_widget': True}),
            # Measure objects segmented on an UPSCALED image using the ORIGINAL
            # pixels (partial-volume weighting). Reading intensities off
            # interpolated pixels pseudoreplicates the statistics and biases small
            # objects; this is the defensible path.
            'Partial-Volume Measurement (measure on original pixels)': (self.central_manager.toolbox_functions_ui._add_partial_volume_measure, {'separate_widget': True}),
        }
        self._add_actions_to_menu(condensate_analysis_actions, condensate_analysis_submenu)

        # ── Spatial Metrology ──────────────────────────────────────────────────
        spatial_metrology_submenu = self.toolbox_menu.addMenu('Spatial Metrology')
        spatial_metrology_actions = {
            'Per-Cell Spatial ACF Analysis': (self.central_manager.toolbox_functions_ui._add_run_sacf_analysis, {'separate_widget': True}),
            'Spatial Metrology (NND, Ripley, Voronoi…)': (self.central_manager.toolbox_functions_ui._add_spatial_metrology, {'separate_widget': True}),
            'Spatial Randomness (noise vs. clustering)': (self.central_manager.toolbox_functions_ui._add_spatial_randomness, {'separate_widget': True}),
            'Intensity Profiles (line / radial)': (self.central_manager.toolbox_functions_ui._add_intensity_profile, {'separate_widget': True}),
            'Morphological Complexity (fractal, lacunarity…)': (self.central_manager.toolbox_functions_ui._add_morphological_complexity, {'separate_widget': True}),
            'Fibril Analysis (beads, morphometry, graph)': (self.central_manager.toolbox_functions_ui._add_fibril_analysis, {'separate_widget': True}),
        }
        self._add_actions_to_menu(spatial_metrology_actions, spatial_metrology_submenu)

        # ── Advanced Analysis ──────────────────────────────────────────────────
        advanced_analysis_submenu = self.toolbox_menu.addMenu('Advanced Analysis')
        advanced_analysis_actions = {
            'Dynamic Spatial Phenotyping / Tracking': (self.central_manager.toolbox_functions_ui._add_advanced_analysis, {'separate_widget': True}),
            'Condensate Biophysics (MSD, C_sat, Kinetics…)': (self.central_manager.toolbox_functions_ui._add_condensate_physics, {'separate_widget': True}),
        }
        self._add_actions_to_menu(advanced_analysis_actions, advanced_analysis_submenu)

        # ── Molecular Counting (quantitative density / stoichiometry) ───────────
        molecular_counting_submenu = advanced_analysis_submenu.addMenu('Molecular Counting')
        molecular_counting_actions = {
            'Photobleaching Step Counting': (self.central_manager.toolbox_functions_ui._add_molecular_counting, {'separate_widget': True}),
            'SpIDA (density & oligomeric state)': (self.central_manager.toolbox_functions_ui._add_spida, {'separate_widget': True}),
            'Number & Brightness (camera / time-series)': (self.central_manager.toolbox_functions_ui._add_number_and_brightness, {'separate_widget': True}),
        }
        self._add_actions_to_menu(molecular_counting_actions, molecular_counting_submenu)

        # ── Brightfield Tools ──────────────────────────────────────────────────
        brightfield_submenu = self.toolbox_menu.addMenu('Brightfield Tools')
        brightfield_actions = {
            'BF Preprocessing (flat-field, halo, CLAHE)': (self.central_manager.toolbox_functions_ui._add_bf_preprocessing, {'separate_widget': True}),
            'BF Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_bf_condensate_segmentation, {'separate_widget': True}),
            'BF Optical Density Metrics': (self.central_manager.toolbox_functions_ui._add_bf_od_metrics, {'separate_widget': True}),
            'BF Per-Cell Summary': (self.central_manager.toolbox_functions_ui._add_bf_per_cell_summary, {'separate_widget': True}),
            'BF Spatial Metrology': (self.central_manager.toolbox_functions_ui._add_bf_spatial, {'separate_widget': True}),
            'BF Dynamics': (self.central_manager.toolbox_functions_ui._add_bf_dynamics, {'separate_widget': True}),
            'BF Texture Analysis': (self.central_manager.toolbox_functions_ui._add_bf_texture, {'separate_widget': True}),
            'BF Frame Quality': (self.central_manager.toolbox_functions_ui._add_bf_frame_qc, {'separate_widget': True}),
        }
        self._add_actions_to_menu(brightfield_actions, brightfield_submenu)

        # ── Z-Stack (3D) Tools ─────────────────────────────────────────────────
        zstack_submenu = self.toolbox_menu.addMenu('Z-Stack (3D) Tools')
        zstack_actions = {
            '3D Background Removal': (self.central_manager.toolbox_functions_ui._add_zstack_bg_removal, {'separate_widget': True}),
            '3D Cell Segmentation': (self.central_manager.toolbox_functions_ui._add_zstack_cell_seg, {'separate_widget': True}),
            '3D Condensate Segmentation': (self.central_manager.toolbox_functions_ui._add_zstack_condensate_seg, {'separate_widget': True}),
            '3D Condensate Metrics': (self.central_manager.toolbox_functions_ui._add_zstack_metrics, {'separate_widget': True}),
            'Best Slice Selector': (self.central_manager.toolbox_functions_ui._add_run_best_slice, {'separate_widget': True}),
        }
        self._add_actions_to_menu(zstack_actions, zstack_submenu)

        # ── Data Visualization ─────────────────────────────────────────────────
        data_visualization_submenu = self.toolbox_menu.addMenu('Data Visualization')
        data_visualization_actions = {
            'Plotting Widget': (self.central_manager.toolbox_functions_ui._add_plotting_widget, {'separate_widget': True}),
            # (Data Quality Control moved to the top level of Analysis Methods — see there.)
            # Per-frame focus/entropy/out-of-focus scoring for ANY stack (was
            # reachable only from the temperature + brightfield workflows).
            'Frame Quality / Focus QC': (self.central_manager.toolbox_functions_ui._add_frame_quality_qc, {'separate_widget': True}),
            # "How far do my objects move per frame, and can I even track them?"
            # — measured from a time-projection with no linking pass. Was locked
            # inside VPT as estimate_linking_distance_um.
            'Motion Scale Estimator (linking distance)': (self.central_manager.toolbox_functions_ui._add_motion_scale_estimator, {'separate_widget': True}),
            # Video export works on any time-series stack, not just the
            # time-series condensate pipeline it was previously locked inside.
            'Export Time-Series Video': (self.central_manager.toolbox_functions_ui._add_export_timeseries_video, {'separate_widget': True}),
        }
        self._add_actions_to_menu(data_visualization_actions, data_visualization_submenu)
