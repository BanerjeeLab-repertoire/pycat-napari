"""Batch replay of the fibril pipeline's image-chain steps (1.7.005, Completion Spec B / E).

The fibril panel is a chain of whole-image operations, each run on whichever layer the user had selected and
each adding a new layer named after its input ("Bilateral Filtered X", "Peak & Edge Enhanced X", ...). Seven of
its steps had no step key, so they were never recorded and a batch could not reproduce the pipeline. The panel
now records each step with the layer it ran on; these replays resolve that layer by name -- a layer an earlier
replay produced first, then the loader/preprocessing slots via ``_resolve_image_layer`` -- run the same
computation the button runs, and store the output under the name the GUI gives it, so the next step finds it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from pycat.batch.steps._common import _get_data, _resolve_image_layer, _save_array
from pycat.kernel import OperationService


def _derived(state):
    return state.setdefault('layers_by_name', {})


def _layer(state, name):
    """The array a recorded layer name refers to: a layer an earlier replay produced, else a loader/preprocessing
    slot; else the most recent derived layer (the GUI's active layer), else the preprocessed or raw image."""
    derived = _derived(state)
    if name and name in derived:
        return derived[name]
    fallback = state.get('_last_derived')
    if fallback is None:
        fallback = state.get('preprocessed', state.get('image'))
    return _resolve_image_layer(state, name, fallback=fallback) if name else fallback


def _store(state, name, arr, output_dir: Path, image_path: Path, suffix: str):
    _derived(state)[name] = arr
    state['_last_derived'] = arr
    _save_array(np.asarray(arr), output_dir / f'{image_path.stem}_{suffix}.tiff')


def replay_bilateral_filter(state: dict, image_path: Path, params: dict, output_dir: Path):
    src = params.get('layer')
    out = OperationService.produce('bilateral', {'image': np.asarray(_layer(state, src))},
                                   {'radius': float(params.get('radius', 2) or 2)})
    _store(state, f'Bilateral Filtered {src}', out, output_dir, image_path, 'bilateral')
    print('[PyCAT Batch]   Bilateral filter done.')


def replay_peak_edge_enhancement(state: dict, image_path: Path, params: dict, output_dir: Path):
    import math
    src = params.get('layer')
    radius = params.get('ball_radius') or _get_data(state['data_instance'], 'ball_radius', 5)
    out = OperationService.produce('peak_edge', {'image': np.asarray(_layer(state, src))},
                                   {'ball_radius': math.ceil(float(radius))})
    _store(state, f'Peak & Edge Enhanced {src}', out, output_dir, image_path, 'peak_edge')
    print('[PyCAT Batch]   Peak & edge enhancement done.')


def replay_morphological_gaussian(state: dict, image_path: Path, params: dict, output_dir: Path):
    src = params.get('layer')
    out = OperationService.produce('morph_gaussian', {'image': np.asarray(_layer(state, src))},
                                   {'filter_size': int(params.get('filter_size', 2) or 2)})
    _store(state, f'Filtered {src}', out, output_dir, image_path, 'morph_gaussian')
    print('[PyCAT Batch]   Morphological Gaussian done.')


def replay_local_thresholding(state: dict, image_path: Path, params: dict, output_dir: Path):
    src = params.get('layer')
    mask = np.asarray(OperationService.produce(
        'local_threshold', {'image': np.asarray(_layer(state, src))},
        {'window_size': int(params.get('window_size', 15)), 'k_val': float(params.get('k_value', 0.0)),
         'mode': params.get('method', 'AND')})).astype(np.int32)
    _derived(state)[f'Locally Thresholded {src}'] = mask
    state['fibril_mask'] = mask
    _save_array(mask.astype(np.uint8), output_dir / f'{image_path.stem}_local_threshold_mask.tiff')
    print(f'[PyCAT Batch]   Local thresholding ({params.get("method", "AND")}) done.')


def replay_label_binary_mask(state: dict, image_path: Path, params: dict, output_dir: Path):
    import skimage as sk
    name = params.get('mask_layer')
    mask = _derived(state).get(name)
    if mask is None:
        mask = state.get('fibril_mask')
    if mask is None:
        print(f'[PyCAT Batch]   Label binary mask skipped for {image_path.name}: no mask named {name!r}.')
        return
    labels = sk.measure.label(np.asarray(mask) > 0).astype(np.int32)
    _derived(state)[f'Labeled {name}'] = labels
    state['fibril_labels'] = labels
    _save_array(labels.astype(np.uint32), output_dir / f'{image_path.stem}_fibril_labels.tiff')
    print(f'[PyCAT Batch]   Labelled {int(labels.max())} connected components.')


def replay_measure_binary_mask(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Whole-mask statistics of the recorded mask over the recorded image -> ``<stem>_binary_mask_stats.csv``."""
    import pandas as pd

    from pycat.toolbox.masks.measurement import _um2_per_px_gated
    name = params.get('mask_layer')
    mask = _derived(state).get(name)
    if mask is None:
        mask = state.get('fibril_labels', state.get('fibril_mask'))
    if mask is None:
        print(f'[PyCAT Batch]   Measure binary mask skipped for {image_path.name}: no mask named {name!r}.')
        return
    image = np.asarray(_layer(state, params.get('image_layer')))
    props = OperationService.produce(
        'masks.measure_binary_mask',
        {'mask': mask, 'image': image, 'um2_per_px': _um2_per_px_gated(state['data_instance'].data_repository)})
    df = pd.DataFrame([props]).round(4)
    state['data_instance'].set_data('binary_mask_stats_df', df)
    df.to_csv(output_dir / f'{image_path.stem}_binary_mask_stats.csv', index=False)
    print(f"[PyCAT Batch]   Binary mask measured: area {int(props['Area'])} px.")
