"""The UI entry point for comparative phenotyping (increment 3, Step 2).

Reads the consolidated long table a batch produces (`consolidated_long.csv`) and renders a
replicate-honest comparative figure from it — the visible payoff of the comparative-phenotyping arc.
Everything scientific lives in the Qt-free `utils.comparative_figures` / `utils.comparative_stats`; this
is only the picker + the embedded canvas + the inspectable summary, and it routes object-point clicks
through the shared `SelectionService` so brushing works with the rest of PyCAT.
"""
from __future__ import annotations


def _condition_fields(columns):
    """The condition columns of a consolidated table — everything that is not the fixed stem / core /
    provenance schema (i.e. the SampleMetadata fields joined per row)."""
    from pycat.utils.consolidated_table import (_CORE_COLS, _DEFAULT_PROVENANCE_COLS, _QC_COLS,
                                                 _RELIABILITY_COLS)
    fixed = ({'image_stem'} | set(_CORE_COLS) | set(_DEFAULT_PROVENANCE_COLS) | set(_QC_COLS)
             | set(_RELIABILITY_COLS))
    return [c for c in columns if c not in fixed]


def open_comparative_figures_dialog(central_manager, viewer):
    """Pick a consolidated_long.csv, choose measurement / condition / unit, render the superplot with
    single-entity brushing, and show the summary numbers behind it."""
    import pandas as pd
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QCheckBox,
        QPushButton, QLabel, QFileDialog, QTableWidget, QTableWidgetItem, QMessageBox)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

    from pycat.utils.comparative_figures import condition_comparison

    parent = getattr(getattr(viewer, 'window', None), '_qt_window', None)
    path, _ = QFileDialog.getOpenFileName(
        parent, "Open consolidated_long.csv", "", "CSV files (*.csv);;All files (*)")
    if not path:
        return
    try:
        df = pd.read_csv(path)
    except Exception as exc:                          # broad-ok: optional_probe — bad/locked file → tell the user, don't crash
        QMessageBox.warning(parent, "Comparative figures", f"Could not read {path}:\n{exc}")
        return
    if 'measurement' not in df.columns or 'value' not in df.columns:
        QMessageBox.warning(parent, "Comparative figures",
                            "This does not look like a consolidated long table "
                            "(no 'measurement'/'value' columns).")
        return

    dlg = QDialog(parent)
    dlg.setWindowTitle("Comparative figures — consolidated table")
    dlg.resize(880, 620)
    outer = QVBoxLayout(dlg)

    controls = QFormLayout()
    meas_dd = QComboBox(); meas_dd.addItems(sorted(str(m) for m in df['measurement'].dropna().unique()))
    cond_dd = QComboBox(); cond_dd.addItems(_condition_fields(df.columns) or ['image_stem'])
    unit_dd = QComboBox()
    unit_dd.addItems((['image_stem'] if 'image_stem' in df.columns else [])
                     + _condition_fields(df.columns))
    kind_dd = QComboBox(); kind_dd.addItems(['box', 'violin'])
    test_cb = QCheckBox("Run the replicate-level test (refuses below 3 units/condition)")
    controls.addRow("Measurement:", meas_dd)
    controls.addRow("Condition field:", cond_dd)
    controls.addRow("Biological unit:", unit_dd)
    controls.addRow("Plot:", kind_dd)
    controls.addRow(test_cb)
    outer.addLayout(controls)

    fig = Figure(figsize=(7, 4.2)); canvas = FigureCanvasQTAgg(fig)
    outer.addWidget(canvas, 1)
    caption = QLabel(""); caption.setWordWrap(True); outer.addWidget(caption)
    table = QTableWidget(); outer.addWidget(table, 1)

    # The Refine→Export engine over this one figure: refining restyles it (never recomputes), and export
    # writes exactly the refined preview (explore_refine_export). Spec starts at the figure's current size.
    from pycat.utils.figure_refine import FigureRefineController
    from pycat.utils.figure_spec import FigureSpec as _FigureSpec
    refiner = FigureRefineController(fig, _FigureSpec(figure_size_in=tuple(fig.get_size_inches())))

    service = getattr(central_manager, 'selection', None)

    def _render():
        fig.clear()
        ax = fig.add_subplot(111)
        try:
            _, summary = condition_comparison(
                df, measurement=meas_dd.currentText(),
                condition_cols=[cond_dd.currentText()], unit_cols=[unit_dd.currentText()],
                kind=kind_dd.currentText(), test=test_cb.isChecked(), ax=ax,
                selection_service=service)
        except Exception as exc:                      # broad-ok: ui_cleanup — a bad column choice must not kill the dialog
            caption.setText(f"Could not render: {exc}")
            canvas.draw_idle(); return
        note = summary.attrs.get('note') or ''
        caption.setText("Replicate-honest superplot: light = objects, dark = unit means; the test (when "
                        "run) compares the units, not the objects." + (f"  Note: {note}" if note else ""))
        _fill_table(table, summary)
        refiner.fig = fig; refiner.summary_df = summary
        refiner.apply()                # re-apply any active refinement (no recompute) after an explore render
        canvas.draw_idle()

    render_btn = QPushButton("Render")
    render_btn.clicked.connect(_render)
    row = QHBoxLayout(); row.addStretch(1); row.addWidget(render_btn); outer.addLayout(row)
    outer.addWidget(_build_refine_row(refiner, canvas.draw_idle, parent))

    _render()
    dlg.setModal(False)
    dlg.show()
    central_manager._pycat_comparative_dialog = dlg   # keep a ref so it isn't GC'd
    return dlg


def _build_refine_row(refiner, redraw, parent):
    """The Refine + Export controls over the current figure. Each control mutates the canonical FigureSpec
    and re-applies presentation via the controller (never recomputing the comparison); Export writes the
    bundle of exactly what the preview shows. Returns a QWidget; its controls are exposed on ``._controls``
    for wiring/tests. This is UX over existing utilities — no new figure capability."""
    from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QLabel, QComboBox, QLineEdit, QCheckBox,
                                 QPushButton, QFileDialog)
    w = QWidget(); row = QHBoxLayout(w); row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(QLabel("Refine:"))

    size_dd = QComboBox()
    size_dd.addItems(['— size —', 'single_column', 'one_and_half_column', 'double_column'])
    size_dd.setToolTip("Preview at the final print width (sizes, NOT a journal-compliance claim).")

    def _on_size(_):
        if size_dd.currentIndex() > 0:
            refiner.size_preset(size_dd.currentText()); redraw()
    size_dd.currentIndexChanged.connect(_on_size)
    row.addWidget(size_dd)

    title_edit = QLineEdit(); title_edit.setPlaceholderText("title")
    title_edit.editingFinished.connect(
        lambda: (refiner.set(title=title_edit.text() or None), redraw()))
    row.addWidget(title_edit)

    row.addWidget(QLabel("y:"))
    yscale_dd = QComboBox(); yscale_dd.addItems(['linear', 'log', 'symlog'])
    yscale_dd.currentTextChanged.connect(lambda t: (refiner.set(y_scale=t), redraw()))
    row.addWidget(yscale_dd)

    sci_dd = QComboBox(); sci_dd.addItems(['default', 'sci', 'plain'])   # y-tick number format (shared ×10ⁿ)
    sci_dd.currentTextChanged.connect(lambda t: (refiner.set(sci_notation=t), redraw()))
    row.addWidget(sci_dd)

    legend_cb = QCheckBox("legend")
    legend_cb.toggled.connect(lambda on: (refiner.set(legend=bool(on)), redraw()))
    row.addWidget(legend_cb)

    row.addStretch(1)
    export_btn = QPushButton("Export bundle…")
    export_btn.setToolTip("Vector PDF/SVG (editable text) + high-DPI PNG + spec JSON + summary CSV — exactly "
                          "the refined preview.")

    def _on_export():
        p, _ = QFileDialog.getSaveFileName(parent, "Export figure bundle", "figure.png",
                                           "PNG (*.png);;All files (*)")
        if p:
            refiner.export_bundle(p)
    export_btn.clicked.connect(_on_export)
    row.addWidget(export_btn)

    w._controls = {'size': size_dd, 'title': title_edit, 'yscale': yscale_dd,
                   'legend': legend_cb, 'export': export_btn}
    return w


def _fill_table(table, summary):
    from PyQt5.QtWidgets import QTableWidgetItem
    cols = list(summary.columns)
    table.setColumnCount(len(cols)); table.setRowCount(len(summary))
    table.setHorizontalHeaderLabels(cols)
    for r in range(len(summary)):
        for c, col in enumerate(cols):
            val = summary.iloc[r][col]
            table.setItem(r, c, QTableWidgetItem('' if val is None else str(val)))
    table.resizeColumnsToContents()
