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
        from PyQt5.QtWidgets import QTextEdit, QAbstractSpinBox
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

    def _hide_napari_native_menus(self, *args, **kwargs):
        """Extracted to src/pycat/ui/napari_menus.py (ui_decomposition)."""
        from pycat.ui.napari_menus import _hide_napari_native_menus as _impl
        return _impl(self, *args, **kwargs)

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

    def _disable_napari_open_actions(self, *args, **kwargs):
        """Extracted to src/pycat/ui/napari_menus.py (ui_decomposition)."""
        from pycat.ui.napari_menus import _disable_napari_open_actions as _impl
        return _impl(self, *args, **kwargs)

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


    def _home_fit_view(self, *args, **kwargs):
        """Extracted (ui_decomposition). *args/**kwargs absorb Qt's `checked` bool — _impl takes only self."""
        from pycat.ui.viewer_actions import _home_fit_view as _impl
        return _impl(self)

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

    def _process_foreign_layers(self, *args, **kwargs):
        """Extracted to src/pycat/ui/viewer_actions.py (ui_decomposition)."""
        from pycat.ui.viewer_actions import _process_foreign_layers as _impl
        return _impl(self, *args, **kwargs)

    def _show_metadata_dialog(self, *args, **kwargs):
        """Per-file metadata dialog. *args/**kwargs absorb Qt's `checked` bool — _impl takes only self."""
        from pycat.ui.metadata_dialogs import _show_metadata_dialog as _impl
        return _impl(self)

    def _load_discovered_session(self, *args, **kwargs):
        """Extracted to src/pycat/ui/session_loader.py (ui_decomposition)."""
        from pycat.ui.session_loader import _load_discovered_session as _impl
        return _impl(self, *args, **kwargs)

    def _open_session_loader(self, *args, **kwargs):
        """Extracted to src/pycat/ui/session_loader.py (ui_decomposition)."""
        from pycat.ui.session_loader import _open_session_loader as _impl
        return _impl(self, *args, **kwargs)

    def _show_recorded_steps_dialog(self, *args, **kwargs):
        """Extracted (ui_decomposition). *args/**kwargs absorb Qt's `checked` bool — _impl takes only self."""
        from pycat.ui.recorded_steps_dialog import _show_recorded_steps_dialog as _impl
        return _impl(self)

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
        """Extracted to src/pycat/ui/grid_view.py (ui_decomposition)."""
        from pycat.ui.grid_view import _toggle_grid_view as _impl
        return _impl(self, *args, **kwargs)

    def _gather_compared_metadata(self, *args, **kwargs):
        """Extracted to src/pycat/ui/metadata_dialogs.py (ui_decomposition Part 2)."""
        from pycat.ui.metadata_dialogs import _gather_compared_metadata as _impl
        return _impl(self, *args, **kwargs)

    def _maybe_warn_metadata_diff(self, *args, **kwargs):
        """Extracted to src/pycat/ui/metadata_dialogs.py (ui_decomposition Part 2)."""
        from pycat.ui.metadata_dialogs import _maybe_warn_metadata_diff as _impl
        return _impl(self, *args, **kwargs)

    def _show_metadata_comparison(self, *args, **kwargs):
        """Extracted to src/pycat/ui/metadata_dialogs.py (ui_decomposition Part 2)."""
        from pycat.ui.metadata_dialogs import _show_metadata_comparison as _impl
        return _impl(self, *args, **kwargs)

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

    def _apply_managed_grid(self, *args, **kwargs):
        """Extracted to src/pycat/ui/grid_view.py (ui_decomposition)."""
        from pycat.ui.grid_view import _apply_managed_grid as _impl
        return _impl(self, *args, **kwargs)

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
        """Open the per-layer tag inspector (extracted to ui/tag_inspector.py)."""
        from pycat.ui.tag_inspector import open_tag_inspector as _impl
        return _impl(self)

    def open_command_palette(self, *_):
        """Open the fuzzy command palette (extracted to ui/command_palette.py)."""
        from pycat.ui.command_palette import open_command_palette as _impl
        return _impl(self)

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
        __import__('pycat.ui.custom_methods_menu', fromlist=['f']).install_custom_methods_submenu(self)  # Spec 2

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
            # Video export works on ANY time-series stack, not just the condensate pipeline it was locked inside.
            'Export Time-Series Video': (self.central_manager.toolbox_functions_ui._add_export_timeseries_video, {'separate_widget': True}),
        }
        self._add_actions_to_menu(data_visualization_actions, data_visualization_submenu)
