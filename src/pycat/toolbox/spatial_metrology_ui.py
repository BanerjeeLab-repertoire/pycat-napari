"""
PyCAT Spatial Metrology UI
============================
Widget for running the spatial metrology toolbox on condensate coordinates
extracted from the puncta segmentation mask.

Sits in the Condensate Analysis pipeline after Condensate Analysis has run
(requires puncta_df with centroids, cell_df, and labeled masks in state).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox,
    QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QLabel,
    QProgressBar, QComboBox,
)
from PyQt5.QtCore import QThread, pyqtSignal


class _SpatialWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)    # dict of results per cell
    error    = pyqtSignal(str)

    def __init__(self, coords_df, labeled_cells, microns_per_pixel,
                 checks, roi_coords, roi_type, n_radial_bins, kwargs):
        super().__init__()
        self._coords_df         = coords_df
        self._labeled_cells     = labeled_cells
        self._mpx               = microns_per_pixel
        self._checks            = checks
        self._roi_coords        = roi_coords
        self._roi_type          = roi_type
        self._n_radial_bins     = n_radial_bins
        self._kwargs            = kwargs

    def run(self):
        try:
            from pycat.toolbox.spatial_metrology_tools import (
                nearest_neighbour_distance, radial_localization_profile,
                local_object_density, ripleys_l, pair_correlation_function,
                voronoi_metrics, delaunay_metrics, minimum_spanning_tree,
                convex_hull_metrics, distance_to_roi,
            )

            df    = self._coords_df
            lc    = self._labeled_cells
            mpx   = self._mpx
            cells = [c for c in df['cell_label'].unique() if c != 0]
            n     = len(cells)
            all_results = {}

            for i, cell_lbl in enumerate(cells):
                sub    = df[df['cell_label'] == cell_lbl]
                coords = sub[['y_um', 'x_um']].values
                cmask  = (lc == cell_lbl).astype(bool)
                cell_area = float(cmask.sum()) * mpx**2

                r = {}
                c = self._checks
                if c.get('nnd'):
                    r['nnd'] = nearest_neighbour_distance(coords)
                if c.get('radial'):
                    r['radial'] = radial_localization_profile(
                        coords, cmask, self._n_radial_bins, mpx)
                if c.get('kde'):
                    r['kde_density'] = local_object_density(coords)
                if c.get('ripley'):
                    r['ripleys_l'] = ripleys_l(coords, cell_area)
                if c.get('pcf'):
                    r['pcf'] = pair_correlation_function(coords, cell_area)
                if c.get('voronoi'):
                    r['voronoi'] = voronoi_metrics(coords, cmask, mpx)
                if c.get('delaunay'):
                    r['delaunay'] = delaunay_metrics(coords)
                if c.get('mst'):
                    r['mst'] = minimum_spanning_tree(coords)
                if c.get('hull'):
                    r['convex_hull'] = convex_hull_metrics(coords, cell_area)
                if c.get('roi') and self._roi_coords is not None:
                    r['roi_distance'] = distance_to_roi(
                        coords, self._roi_coords, self._roi_type)

                all_results[cell_lbl] = r
                self.progress.emit(i + 1, n)

            self.finished.emit(all_results)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


def _results_to_dataframes(all_results: dict) -> dict[str, pd.DataFrame]:
    """Flatten per-cell spatial results into tidy DataFrames."""
    scalar_rows = []
    nnd_rows, radial_rows, ripley_rows, pcf_rows = [], [], [], []
    voronoi_rows, delaunay_rows = [], []

    for cell_lbl, r in all_results.items():
        row = {'cell_label': cell_lbl}

        if 'nnd' in r:
            row.update({
                'nnd_mean_um':   r['nnd']['mean_nnd'],
                'nnd_median_um': r['nnd']['median_nnd'],
                'nnd_std_um':    r['nnd']['std_nnd'],
                'nnd_cv':        r['nnd']['cv_nnd'],
            })
            for v in r['nnd']['nnd_values']:
                nnd_rows.append({'cell_label': cell_lbl, 'nnd_um': v})

        if 'kde_density' in r:
            row['kde_mean_density'] = r['kde_density']['mean_density']

        if 'mst' in r:
            m = r['mst']
            row.update({
                'mst_mean_edge_um':   m['mean_mst_edge_um'],
                'mst_median_edge_um': m['median_mst_edge_um'],
                'mst_total_length_um':m['total_mst_length_um'],
            })

        if 'convex_hull' in r:
            h = r['convex_hull']
            row.update({
                'hull_area_um2':       h['hull_area_um2'],
                'hull_perimeter_um':   h['hull_perimeter_um'],
                'hull_compactness':    h['hull_compactness'],
                'hull_occupancy':      h['occupancy_fraction'],
            })

        if 'roi_distance' in r:
            rd = r['roi_distance']
            row.update({
                'roi_dist_mean_um':   rd['mean_dist_um'],
                'roi_dist_median_um': rd['median_dist_um'],
                'roi_n_inside':       rd['n_inside'],
            })

        if 'voronoi' in r:
            vdf = r['voronoi']
            row['voronoi_regularity_cv'] = vdf.attrs.get('regularity_cv', np.nan)
            row['voronoi_mean_area_um2']  = vdf.attrs.get('mean_area_um2', np.nan)
            vdf = vdf.copy()
            vdf['cell_label'] = cell_lbl
            voronoi_rows.append(vdf)

        if 'delaunay' in r:
            ddf = r['delaunay']
            row['delaunay_mean_edge_um'] = ddf.attrs.get('mean_edge_um', np.nan)
            ddf = ddf.copy()
            ddf['cell_label'] = cell_lbl
            delaunay_rows.append(ddf)

        scalar_rows.append(row)

        if 'radial' in r:
            rdf = r['radial'].copy()
            rdf['cell_label'] = cell_lbl
            radial_rows.append(rdf)

        if 'ripleys_l' in r:
            ldf = r['ripleys_l'].copy()
            ldf['cell_label'] = cell_lbl
            ripley_rows.append(ldf)

        if 'pcf' in r:
            pdf = r['pcf'].copy()
            pdf['cell_label'] = cell_lbl
            pcf_rows.append(pdf)

    out = {}
    out['spatial_summary']  = pd.DataFrame(scalar_rows)
    if nnd_rows:       out['nnd_per_condensate'] = pd.DataFrame(nnd_rows)
    if radial_rows:    out['radial_profile']      = pd.concat(radial_rows, ignore_index=True)
    if ripley_rows:    out['ripleys_l']           = pd.concat(ripley_rows, ignore_index=True)
    if pcf_rows:       out['pcf']                 = pd.concat(pcf_rows, ignore_index=True)
    if voronoi_rows:   out['voronoi']             = pd.concat(voronoi_rows, ignore_index=True)
    if delaunay_rows:  out['delaunay']            = pd.concat(delaunay_rows, ignore_index=True)
    return out


def _add_spatial_metrology(ui_instance, layout=None, separate_widget=False):
    """
    Spatial metrology widget for the Condensate Analysis pipeline.
    Requires condensate analysis to have run first (needs puncta masks +
    labeled cell mask in the viewer).
    """
    grp  = QGroupBox("Spatial Metrology")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 4, 4, 4)
    form.setSpacing(5)

    desc = QLabel('Quantitative spatial analysis of condensate positions within cells.')
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:10px; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    # Layer selectors
    puncta_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd   = ui_instance.create_layer_dropdown(napari.layers.Labels)
    form.addRow("Condensate mask:", puncta_dd)
    form.addRow("Labeled cell mask:", cell_dd)

    # Analysis checkboxes
    checks_grp = QGroupBox("Analyses to run")
    checks_layout = QVBoxLayout(checks_grp)
    check_defs = [
        ('nnd',     'Nearest-neighbour distance (NND)'),
        ('radial',  'Radial localization profiling'),
        ('kde',     'Local object density (KDE)'),
        ('ripley',  "Ripley's L(r)"),
        ('pcf',     'Pair correlation function g(r)'),
        ('voronoi', 'Voronoi tessellation'),
        ('delaunay','Delaunay triangulation'),
        ('mst',     'Minimum spanning tree (MST)'),
        ('hull',    'Convex hull / occupancy metrics'),
        ('roi',     'Distance to user-defined ROI'),
    ]
    check_widgets = {}
    for key, label in check_defs:
        cb = QCheckBox(label)
        cb.setChecked(key not in ('voronoi', 'roi'))  # most on by default
        checks_layout.addWidget(cb)
        check_widgets[key] = cb
    form.addRow(checks_grp)

    # ROI options — shown only when ROI distance checked
    roi_grp = QWidget()
    roi_form = QFormLayout(roi_grp)
    roi_shapes_dd = ui_instance.create_layer_dropdown(napari.layers.Shapes)
    roi_type_dd = QComboBox()
    roi_type_dd.addItems(['polygon', 'line', 'point'])
    roi_form.addRow("ROI shapes layer:", roi_shapes_dd)
    roi_form.addRow("ROI type:", roi_type_dd)
    roi_grp.setVisible(False)
    form.addRow(roi_grp)

    check_widgets['roi'].stateChanged.connect(
        lambda s: roi_grp.setVisible(bool(s)))

    # Radial bins
    n_bins_spin = QSpinBox()
    n_bins_spin.setRange(3, 50)
    n_bins_spin.setValue(10)
    form.addRow("Radial profile bins:", n_bins_spin)

    # Progress
    prog_bar   = QProgressBar()
    prog_bar.setVisible(False)
    prog_label = QLabel("")
    prog_label.setVisible(False)
    form.addRow(prog_label)
    form.addRow(prog_bar)

    run_btn = QPushButton("▶  Run Spatial Metrology")

    def _on_run():
        # Get layers
        try:
            pmask = ui_instance.viewer.layers[puncta_dd.currentText()].data
            cmask = ui_instance.viewer.layers[cell_dd.currentText()].data
        except KeyError as e:
            napari_show_warning(f"Layer not found: {e}")
            return

        # Get coordinates from data repository or compute from mask
        dr  = ui_instance.central_manager.active_data_class.data_repository
        mpx = float(dr.get('microns_per_pixel_sq', 1.0))**0.5

        from pycat.toolbox.spatial_metrology_tools import get_puncta_centroids
        coords_df = get_puncta_centroids(pmask, cmask, mpx)

        if coords_df.empty:
            napari_show_warning("No condensates found in the mask.")
            return

        # Get ROI coords if requested
        roi_coords = None
        if check_widgets['roi'].isChecked():
            try:
                shapes_layer = ui_instance.viewer.layers[
                    roi_shapes_dd.currentText()]
                # napari shapes data: list of (N,2) arrays in (y,x) px order
                all_pts = np.vstack(shapes_layer.data) * mpx
                roi_coords = all_pts
            except Exception as e:
                napari_show_warning(f"Could not read ROI shapes: {e}")
                return

        checks = {k: cb.isChecked() for k, cb in check_widgets.items()}
        n_cells = coords_df['cell_label'].nunique()
        prog_bar.setMaximum(n_cells)
        prog_bar.setValue(0)
        prog_bar.setVisible(True)
        prog_label.setText(f"Analysing cell 0 / {n_cells}…")
        prog_label.setVisible(True)
        run_btn.setEnabled(False)

        worker = _SpatialWorker(
            coords_df, cmask, mpx, checks,
            roi_coords, roi_type_dd.currentText(),
            n_bins_spin.value(), {}
        )
        ui_instance._spatial_worker = worker

        def _on_progress(done, total):
            prog_bar.setValue(done)
            prog_label.setText(f"Analysing cell {done} / {total}…")

        def _on_finished(all_results):
            prog_bar.setVisible(False)
            prog_label.setVisible(False)
            run_btn.setEnabled(True)

            dfs = _results_to_dataframes(all_results)
            # Store in data repository
            for k, v in dfs.items():
                dr[f'spatial_{k}'] = v

            # Show in dialog
            from pycat.ui.ui_utils import show_dataframes_dialog
            tables = [(k.replace('_', ' ').title(), v.round(4))
                      for k, v in dfs.items()]
            show_dataframes_dialog("Spatial Metrology Results", tables)

            # Record for batch
            ui_instance._record('spatial_metrology', {
                'puncta_layer': puncta_dd.currentText(),
                'cell_layer':   cell_dd.currentText(),
                'analyses':     [k for k, cb in check_widgets.items()
                                 if cb.isChecked()],
                'n_cells':      len(all_results),
            })
            napari_show_info(
                f"Spatial metrology complete: {len(all_results)} cells analysed, "
                f"{len(dfs)} result tables generated."
            )

        def _on_error(msg):
            prog_bar.setVisible(False)
            prog_label.setVisible(False)
            run_btn.setEnabled(True)
            napari_show_warning("Spatial metrology error — see terminal.")
            print(f"[PyCAT Spatial] ERROR:\n{msg}")

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.start()

    run_btn.clicked.connect(_on_run)
    form.addRow("", run_btn)

    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    outer  = QVBoxLayout(widget)
    outer.setSpacing(6)
    outer.setContentsMargins(2, 2, 2, 2)
    outer.addWidget(grp)
    ui_instance._add_widget_to_layout_or_dock(
        widget, layout, separate_widget, "Spatial Metrology"
    )
