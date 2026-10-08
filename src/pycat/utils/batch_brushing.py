"""**Turn a finished batch's per-image CSVs into brush-ready tables that resolve to their source images.**

Phase 4 of the brushable-results-workspace spec. The consolidated table (`consolidated_long.csv`) carries a
stable `entity_id` per object — enough to brush plots ↔ tables cross-view — but the bbox was melted into
`measurement`/`value` rows and the source path was never stored, so a batch point could not become an
*image*. The per-image `<stem>_cell_df.csv` / `<stem>_puncta_df.csv` files DO keep the bbox and the entity
id. This reads them back, tags each row with its originating image path, and concatenates by object type —
so each row's `ObjectRef.from_row(...)` is `is_resolvable_offline()` (source path + bbox), and
`resolve_offline` can open the image and slice the crop with **no session and no re-segmentation**.
"""
from __future__ import annotations

import pathlib

import pandas as pd

from pycat.utils.consolidated_table import DEFAULT_OBJECT_TABLES, records_from_output_dir

SOURCE_PATH_COLUMN = '_pycat_source_path'


def assemble_batch_object_tables(output_dir, source_paths, object_tables=DEFAULT_OBJECT_TABLES):
    """Read every image's per-image object CSVs from ``output_dir/<stem>/`` and concatenate them by object
    type, tagging each row with the image it came from.

    ``source_paths`` are the batch's input image paths (``BatchWorker.files``) — the stem locates the
    per-image folder and the full path is what makes the row resolve offline. Returns
    ``{object_type: DataFrame}`` (``'cell'``, ``'puncta'``, …); an image that produced no objects simply
    contributes no rows. Pure — point it at a temp dir in a test.
    """
    out = pathlib.Path(output_dir)
    by_type: dict = {}
    for src in source_paths:
        src = pathlib.Path(src)
        file_output = out / src.stem
        for object_type, df in records_from_output_dir(file_output, src.stem, object_tables=object_tables):
            df = df.copy()
            df[SOURCE_PATH_COLUMN] = str(src)       # the row's own image — each batch row a different one
            by_type.setdefault(object_type, []).append(df)
    return {t: pd.concat(dfs, ignore_index=True) for t, dfs in by_type.items() if dfs}


_EXCLUDED_FLAGS = ('ring_rejected', 'below_contrast_floor')


def with_cell_context(puncta_df, cell_df):
    """The condensate table with each condensate's own total intensity (``condensate_intensity_total`` =
    mean x area, on the image it was measured on) and its cell's (``cell_intensity_total``), joined on the
    image and the cell label — so one condensate can be plotted against the cell it sits in."""
    p = puncta_df.copy()
    if {'intensity_mean', 'area'} <= set(p.columns):
        p['condensate_intensity_total'] = p['intensity_mean'] * p['area']
    if cell_df is not None and {'label', 'intensity_total'} <= set(cell_df.columns) and 'cell label' in p.columns:
        key = [SOURCE_PATH_COLUMN] if SOURCE_PATH_COLUMN in p.columns and SOURCE_PATH_COLUMN in cell_df.columns else []
        cells = cell_df[key + ['label', 'intensity_total']].rename(
            columns={'label': 'cell label', 'intensity_total': 'cell_intensity_total'})
        p = p.merge(cells, on=key + ['cell label'], how='left')
    return p


def counted_condensates(puncta_df):
    """The rows that are condensates: not optical halo fragments, not under the contrast floor (those stay
    listed in the table, flagged, but are not plotted as condensates)."""
    keep = pd.Series(True, index=puncta_df.index)
    for flag in _EXCLUDED_FLAGS:
        if flag in puncta_df.columns:
            keep &= ~puncta_df[flag].fillna(False).astype(bool)
    return puncta_df[keep]


def mount_batch_workspace(output_dir, source_paths, central_manager, viewer=None):
    """Build the batch brushable workspace over EVERY image's cells and condensates: plots and tables
    brushing cross-view by `entity_id`, and — with a ``viewer`` — a `BatchImageNavigator`, so a selected
    object opens its own image in napari, centred and outlined, and clicking an object there selects it
    back. Without a viewer the selected object's crop is read offline from its source file instead.

    Plots: cell total fluorescence vs number of condensates; each condensate's total intensity vs its
    cell's; Csat and dilute phase; and a builder for any two columns of either table. Returns the
    `BrushableWorkspace`, or None if there is nothing to show."""
    from pycat.ui.brushable_workspace import BrushableWorkspace

    service = getattr(central_manager, 'selection', None)
    tables = assemble_batch_object_tables(output_dir, source_paths)
    cell_df = tables.get('cell')
    puncta_df = tables.get('puncta')
    if service is None or (cell_df is None and puncta_df is None):
        return None
    if puncta_df is not None:
        puncta_df = with_cell_context(puncta_df, cell_df)

    ws = BrushableWorkspace(service)
    if cell_df is not None:
        cols = cell_df.columns
        if 'intensity_total' in cols and 'number_of_puncta' in cols:
            ws.add_plot(cell_df, 'intensity_total', 'number_of_puncta', 'batch.cell.count',
                        title='Cells: total fluorescence vs number of condensates')
        if 'intensity_total' in cols and 'puncta_intensity_total' in cols:
            ws.add_plot(cell_df, 'intensity_total', 'puncta_intensity_total', 'batch.cell.csat',
                        title='Csat (batch)')
        if 'intensity_total' in cols and 'cell_xor_puncta_int_total' in cols:
            ws.add_plot(cell_df, 'intensity_total', 'cell_xor_puncta_int_total', 'batch.cell.dilute',
                        title='Dilute phase (batch)')
    if puncta_df is not None and {'cell_intensity_total', 'condensate_intensity_total'} <= set(puncta_df.columns):
        ws.add_plot(counted_condensates(puncta_df), 'cell_intensity_total', 'condensate_intensity_total',
                    'batch.condensate.vs_cell', title="Condensates: intensity vs their cell's total")
    ws.add_plot_builder({'Cells': cell_df, 'Condensates': puncta_df}, 'batch.builder')
    if viewer is not None:
        from pycat.ui.batch_navigator import BatchImageNavigator
        ws.add_view_widget(BatchImageNavigator(viewer, output_dir, {'cell': cell_df, 'condensate': puncta_df},
                                               service, 'batch.image'))
    elif cell_df is not None:
        ws.add_offline_crop_view(cell_df, 'batch.cell.crop', title='Object image (from batch)')
    if cell_df is not None:
        ws.add_table(cell_df, 'batch.cell.table', title='Cells (all images)')
    if puncta_df is not None:
        ws.add_table(puncta_df, 'batch.condensate.table', title='Condensates (all images)')
    return ws
