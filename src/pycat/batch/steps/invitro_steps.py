"""Batch replay handlers (invitro steps), moved from batch_step_registry.py (decomposition, 1.6.150).
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


def replay_ivf_preprocess(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro fluorescence preprocessing (no cell mask).

    Mirrors the interactive GUI's three distinct methods exactly (see
    invitro_fluor_ui.py's _ivf_preprocess/_task). This previously ALWAYS ran
    the heavy pre_process_image (white top-hat + LoG + WBNS + CLAHE)
    rolling-ball pipeline regardless of the recorded 'method' -- so a session
    recorded with 'gaussian' or 'log' replayed as a visibly different image
    than the one the interactive session actually produced, silently.
    """
    from pycat.toolbox.image_processing_tools import (
        gaussian_smooth_2d, apply_laplace_of_gauss_enhancement, pre_process_image)

    # RAW counts: pre_process_image's rolling ball is NOT scale-invariant.
    # `_normalize_to_float` subtracts the pedestal; the GUI does not. See _proc, above.
    image  = _raw_counts(state.get('preprocessed', state['image']))
    method = params.get('method', 'rolling-ball')
    sigma  = params.get('sigma', 1.5)
    ball   = params.get('ball_radius', 15)

    if method == 'gaussian':
        proc = gaussian_smooth_2d(image, sigma=sigma)
    elif method == 'log':
        proc = apply_laplace_of_gauss_enhancement(image, sigma=sigma)
    else:  # 'rolling-ball' (legacy default)
        proc = pre_process_image(image, ball_radius=ball, window_size=ball * 2)
    state['preprocessed'] = np.asarray(proc).astype(np.float32)

    _save_array(state['preprocessed'],
                output_dir / f"{image_path.stem}_ivf_preprocessed.tiff")
    print(f"[PyCAT Batch]   In vitro fluorescence preprocessing done ({method}).")


def replay_ivf_field_summary(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro field summary + partition coefficient from the droplet mask."""
    from pycat.toolbox.invitro_tools import field_summary, partition_coefficient_local
    import pandas as pd
    mask, img = _ivf_droplet_mask_and_image(state)
    # The recorded step may have measured a DIFFERENT layer than the primary
    # segmentation image -- e.g. a second channel/file (brightfield, a
    # partner marker) opened separately and recorded as this step's
    # 'image_layer'. Honour that recorded layer instead of silently always
    # using the primary image: field statistics computed on the wrong
    # channel are wrong data, not just a cosmetic mismatch.
    recorded_layer = params.get('image_layer')
    resolved = _resolve_image_layer(state, recorded_layer, fallback=None)
    if resolved is not None:
        img = _raw_counts(resolved)
        if img.ndim == 3:
            img = img[0]
    if mask is None or img is None:
        print('[PyCAT Batch]   IVF field summary skipped (no droplet mask/image in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    summ = field_summary(mask, img, mpx)

    # Partition coefficient: mirror the interactive GUI exactly
    # (partition_coefficient_local, not the older percentile-based
    # partition_coefficient_field) and honour the recorded dark-reference
    # choice. Recording 'use_dark_reference'/'dark_reference_layer' is
    # pointless if replay doesn't act on them -- a batch run must not
    # silently produce an uncorrected ratio when the GUI session used (or
    # explicitly declined) a dark reference, or vice versa.
    use_dark = bool(params.get('use_dark_reference', False))
    dark = None
    if use_dark:
        dark_layer = params.get('dark_reference_layer')
        dark = _resolve_image_layer(state, dark_layer, fallback=None)
        if dark is None:
            print(f"[PyCAT Batch]   IVF field summary: recorded dark reference "
                  f"'{dark_layer}' not found for this sample -- proceeding "
                  f"without one (result is an uncorrected ratio, not a true Kp).")
    part = partition_coefficient_local(
        img, mask.astype(np.int32), sample_type='in_vitro',
        dark_reference=dark, allow_no_reference=True)

    pd.DataFrame([summ]).to_csv(
        output_dir / f"{image_path.stem}_ivf_field_summary.csv", index=False)
    if isinstance(part.get('per_droplet_df'), pd.DataFrame):
        part['per_droplet_df'].to_csv(
            output_dir / f"{image_path.stem}_ivf_partition.csv", index=False)
    _kp_label = 'Kp' if part.get('is_true_kp') else 'raw ratio (no dark reference)'
    print(f"[PyCAT Batch]   IVF field summary: Phi={summ.get('volume_fraction', float('nan')):.3f}, "
          f"n={summ.get('n_droplets', 0)}, {_kp_label}="
          f"{part.get('partition_coefficient', float('nan')):.2f}.")


def replay_ivf_size_distribution(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro droplet size-distribution fit from the droplet mask."""
    from pycat.toolbox.invitro_tools import fit_size_distribution
    import pandas as pd, numpy as np
    import skimage as sk
    mask, _ = _ivf_droplet_mask_and_image(state)
    if mask is None:
        print('[PyCAT Batch]   IVF size distribution skipped (no droplet mask in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    props = sk.measure.regionprops(mask.astype(np.int32))
    radii = np.array([np.sqrt(p.area * mpx**2 / np.pi) for p in props])
    if len(radii) < 5:
        print(f'[PyCAT Batch]   IVF size distribution skipped ({len(radii)} droplets < 5).')
        return
    res = fit_size_distribution(radii, n_bins=int(params.get('n_bins', 30)))
    row = {k: v for k, v in res.items() if not hasattr(v, '__len__')}
    pd.DataFrame([row]).to_csv(
        output_dir / f"{image_path.stem}_ivf_size_distribution.csv", index=False)
    print(f"[PyCAT Batch]   IVF size distribution: {res.get('preferred_model', '?')} preferred.")


def replay_ivf_spatial_metrology(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro spatial metrology (whole field as one 'cell') from the droplet mask."""
    from pycat.toolbox.spatial_metrology_tools import get_puncta_centroids, run_all_spatial_metrics
    import numpy as np
    import pandas as pd

    def _flatten_scalars(prefix, obj, out):
        # Recursively collect scalar (non-array) values into flat columns.
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten_scalars(f"{prefix}_{k}" if prefix else str(k), v, out)
        elif np.isscalar(obj):
            out[prefix] = obj

    mask, _ = _ivf_droplet_mask_and_image(state)
    if mask is None:
        print('[PyCAT Batch]   IVF spatial metrology skipped (no droplet mask in state).')
        return
    mpx = state['data_instance'].data_repository.get('microns_per_pixel_sq', 1.0) ** 0.5
    H, W = mask.shape[:2]
    field_lbl = np.ones((H, W), dtype=np.int32); field_lbl[:2, :2] = 0
    coords_df = get_puncta_centroids(mask, field_lbl, mpx)
    rows = []
    for cl in [c for c in coords_df['cell_label'].unique() if c != 0]:
        sub = coords_df[coords_df['cell_label'] == cl]
        coords = sub[['y_um', 'x_um']].values
        if len(coords) < 2:
            continue
        res = run_all_spatial_metrics(coords, (field_lbl == cl), mpx)
        row = {'field_label': int(cl)}
        _flatten_scalars('', res, row)
        rows.append(row)
    if not rows:
        print('[PyCAT Batch]   IVF spatial metrology skipped (<2 droplets).')
        return
    pd.DataFrame(rows).to_csv(
        output_dir / f"{image_path.stem}_ivf_spatial_metrology.csv", index=False)
    print(f"[PyCAT Batch]   IVF spatial metrology: {len(rows)} field(s) analysed.")


def replay_ivf_segmentation(state: dict, image_path: Path, params: dict, output_dir: Path):
    """Replay in vitro fluorescence droplet segmentation (whole field, no cell mask).

    Must dispatch on the recorded 'method' (otsu/multiotsu/sauvola/rf/spot)
    exactly like the GUI's 5-branch _task() in invitro_fluor_ui.py's
    _ivf_segmentation -- this previously always ran the 'spot' (Advanced
    spot detection) branch regardless of what was recorded, the same bug
    class already found and fixed in replay_ivf_preprocess/
    replay_ivbf_segmentation. That branch expects a cellular-condensate-
    style preprocessed image; feeding it a plain Gaussian/LoG-preprocessed
    in-vitro image (as recorded for 'otsu'/'multiotsu'/etc.) crashes inside
    cell_mask_stretching's CLAHE call, which is the exact error this fixes.
    """
    import skimage as sk
    from skimage import filters, measure
    from pycat.utils.general_utils import remove_small_objects_compat

    pre = state.get('preprocessed', state['image'])
    raw = _normalize_to_float(state['image'])
    ball = state['data_instance'].data_repository.get('ball_radius', 15)
    method = params.get('method', 'otsu')
    p_minarea = int(params.get('min_area', 6))
    p_round = bool(params.get('reject_nonround', False))

    def _postfilter(binary):
        b = np.asarray(binary) > 0
        if p_minarea > 0:
            b = remove_small_objects_compat(b, p_minarea)
        lab = measure.label(b)
        if p_round:
            keep = np.zeros_like(lab)
            for pr in measure.regionprops(lab):
                if pr.area >= 5 and pr.solidity >= 0.85:
                    keep[lab == pr.label] = pr.label
            lab = measure.label(keep > 0)
        return lab.astype(np.int32)

    if method == 'otsu':
        t = filters.threshold_otsu(pre) * params.get('otsu_sensitivity', 1.0)
        labeled = _postfilter(pre > t)

    elif method == 'multiotsu':
        ts = filters.threshold_multiotsu(pre, classes=int(params.get('multiotsu_classes', 3)))
        cut = ts[-1] if params.get('multiotsu_upper', False) else ts[0]
        labeled = _postfilter(pre > cut)

    elif method == 'sauvola':
        from pycat.toolbox.segmentation_tools import local_thresholding_func
        binary = local_thresholding_func(
            pre, window_size=int(params.get('sauvola_window', 35)),
            k_val=params.get('sauvola_k', 0.0), mode='Sauvola')
        labeled = _postfilter(np.asarray(binary) > 0)

    elif method == 'rf':
        # Random Forest needs interactively-painted background/droplet
        # scribbles -- there is no file or recorded geometry to reconstruct
        # them from headlessly, so this method cannot be batch-replayed.
        print("[PyCAT Batch]   IVF segmentation (Random Forest) skipped in "
              "headless mode: the training scribbles were painted "
              "interactively and cannot be reconstructed from the recorded "
              "config.")
        return

    else:  # 'spot' -- Advanced spot detection (original pipeline)
        from pycat.toolbox.segmentation_tools import (
            segment_subcellular_objects, cell_mask_stretching)
        H, W = pre.shape
        whole = np.ones((H, W), dtype=bool)
        whole[:2, :2] = False
        cms = cell_mask_stretching(pre, whole.astype(int))
        refined, unrefined = segment_subcellular_objects(
            raw.copy(), cms.copy(), whole, 1, ball, cell_df=None,
            min_spot_radius=params.get('min_radius', 2.0),
            kurtosis_threshold=params.get('kurtosis', -3.0),
            local_snr_threshold=params.get('local_snr_threshold', 0.8),
            global_snr_threshold=0.8,
        )
        labeled = sk.measure.label(refined).astype(np.int32)

    state['ivf_droplet_mask'] = labeled
    state['cellpose_mask']    = labeled
    state['labeled_cells']    = labeled

    _save_array(labeled.astype(np.uint16),
                output_dir / f"{image_path.stem}_ivf_droplet_mask.tiff")
    print(f"[PyCAT Batch]   In vitro fluorescence segmentation ({method}): "
          f"{int(labeled.max())} droplets.")
