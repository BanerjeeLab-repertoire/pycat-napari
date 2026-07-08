"""
Field-status framework for PyCAT workflow docks.

A small left-edge status circle in front of each *interactive* input tells the
user, at a glance, where they still need to act:

    RED     required input not yet provided  (or a prior-step field still
            waiting for its upstream layer)
    YELLOW  optional input sitting at its sensible default — you may change it
    GREEN   satisfied: a required field that is filled, or an auto/prior-step
            field that received its value and matches the suggested option
    BLUE    an optional or auto field the user deliberately set away from the
            default / suggested value
    (none)  expert / auto-tuning knobs most users never touch — no circle
            (and hidden later in "easy mode")

Each field is registered with a ROLE, its default value, and (for dropdowns) an
optional name hint identifying the auto-populated choice. That single tag drives
the circle colour, the tooltip, the per-step / whole-workflow reset, and the
future easy/advanced visibility toggle.
"""

from PyQt5.QtWidgets import (
    QLabel, QWidget, QHBoxLayout, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QRadioButton,
)
from PyQt5.QtCore import Qt


# ── roles ──────────────────────────────────────────────────────────────────
REQUIRED = 'required'   # user must provide; red → green
OPTIONAL = 'optional'   # has a default; yellow → blue (if changed)
AUTO     = 'auto'       # fills from a prior step; red(waiting) → green/blue
EXPERT   = 'expert'     # tuning knob; no circle, hidden in easy mode


_COLORS = {
    'red':    '#e53935',
    'yellow': '#f5b301',
    'green':  '#43a047',
    'blue':   '#1e88e5',
}
_TIPS = {
    'red':    "Required — this still needs your input.",
    'yellow': "Optional — using the sensible default. You can change it.",
    'green':  "Done — this input is satisfied.",
    'blue':   "Changed — you set this away from the default.",
    'red_wait':  "Waiting — this fills in from an earlier step's output.",
    'green_auto': "Auto-filled from an earlier step.",
    'blue_auto':  "Changed — you picked something other than the suggested option.",
    'green_req':  "Done — required input provided.",
}


class StatusCircle(QLabel):
    """A small coloured dot with a colour-specific tooltip. Painted directly
    (not via CSS border-radius) so a global stylesheet can never flatten it into
    a square."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = _COLORS['red']
        self._set('red', _TIPS['red'])

    def _set(self, color_key, tip):
        self._color = _COLORS.get(color_key, '#888')
        self.setToolTip(tip)
        self.update()

    def paintEvent(self, _event):
        from PyQt5.QtGui import QPainter, QColor, QBrush, QPen
        from PyQt5.QtCore import Qt as _Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QBrush(QColor(self._color)))
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        # inset by 1px so the border isn't clipped
        p.drawEllipse(1, 1, self.width() - 3, self.height() - 3)
        p.end()


# ── per-widget value helpers ────────────────────────────────────────────────

def _read(widget):
    if isinstance(widget, QComboBox):
        return widget.currentText()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, (QCheckBox, QRadioButton)):
        return widget.isChecked()
    return None


def _write(widget, value):
    try:
        if isinstance(widget, QComboBox):
            i = widget.findText(str(value))
            if i >= 0:
                widget.setCurrentIndex(i)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif isinstance(widget, (QCheckBox, QRadioButton)):
            widget.setChecked(bool(value))
    except Exception:
        pass


def _has_value(widget):
    v = _read(widget)
    if isinstance(widget, QComboBox):
        return bool(v) and v not in ('', '(no valid layer)')
    if isinstance(widget, QLineEdit):
        return bool(str(v).strip())
    return True


# ── colour decision (pure logic) ────────────────────────────────────────────

def decide(role, widget, default, name_hint):
    """Return (color_key, tooltip) for a field, or (None, None) for expert."""
    if role == EXPERT:
        return None, None
    has = _has_value(widget)
    if role == REQUIRED:
        return ('green', _TIPS['green_req']) if has else ('red', _TIPS['red'])
    if role == AUTO:
        if not has:
            return 'red', _TIPS['red_wait']
        # a hinted dropdown is "at default" when its selection matches the hint
        if name_hint and isinstance(widget, QComboBox):
            at_def = name_hint.lower() in str(_read(widget)).lower()
        else:
            at_def = (_read(widget) == default)
        return ('green', _TIPS['green_auto']) if at_def else ('blue', _TIPS['blue_auto'])
    # OPTIONAL
    at_def = (_read(widget) == default)
    return ('yellow', _TIPS['yellow']) if at_def else ('blue', _TIPS['blue'])


# ── registry ────────────────────────────────────────────────────────────────

class FieldRegistry:
    """Tracks every registered field, owns its status circle, and drives colour
    refresh, per-step / whole-workflow reset, and (later) easy/advanced hiding."""

    def __init__(self):
        # each entry: dict(widget, role, default, name_hint, circle, step, row_widget)
        self._fields = []

    def register(self, widget, role, default=None, name_hint=None, step=None,
                 row_widget=None):
        circle = None
        if role != EXPERT:
            circle = StatusCircle()
        entry = dict(widget=widget, role=role, default=default,
                     name_hint=name_hint, circle=circle, step=step,
                     row_widget=row_widget)
        self._fields.append(entry)
        # refresh this circle whenever the widget changes
        if circle is not None:
            self._connect(widget)
            self._refresh_entry(entry)
        return circle

    def _connect(self, widget):
        try:
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.refresh)
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self.refresh)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.refresh)
            elif isinstance(widget, (QCheckBox, QRadioButton)):
                widget.toggled.connect(self.refresh)
        except Exception:
            pass

    def _refresh_entry(self, entry):
        if entry['circle'] is None:
            return
        color, tip = decide(entry['role'], entry['widget'], entry['default'],
                            entry['name_hint'])
        if color is not None:
            entry['circle']._set(color, tip)

    def refresh(self, *args):
        for e in self._fields:
            self._refresh_entry(e)

    def reset_step(self, step):
        for e in self._fields:
            if e['step'] == step and e['role'] in (OPTIONAL, EXPERT):
                _write(e['widget'], e['default'])
        self.refresh()

    def reset_all(self):
        for e in self._fields:
            if e['role'] in (OPTIONAL, EXPERT):
                _write(e['widget'], e['default'])
        self.refresh()

    def set_advanced(self, advanced: bool):
        """Show/hide expert rows for easy/advanced mode."""
        for e in self._fields:
            if e['role'] == EXPERT and e['row_widget'] is not None:
                try:
                    e['row_widget'].setVisible(advanced)
                except Exception:
                    pass


# ── row helper ──────────────────────────────────────────────────────────────

def status_row(form, registry, label, widget, role, default=None,
               name_hint=None, step=None):
    """Add a form row with a status circle in front of its label.

    For labelled rows the circle sits just left of the label text, so across the
    form the circles line up as a column down the left edge. Expert rows get no
    circle (an aligned spacer keeps the label position stable).
    """
    circle = registry.register(widget, role, default=default,
                               name_hint=name_hint, step=step)
    holder = QWidget()
    hb = QHBoxLayout(holder); hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(5)
    if circle is not None:
        hb.addWidget(circle)
    else:
        sp = QLabel(); sp.setFixedSize(14, 14); hb.addWidget(sp)   # keep alignment
    if label:
        hb.addWidget(QLabel(label))
    hb.addStretch(1)
    form.addRow(holder, widget)
    return widget


# ── Step 1 (file I/O) + pixel-size gate ─────────────────────────────────────

from PyQt5.QtWidgets import (QGroupBox, QFormLayout, QDoubleSpinBox, QPushButton,
                             QSizePolicy)


def add_step1_file_io(viewer, layout, registry=None, on_change=None,
                      instruction_html=None):
    """Add a standard 'Step 1 — Load Image' block that auto-completes when an
    image is already loaded (e.g. the user opened a file, then opened the
    workflow) and un-completes when the canvas is cleared.

    If ``instruction_html`` is given, it replaces the generic "Open an image via
    the Open/Save File(s) menu…" note with a workflow-specific instruction
    (e.g. "Open/Save File(s) → Open Image Stack (T/Z / IMS)"), rendered BELOW the
    red/green status marker so every workflow shows one consistent Step 1: the
    status marker on top, the load instruction beneath it."""
    import napari

    # Step-header label rendered the SAME way as the enumerated steps below
    # (rich text, "Step N —" prefix emphasized), so Step 1 matches by construction
    # instead of relying on the QGroupBox-title mechanism (which sized differently).
    header = QLabel(
        "<span style='font-weight:800;'>Step 1 —</span> "
        "<span style='font-weight:600;'>Load Image / File</span>")
    header.setTextFormat(Qt.RichText)
    header.setStyleSheet("font-size: 14px; margin-top: 4px;")
    header.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    layout.addWidget(header)

    # The groupbox now carries a short grey description of what this block does,
    # in the title position, instead of repeating the step name.
    grp = QGroupBox("Load an image to begin — completes automatically")
    grp.setStyleSheet(
        "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;"
        " left: 8px; top: 2px; padding: 0 4px; color:#888; font-style: italic; }")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    circle = StatusCircle()
    status_lbl = QLabel("")
    row = QWidget(); hb = QHBoxLayout(row)
    hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(5)
    hb.addWidget(circle); hb.addWidget(status_lbl); hb.addStretch(1)
    form.addRow(row)

    if instruction_html:
        note = QLabel(instruction_html)
    else:
        note = QLabel("Open an image via the <b>Open/Save File(s)</b> menu, or drag "
                      "one onto the canvas. This step completes automatically once "
                      "an image is loaded.")
    note.setWordWrap(True)
    note.setStyleSheet("color:#888; font-size:9pt;")
    note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    form.addRow(note)

    def _has_image():
        return any(isinstance(l, napari.layers.Image) for l in viewer.layers)

    def _update(*_a):
        if _has_image():
            circle._set('green', "Done — an image is loaded.")
            status_lbl.setText("Image loaded")
        else:
            circle._set('red', "Required — load an image to begin.")
            status_lbl.setText("No image loaded yet")
        if on_change:
            try:
                on_change()
            except Exception:
                pass

    try:
        viewer.layers.events.inserted.connect(_update)
        viewer.layers.events.removed.connect(_update)
    except Exception:
        pass
    _update()
    layout.addWidget(grp)
    return grp


def prompt_pixel_size_on_load(get_dr, parent=None, central_manager=None):
    """Show a modal pixel-size dialog after a file load when the image has no
    valid physical scale (pixel size fell back to 1.0 and did not come from
    metadata). Writes the confirmed value into the same
    data_repository['microns_per_pixel_sq'] the in-dock gates read, so the two
    stay consistent. Dismissible: Skip leaves the scale unset (the in-dock gate
    will still prompt). This is separate from the dock widgets — a deliberate
    top-level dialog, so it has none of the embedding/parenting subtleties of an
    in-panel widget.

    Returns True if a value was set, False if skipped/closed.
    """
    dr = get_dr() or {}
    mpp_sq = dr.get('microns_per_pixel_sq')
    from_meta = bool(dr.get('pixel_size_from_metadata'))
    # Only prompt for the no-valid-scale case (scale is 1.0 and not from metadata).
    has_valid = bool(mpp_sq) and abs(float(mpp_sq if mpp_sq else 1.0) - 1.0) > 1e-9
    if has_valid or from_meta:
        return False

    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QDoubleSpinBox,
                                     QDialogButtonBox, QHBoxLayout)
        from PyQt5.QtCore import Qt
    except Exception:
        return False

    dlg = QDialog(parent)
    dlg.setWindowTitle("Set pixel size")
    dlg.setModal(True)
    v = QVBoxLayout(dlg)

    title = QLabel("<b>This image has no pixel size in its metadata.</b>")
    title.setWordWrap(True)
    v.addWidget(title)

    # Teaching line — why it matters.
    why = QLabel(
        "<span style='color:#333333;font-size:9pt;'>The pixel size (µm per pixel) "
        "sets the physical scale for every downstream measurement — object sizes, "
        "distances, diffusion coefficients, and viscosities are all computed from "
        "it. Without a correct value these results default to a scale of 1.0 µm/px "
        "and will be wrong. Enter the value for this acquisition, or Skip to set it "
        "later.</span>")
    why.setWordWrap(True)
    v.addWidget(why)

    row = QHBoxLayout()
    row.addWidget(QLabel("Pixel size:"))
    field = QDoubleSpinBox()
    field.setRange(0.0, 1000.0); field.setDecimals(4); field.setSuffix(" µm/px")
    field.setValue(0.0)
    row.addWidget(field)
    v.addLayout(row)

    buttons = QDialogButtonBox()
    set_btn = buttons.addButton("Set scale", QDialogButtonBox.AcceptRole)
    skip_btn = buttons.addButton("Skip", QDialogButtonBox.RejectRole)
    v.addWidget(buttons)

    result = {'set': False}

    def _accept():
        val = field.value()
        if val and val > 0:
            d = get_dr()
            if d is not None:
                d['microns_per_pixel_sq'] = float(val) ** 2
                d['pixel_size_from_metadata'] = False
                result['set'] = True
            dlg.accept()
            # The scale just changed — tell any registered gates (e.g. an open
            # method panel's in-dock pixel-size gate) to re-evaluate, so a gate
            # that was showing now hides instead of contradicting the popup.
            if central_manager is not None:
                try:
                    central_manager.notify_data_changed()
                except Exception:
                    pass
        # if 0/invalid, do nothing (keep dialog open so they enter a value or Skip)

    set_btn.clicked.connect(_accept)
    skip_btn.clicked.connect(dlg.reject)
    dlg.exec_()
    return result['set']


def add_pixel_size_gate(layout, get_dr, on_set=None, central_manager=None):
    """Add a pixel-size input that is shown ONLY when the image metadata did not
    provide a real scale, and hides itself once a valid scale exists.

    Includes an off-by-default "Keep this pixel size for the session" checkbox:
    when checked and a valid pixel size has been entered, switching to other data
    that lacks a scale re-applies the remembered value automatically instead of
    re-prompting. When unchecked (default), switching to unscaled data re-shows
    the gate so the user sets the scale for that data explicitly.

    If ``central_manager`` is given, the gate registers itself to re-evaluate
    whenever the active data class is switched (so it reappears for new unscaled
    data, or auto-applies the remembered value when persist is on).

    Returns a callable ``refresh()`` to re-evaluate visibility/state (call it
    after a file loads, a scale is set elsewhere, or the active data switches).
    """
    grp = QGroupBox("Pixel size")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    circle = StatusCircle()
    field = QDoubleSpinBox()
    field.setRange(0.0, 1000.0); field.setDecimals(4); field.setSuffix(" µm/px")
    field.setToolTip("Physical size of one pixel. Needed because the image "
                     "metadata did not include a scale.")
    row = QWidget(); hb = QHBoxLayout(row)
    hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(5)
    hb.addWidget(circle); hb.addWidget(QLabel("Pixel size:")); hb.addStretch(1)
    form.addRow(row, field)

    persist_cb = QCheckBox("Keep this pixel size for the session")
    persist_cb.setChecked(False)
    persist_cb.setToolTip(
        "When on, the pixel size you enter is remembered and automatically "
        "re-applied to other data you switch to that has no scale of its own, "
        "instead of asking again. Off by default so each dataset's scale is set "
        "explicitly.")
    form.addRow(persist_cb)

    # Confirmation row: the gate does NOT auto-hide the instant a valid number
    # appears while typing (which made it vanish mid-entry). Instead the user
    # presses Confirm once the value is right, and only then does it hide.
    confirm_lbl = QLabel("")
    confirm_lbl.setWordWrap(True)
    form.addRow(confirm_lbl)
    confirm_btn = QPushButton("Confirm pixel size")
    confirm_btn.setToolTip("Apply this pixel size and hide the panel.")
    form.addRow(confirm_btn)

    # Remembered value across data switches (only used when persist is on).
    state = {'remembered': None, 'confirmed': False}

    def _valid_scale():
        dr = get_dr() or {}
        return bool(dr.get('microns_per_pixel_sq')) and \
            abs(float(dr.get('microns_per_pixel_sq', 1.0)) - 1.0) > 1e-9

    def _from_metadata():
        dr = get_dr() or {}
        return bool(dr.get('pixel_size_from_metadata'))

    def _image_present():
        # The gate only makes sense when an image is actually loaded. After a
        # Clear there are no image layers, so the gate must stay hidden even
        # though there's no scale. Check the viewer for any Image layer.
        try:
            import napari
            viewer = getattr(central_manager, 'viewer', None)
            if viewer is None:
                return True   # can't tell — don't suppress
            return any(isinstance(l, napari.layers.Image) for l in viewer.layers)
        except Exception:
            return True   # fail open — don't hide if we can't determine

    def _apply_value(v):
        dr = get_dr()
        if dr is not None and v and v > 0:
            dr['microns_per_pixel_sq'] = float(v) ** 2
            dr['pixel_size_from_metadata'] = False
            if on_set:
                try:
                    on_set(v)
                except Exception:
                    pass

    # Embed the gate in the panel's layout. Without this the QGroupBox has no
    # parent and Qt renders it as a FLOATING top-level window — which is what
    # caused multiple gate windows to appear and to persist after the main GUI
    # closed. Start hidden; the coordinator shows it (at most one) when needed.
    layout.addWidget(grp)
    grp.setVisible(False)

    def refresh(*_a):
        # No image loaded (e.g. after Clear) → the gate is irrelevant; hide it.
        if not _image_present():
            grp.setVisible(False)
            return

        # If persist is on, we have a remembered scale, and the current data has
        # no scale of its own, re-apply the remembered value instead of prompting.
        if (persist_cb.isChecked() and state['remembered']
                and not _valid_scale() and not _from_metadata()):
            _apply_value(state['remembered'])
            field.blockSignals(True)
            field.setValue(state['remembered'])
            field.blockSignals(False)
            state['confirmed'] = True

        # Hide entirely once metadata supplied a scale.
        if _from_metadata() and _valid_scale():
            grp.setVisible(False)
            return
        # Hide once the user has CONFIRMED a valid scale (not merely typed one).
        if _valid_scale() and state['confirmed']:
            circle._set('green', "Scale set.")
            grp.setVisible(False)
            return
        # Hide when a valid scale was set EXTERNALLY (e.g. the load-time popup or
        # a persisted value) rather than typed into this field. We detect that as
        # "the repository has a valid scale but this field is still empty/zero" —
        # i.e. the user did not enter it here, so there is nothing to confirm and
        # the gate should not contradict the already-set scale.
        if _valid_scale() and field.value() <= 0:
            state['confirmed'] = True
            circle._set('green', "Scale set.")
            grp.setVisible(False)
            return

        # Otherwise this gate wants to be visible (the coordinator shows one).
        grp.setVisible(True)
        v = field.value()
        if v > 0:
            circle._set('yellow', "Review the pixel size, then Confirm.")
            confirm_lbl.setText(
                f"<span style='color:#f0a500;font-size:9pt;'>Is <b>{v:.4f} µm/px</b> "
                f"the correct scale? Adjust the value above if not, then press "
                f"Confirm.</span>")
            confirm_btn.setEnabled(True)
        else:
            circle._set('red', "Required — no scale in metadata; enter the pixel size.")
            confirm_lbl.setText(
                "<span style='color:#d9534f;font-size:9pt;'>No scale found in the "
                "image metadata — enter the pixel size (µm/px).</span>")
            confirm_btn.setEnabled(False)

    def _on_edit(v):
        # Typing updates the preview/prompt but does NOT hide the panel. It also
        # invalidates any prior confirmation so a changed value must be re-confirmed.
        state['confirmed'] = False
        refresh()

    def _on_confirm():
        v = field.value()
        if v <= 0:
            return
        _apply_value(v)
        if persist_cb.isChecked():
            state['remembered'] = float(v)
        state['confirmed'] = True
        refresh()

    def _on_persist_toggle(checked):
        if checked and field.value() > 0:
            state['remembered'] = float(field.value())
        refresh()

    field.valueChanged.connect(_on_edit)
    confirm_btn.clicked.connect(_on_confirm)
    persist_cb.toggled.connect(_on_persist_toggle)
    # Re-evaluate whenever the active data class switches (reappear for new
    # unscaled data, or auto-apply the remembered value when persist is on).
    if central_manager is not None:
        try:
            central_manager.register_data_switch_callback(refresh)
        except Exception:
            pass
    # NOTE: do NOT call refresh() here at construction time. The panel is not yet
    # docked, so grp has no parent window — calling setVisible(True) now would
    # flash the gate as a brief floating top-level window before it settles into
    # the dock. The gate starts hidden (set above) and is refreshed by the real
    # triggers instead: the data-switch callback (registered above) and the
    # notify_data_changed() fired after a file loads — both of which run once the
    # panel is docked and grp is properly embedded, so it appears cleanly in-dock.

    # Expose a reset so Clear can re-show the gate for the next dataset.
    def _reset_gate():
        state['confirmed'] = False
        field.blockSignals(True); field.setValue(0.0); field.blockSignals(False)
        if not persist_cb.isChecked():
            state['remembered'] = None
        refresh()
    refresh._reset_gate = _reset_gate
    return refresh


def add_reset_buttons(form, registry, step):
    """Add 'Reset this step' and 'Reset all' buttons to a step's form."""
    from PyQt5.QtWidgets import QSizePolicy
    row = QWidget(); hb = QHBoxLayout(row)
    hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(5)
    b1 = QPushButton("Reset this step")
    b1.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b1.setToolTip("Restore this step's optional inputs to their defaults.")
    b1.clicked.connect(lambda: registry.reset_step(step))
    b2 = QPushButton("Reset all")
    b2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b2.setToolTip("Restore every optional input in this workflow to its default.")
    b2.clicked.connect(registry.reset_all)
    hb.addWidget(b1); hb.addWidget(b2)
    form.addRow(row)


def label_with_circle(text, optional=False, dropdown=None):
    """Return a QWidget containing [● circle][label text] for use as the
    label column of a QFormLayout.addRow() call. The circle is red (required)
    or yellow (optional) until a real layer is selected, then turns green.
    If *dropdown* is given (a QComboBox), the circle updates automatically
    when its selection changes — wire it after calling addRow()."""
    from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel
    w=QWidget(); hb=QHBoxLayout(w); hb.setContentsMargins(0,0,0,0); hb.setSpacing(4)
    c=StatusCircle()
    init='yellow' if optional else 'red'
    tip=('Optional — a default will be used.' if optional
         else 'Required — select a layer to continue.')
    c._set(init, tip)
    hb.addWidget(c); hb.addWidget(QLabel(text))
    if dropdown is not None:
        def _upd(*_):
            txt=(dropdown.currentText() or '').strip().lower()
            bad=not txt or txt.startswith(('select','none','--','—','no ','choose'))
            c._set(init if bad else 'green',
                   tip if bad else 'Layer selected.')
        dropdown.currentIndexChanged.connect(_upd); _upd()
    return w


def button_with_circle(button, optional=False, watch_dropdowns=None):
    """Return a QWidget containing [● circle][button] — a status square to the
    left of an action button. Red (required) or yellow (optional). If
    *watch_dropdowns* is given (a list of QComboBox), the circle turns green once
    all of them have a real (non-placeholder) selection, so the square reflects
    whether the action is ready to run.

    The button's own click also marks completion: after the user runs the action,
    the circle goes GREEN for a required step or BLUE for an optional step (blue =
    "you did this optional thing"). A ``reset()`` method is attached to the
    wrapper so a per-step / whole-workflow Clear can revert it to its initial
    red/yellow. Returns the wrapper widget; add it to a layout in place of the
    bare button.
    """
    from PyQt5.QtWidgets import QWidget, QHBoxLayout
    w = QWidget(); hb = QHBoxLayout(w)
    hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(4)
    c = StatusCircle()
    init = 'yellow' if optional else 'red'
    tip = ('Optional step.' if optional else 'Required — run this step to continue.')
    c._set(init, tip)
    hb.addWidget(c)
    hb.addWidget(button, 1)

    state = {'done': False}
    dds = list(watch_dropdowns or [])

    def _refresh():
        if state['done']:
            # Completed: blue for an optional action, green for a required one.
            if optional:
                c._set('blue', 'Done — you ran this optional step.')
            else:
                c._set('green', 'Done — this step has been run.')
            return
        if dds:
            def _ok(dd):
                t = (dd.currentText() or '').strip().lower()
                return t and not t.startswith(
                    ('select', 'none', '--', '—', 'no ', 'choose'))
            ready = all(_ok(dd) for dd in dds)
            c._set('green' if ready else init,
                   'Ready to run.' if ready else tip)
        else:
            c._set(init, tip)

    def _mark_done(*_):
        state['done'] = True
        _refresh()

    def reset():
        state['done'] = False
        _refresh()

    # Clicking the button marks the step done (after its own handler runs).
    try:
        button.clicked.connect(_mark_done)
    except Exception:
        pass
    for dd in dds:
        try:
            dd.currentIndexChanged.connect(lambda *_: _refresh())
        except Exception:
            pass
    _refresh()
    # Expose reset + circle for per-step Clear wiring.
    w.reset = reset
    w._status_circle = c
    return w
