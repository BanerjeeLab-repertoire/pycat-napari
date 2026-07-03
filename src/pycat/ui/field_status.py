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
    """A small coloured dot with a colour-specific tooltip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setAlignment(Qt.AlignCenter)
        self._set('red', _TIPS['red'])

    def _set(self, color_key, tip):
        c = _COLORS.get(color_key, '#888')
        self.setStyleSheet(
            f"background:{c}; border-radius:7px; border:1px solid rgba(0,0,0,0.35);")
        self.setToolTip(tip)


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

from PyQt5.QtWidgets import QGroupBox, QFormLayout, QDoubleSpinBox, QPushButton


def add_step1_file_io(viewer, layout, registry=None, on_change=None):
    """Add a standard 'Step 1 — Load Image' block that auto-completes when an
    image is already loaded (e.g. the user opened a file, then opened the
    workflow) and un-completes when the canvas is cleared."""
    import napari
    grp = QGroupBox("Step 1 — Load Image / File")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    circle = StatusCircle()
    status_lbl = QLabel("")
    row = QWidget(); hb = QHBoxLayout(row)
    hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(5)
    hb.addWidget(circle); hb.addWidget(status_lbl); hb.addStretch(1)
    form.addRow(row)

    note = QLabel("Open an image via the <b>Open/Save File(s)</b> menu, or drag "
                  "one onto the canvas. This step completes automatically once "
                  "an image is loaded.")
    note.setWordWrap(True)
    note.setStyleSheet("color:#888; font-size:9pt;")
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


def add_pixel_size_gate(layout, get_dr, on_set=None):
    """Add a pixel-size input that is shown ONLY when the image metadata did not
    provide a real scale, and hides itself once a valid scale exists.

    Returns a callable ``refresh()`` to re-evaluate visibility (call it after a
    file loads or a scale is set elsewhere).
    """
    grp = QGroupBox("Pixel size (no scale in metadata)")
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

    def _valid_scale():
        dr = get_dr() or {}
        return bool(dr.get('microns_per_pixel_sq')) and \
            abs(float(dr.get('microns_per_pixel_sq', 1.0)) - 1.0) > 1e-9

    def _from_metadata():
        dr = get_dr() or {}
        return bool(dr.get('pixel_size_from_metadata'))

    def refresh(*_a):
        # Hide entirely once metadata supplied a scale, or a valid scale exists.
        if _from_metadata() and _valid_scale():
            grp.setVisible(False)
            return
        grp.setVisible(True)
        if field.value() > 0:
            circle._set('green', "Done — pixel size set.")
        elif _valid_scale():
            circle._set('green', "Scale already set.")
            grp.setVisible(False)
        else:
            circle._set('red', "Required — no scale in metadata; enter the pixel size.")

    def _on_edit(v):
        if v > 0:
            dr = get_dr()
            if dr is not None:
                dr['microns_per_pixel_sq'] = float(v) ** 2
                dr['pixel_size_from_metadata'] = False
            if on_set:
                try:
                    on_set(v)
                except Exception:
                    pass
        refresh()

    field.valueChanged.connect(_on_edit)
    refresh()
    layout.addWidget(grp)
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
