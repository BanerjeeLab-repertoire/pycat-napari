"""Shared UI base — ``BaseUIClass`` and the scroll-guard helpers, split out of ui_modules.py
(ui_decomposition).

``BaseUIClass`` is the foundation every PyCAT UI class inherits (``create_layer_dropdown``, the dock/layout
helpers, the scroll-area plumbing). It moves to its own **leaf** module FIRST — deviating from the spec's
"move the base last" ordering — so the subclass modules can import it without an import cycle back through
``ui_modules``. ``ui_modules`` re-exports everything here, so ``from pycat.ui.ui_modules import BaseUIClass``
(and ``guard_wheel`` etc.) still works.
"""
from __future__ import annotations

import napari
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import (QAction, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider,
                             QTabWidget, QToolButton, QVBoxLayout, QWidget)
from PyQt5.QtCore import Qt, QObject

from pycat.utils.general_utils import debug_log


class _WheelScrollGuard(QObject):
    """
    Event filter that stops the mouse wheel from changing spin box / slider /
    combo values unless the control has keyboard focus, and forwards the wheel
    event to the enclosing QScrollArea so the panel scrolls instead.

    This replaces the older instance-attribute `wheelEvent` patch, which does
    not work in PyQt5: Qt dispatches the C++ virtual `wheelEvent`, which never
    looks up a Python instance attribute, so the guard was silently bypassed
    (the control changed value AND swallowed the scroll).
    """
    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            from PyQt5.QtWidgets import QScrollArea, QApplication
            p = obj.parentWidget()
            while p is not None:
                if isinstance(p, QScrollArea):
                    QApplication.sendEvent(p.viewport(), event)
                    break
                p = p.parentWidget()
            return True   # consume: the control must not change its value
        return False


# Module-level singleton filter, held by this reference so it is never GC'd.
_GLOBAL_WHEEL_GUARD = None


def _wheel_guard():
    global _GLOBAL_WHEEL_GUARD
    if _GLOBAL_WHEEL_GUARD is None:
        _GLOBAL_WHEEL_GUARD = _WheelScrollGuard()
    return _GLOBAL_WHEEL_GUARD


def guard_wheel(control):
    """
    Install the wheel-scroll guard on a SINGLE control (spin box / slider /
    combo). Safe to call at widget-creation time, before the control is placed
    inside a QScrollArea — the enclosing scroll area is located at event time.
    """
    from PyQt5.QtCore import Qt
    if control is None or getattr(control, '_pycat_scroll_guard', False):
        return
    control.setFocusPolicy(Qt.StrongFocus)
    control.installEventFilter(_wheel_guard())
    control._pycat_scroll_guard = True




def _relax_min_widths(widget):
    """
    Recursively relax minimum widths so a dock's content can shrink to the dock
    width instead of being clipped when the horizontal scrollbar is disabled.

    Buttons, combo boxes, line edits and labels with long text otherwise report a
    wide minimum-size hint, forcing the row wider than the dock and pushing
    controls off the right edge. Setting a small minimum width and allowing labels
    to elide/wrap lets the layout compress gracefully. Call once on the root widget
    of any dock that lives inside a horizontal-scroll-disabled QScrollArea.
    """
    from PyQt5.QtWidgets import (QComboBox as _QCB, QLabel as _QLbl)
    for w in widget.findChildren((QPushButton, _QCB, QLineEdit)):
        try:
            w.setMinimumWidth(0)
            # Preferred (not Ignored): respects the size hint when there is room,
            # but allows shrinking below it when the dock is narrow, rather than
            # forcing the row wider than the dock and clipping.
            sp = w.sizePolicy()
            sp.setHorizontalPolicy(QSizePolicy.Preferred)
            w.setSizePolicy(sp)
        except Exception:
            pass
    for lbl in widget.findChildren(_QLbl):
        try:
            lbl.setMinimumWidth(0)
            lbl.setWordWrap(True)  # wrap long labels instead of forcing width
        except Exception:
            pass


def _apply_scroll_guard(widget):
    """
    Recursively install a wheel-scroll guard on all interactive controls
    (QComboBox, QAbstractSpinBox, QAbstractSlider) in a widget tree so that,
    inside a QScrollArea dock, hovering over a spin box / slider / combo while
    scrolling scrolls the panel instead of silently adjusting the control.

    Call once on the root widget of any dock that lives inside a QScrollArea.
    """
    from PyQt5.QtWidgets import (QAbstractSpinBox, QAbstractSlider,
                                  QComboBox as _QCB)
    controls = list(widget.findChildren((_QCB, QAbstractSpinBox, QAbstractSlider)))
    if isinstance(widget, (_QCB, QAbstractSpinBox, QAbstractSlider)):
        controls.insert(0, widget)
    for w in controls:
        guard_wheel(w)


class BaseUIClass:
    """
    A base UI class designed to provide utility functions for managing UI elements
    and interactions within a napari viewer instance. This class includes methods
    for creating dropdown menus for layer selection, updating these dropdowns based
    on viewer layer changes, handling button clicks, and managing dock widgets.

    Attributes
    ----------
    viewer : napari.Viewer
        The napari viewer instance with which the UI components will interact.
    """

    def __init__(self, viewer):
        """
        Initializes the BaseUIClass with a reference to the napari viewer instance.

        Parameters
        ----------
        viewer : napari.Viewer
            The napari viewer instance to interact with.
        """
        self.viewer = viewer

    def _hint_matches(self, name_hint, layer_name):
        """Return True if layer_name is the intended target of name_hint.

        Plain substring matching is wrong: a hint of 'Upscaled Fluorescence'
        substring-matches derived layers like 'Pre-Processed Upscaled
        Fluorescence Image' or 'Enhanced Background Removed Upscaled Fluorescence
        Image', causing a derived layer to auto-populate a dropdown that wants the
        plain upscaled image. We reject a match when the layer name carries an
        EXTRA leading modifier prefix that the hint does not — i.e. the layer is a
        more-derived version than the dropdown asked for. A hint that itself names
        the modifier still matches (e.g. hint 'Pre-Processed' matches the
        pre-processed layer).
        """
        if not name_hint:
            return False
        hl = name_hint.lower().strip()
        nl = layer_name.lower().strip()
        if hl not in nl:
            return False
        # Leading modifier prefixes that mark a DERIVED layer. Any of these at the
        # START of a layer name means the layer is a processed derivative; if the
        # hint doesn't itself mention the modifier, that layer is not what the
        # dropdown asked for. Longest-first so multi-word prefixes match.
        _modifiers = (
            'enhanced background removed',
            'background removed',
            'pre-processed',
            'preprocessed',
        )
        for mod in _modifiers:
            # If the layer name STARTS with this modifier but the hint neither
            # starts with nor contains it, the layer is a derived version the
            # dropdown didn't ask for — reject.
            if nl.startswith(mod) and mod not in hl:
                return False
        return True

    def create_layer_dropdown(self, layer_type, name_hint: str = '', binding: str = ''):
        """
        Creates a dropdown (QComboBox) widget that lists layers of a specific type.

        Parameters
        ----------
        layer_type : type
            The type of layer to list in the dropdown, e.g., napari.layers.Image
            or napari.layers.Labels.
        name_hint : str, optional
            A substring to match against layer names when auto-selecting after a
            new layer is inserted. When a new layer whose name contains name_hint
            is added to the viewer, this dropdown will automatically jump to it.
            This implements the "auto-populate the appropriate layer as it is
            generated" UX pattern for sequential pipelines: pass e.g.
            name_hint='BG-Removed' for a background-removal output dropdown,
            name_hint='Labeled Cell Mask' for a cell-mask dropdown, etc.
            If name_hint is empty (the default), no auto-selection occurs on
            insert — the dropdown stays on whatever the user last chose.
        binding : str, optional
            **The TAG-based way to say the same thing, and it is the stronger one.**

            ``name_hint`` matches a substring of a LAYER NAME. That works until someone renames a
            layer, or a new operation produces a name that happens to contain the same substring —
            and then it silently selects the wrong one. **It is matching a label, not a fact.**

            A ``binding`` names an entry in ``layer_bindings.json`` (e.g.
            ``'cell_segmentation.cell_labels'``), and the resolver finds the layer whose **TAGS**
            match: ``role=labels, target=cell``. That is a statement about what the layer IS, and
            it survives renaming, reordering, and a user who calls their mask "asdf".

            It also knows when it does not know. When several layers match and none is clearly
            right, **it selects nothing and says which ones matched** — because *a wrong
            auto-selection the user does not notice is worse than an empty dropdown: they run the
            analysis on the wrong layer, get a number, and never know.*

            ``name_hint`` still works, and is used when no binding is given.

        Returns
        -------
        dropdown : QComboBox
            The created dropdown widget populated with layers of the specified type.
        """
        dropdown = QComboBox()
        # Don't let long layer names balloon the dropdown (and the whole form)
        # past the dock width — size to a small minimum and let it shrink so the
        # right side of rows (spinbox controls, buttons) stays visible.
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        dropdown.setSizePolicy(_QSP.Ignored, _QSP.Fixed)
        try:
            dropdown.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            dropdown.setMinimumContentsLength(8)
        except Exception:
            pass
        # Prevent scroll wheel from accidentally changing the selection while
        # the user scrolls through the dock panel (event-filter based; the
        # older instance-attribute wheelEvent patch never fired under PyQt5).
        guard_wheel(dropdown)

        # The binding is what the dropdown WANTS, in tags. update_dropdown_items reads it.
        if binding:
            self.bind_dropdown(dropdown, binding)

        self.update_dropdown_items(dropdown, layer_type)

        def _on_inserted(event):
            # Performance: only rebuild if the inserted layer is of this
            # dropdown's type. Adding one Shapes ROI layer would otherwise
            # trigger a full rebuild of EVERY dropdown in the dock (dozens on the
            # time-series/condensate docks), each looping all layers — the cause
            # of the multi-second lag when clicking "Add ROI Drawing Layer" on a
            # large lazy IMS stack.
            try:
                inserted_layer = getattr(event, 'value', None)
                if inserted_layer is not None and not isinstance(inserted_layer, layer_type):
                    return
            except Exception:
                pass
            try:
                self.update_dropdown_items(dropdown, layer_type)
            except RuntimeError:
                return  # dropdown deleted
            # Auto-select: if a name_hint was given and the new layer matches,
            # switch to it so the user doesn't have to manually find it.
            if name_hint:
                try:
                    new_name = event.value.name if hasattr(event, 'value') else ''
                    if not new_name or not self._hint_matches(name_hint, new_name):
                        # fallback: most-recently added layer that truly matches
                        new_name = ''
                        for layer in reversed(self.viewer.layers):
                            if isinstance(layer, layer_type) and self._hint_matches(name_hint, layer.name):
                                new_name = layer.name
                                break
                    if new_name and self._hint_matches(name_hint, new_name):
                        idx = dropdown.findText(new_name)
                        if idx != -1:
                            dropdown.setCurrentIndex(idx)
                except RuntimeError:
                    return
                except Exception:
                    pass

        self.viewer.layers.events.inserted.connect(_on_inserted)
        _removed_handler = lambda e: self.update_dropdown_items(dropdown, layer_type)
        self.viewer.layers.events.removed.connect(_removed_handler)

        # Disconnect both viewer-level handlers when the dropdown is destroyed,
        # so a later insert/remove doesn't fire a callback that touches a deleted
        # QComboBox (RuntimeError: wrapped C/C++ object has been deleted).
        def _disconnect(*_):
            for _sig, _h in ((self.viewer.layers.events.inserted, _on_inserted),
                             (self.viewer.layers.events.removed, _removed_handler)):
                try:
                    _sig.disconnect(_h)
                except Exception:
                    pass
        try:
            dropdown.destroyed.connect(_disconnect)
        except Exception:
            pass
        return dropdown

    def update_dropdown_items(self, dropdown, layer_type):
        """
        Updates the items in the dropdown based on the current layers in the viewer that match the specified type.
        Optionally ensures a 'None' option is available in the dropdown.

        Preserves the user's current selection across rebuilds when possible —
        previously the dropdown silently reset to index 0 (the first/oldest
        layer) every time any layer was added or removed anywhere in the
        viewer, discarding the user's actual choice without any visual
        indication. This caused batch config recordings to capture stale
        defaults like "Segmentation Image" instead of the intended
        "Upscaled Segmentation Image" the user had selected.

        Parameters
        ----------
        dropdown : QComboBox
            The dropdown widget to update.
        layer_type : type
            The type of layer to include in the dropdown.
        """
        # Remember what was selected before rebuilding. If the dropdown's C++
        # object has already been deleted (its parent workflow was torn down but
        # a viewer-level layer signal still references it), bail out silently.
        try:
            previous_selection = dropdown.currentText()
        except RuntimeError:
            return

        # Check if 'None' option exists and store its state
        none_option_exists = dropdown.findText("None") != -1

        # Clear the dropdown and re-populate it
        dropdown.clear()
        for layer in self.viewer.layers:
            if isinstance(layer, layer_type):
                dropdown.addItem(layer.name)

        # Add 'None' option if it was present before
        if none_option_exists: #or dropdown.count() == 0:
            dropdown.insertItem(0, "None")

        # Restore the previous selection if it still exists among the
        # current layers; only fall back to the default (index 0) if the
        # previously selected layer was actually removed from the viewer.
        restored_index = dropdown.findText(previous_selection)
        if restored_index != -1:
            dropdown.setCurrentIndex(restored_index)
            return

        # ── AUTOPOPULATE — and NEVER over a choice the user already made ────────
        #
        # We only reach here when the previous selection is GONE (or there never was one). That
        # matters: the bug this function's docstring records — dropdowns silently resetting to the
        # first layer, so a batch config captured "Segmentation Image" instead of the user's
        # "Upscaled Segmentation Image" — is **exactly the bug autopopulation could reintroduce.**
        #
        # So the rule is absolute: **a restored selection wins, always.** Autopopulation fills a
        # dropdown that is EMPTY; it never overrides a decision.
        #
        # And it only fills on CERTAINTY. When several layers match, the resolver refuses to
        # choose and says which — because *a wrong auto-selection the user does not notice is
        # worse than an empty dropdown: they run the analysis on the wrong layer, get a number,
        # and never know.*
        binding_key = getattr(dropdown, '_pycat_binding', None)
        if not binding_key:
            return

        try:
            from pycat.utils.tag_resolver import autopopulate
            autopopulate(self.viewer, dropdown, binding_key)
        except Exception as exc:
            debug_log('update_dropdown_items: autopopulation failed', exc)

    def bind_dropdown(self, dropdown, binding_key):
        """**Declare what this dropdown wants**, and it will fill itself.

        ``binding_key`` names an entry in ``layer_bindings.json`` — e.g.
        ``'cell_segmentation.cell_labels'``. The resolver looks for a layer whose TAGS match, and
        fills the dropdown **only when exactly one does.**

        The binding is data, not code: which layer a step should want is a *scientific* judgement
        (does this want the raw image, or the filtered one?) and it will be revised as the
        workflows are curated. Changing it does not mean touching this UI.

        **A dropdown with no binding is simply not autopopulated**, which is the correct behaviour
        for any field whose right layer cannot be decided from tags alone. *Leaving it unbound is
        how that is said.*
        """
        try:
            dropdown._pycat_binding = str(binding_key)
        except Exception as exc:
            debug_log('bind_dropdown: could not attach the binding', exc)
        return dropdown

    def _consume_step_label(self):
        """Return the staged 'Step N — ' prefix (and clear it), or '' if none.
        For builders that render their title via QGroupBox(...) or a bare button
        rather than add_text_label(bold=True); they can prepend the returned
        string to their title so _stage_step works uniformly across mechanisms."""
        pending = getattr(self, '_pending_step_label', None)
        if pending:
            self._pending_step_label = None
            return pending
        return ''

    def _stage_step(self, step_label):
        """Stage a 'Step N — ' prefix to be prepended to the next shared widget
        builder's bold title. Set on the toolbox_functions_ui instance, since that
        is the object whose _add_* builders render the titles. No-op if the
        toolbox UI isn't available yet."""
        try:
            tfu = self.central_manager.toolbox_functions_ui
            tfu._pending_step_label = step_label
        except Exception:
            pass

    def add_text_label(self, layout, text, font_size=10, bold=False):
        """
        Adds a text label above a dropdown widget in the given layout, with an option to make the text bold.

        If a step label has been staged via ``self._pending_step_label`` (set by a
        workflow just before calling a shared widget builder), it is prepended to
        the FIRST bold label rendered and then cleared — this is how the built-in
        workflows enumerate shared widgets ("Step 4 — Pre-process image") without
        hardcoding a number into the reusable builder, since the same builder
        appears at different step numbers in different pipelines.

        Parameters
        ----------
        layout : QLayout
            The layout to which the label will be added.
        text : str
            The text of the label.
        font_size : int, optional
            The font size of the label text.
        bold : bool, optional
            If True, the label text will be bold.
        """
        stepped = False
        if bold:
            pending = getattr(self, '_pending_step_label', None)
            if pending:
                # Render the "Step N — " prefix in a stronger emphasis than the
                # title, and use rich text so the two weights show. The stepped
                # section titles also get a larger font so they read as primary
                # section headers (matching the Step 1 block), not sub-labels.
                prefix = pending.strip()
                # normalise trailing dash/spacing for consistent rendering
                title = text
                text = (f"<span style='font-weight:800;'>{prefix}</span> "
                        f"<span style='font-weight:600;'>{title}</span>")
                stepped = True
                self._pending_step_label = None
        label = QLabel(text)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        if stepped:
            label.setTextFormat(Qt.RichText)
            # 14px to match the QGroupBox::title size (Step 1's block), so all
            # section headers read at the same scale.
            label.setStyleSheet("font-size: 14px; margin-top: 4px;")
        else:
            # Conditionally set font-weight based on the `bold` argument
            font_weight = "bold" if bold else "normal"
            label.setStyleSheet(f"font-size: {font_size}px; font-weight: {font_weight};")
        layout.addWidget(label)


    def on_general_button_clicked(self, processing_function, viewer=None, *args, **kwargs):
        """
        A general-purpose method to be connected to button click signals. It extracts selected layers
        from dropdowns, filters out non-layer arguments, and calls a specified processing function with
        these layers and any additional arguments.

        Parameters
        ----------
        processing_function : callable
            The function to call with the extracted layers and additional arguments.
        viewer : napari.Viewer, optional
            The napari viewer instance, if not already provided as part of the class.
        """
        # Extract layers if viewer is provided in the first argument position
        if viewer:
            layers = []
            for dropdown in args:
                if isinstance(dropdown, QComboBox):
                    name = dropdown.currentText()
                    if name == "None" or not name:
                        layers.append(None)
                    elif name not in [l.name for l in viewer.layers]:
                        from napari.utils.notifications import show_warning as _warn
                        _warn(
                            f"Layer '{name}' not found in viewer. "                            f"The dropdown may be pointing to a layer that was "                            f"removed or renamed. Re-run the previous step or "                            f"select the correct layer from the dropdown.")
                        return
                    else:
                        layers.append(viewer.layers[name])
        else:
            layers = []

        # Filter out the dropdowns, so we don't pass them to the processing function
        non_dropdown_args = [arg for arg in args if not isinstance(arg, QComboBox)]

        # Call the processing function and time it for performance metrics
        import time
        import pandas as pd
        from pycat.data.data_modules import BaseDataClass
        t0 = time.perf_counter()
        try:
            processing_function(*layers, *non_dropdown_args, **kwargs)
        except Exception as _e:
            from napari.utils.notifications import show_warning as _warn
            import traceback as _tb
            _warn(f"Step failed: {type(_e).__name__}: {_e}\n"                  f"See terminal for details.")
            _tb.print_exc()
            return
        elapsed = time.perf_counter() - t0

        # Store timing in data_instance if one is present in the args
        data_instance = next(
            (a for a in non_dropdown_args if isinstance(a, BaseDataClass)), None
        )
        if data_instance is not None:
            step_name = getattr(processing_function, '__name__', str(processing_function))
            image_shape = str(layers[0].data.shape) if layers else ''
            new_row = pd.DataFrame([{
                'step': step_name,
                'elapsed_s': round(elapsed, 4),
                'image_shape': image_shape,
            }])
            if 'timing_df' not in data_instance.data_repository:
                data_instance.data_repository['timing_df'] = new_row
            else:
                data_instance.data_repository['timing_df'] = pd.concat(
                    [data_instance.data_repository['timing_df'], new_row],
                    ignore_index=True
                )
            print(f"[PyCAT Timing] {step_name}: {elapsed:.3f}s")

    def clear_dock(self):
        """
        Removes all dock widgets from the viewer's window.
        """
        # Remove all widgets from the dock. napari 0.7 renamed the private
        # `_dock_widgets` to the public `dock_widgets`; prefer the public API
        # and fall back to the old attribute for older napari versions.
        container = getattr(self.viewer.window, 'dock_widgets', None)
        if container is None:
            container = getattr(self.viewer.window, '_dock_widgets', {})
        try:
            dock_widgets = list(container.values())
        except AttributeError:
            dock_widgets = list(container)
        for dw in dock_widgets:
            self.viewer.window.remove_dock_widget(dw)

    def update_tool(self, event):
        """
        Updates the active tool based on the currently active layer. This could adjust brush sizes for label layers
        or switch modes for shape layers.

        Parameters
        ----------
        event : Event
            The event that triggered the tool update, not directly used.
        """
        active_layer = self.viewer.layers.selection.active
        if active_layer is None:
            return
        
        # Adjust the brush size for label layers and switch modes for shape layers
        if isinstance(active_layer, napari.layers.Labels):
            # Base brush size on the SPATIAL extent (last two dims), not shape[0]
            # — for a 3D (T/Z, H, W) mask shape[0] is the frame count, which for
            # a short stack gives 0 and makes napari divide-by-zero (NaN) on the
            # first paint click. Floor at 1 so the brush is always valid.
            spatial = active_layer.data.shape[-2:]
            active_layer.brush_size = max(1, max(spatial) // 150)
            active_layer.mode = 'paint'
            active_layer.selected_label = 1
        elif isinstance(active_layer, napari.layers.Shapes):
            active_layer.mode = 'add_line'

    def _add_widget_to_layout_or_dock(self, widget, layout, separate_widget, dock_name):
        """
        Adds a widget to the specified layout or creates a new dock widget for it, based on the provided parameters.

        Parameters
        ----------
        widget : QWidget
            The widget to add.
        layout : QLayout
            The layout to add the widget to if not creating a separate dock widget.
        separate_widget : bool
            If True, creates a separate dock widget for the widget.
        dock_name : str
            The name of the dock widget if creating a separate one.
        """
        if separate_widget==True:
            # Prevent opening a second instance of the same toolbox widget. If a
            # dock with this name is already open, warn (OK) and don't add another.
            # Uses the same public/fallback access pattern as clear_dock().
            container = getattr(self.viewer.window, 'dock_widgets', None)
            if container is None:
                container = getattr(self.viewer.window, '_dock_widgets', {})
            already_open = False
            # napari's dock_widgets is a dict keyed by dock name — check keys first.
            try:
                if dock_name in container:
                    already_open = True
            except Exception:
                pass
            if not already_open:
                try:
                    _dws = list(container.values())
                except AttributeError:
                    _dws = list(container)
                except Exception:
                    _dws = []
                for dw in _dws:
                    try:
                        name_attr = getattr(dw, 'name', None)
                        title = ''
                        if hasattr(dw, 'windowTitle'):
                            try:
                                title = dw.windowTitle()
                            except Exception:
                                title = ''
                        if name_attr == dock_name or title == dock_name:
                            already_open = True
                            break
                    except Exception:
                        continue
            if already_open:
                try:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(
                        None, "Already open",
                        f"\u201c{dock_name}\u201d is already open.\n\n"
                        "Close the existing one first if you want a fresh copy.",
                        QMessageBox.Ok)
                except Exception:
                    pass
                return
            # Create a new layout for the separate widget
            dock_layout = QVBoxLayout()
            dock_layout.addWidget(widget)
            
            # Create a main widget to contain the input widget
            main_widget = QWidget()
            main_widget.setLayout(dock_layout)
            # Allow the dock content to shrink to the dock width instead of forcing
            # a minimum width that gets clipped (horizontal scroll is disabled).
            # Matches the main analysis docks, which all set this.
            main_widget.setMinimumWidth(0)

            # Guard all spin boxes / sliders / combos in this widget against
            # accidental wheel-scroll value changes (covers every toolbox tool
            # that goes through this common docking path).
            try:
                _apply_scroll_guard(main_widget)
            except Exception:
                pass
            try:
                _relax_min_widths(main_widget)
            except Exception:
                pass

            # Add the main widget to the viewer as a dock widget, wrapped in a
            # scroll area whose horizontal scrollbar is disabled so content fits
            # the dock width (vertical scroll only) — consistent with the pipeline
            # docks and the separate workflow modules.
            try:
                _sa = QScrollArea()
                _sa.setWidgetResizable(True)
                _sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                _sa.setWidget(main_widget)
                self.viewer.window.add_dock_widget(_sa, name=dock_name)
            except Exception:
                self.viewer.window.add_dock_widget(main_widget, name=dock_name)
        else:        
            # Add the widget to the existing layout in the dock                    
            layout.addWidget(widget)
            layout.setContentsMargins(1, 1, 1, 1)
            try:
                _apply_scroll_guard(widget)
            except Exception:
                pass


    def _record(self, step_name, params):
        """Record a pipeline step to the BatchProcessor if one is attached."""
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record(step_name, params)

    def _layer_row(self, layout, label_text, layer_type, name_hint='',
                   optional=False, binding=''):
        """Add a status-circle + label + layer-dropdown row to *layout*, matching
        the field-status UEX from the temperature workflow. Returns the dropdown.
        Circle is red (required) or yellow (optional) until a real layer is
        selected, then turns green. 'None' placeholders stay red/yellow.
        ``binding`` forwards to create_layer_dropdown (tag-match autopopulate; see its docstring)."""
        from PyQt5.QtWidgets import QLabel, QWidget
        try:
            from pycat.ui.field_status import StatusCircle
        except Exception:
            dd = self.create_layer_dropdown(layer_type, name_hint, binding=binding)
            self.add_text_label(layout, label_text)
            layout.addWidget(dd)
            return dd
        circle = StatusCircle()
        init_color = 'yellow' if optional else 'red'
        init_tip = ('Optional — a default will be used.' if optional
                    else 'Required — select a layer to continue.')
        circle._set(init_color, init_tip)
        # Label on its own row (no marker), then the marker sits inline to the
        # LEFT of the dropdown it applies to.
        self.add_text_label(layout, label_text)
        dd = self.create_layer_dropdown(layer_type, name_hint, binding=binding)
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0); row_h.setSpacing(4)
        row_h.addWidget(circle)
        row_h.addWidget(dd, 1)
        layout.addWidget(row_w)
        # activated fires only on real user interaction (not programmatic setCurrentIndex
        # or the index-0 default), so it distinguishes a deliberate pick from a default.
        _user_picked = [False]
        def _mark_user_picked(*_):
            _user_picked[0] = True
        try:
            dd.activated.connect(_mark_user_picked)
        except Exception:
            pass

        def _update_circle(*_):
            # The layers.events.inserted signal (connected below) outlives this
            # widget: after a workflow is torn down and its dropdown deleted, a
            # later layer insertion would still fire this callback with a stale
            # `dd`, raising "wrapped C/C++ object of type QComboBox has been
            # deleted". Guard every access so a stale call is a harmless no-op.
            try:
                txt = (dd.currentText() or '').strip()
            except RuntimeError:
                return  # dd was deleted; nothing to update
            txt_l = txt.lower()
            is_placeholder = (not txt_l or txt_l.startswith(
                ('select', 'none', '--', '—', 'no ', 'choose')))
            try:
                if is_placeholder:
                    # Nothing chosen → back to the initial required/optional state.
                    circle._set(init_color, init_tip)
                    return
                # A real layer is selected. Distinguish:
                #   GREEN  — the selection matches the name hint (the auto-filled /
                #            suggested layer), or a required field with no hint is
                #            now satisfied.
                #   BLUE   — the user deliberately picked a non-suggested layer, OR
                #            an OPTIONAL field with no hint was set to a real value
                #            (i.e. changed away from its 'None'/default).
                if name_hint:
                    matches_hint = self._hint_matches(name_hint, txt)
                    if matches_hint:
                        circle._set('green', 'Done — using the suggested layer.')
                    elif _user_picked[0]:
                        circle._set('blue', 'Changed — you picked a different '
                                            'layer than the suggested one.')
                    else:
                        circle._set('green', 'Done — layer selected.')  # valid (non-hint) selection → satisfied, never red (Fix 4)
                else:
                    # No hint: a required field is simply satisfied (green); an
                    # optional field with a real value has been changed from its
                    # default (blue).
                    if optional:
                        circle._set('blue', 'Changed — you set this optional layer.')
                    else:
                        circle._set('green', 'Done — layer selected.')
            except RuntimeError:
                return
        dd.currentIndexChanged.connect(_update_circle)
        _update_circle()

        # Also re-evaluate when a new layer lands (auto-selection via name_hint
        # may not fire currentIndexChanged if the index doesn't change). This
        # connects to the viewer-level inserted signal, which outlives the
        # dropdown — so we disconnect it when the dropdown is destroyed to avoid
        # leaking stale callbacks (see the guard in _update_circle above).
        def _on_inserted_with_circle_refresh(event):
            # Only react to layers of this row's type (see perf note in
            # create_layer_dropdown._on_inserted).
            try:
                inserted_layer = getattr(event, 'value', None)
                if inserted_layer is not None and not isinstance(inserted_layer, layer_type):
                    return
            except Exception:
                pass
            try:
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, _update_circle)
            except Exception:
                _update_circle()

        self.viewer.layers.events.inserted.connect(_on_inserted_with_circle_refresh)

        def _disconnect_on_destroy(*_):
            try:
                self.viewer.layers.events.inserted.disconnect(
                    _on_inserted_with_circle_refresh)
            except Exception:
                pass
        try:
            dd.destroyed.connect(_disconnect_on_destroy)
        except Exception:
            pass
        return dd

    def _add_workflow_header(self, layout, include_pixel_gate=False,
                             instruction_html=None):
        """Add the Step 1 file-I/O status block to a workflow layout.
        The 'Image loaded' indicator turns green once a file is open.
        Pass include_pixel_gate=True only for imaging pipelines that need a
        physical pixel size (condensate, time-series, general, fibril analysis).
        Non-imaging workflows (FD-curve, Droplet Fusion, Force-Distance) omit it."""
        try:
            from pycat.ui.field_status import (
                FieldRegistry, add_step1_file_io, add_pixel_size_gate)
            reg = FieldRegistry()
            self._field_registry = reg
            add_step1_file_io(self.viewer, layout, reg,
                              instruction_html=instruction_html)
            if include_pixel_gate:
                def _on_px(v):
                    try:
                        reg.refresh()
                        self.central_manager.file_io._enable_auto_scale_bar()
                    except Exception:
                        pass
                _px_refresh = add_pixel_size_gate(
                    layout,
                    lambda: self.central_manager.active_data_class.data_repository,
                    on_set=_on_px, central_manager=self.central_manager)
                # Store the gate refresh (which carries a ._reset_gate) so Clear
                # can re-show the gate for the next dataset.
                try:
                    self.central_manager._pixel_gate_refresh = _px_refresh
                except Exception:
                    pass
                # The pixel-size gate only re-evaluated on field edit / data
                # switch, so its status marker went stale when an image loaded
                # (metadata scale detected) or the canvas was cleared. Wire its
                # refresh to layer insert/remove so it updates in lock-step with
                # the "Image loaded" marker.
                if callable(_px_refresh):
                    try:
                        self.viewer.layers.events.inserted.connect(
                            lambda e: _px_refresh())
                        self.viewer.layers.events.removed.connect(
                            lambda e: _px_refresh())
                    except Exception:
                        pass
        except Exception as _exc:
            # ── The PIXEL-SIZE GATE must never fail SILENTLY ────────────────────
            #
            # This block installs the gate — the thing that tells a user their lengths are in            # PIXELS because the metadata carried no resolution. **35 lines of it, including            # ``add_pixel_size_gate`` itself, were wrapped in ``except Exception: pass``.**
            #
            # So if ANYTHING in here threw, **the gate simply never appeared** — and the user            # got no warning at all. That is not hypothetical: the gate stopped firing once            # before (the 1.5.273-278 regression), and a silent handler is exactly why it took            # a bracketing hunt through git tags to find out why.
            #
            # **A guard that can vanish without saying so is not a guard.**
            debug_log('BaseUIClass: the pixel-size gate could NOT be installed', _exc)
            try:
                napari_show_warning(
                    'The pixel-size check could not be installed on this panel. **Lengths and '
                    'areas from it may be in PIXELS, not microns** — there is nothing here to '
                    'tell you if the metadata carried no resolution. See the debug log.')
            except Exception:
                pass
