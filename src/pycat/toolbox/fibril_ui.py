"""
PyCAT Fibril Analysis UI
========================
Toolbox widget for fibril / filament quantification.  Four panels:

1. Bead-on-fibril detection
2. Fibril morphometry (length, tortuosity, persistence length, mesh size)
3. Before/after registration
4. Crossing node map + graph theory
"""

import napari
from PyQt5.QtWidgets import (
    QVBoxLayout, QWidget, QGroupBox, QFormLayout, QLabel,
    QPushButton, QDoubleSpinBox, QSizePolicy, QTabWidget)

from pycat.toolbox.fibril_tools import (
    run_bead_detection, run_fibril_morphometry,
    run_fibril_registration, run_fibril_graph)
from pycat.ui.field_status import button_with_circle as _bwc


def _add_fibril_analysis(ui_instance, layout=None, separate_widget=False):
    """Build the fibril analysis widget with four tabbed panels."""
    outer = QVBoxLayout()
    ui_instance.add_text_label(outer, 'Fibril Analysis', bold=True)
    ui_instance.add_text_label(
        outer,
        'Skeleton-graph-based analysis of fibrillar / filamentous structures: '
        'bead/varicosity detection, morphometry, before/after registration, '
        'and crossing-node graph theory.')

    tabs = QTabWidget()
    tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    # ------------------------------------------------------------------ tab 1
    t1 = QWidget(); f1 = QFormLayout(t1); f1.setContentsMargins(6, 8, 6, 8)
    note1 = QLabel("Detects local widenings (beads, varicosities, condensate "
                   "decorations) along fibrils using the distance-transform "
                   "local-width profile. A point is a bead if its half-width "
                   "exceeds the threshold × background fibril width.")
    note1.setWordWrap(True); note1.setStyleSheet("color:#888;")
    f1.addRow(note1)
    mask_dd1 = ui_instance.create_layer_dropdown(napari.layers.Labels)
    f1.addRow("Fibril mask:", mask_dd1)
    min_r = QDoubleSpinBox(); min_r.setRange(0.5, 100); min_r.setValue(2.0)
    min_r.setToolTip("Minimum bead half-width in pixels; below this nothing is a bead.")
    f1.addRow("Min bead radius (px):", min_r)
    wf = QDoubleSpinBox(); wf.setRange(1.1, 20); wf.setValue(2.0); wf.setDecimals(1)
    wf.setToolTip("A bead needs width > this × background fibril half-width.")
    f1.addRow("Width threshold factor:", wf)
    b1 = QPushButton("Detect Beads")
    b1.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b1.clicked.connect(lambda: run_bead_detection(
        _layer(ui_instance, mask_dd1), _layer(ui_instance, mask_dd1),
        min_r.value(), wf.value(), ui_instance.viewer))
    f1.addRow(_bwc(b1, watch_dropdowns=[mask_dd1]))
    tabs.addTab(t1, "Beads")

    # ------------------------------------------------------------------ tab 2
    t2 = QWidget(); f2 = QFormLayout(t2); f2.setContentsMargins(6, 8, 6, 8)
    note2 = QLabel("Per-segment length, tortuosity, curvature, persistence "
                   "length (tangent autocorrelation), and mean half-width. "
                   "Global summary: total length, junction/endpoint counts, "
                   "mean mesh size from cycle basis.")
    note2.setWordWrap(True); note2.setStyleSheet("color:#888;")
    f2.addRow(note2)
    mask_dd2 = ui_instance.create_layer_dropdown(napari.layers.Labels)
    f2.addRow("Fibril mask:", mask_dd2)
    px2 = QDoubleSpinBox(); px2.setRange(0.001, 100); px2.setValue(0.1)
    px2.setDecimals(4)
    f2.addRow("Pixel size (µm/px):", px2)
    b2 = QPushButton("Run Morphometry")
    b2.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b2.clicked.connect(lambda: run_fibril_morphometry(
        _layer(ui_instance, mask_dd2), px2.value(), ui_instance.viewer))
    f2.addRow(_bwc(b2, watch_dropdowns=[mask_dd2]))
    tabs.addTab(t2, "Morphometry")

    # ------------------------------------------------------------------ tab 3
    t3 = QWidget(); f3 = QFormLayout(t3); f3.setContentsMargins(6, 8, 6, 8)
    note3 = QLabel("Subpixel phase-correlation registration (Guizar-Sicairos "
                   "2008 — same algorithm as BlebQuant's dftregistration.m). "
                   "Aligns the moving image to the reference, then shows the "
                   "registered image and a difference map for before/after "
                   "comparison.")
    note3.setWordWrap(True); note3.setStyleSheet("color:#888;")
    f3.addRow(note3)
    ref_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    mov_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    f3.addRow("Reference (before):", ref_dd)
    f3.addRow("Moving (after):", mov_dd)
    b3 = QPushButton("Register & Difference")
    b3.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b3.clicked.connect(lambda: run_fibril_registration(
        _layer(ui_instance, ref_dd), _layer(ui_instance, mov_dd),
        ui_instance.viewer))
    f3.addRow(_bwc(b3, watch_dropdowns=[ref_dd, mov_dd]))
    tabs.addTab(t3, "Registration")

    # ------------------------------------------------------------------ tab 4
    t4 = QWidget(); f4 = QFormLayout(t4); f4.setContentsMargins(6, 8, 6, 8)
    note4 = QLabel("Builds a NetworkX graph of the fibril skeleton. Junction "
                   "pixels (crossings, degree > 2) are the crossing nodes — "
                   "shown as a Points layer. Reports: degree distribution, "
                   "betweenness centrality of crossings, connected components, "
                   "cycle basis (mesh loops).")
    note4.setWordWrap(True); note4.setStyleSheet("color:#888;")
    f4.addRow(note4)
    mask_dd4 = ui_instance.create_layer_dropdown(napari.layers.Labels)
    f4.addRow("Fibril mask:", mask_dd4)
    px4 = QDoubleSpinBox(); px4.setRange(0.001, 100); px4.setValue(0.1)
    px4.setDecimals(4)
    f4.addRow("Pixel size (µm/px):", px4)
    b4 = QPushButton("Build Graph & Crossing Map")
    b4.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    b4.clicked.connect(lambda: run_fibril_graph(
        _layer(ui_instance, mask_dd4), px4.value(), ui_instance.viewer))
    f4.addRow(_bwc(b4, watch_dropdowns=[mask_dd4]))
    tabs.addTab(t4, "Graph / Crossings")

    outer.addWidget(tabs)
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    widget.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Fibril Analysis")


def _layer(ui_instance, dropdown):
    name = dropdown.currentText()
    if not name:
        return None
    try:
        return ui_instance.viewer.layers[name]
    except KeyError:
        return None
