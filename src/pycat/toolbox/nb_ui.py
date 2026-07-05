"""
PyCAT Number & Brightness (N&B) UI
==================================
Toolbox widget for Number & Brightness analysis of a fluorescence time-series —
the camera / widefield / TIRF counterpart to SpIDA. Produces per-pixel brightness
and number maps plus an ROI (or whole-frame) summary.

Detector correction: scalar gain / offset / read-variance are entered here for
true (rather than apparent) brightness. A per-pixel variance-map correction (full
sCMOS handling) is a planned extension.
"""

import napari
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QGroupBox, QFormLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QSizePolicy)

from pycat.toolbox.nb_tools import run_nb_analysis
from pycat.ui.field_status import button_with_circle as _bwc


def _add_number_and_brightness(ui_instance, layout=None, separate_widget=False):
    """Build the Number & Brightness widget."""
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Number & Brightness (N&B)', bold=True)
    ui_instance.add_text_label(
        outer,
        'Per-pixel molecular number and brightness from the temporal fluctuations '
        'of a time-series. The camera/widefield/TIRF counterpart to SpIDA — the '
        'molecules must exchange between frames, and photobleaching should be '
        'minimal (a bleaching correction is applied).')

    grp = QGroupBox("Inputs")
    form = QFormLayout(grp)
    form.setContentsMargins(6, 20, 6, 6)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    roi_dd = ui_instance.create_layer_dropdown(napari.layers.Shapes)
    form.addRow("Time-series layer (T,H,W):", image_dd)
    form.addRow("ROI shapes (optional):", roi_dd)

    win_spin = QSpinBox(); win_spin.setRange(2, 500); win_spin.setValue(30)
    win_spin.setToolTip("Boxcar window (frames) for the global bleaching "
                        "correction. Larger = gentler.")
    form.addRow("Bleaching-detrend window:", win_spin)
    outer.addWidget(grp)

    # Detector correction
    det_grp = QGroupBox("Detector correction (for TRUE, not apparent, brightness)")
    det_form = QFormLayout(det_grp)
    det_form.setContentsMargins(6, 20, 6, 6)
    det_note = QLabel("Enter your camera's values (e.g. Kinetix sCMOS). Leave gain "
                      "= 1, offset = 0, read-var = 0 to get apparent brightness in "
                      "raw units. Per-pixel variance-map correction is planned.")
    det_note.setWordWrap(True); det_note.setStyleSheet("color:#888;")
    det_form.addRow(det_note)

    gain_spin = QDoubleSpinBox(); gain_spin.setRange(0.0001, 1e6); gain_spin.setDecimals(4)
    gain_spin.setValue(1.0)
    det_form.addRow("Gain S (iu per event):", gain_spin)
    off_spin = QDoubleSpinBox(); off_spin.setRange(0, 1e6); off_spin.setDecimals(1)
    off_spin.setValue(0.0)
    det_form.addRow("Offset / dark level:", off_spin)
    rv_spin = QDoubleSpinBox(); rv_spin.setRange(0, 1e9); rv_spin.setDecimals(2)
    rv_spin.setValue(0.0)
    det_form.addRow("Read-noise variance:", rv_spin)
    outer.addWidget(det_grp)

    # Oligomeric state reference
    ref_grp = QGroupBox("Oligomeric state (optional)")
    ref_form = QFormLayout(ref_grp)
    ref_form.setContentsMargins(6, 20, 6, 6)
    eps0_spin = QDoubleSpinBox(); eps0_spin.setRange(0, 1e6); eps0_spin.setDecimals(3)
    eps0_spin.setValue(0.0)
    eps0_spin.setToolTip("Monomer-reference brightness measured on the same "
                         "camera/settings. If >0, the summary reports "
                         "brightness / eps_0 as oligomeric state.")
    ref_form.addRow("Monomer brightness eps_0:", eps0_spin)
    outer.addWidget(ref_grp)

    run_btn = QPushButton("Run N&B")
    run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    run_btn.clicked.connect(lambda: run_nb_analysis(
        _layer(ui_instance, image_dd), gain_spin.value(), off_spin.value(),
        rv_spin.value(), win_spin.value(), ui_instance.viewer,
        eps0_spin.value(), _layer(ui_instance, roi_dd)))
    outer.addWidget(_bwc(run_btn, watch_dropdowns=[image_dd]))

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(widget, layout, separate_widget,
                                              "Number & Brightness")


def _layer(ui_instance, dropdown):
    name = dropdown.currentText()
    if not name:
        return None
    try:
        return ui_instance.viewer.layers[name]
    except KeyError:
        return None
