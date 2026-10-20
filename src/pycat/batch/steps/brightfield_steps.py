"""Batch replay handlers (brightfield steps), moved from batch_step_registry.py (decomposition, 1.6.150).
Handlers unchanged; each has signature (state, image_path, params, output_dir). The _STEP_MAP dispatch
table stays in batch_step_registry.py and imports these."""
from __future__ import annotations

from __future__ import annotations
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from pycat.file_io.image_reader import open_image
from pycat.batch.steps._common import (
    _get_data, _derive_split_companion_path, _source_path_for_recorded_channel, _load_image, _resolve_channel_for_layer, _save_array, _raw_counts, _normalize_to_float, _resolve_image_layer, _ivf_droplet_mask_and_image)
from pycat.kernel import OperationService


def _resolve_flat_field_reference(state, params, step_label):
    """Best-effort resolution of a recorded flat-field reference layer.

    The reference is an interactively-selected Image layer, not necessarily
    loadable from a fixed file path -- it may be another channel of the same
    multi-channel file (state['channels_by_name'], populated by
    channel_assignment) or a companion file from a split-file recording
    (also lands in channels_by_name, see replay_open_image). If neither has
    it, batch replay cannot reconstruct it; warn explicitly rather than
    silently proceeding without it (background_image=None self-estimates the
    illumination field instead, a different and less accurate result).
    """
    if not params.get('use_flat_field_reference'):
        return None
    layer_name = params.get('flat_field_reference_layer')
    ref = _resolve_image_layer(state, layer_name, fallback=None)
    if ref is None:
        print(f"[PyCAT Batch]   {step_label}: recorded flat-field reference "
              f"'{layer_name}' could not be located for this sample -- "
              f"proceeding WITHOUT one (self-estimated illumination field "
              f"instead, a different result than the interactive session).")
    return ref


def replay_bf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield preprocessing (flat-field, BG subtract, halo, CLAHE)."""
    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    ref = _resolve_flat_field_reference(state, params, 'BF preprocessing')
    result = OperationService.produce(
        'bf_preprocess.stages', {'image': image},
        {'bg_kernel': params.get('bg_kernel', 50), 'halo_weight': params.get('halo_weight', 0.35),
         'background_image': ref})
    state['bf_enhanced']      = result['enhanced']
    state['bf_bg_subtracted'] = result['bg_subtracted']

    _save_array(result['enhanced'].astype(np.float32),
                output_dir / f"{image_path.stem}_bf_enhanced.tiff")
    print("[PyCAT Batch]   BF preprocessing done.")


def replay_bf_condensate_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield dark-blob condensate segmentation."""

    enhanced = state.get('bf_enhanced')
    if enhanced is None:
        print("[PyCAT Batch] BF condensate segmentation: no enhanced image — skipping.")
        return

    labeled = OperationService.produce(
        'bf_segment', {'enhanced_image': enhanced},
        {
            'min_diameter_px': params.get('min_diameter_px', 3.0),
            'max_diameter_px': params.get('max_diameter_px', 50.0),
            'min_circularity': params.get('min_circularity', 0.5),
        })
    state['bf_condensate_mask'] = labeled
    state['cellpose_mask']      = labeled   # alias for downstream compatibility
    state['labeled_cells']      = labeled
    # The detection limit, for a size fit that follows (the in-vitro steps read it -- see _ivbf_droplets).
    state['bf_min_area_px']     = int(np.pi * (float(params.get('min_diameter_px', 3.0)) / 2) ** 2)
    state['bf_source']          = _raw_counts(state.get('preprocessed', state['image']))

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_bf_condensate_mask.tiff")
    print(f"[PyCAT Batch]   BF condensate segmentation: {int(labeled.max())} spots.")


def replay_bf_condensate_analysis(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Per-condensate brightfield metrics (optical density, area, shape) on the directly-labelled condensate
    mask — the CELL-LESS in-vitro path, ``bf_condensate_metrics(raw, mask, None, mpx)`` (invitro_bf_ui). This is
    the brightfield equivalent of ``puncta_analysis_func``: it measures each labelled condensate as a first-class
    object, so it fits the navigator's brightfield chain where ``condensate_analysis`` (needs a puncta_mask) and
    ``cell_analysis`` (cell-sized min-area filter) do not."""
    mask = state.get('bf_condensate_mask')
    if mask is None or int(np.asarray(mask).max()) == 0:
        print(f"[PyCAT Batch]   BF condensate analysis skipped for {image_path.name}: no condensates.")
        return
    # RAW counts, not a normalised image: optical density is -log10(I / I0); min-max normalisation moves the
    # zero point and makes the OD of the darkest (strongest) condensate diverge. See invitro_bf_ui._on_run.
    raw = _raw_counts(state.get('image'))
    data_instance = state['data_instance']
    from pycat.utils.pixel_size import pixel_size_um_or_default
    mpx = pixel_size_um_or_default(data_instance.data_repository, context='batch brightfield condensates')
    df = OperationService.produce(
        'brightfield.condensate_metrics',
        {'image': raw, 'labeled_condensates': np.asarray(mask), 'labeled_cells': None, 'microns_per_pixel': mpx},
        {'bg_kernel': params.get('bg_kernel', 50)})
    state['bf_condensate_df'] = df
    data_instance.set_data('bf_condensate_df', df)
    df.to_csv(output_dir / f"{image_path.stem}_bf_condensate_df.csv", index=False)
    print(f"[PyCAT Batch]   BF condensate analysis done: {len(df)} condensates.")


def replay_bf_cell_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay brightfield Cellpose cell segmentation."""
    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    diameter = params.get('cell_diameter', 80)

    try:
        from cellpose import models
        try:
            model = models.CellposeModel(pretrained_model='brightfield')
        except Exception:  # broad-ok: optional_probe — batch replay best-effort model probe → fallback; a per-step failure must not abort the whole batch
            model = models.CellposeModel(pretrained_model='cyto2')
        masks, _, _ = model.eval(image, diameter=diameter, channels=[0, 0])
        state['bf_cell_mask'] = masks.astype(np.int32)
        _save_array(masks.astype(np.uint16),
                    output_dir / f"{image_path.stem}_bf_cell_mask.tiff")
        print(f"[PyCAT Batch]   BF cell segmentation: {int(masks.max())} cells.")
    except Exception as e:  # broad-ok: batch_step — batch replay robustness: logged, this step is skipped, the batch continues
        print(f"[PyCAT Batch]   BF cell segmentation failed: {e} — skipping.")


def replay_ivbf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro brightfield preprocessing."""
    # RAW counts: this feeds `pre_process_image`, whose rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image = _raw_counts(state.get('preprocessed', state['image']))
    ref = _resolve_flat_field_reference(state, params, 'IVBF preprocessing')
    result = OperationService.produce(
        'bf_preprocess.stages', {'image': image},
        {'bg_kernel': params.get('bg_kernel', 60), 'halo_weight': params.get('halo_weight', 0.3),
         'background_image': ref})
    state['ivbf_enhanced']      = result['enhanced']
    state['ivbf_bg_subtracted'] = result['bg_subtracted']
    state['ivbf_source']        = image

    _save_array(result['enhanced'].astype(np.float32),
                output_dir / f"{image_path.stem}_ivbf_enhanced.tiff")
    print("[PyCAT Batch]   In vitro BF preprocessing done.")


def replay_ivbf_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro brightfield droplet segmentation.

    Must dispatch on the recorded 'method' (texture/dog/invert_reconcile/
    intensity) exactly like the GUI's 4-option dropdown -- this previously
    never read 'method'/'texture_window'/'split' at all, so
    segment_bf_condensates always ran its function-default 'intensity' path
    regardless of what was recorded, the same bug class already found and
    fixed in replay_ivf_preprocess.
    """

    enhanced = state.get('ivbf_enhanced')
    if enhanced is None:
        print("[PyCAT Batch] IVBF segmentation: no enhanced image — skipping.")
        return

    labeled = OperationService.produce(
        'bf_segment', {'enhanced_image': enhanced},
        {
            'min_diameter_px': params.get('min_d', 4.0),
            'max_diameter_px': params.get('max_d', 200.0),
            'min_circularity': params.get('min_circularity', 0.5),
            'method': params.get('method', 'intensity'),
            'texture_window': params.get('texture_window', 9),
            'split_touching': params.get('split', True),
        })
    state['ivbf_droplet_mask'] = labeled
    state['cellpose_mask']     = labeled
    state['labeled_cells']     = labeled
    # The segmentation's detection limit, as the panel stores it -- the size fit is truncated there (A4).
    state['ivbf_min_area_px']  = int(np.pi * (float(params.get('min_d', 4.0)) / 2) ** 2)

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_ivbf_droplet_mask.tiff")
    print(f"[PyCAT Batch]   In vitro BF segmentation: {int(labeled.max())} droplets.")


# ── 1.7.005: the brightfield panels' result steps (recorded, never registered -- Completion Spec E2) ─────────

def _bf_mpx(state, context):
    from pycat.utils.pixel_size import pixel_size_um_or_default
    return pixel_size_um_or_default(state['data_instance'].data_repository, context=context)


def _bf_source(state, image_path):
    return state['data_instance'].data_repository.get('file_path') or str(image_path)


def replay_bf_od_metrics(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Per-condensate OD metrics on raw counts, with the cell each sits in when a cell mask was used."""
    mask = state.get('bf_condensate_mask')
    if mask is None or int(np.asarray(mask).max()) == 0:
        print(f'[PyCAT Batch]   BF OD metrics skipped for {image_path.name}: no condensate mask.')
        return
    cell_layer = params.get('cell_layer')
    cells = state.get('bf_cell_mask') if cell_layer and str(cell_layer).lower() != 'none' else None
    df = OperationService.produce(
        'brightfield.od_metrics',
        {'raw': _raw_counts(state['image']), 'condensate_mask': mask, 'cell_labels': cells,
         'microns_per_pixel': _bf_mpx(state, 'batch bf_od_metrics'), 'source_path': _bf_source(state, image_path)})
    state['bf_condensate_df'] = df
    state['data_instance'].set_data('bf_condensate_df', df)
    df.to_csv(output_dir / f'{image_path.stem}_bf_condensate_df.csv', index=False)
    print(f'[PyCAT Batch]   BF OD metrics: {len(df)} condensates.')


def replay_bf_per_cell_summary(state: dict, image_path: Path, params: dict, output_dir: Path):
    cond = state.get('bf_condensate_df')
    cells = state.get('bf_cell_mask')
    if cond is None or len(cond) == 0 or cells is None:
        print(f'[PyCAT Batch]   BF per-cell summary skipped for {image_path.name}: needs OD metrics and a cell mask.')
        return
    df = OperationService.produce(
        'brightfield.per_cell',
        {'condensate_df': cond, 'cell_labels': cells,
         'microns_per_pixel': _bf_mpx(state, 'batch bf_per_cell_summary'),
         'source_path': _bf_source(state, image_path)})
    state['data_instance'].set_data('bf_per_cell_df', df)
    df.to_csv(output_dir / f'{image_path.stem}_bf_per_cell_df.csv', index=False)
    print(f'[PyCAT Batch]   BF per-cell summary: {len(df)} cells.')


def _ivbf_droplets(state):
    """``(droplet mask, raw source, detection limit in px)``: the in-vitro BF segmentation's, or -- when the
    Navigator ran the same ops (``bf_preprocess`` -> ``bf_segment``) under their brightfield step names -- that
    segmentation's. ``(None, None, None)`` with neither."""
    if state.get('ivbf_droplet_mask') is not None:
        return state['ivbf_droplet_mask'], state.get('ivbf_source'), state.get('ivbf_min_area_px')
    if state.get('bf_condensate_mask') is not None:
        return state['bf_condensate_mask'], state.get('bf_source'), state.get('bf_min_area_px')
    return None, None, None


def replay_ivbf_field_summary(state: dict, image_path: Path, params: dict, output_dir: Path):
    mask = _ivbf_droplets(state)[0]
    if mask is None:
        print(f'[PyCAT Batch]   IVBF field summary skipped for {image_path.name}: no droplet mask.')
        return
    summary, summary_df, per_drop = OperationService.produce(
        'brightfield.invitro_field_summary',
        {'raw': _raw_counts(state['image']), 'droplet_mask': mask,
         'microns_per_pixel': _bf_mpx(state, 'batch ivbf_field_summary'),
         'source_path': _bf_source(state, image_path)})
    di = state['data_instance']
    di.set_data('ivbf_field_summary', summary)
    di.set_data('ivbf_droplet_df', per_drop)
    summary_df.to_csv(output_dir / f'{image_path.stem}_ivbf_field_summary.csv', index=False)
    per_drop.to_csv(output_dir / f'{image_path.stem}_ivbf_droplet_df.csv', index=False)
    print(f"[PyCAT Batch]   IVBF field summary: {summary.get('n_droplets', 0)} droplets.")


def replay_ivbf_spatial_metrology(state: dict, image_path: Path, params: dict, output_dir: Path):
    from pycat.toolbox.spatial_metrology_ui import _results_to_dataframes
    mask = state.get('ivbf_droplet_mask')
    if mask is None:
        print(f'[PyCAT Batch]   IVBF spatial metrology skipped for {image_path.name}: no droplet mask.')
        return
    results = OperationService.produce(
        'spatial_metrology.whole_field',
        {'object_mask': mask, 'microns_per_pixel': _bf_mpx(state, 'batch ivbf_spatial_metrology')})
    if not results:
        print(f'[PyCAT Batch]   IVBF spatial metrology skipped for {image_path.name}: fewer than 2 droplets.')
        return
    for name, df in _results_to_dataframes(results).items():
        slug = ''.join(c if c.isalnum() else '_' for c in name.lower()).strip('_')
        df.to_csv(output_dir / f'{image_path.stem}_ivbf_spatial_{slug}.csv', index=False)
    print('[PyCAT Batch]   IVBF spatial metrology done.')


def replay_ivbf_size_distribution(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Size-distribution fit (truncated at the segmentation's detection limit) + one droplet's contact angle."""
    mask, source, min_area_px = _ivbf_droplets(state)
    if mask is None:
        print(f'[PyCAT Batch]   IVBF size distribution skipped for {image_path.name}: no droplet mask.')
        return
    fit, fit_df, contact = OperationService.produce(
        'brightfield.invitro_size_distribution',
        {'droplet_mask': mask, 'raw_source': source,
         'microns_per_pixel': _bf_mpx(state, 'batch ivbf_size_distribution'),
         'n_bins': int(params.get('n_bins', 30) or 30), 'min_area_px': min_area_px,
         'contact_label': int(params.get('contact_label', 0) or 0)})
    if fit is None:
        print(f'[PyCAT Batch]   IVBF size distribution skipped for {image_path.name}: no droplets.')
        return
    state['data_instance'].set_data('ivbf_size_dist', fit)
    fit_df.to_csv(output_dir / f'{image_path.stem}_ivbf_size_distribution.csv', index=False)
    if contact is not None:
        contact.to_csv(output_dir / f'{image_path.stem}_ivbf_contact_angle.csv', index=False)
    print(f"[PyCAT Batch]   IVBF size distribution: {fit.get('preferred_model', '?')} preferred.")
