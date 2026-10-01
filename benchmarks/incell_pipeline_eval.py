"""**How does the shipped 2D cellular pipeline score against the human in-cell masks?**

Drives the 2D cellular fluorescence (condensate) workflow headlessly, step for step as the GUI
runs it at its defaults (mapped from `ui/analysis_methods_ui.CondensateAnalysis` and the batch
replay steps): x2 upscale, pre-process, enhanced rolling-ball/Gaussian background removal,
Cellpose on DAPI, cell analysis, then `segment_subcellular_objects` per cell with the image-wide
intensity stats, multiscale detection and boundary refit. The refined puncta mask is downsampled
back to 512 with nearest-neighbour (as the manuscript masks were) and scored against every
annotator in the slide's pixel metric (recall, FN/FP as % of union, IoU), plus object-level
detection and pred/GT area ratio, and CellProfiler is scored the same way for reference.

The one input a user supplies by hand — the measure-line object and cell diameters — is not
recorded anywhere for these fields, so it is derived from the IMAGES, never the annotations:
object diameter from `estimate_object_size_px` on the GFP (the batch processor's auto
estimator; x `--object-scale`), cell diameter = median DAPI-Otsu nucleus diameter. Both are
printed per field. (PYCAT_EVAL_SIZES=consensus reproduces older runs that used annotation sizes.)

Run it
------
    python -m benchmarks.incell_pipeline_eval [--classes "large puncta"] [--object-scale 1.0]
        [--refit-level 0.5] [--boundary-mode regional|level] [--save DIR]

Every method, CellProfiler included, is scored only on the nuclei the annotators traced in: these
are transient transfections, a nucleus without meaningful GFP was not annotated, and choosing
cells is not the condensate segmentation's job.
"""
from __future__ import annotations

import argparse
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np
import scipy.ndimage as ndi
import skimage as sk
import tifffile
from PIL import Image
from skimage.filters import threshold_otsu

from benchmarks.mask_inventory import (
    DEFAULT_ROOT,
    _annotator_dirs,
    _content_match,
    _mask_path,
)

ROOT = Path(DEFAULT_ROOT)
UPSCALE = 2
# 'auto' (default): object size from the image; 'consensus': from the annotations (old runs).
SIZE_SOURCE = os.environ.get('PYCAT_EVAL_SIZES', 'auto')
# What the 2D cellular fluorescence GUI handler (`run_segment_subcellular_objects`) passes on top of
# `segment_subcellular_objects`' library defaults, which the other workflows keep.
GUI_2D_CELLULAR = {'boundary_mode': 'regional', 'second_pass': True, 'transfected_route': True}
ANNOTATORS = ('meet', 'shamli', 'gable', 'consensus')
CELLPROFILER_DIRS = {'Small puncta': 'Cell profiler masks', 'large puncta': 'Cellprofiler analysis',
                     'Irregular puncta': 'Cellprofiler masks'}


def _load(path):
    return np.array(Image.open(path)) if str(path).lower().endswith('.png') else tifffile.imread(path)


def gui_layer(raw):
    from pycat.utils.general_utils import dtype_conversion_func
    if np.issubdtype(raw.dtype, np.signedinteger):
        raw = raw.astype(np.uint16)
    return dtype_conversion_func(raw, 'float32')


def _gui_upscale(data):
    """The float range handling `run_upscaling_func` applies after interpolation."""
    from pycat.toolbox.image_processing_tools import apply_rescale_intensity
    if data.min() < 0 or data.max() > 1.0:
        if data.min() < -0.05 or data.max() > 1.05:
            return np.clip(data, 0.0, 1.0).astype(np.float32)
        return apply_rescale_intensity(data, out_min=0.0, out_max=1.0).astype(np.float32)
    return data


def measured_sizes(gfp_dapi, consensus=None):
    """The measure-line sizes, in 512-px units, WITHOUT the annotations by default.

    Object size comes from `estimate_object_size_px` on the GFP image — the estimator the
    batch processor uses when auto ball_radius is on — so no human mask feeds the run. Pass
    `consensus` only to reproduce the earlier consensus-median sizes for comparison. Cell
    diameter is the median DAPI-Otsu nucleus diameter either way.
    """
    gfp, dapi = gfp_dapi
    dapi = dapi.astype(np.float64)
    if consensus is None:
        from pycat.toolbox.image_processing_tools import estimate_object_size_px
        object_d = float(estimate_object_size_px(np.asarray(gfp, dtype=np.float64))['object_size_px'])
    else:
        areas = np.bincount(consensus.ravel())[1:]
        areas = areas[areas > 0]
        object_d = float(2 * np.sqrt(np.median(areas) / np.pi))
    nuc = sk.measure.label(dapi > threshold_otsu(dapi))
    nareas = np.bincount(nuc.ravel())[1:]
    nareas = nareas[nareas >= 1500]
    cell_d = float(2 * np.sqrt(np.median(nareas) / np.pi)) if nareas.size else 110.0
    return object_d, cell_d


def run_pipeline(gfp, dapi, object_d, cell_d, refit_level=0.5, boundary_refit=True, **seg_kwargs):
    """The GUI chain at 1.6.460 defaults. Returns the refined puncta mask at 1024 and timing.

    `seg_kwargs` pass straight to `segment_subcellular_objects`; `info['gfp_up']` is the
    working image, so a caller can re-run the refit alone on a cached pre-refit mask.
    """
    from pycat.data.data_modules import BaseDataClass
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func
    from pycat.toolbox.image_processing._base import upscale_image_interp
    from pycat.toolbox.image_processing.background import (
        rb_gaussian_bg_removal_with_edge_enhancement,
    )
    from pycat.toolbox.image_processing.preprocessing import pre_process_image
    from pycat.toolbox.segmentation.cellpose import cellpose_segmentation
    from pycat.toolbox.segmentation.intensity import compute_image_intensity_stats
    from pycat.toolbox.segmentation.morphology import cell_mask_stretching
    from pycat.toolbox.segmentation.subcellular import segment_subcellular_objects

    t0 = time.perf_counter()
    rows, cols = gfp.shape
    gfp_up = _gui_upscale(upscale_image_interp(gfp, rows, cols, upscale_factor=2))
    dapi_up = _gui_upscale(upscale_image_interp(dapi, rows, cols, upscale_factor=2))
    ball_radius = math.ceil(1.5 * object_d / 2.0) * 2          # measure line, then x2 on upscale
    cell_d2 = cell_d * 2
    data = BaseDataClass()
    data.data_repository.update({'object_size': object_d * 2, 'cell_diameter': cell_d2,
                                 'ball_radius': ball_radius})
    max_radius = max(4, int(min(gfp_up.shape) * 0.05))
    pre = pre_process_image(gfp_up, min(int(ball_radius), max_radius),
                            min(int(cell_d2) // 2, max_radius * 2))
    enhanced = rb_gaussian_bg_removal_with_edge_enhancement(pre, math.ceil(ball_radius))
    cells_raw = cellpose_segmentation(dapi_up, cell_d2, postprocess=False)
    labeled, cell_df = cell_analysis_func(gfp_up, cells_raw, None, data)
    t_cells = time.perf_counter()
    stretched = cell_mask_stretching(enhanced, labeled)
    stats = compute_image_intensity_stats(gfp_up, labeled, smooth_sigma=max(0.5, 2 / 2.0))
    total = np.zeros(labeled.shape, dtype=bool)
    total_raw = np.zeros(labeled.shape, dtype=bool)
    for lab in np.unique(labeled)[1:]:
        refined, raw = segment_subcellular_objects(
            gfp_up.copy(), stretched.copy(), labeled == lab, lab, ball_radius, cell_df,
            image_stats=stats, multiscale=True, boundary_refit=boundary_refit,
            refit_level=refit_level, **{**GUI_2D_CELLULAR, **seg_kwargs})
        total |= refined.astype(bool)
        total_raw |= raw.astype(bool)
    t_end = time.perf_counter()
    return total, labeled, {'total_s': t_end - t0, 'segment_s': t_end - t_cells,
                            'n_cells': int(len(np.unique(labeled)) - 1), 'ball_radius': ball_radius,
                            'gfp_up': gfp_up, 'raw_mask': total_raw}


def downsample(mask1024):
    return cv2.resize(mask1024.astype(np.uint8), (512, 512), interpolation=cv2.INTER_NEAREST) > 0


def annotated_cells(cells1024, truths):
    """The nuclei any annotator traced in, at 512 (plus the traces themselves)."""
    cells = cv2.resize(np.asarray(cells1024, np.float32), (512, 512),
                       interpolation=cv2.INTER_NEAREST).astype(int)
    traced = np.zeros(cells.shape, bool)
    for t in truths.values():
        traced |= t > 0
    return np.isin(cells, [c for c in np.unique(cells[traced]) if c > 0]) | traced


def keep_objects_in(mask, roi):
    """Whole predicted objects that touch the region — never clipped mid-object."""
    lab, _ = ndi.label(mask)
    keep = np.unique(lab[mask & roi])
    return np.isin(lab, keep[keep > 0])


def pixel_scores(pred, truth):
    tp = int((pred & truth).sum())
    fn = int((~pred & truth).sum())
    fp = int((pred & ~truth).sum())
    return np.array([tp, fn, fp])


def object_scores(pred, truth_labels):
    """GT objects missed entirely, and pooled pred/GT area over the GT objects' neighbourhoods."""
    ids = np.unique(truth_labels[truth_labels > 0])
    missed = sum(1 for i in ids if not (pred & (truth_labels == i)).any())
    return np.array([missed, ids.size, int(pred.sum()), int((truth_labels > 0).sum())])


def cellprofiler_masks(cls, imgs):
    folder = ROOT / cls / CELLPROFILER_DIRS[cls]
    out = {}
    for f in os.listdir(folder):
        if f.lower().endswith('.png'):
            m = _load(folder / f) > 0
            best, _ = _content_match([m], imgs)[0]
            out[best + 1] = m
    return out


def _report(name, pix, obj):
    tp, fn, fp = pix
    u = tp + fn + fp
    return (f'{name:22s} recall {tp / (tp + fn):.2f}  FN {fn / u:4.0%}  FP {fp / u:4.0%}  '
            f'IoU {tp / u:.2f}  area ratio {obj[2] / obj[3]:.2f}  missed objects {obj[0]}/{obj[1]}')


def evaluate(cls, object_scale, refit_level, boundary_mode, save_dir):
    class_dir = ROOT / cls
    ann = _annotator_dirs(class_dir)
    gfps = [tifffile.imread(class_dir / f'In Cell {n}-GFP.tif').astype(np.float64) for n in range(1, 10)]
    cp = cellprofiler_masks(cls, gfps)
    acc = {t: {a: [np.zeros(3, int), np.zeros(4, int)] for a in ANNOTATORS} for t in ('pycat', 'cellprofiler')}
    print(f'\n=== {cls}  (object_scale {object_scale}, refit_level {refit_level}, '
          f'boundary_mode {boundary_mode}; scored on annotated cells)')
    for n in range(1, 10):
        # As the GUI layer holds it (`file_io/viewer_load`): signed int -> uint16 -> float32 at
        # dtype-max [0, 1]. The GUI upscale clips any float image to [0, 1], so raw counts would
        # saturate there.
        gfp = gui_layer(tifffile.imread(class_dir / f'In Cell {n}-GFP.tif'))
        dapi = gui_layer(tifffile.imread(class_dir / f'In Cell {n}-DAPI.tif'))
        truths = {a: _load(_mask_path(ann[a], a, n)).astype(np.int64) for a in ANNOTATORS}
        od, cd = measured_sizes((gfp, dapi), truths['consensus'] if SIZE_SOURCE == 'consensus' else None)
        mask1024, cells, info = run_pipeline(gfp, dapi, od * object_scale, cd, refit_level,
                                             boundary_mode=boundary_mode)
        roi = annotated_cells(cells, truths)
        pred = keep_objects_in(downsample(mask1024), roi)
        if save_dir:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(Path(save_dir) / f'{cls} In Cell {n}_mask1024.tif', mask1024.astype(np.uint8))
        for tool, m in (('pycat', pred), ('cellprofiler', cp.get(n))):
            if m is None:
                continue
            m = keep_objects_in(m, roi)
            for a in ANNOTATORS:
                acc[tool][a][0] += pixel_scores(m, truths[a] > 0)
                acc[tool][a][1] += object_scores(m, truths[a])
        print(f'  field {n}: object d {od * object_scale:.1f}px cell d {cd:.0f}px ball_radius(1024) '
              f'{info["ball_radius"]} cells {info["n_cells"]}  {info["total_s"]:.1f}s '
              f'(segmentation {info["segment_s"]:.1f}s)', flush=True)
    for a in ANNOTATORS:
        print(f'  vs {a}')
        for tool in ('pycat', 'cellprofiler'):
            print('    ' + _report(tool, *acc[tool][a]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--classes', nargs='+', default=['large puncta'])
    ap.add_argument('--object-scale', type=float, default=1.0)
    ap.add_argument('--refit-level', type=float, default=0.5)
    ap.add_argument('--boundary-mode', default='regional', choices=('regional', 'level'))
    ap.add_argument('--save', default=None)
    args = ap.parse_args()
    for cls in args.classes:
        evaluate(cls, args.object_scale, args.refit_level, args.boundary_mode, args.save)


if __name__ == '__main__':
    main()
