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


def mount_batch_workspace(output_dir, source_paths, central_manager):
    """Build the batch brushable workspace: the cellular plots + cell/condensate tables (brushing
    cross-view by `entity_id`), plus a `BatchCropView` that pulls each clicked object's image OFFLINE from
    its source file. Returns the `BrushableWorkspace`, or None if there is nothing to show. The caller
    docks/shows it (there is no live viewer to reveal into at batch end)."""
    from pycat.ui.brushable_workspace import BrushableWorkspace

    service = getattr(central_manager, 'selection', None)
    tables = assemble_batch_object_tables(output_dir, source_paths)
    cell_df = tables.get('cell')
    puncta_df = tables.get('puncta')
    if service is None or (cell_df is None and puncta_df is None):
        return None

    ws = BrushableWorkspace(service)
    if cell_df is not None:
        cols = cell_df.columns
        if 'intensity_total' in cols and 'puncta_intensity_total' in cols:
            ws.add_plot(cell_df, 'intensity_total', 'puncta_intensity_total', 'batch.cell.csat',
                        title='Csat (batch)')
        if 'intensity_total' in cols and 'cell_xor_puncta_int_total' in cols:
            ws.add_plot(cell_df, 'intensity_total', 'cell_xor_puncta_int_total', 'batch.cell.dilute',
                        title='Dilute phase (batch)')
        ws.add_offline_crop_view(cell_df, 'batch.cell.crop', title='Object image (from batch)')
        ws.add_table(cell_df, 'batch.cell.table', title='Cells (all images)')
    if puncta_df is not None:
        ws.add_table(puncta_df, 'batch.condensate.table', title='Condensates (all images)')
    return ws

