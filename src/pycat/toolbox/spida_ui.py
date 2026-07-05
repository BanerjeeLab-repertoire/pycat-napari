"""
PyCAT SpIDA UI
==============
Toolbox widget for Spatial Intensity Distribution Analysis. Provides two steps:

1. **Calibrate monomer** — fit a monomeric-control ROI to obtain the reference
   quantal brightness epsilon_0.
2. **Run SpIDA** — fit an ROI for density N and quantal brightness epsilon, and
   (if epsilon_0 is known) report the oligomeric state epsilon/epsilon_0.

The widget deliberately foregrounds the assumptions SpIDA depends on: it takes a
white-noise background level, works on a user-drawn ROI, and surfaces guardrail
warnings from :mod:`pycat.toolbox.spida_tools` rather than silently returning
numbers.
"""

import napari
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QGroupBox, QFormLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QSizePolicy)

from pycat.toolbox.spida_tools import run_spida_calibration, run_spida_analysis
from pycat.ui.field_status import button_with_circle as _bwc


def _add_spida(ui_instance, layout=None, separate_widget=False):
    """Build the SpIDA widget (calibration + analysis)."""
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Spatial Intensity Distribution Analysis (SpIDA)', bold=True)
    ui_instance.add_text_label(
        outer,
        'Estimate fluorescent particle density and quantal brightness from the '
        'intensity histogram of a confocal-image ROI. Oligomeric state needs a '
        'monomeric-control calibration first.')

    # ---- Shared inputs -------------------------------------------------
    grp = QGroupBox("Inputs")
    form = QFormLayout(grp)
    form.setContentsMargins(6, 20, 6, 6)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    roi_dd = ui_instance.create_layer_dropdown(napari.layers.Shapes)
    form.addRow("Image layer:", image_dd)
    form.addRow("ROI shapes layer:", roi_dd)

    bins_spin = QSpinBox(); bins_spin.setRange(16, 2048); bins_spin.setValue(256)
    form.addRow("Histogram bins:", bins_spin)

    wn_spin = QDoubleSpinBox(); wn_spin.setRange(0, 1e6); wn_spin.setDecimals(1)
    wn_spin.setValue(0.0)
    wn_spin.setToolTip("Background / white-noise level: mean intensity of a "
                       "cell-free region, or the camera/PMT dark level. "
                       "Subtracted before analysis.")
    form.addRow("White-noise background:", wn_spin)

    from PyQt5.QtWidgets import QComboBox
    modality_dd = QComboBox()
    modality_dd.addItems(['Confocal / laser-scanning', 'TIRF (camera)',
                          'Widefield (not recommended)'])
    modality_dd.setToolTip(
        "SpIDA assumes optically-sectioned confocal data. TIRF is usable with "
        "camera-noise caveats. Plain widefield epifluorescence violates SpIDA's "
        "assumptions — density/brightness will not be valid.")
    form.addRow("Acquisition modality:", modality_dd)
    outer.addWidget(grp)

    def _modality():
        t = modality_dd.currentText().lower()
        if 'widefield' in t:
            return 'widefield'
        if 'tirf' in t:
            return 'tirf'
        return 'confocal'

    # ---- Calibration ---------------------------------------------------
    cal_grp = QGroupBox("Step 1 — Calibrate monomer reference (epsilon_0)")
    cal_form = QFormLayout(cal_grp)
    cal_form.setContentsMargins(6, 20, 6, 6)
    cal_note = QLabel("Draw an ROI over a region known to be monomeric "
                      "(e.g. a monomeric-GFP control) and calibrate. The fitted "
                      "brightness becomes the monomer reference.")
    cal_note.setWordWrap(True); cal_note.setStyleSheet("color:#888;")
    cal_form.addRow(cal_note)
    cal_btn = QPushButton("Calibrate monomer")
    cal_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    cal_btn.clicked.connect(lambda: run_spida_calibration(
        _layer(ui_instance, image_dd), _layer(ui_instance, roi_dd),
        bins_spin.value(), wn_spin.value(), ui_instance.viewer, _modality()))
    cal_form.addRow(_bwc(cal_btn, optional=True, watch_dropdowns=[image_dd]))
    outer.addWidget(cal_grp)

    # ---- Analysis ------------------------------------------------------
    an_grp = QGroupBox("Step 2 — Run SpIDA")
    an_form = QFormLayout(an_grp)
    an_form.setContentsMargins(6, 20, 6, 6)
    eps0_spin = QDoubleSpinBox(); eps0_spin.setRange(0, 1e6); eps0_spin.setDecimals(1)
    eps0_spin.setValue(0.0)
    eps0_spin.setToolTip("Monomeric reference brightness epsilon_0. Leave 0 to "
                         "use the value from a prior calibration, or enter a "
                         "known value. If 0 and no calibration exists, density "
                         "and brightness are still reported but no oligomeric "
                         "state.")
    an_form.addRow("Monomer eps_0 (0 = auto):", eps0_spin)
    an_btn = QPushButton("Run SpIDA")
    an_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    an_btn.clicked.connect(lambda: run_spida_analysis(
        _layer(ui_instance, image_dd), _layer(ui_instance, roi_dd),
        bins_spin.value(), wn_spin.value(), eps0_spin.value(),
        ui_instance.viewer, _modality()))
    an_form.addRow(_bwc(an_btn, watch_dropdowns=[image_dd]))
    outer.addWidget(an_grp)

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(widget, layout, separate_widget, "SpIDA")


def _layer(ui_instance, dropdown):
    """Resolve a dropdown selection to a napari layer, or None."""
    name = dropdown.currentText()
    if not name:
        return None
    try:
        return ui_instance.viewer.layers[name]
    except KeyError:
        return None
