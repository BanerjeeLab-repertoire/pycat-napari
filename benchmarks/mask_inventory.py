"""**What human ground truth do we actually have for in-cell puncta, and how much of it?**

Phase 0 of the 2D cellular region-selection calibration
(`docs/audits/PyCAT_2D_Cellular_Region_Selection_Spec.md`). Everything downstream — the level
at which humans trace a boundary, the fusion-safe relaxation, the dim-object gates — is
calibrated against these masks, so this script establishes, from the files themselves rather
than from their names:

    1. which image each annotation belongs to (verified by pixel content, not filename),
    2. how many annotated objects exist, per size class, field, annotator and size bin,
    3. how well the annotators agree with one another — the ceiling on any automated method.

Data layout (discovered, Box `Biological data comparison`): three size classes
(`Small puncta`, `large puncta`, `Irregular puncta`), each with nine 512x512 int16 fields
`In Cell N-GFP.tif` (+ `-DAPI.tif`) and one folder of label masks per annotator,
`Puncta Mask N.png` (uint16 label image; the `Tiff files` float32 copy is checked identical).

Run it
------
    python -m benchmarks.mask_inventory [DATA_ROOT]

Writes `benchmarks/bio_gt_pairing.csv` (one row per image/annotation pairing, unpaired files
included) and `benchmarks/bio_gt_objects.csv` (one row per annotated object), and prints the
summary tables.
"""
from __future__ import annotations

import csv
import itertools
import os
import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from scipy.optimize import linear_sum_assignment
from skimage.filters import threshold_otsu
from skimage.measure import label, regionprops

DEFAULT_ROOT = (r"C:\Users\gmwadsworth\Box\[0] PyCAT manuscript\[3] Experimental data"
                r"\Biological data comparison")
CLASSES = ('Small puncta', 'large puncta', 'Irregular puncta')
N_FIELDS = 9
UM_PER_PX_FALLBACK = None  # filled from TIFF XResolution; never assumed

# Annotator folders, matched case-insensitively on the folder name (the three classes spell
# them differently). The un-named 'Ground Truth masks' / 'Ground truth' set is Meet's
# (confirmed by the author; the folder itself does not say).
ANNOTATOR_FOLDERS = {
    'gable': ('ground truth masks gable', 'ground truth gable'),
    'shamli': ('ground truth masks - shamli',),
    'meet': ('ground truth masks', 'ground truth'),
    'consensus': ('concensus masks',),
}
HUMAN_ANNOTATORS = ('gable', 'meet', 'shamli')

# Equivalent-radius bins (px), matching benchmarks/condensate_scale.RADIUS_BINS so the real
# and synthetic results can be read side by side.
RADIUS_BINS = ((0, 2), (2, 5), (5, 8), (8, 11), (11, 14), (14, 17), (17, 99))
MATCH_IOU = 0.1  # objects overlapping less than this are not considered the same object

HERE = Path(__file__).resolve().parent


def _load(path):
    if path.suffix.lower() == '.png':
        return np.array(Image.open(path))
    return tifffile.imread(path)


def _um_per_px(tif_path):
    """Pixel size from the TIFF resolution tags, or None when the file does not record it."""
    with tifffile.TiffFile(tif_path) as tif:
        tags = tif.pages[0].tags
        if 'XResolution' not in tags or 'ResolutionUnit' not in tags:
            return None
        num, den = tags['XResolution'].value
        unit = tags['ResolutionUnit'].value
        per_unit_um = {2: 25400.0, 3: 10000.0}.get(int(unit))
        if not num or per_unit_um is None:
            return None
        return per_unit_um * den / num


def _annotator_dirs(class_dir):
    """Map annotator -> directory holding its `Puncta Mask N.png` files."""
    found = {}
    for root, _dirs, files in os.walk(class_dir):
        name = Path(root).name.lower()
        if not any(f.lower().startswith('puncta mask') and f.lower().endswith('.png')
                   for f in files) and 'concensus' not in name:
            continue
        for annotator, names in ANNOTATOR_FOLDERS.items():
            if name in names and annotator not in found:
                found[annotator] = Path(root)
    return found


def _mask_path(ann_dir, annotator, n):
    if annotator == 'consensus':
        return ann_dir / f'Puncta Mask {n}_consensus_labeled.tif'
    return ann_dir / f'Puncta Mask {n}.png'


def _tif_twin(png_path):
    """The float32 `Tiff files` copy of a PNG label mask, if one exists."""
    for sub in ('Tiff files', 'tiff files'):
        twin = png_path.parent / sub / (png_path.stem + '.tif')
        if twin.exists():
            return twin
    return None


def _content_match(masks, images):
    """For each mask, the field whose GFP is brightest inside it relative to outside.

    A correctly paired label mask sits on that field's puncta, so its inside/outside contrast
    is far higher on its own image than on any other. Returns (best index, contrast ratio of
    best over second best) per mask.
    """
    result = []
    for m in masks:
        fg = m > 0
        if not fg.any():
            result.append((None, float('nan')))
            continue
        scores = [float(img[fg].mean() / max(img[~fg].mean(), 1e-9)) for img in images]
        order = np.argsort(scores)[::-1]
        result.append((int(order[0]), scores[order[0]] / max(scores[order[1]], 1e-9)))
    return result


def _nuclei(dapi):
    """Nucleus labels from DAPI (Otsu, components >= 1500 px) — used only to attribute objects
    to cells for the per-cell counts, not as ground truth."""
    smooth = dapi.astype(float)
    lab = label(smooth > threshold_otsu(smooth))
    sizes = np.bincount(lab.ravel())
    keep = np.flatnonzero(sizes >= 1500)
    keep = keep[keep != 0]
    return np.where(np.isin(lab, keep), lab, 0)


def _radius_bin(r):
    for lo, hi in RADIUS_BINS:
        if lo <= r < hi:
            return f'{lo}-{hi}'
    return 'other'


def _object_rows(cls, n, annotator, mask, nuclei, um_per_px):
    rows = []
    for rp in regionprops(mask.astype(np.int32)):
        r_eq = float(np.sqrt(rp.area / np.pi))
        nuc_ids = nuclei[tuple(rp.coords.T)]
        nuc_ids = nuc_ids[nuc_ids > 0]
        rows.append({
            'size_class': cls, 'field': n, 'annotator': annotator, 'label': rp.label,
            'area_px': int(rp.area), 'eq_radius_px': round(r_eq, 3),
            'eq_radius_um': round(r_eq * um_per_px, 4) if um_per_px else '',
            'radius_bin_px': _radius_bin(r_eq),
            'circularity': round(4 * np.pi * rp.area / rp.perimeter ** 2, 4) if rp.perimeter else '',
            'solidity': round(float(rp.solidity), 4),
            'eccentricity': round(float(rp.eccentricity), 4),
            'n_pieces': int(label(rp.image).max()),
            'nucleus': int(np.bincount(nuc_ids).argmax()) if nuc_ids.size else 0,
            'centroid_y': round(rp.centroid[0], 2), 'centroid_x': round(rp.centroid[1], 2),
        })
    return rows


def _match(a, b):
    """One-to-one object matching between two label masks by IoU (Hungarian).

    Returns (matched IoUs, n_a, n_b, pixel-level foreground IoU)."""
    la, lb = np.unique(a[a > 0]), np.unique(b[b > 0])
    fg_a, fg_b = a > 0, b > 0
    pix_iou = float((fg_a & fg_b).sum() / max((fg_a | fg_b).sum(), 1))
    if not la.size or not lb.size:
        return [], la.size, lb.size, pix_iou
    ia = {v: i for i, v in enumerate(la)}
    ib = {v: i for i, v in enumerate(lb)}
    both = fg_a & fg_b
    inter = np.zeros((la.size, lb.size))
    for va, vb in zip(a[both], b[both], strict=True):
        inter[ia[va], ib[vb]] += 1
    area_a = np.array([(a == v).sum() for v in la])
    area_b = np.array([(b == v).sum() for v in lb])
    iou = inter / (area_a[:, None] + area_b[None, :] - inter)
    rows, cols = linear_sum_assignment(-iou)
    matched = [float(iou[r, c]) for r, c in zip(rows, cols, strict=True) if iou[r, c] >= MATCH_IOU]
    return matched, la.size, lb.size, pix_iou


def _pair_class(root, cls, pairing, objects, agreement):
    class_dir = root / cls
    img_paths = [class_dir / f'In Cell {n}-GFP.tif' for n in range(1, N_FIELDS + 1)]
    images = [_load(p).astype(float) if p.exists() else None for p in img_paths]
    um_per_px = next((_um_per_px(p) for p in img_paths if p.exists()), None)
    ann_dirs = _annotator_dirs(class_dir)
    masks = {}
    for annotator, ann_dir in sorted(ann_dirs.items()):
        paths = [_mask_path(ann_dir, annotator, n) for n in range(1, N_FIELDS + 1)]
        loaded = [_load(p) if p.exists() else None for p in paths]
        matches = _content_match([m if m is not None else np.zeros((1, 1)) for m in loaded],
                                 [im for im in images if im is not None])
        for n, (path, mask, (best, margin)) in enumerate(zip(paths, loaded, matches, strict=True), start=1):
            img_ok = img_paths[n - 1].exists()
            twin = _tif_twin(path) if mask is not None and path.suffix == '.png' else None
            twin_same = (bool(np.array_equal(_load(twin).astype(np.int64), mask.astype(np.int64)))
                         if twin is not None else '')
            pairing.append({
                'size_class': cls, 'field': n, 'annotator': annotator,
                'image': img_paths[n - 1].name if img_ok else '',
                'annotation': str(path.relative_to(root)) if mask is not None else '',
                'status': 'paired' if (img_ok and mask is not None) else
                          ('image_without_annotation' if img_ok else 'annotation_without_image'),
                'content_best_field': (best + 1) if best is not None else '',
                'content_verified': best is not None and best + 1 == n,
                'content_margin': round(margin, 2) if best is not None else '',
                'tif_copy_identical': twin_same,
                'n_objects': int(len(np.unique(mask[mask > 0]))) if mask is not None else 0,
            })
            if mask is None or not img_ok:
                continue
            masks[(annotator, n)] = mask
            dapi = class_dir / f'In Cell {n}-DAPI.tif'
            nuclei = _nuclei(_load(dapi)) if dapi.exists() else np.zeros_like(mask)
            objects.extend(_object_rows(cls, n, annotator, mask, nuclei, um_per_px))
    for a, b in itertools.combinations([h for h in HUMAN_ANNOTATORS if h in ann_dirs], 2):
        for n in range(1, N_FIELDS + 1):
            if (a, n) in masks and (b, n) in masks:
                matched, na, nb, pix = _match(masks[(a, n)], masks[(b, n)])
                agreement.append({'size_class': cls, 'field': n, 'pair': f'{a}~{b}',
                                  'n_a': na, 'n_b': nb, 'n_matched': len(matched),
                                  'matched_ious': matched, 'pixel_iou': pix})
    return um_per_px


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, 'w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _summarise(pairing, objects, agreement, um):
    print(f'pixel size: {um}')
    bad = [p for p in pairing if p['status'] != 'paired' or not p['content_verified']
           or p['tif_copy_identical'] is False]
    print(f'pairings: {len(pairing)}  problems: {len(bad)}')
    for p in bad:
        print('  PROBLEM', p)
    print('\nannotated objects (size_class x annotator):')
    for cls in CLASSES:
        counts = {a: sum(1 for o in objects if o['size_class'] == cls and o['annotator'] == a)
                  for a in ANNOTATOR_FOLDERS}
        print(f'  {cls:18s}', '  '.join(f'{a}={c}' for a, c in counts.items()))
    print('\nobjects per equivalent-radius bin (px), human annotators pooled:')
    for cls in CLASSES:
        bins = {b: 0 for b in [f'{lo}-{hi}' for lo, hi in RADIUS_BINS]}
        for o in objects:
            if o['size_class'] == cls and o['annotator'] in HUMAN_ANNOTATORS:
                bins[o['radius_bin_px']] += 1
        print(f'  {cls:18s}', '  '.join(f'{b}:{c}' for b, c in bins.items()))
    print('\ninter-annotator agreement (Hungarian-matched objects, IoU >= %.1f):' % MATCH_IOU)
    for cls in CLASSES:
        for pair in sorted({r['pair'] for r in agreement if r['size_class'] == cls}):
            rs = [r for r in agreement if r['size_class'] == cls and r['pair'] == pair]
            ious = [i for r in rs for i in r['matched_ious']]
            na, nb, nm = (sum(r[k] for r in rs) for k in ('n_a', 'n_b', 'n_matched'))
            print(f'  {cls:18s} {pair:22s} objects {na:4d} vs {nb:4d}  matched {nm:4d}  '
                  f'median IoU {np.median(ious) if ious else float("nan"):.3f}  '
                  f'mean IoU {np.mean(ious) if ious else float("nan"):.3f}  '
                  f'pixel IoU {np.mean([r["pixel_iou"] for r in rs]):.3f}')


def main(root=DEFAULT_ROOT):
    root = Path(root)
    pairing, objects, agreement = [], [], []
    um = None
    for cls in CLASSES:
        um = _pair_class(root, cls, pairing, objects, agreement) or um
    _write_csv(HERE / 'bio_gt_pairing.csv', pairing)
    _write_csv(HERE / 'bio_gt_objects.csv', objects)
    _summarise(pairing, objects, agreement, um)
    return pairing, objects, agreement


if __name__ == '__main__':
    main(*sys.argv[1:2])
