"""**Does segmentation still work when the objects are not the size the user measured?**

The canonical suite (`cases.py`) asks whether segmentation quality drifts between releases.
This asks a question that suite cannot: whether quality depends on OBJECT SCALE — because
every scale in the condensate path descends from `ball_radius`, and `ball_radius` descends
from one line the user drew across one object (`DataClass.update_sizes`).

Ground truth is CONSTRUCTED, in the same sense and for the same reason as `cases.py`: each
object's footprint is placed geometrically BEFORE the PSF blur, so the truth is known
exactly and cannot drift with the method. For an object larger than the PSF that footprint
is the half-maximum contour to within a pixel — the same convention
`tests/fixtures_synthetic.py` uses (`core = blob > amplitude * 0.5`), and roughly where a
human annotator traces a condensate.

Four regimes, chosen to match how condensates actually present:

    small       object radii 3-8 px
    irregular   major axis 3-20 px, elongated
    large       object radii 10-20 px
    mixed       small AND large in the SAME field

`mixed` is the one that matters. A single band-pass can be tuned for small objects or for
large ones; it cannot be tuned for both, and `mixed` is the regime where that shows up as a
DETECTION failure rather than a sizing one.

Run it
------
    python -m benchmarks.condensate_scale

It prints, per regime and per true-radius bin, what fraction of objects were detected at
all, how much of each was covered, and the predicted/true area ratio of the ones that were
found. Those three are deliberately separate: a single "false-negative fraction" mixes
"missed the object" with "found it and drew it too small", and the two have different
causes and different fixes.
"""
from __future__ import annotations

import math

import numpy as np
import scipy.ndimage as ndi

# Imaging model. Deliberately plain: the point of this benchmark is object SCALE,
# so everything else is held fixed and unremarkable.
PSF_SIGMA = 1.2            # px
NUCLEOPLASM_FRACTION = 0.18  # cell baseline, as a fraction of object amplitude
CAMERA_OFFSET = 100.0
OBJECT_AMPLITUDE = 900.0
NOISE_SIGMA = 6.0
AMPLITUDE_JITTER = 0.35     # real condensates are not all equally bright

RADIUS_BINS = ((2, 5), (5, 8), (8, 11), (11, 14), (14, 17), (17, 21))

# The object diameter a careful user would draw a line across in each regime: the
# middle of the true size range. `mixed` gets a small value because that is what a
# user measuring the population they can SEE most of would pick.
MEASURED_DIAMETER_PX = {'small': 11.0, 'irregular': 11.0, 'large': 30.0, 'mixed': 12.0}
CELL_DIAMETER_PX = 110.0

_OBJECTS_PER_CELL = {'small': 26, 'irregular': 26, 'large': 10, 'mixed': 18}


def _ellipse(yy, xx, cy, cx, ry, rx, theta):
    ct, st = math.cos(theta), math.sin(theta)
    u = (xx - cx) * ct + (yy - cy) * st
    v = -(xx - cx) * st + (yy - cy) * ct
    return (u / rx) ** 2 + (v / ry) ** 2 <= 1.0


def _nuclei(shape, rng, n_cells):
    """Well-separated elliptical nuclei — the cell mask the segmentation is given."""
    height, width = shape
    yy, xx = np.mgrid[0:height, 0:width]
    labels = np.zeros(shape, np.int32)
    placed, next_id, attempts = 0, 1, 0
    while placed < n_cells and attempts < 400:
        attempts += 1
        ry, rx = int(rng.integers(46, 66)), int(rng.integers(46, 66))
        cy = int(rng.integers(ry + 4, height - ry - 4))
        cx = int(rng.integers(rx + 4, width - rx - 4))
        theta = float(rng.uniform(0, math.pi))
        if labels[_ellipse(yy, xx, cy, cx, ry * 1.12, rx * 1.12, theta)].any():
            continue          # keep a gap, so cells never touch
        labels[_ellipse(yy, xx, cy, cx, ry, rx, theta)] = next_id
        next_id += 1
        placed += 1
    return labels


def _object_axes(kind, rng):
    """Semi-axes and orientation for one object of this regime."""
    if kind == 'small':
        r = float(rng.uniform(3, 8));   return r, r, 0.0
    if kind == 'large':
        r = float(rng.uniform(10, 20)); return r, r, 0.0
    if kind == 'mixed':
        r = float(rng.uniform(3, 7)) if rng.random() < 0.7 else float(rng.uniform(11, 20))
        return r, r, 0.0
    if kind == 'irregular':
        major = float(rng.uniform(3, 20))
        ratio = float(rng.uniform(0.25, 0.7))
        return major / 2.0, max(1.2, major * ratio / 2.0), float(rng.uniform(0, math.pi))
    raise ValueError(f"unknown regime {kind!r}")


def _place_objects(shape, nuclei, rng, kind, per_cell):
    height, width = shape
    yy, xx = np.mgrid[0:height, 0:width]
    truth = np.zeros(shape, np.int32)
    next_id = 1
    for cell in range(1, int(nuclei.max()) + 1):
        cell_mask = nuclei == cell
        ys, xs = np.where(ndi.binary_erosion(cell_mask, np.ones((21, 21))))
        if ys.size == 0:
            continue
        placed, attempts = 0, 0
        while placed < per_cell and attempts < per_cell * 60:
            attempts += 1
            i = int(rng.integers(0, ys.size))
            ry, rx, theta = _object_axes(kind, rng)
            footprint = _ellipse(yy, xx, int(ys[i]), int(xs[i]), ry, rx, theta)
            if not footprint.any():
                continue
            # objects never touch, and never hang off the edge of their cell
            halo = ndi.binary_dilation(footprint, np.ones((7, 7)))
            if truth[halo].any() or not cell_mask[footprint].all():
                continue
            truth[footprint] = next_id
            next_id += 1
            placed += 1
    return truth


def make_scene(kind, shape=(512, 512), seed=0, n_cells=6, per_cell=None):
    """Returns ``(image int16, truth labels int32, nuclei labels int32)``."""
    rng = np.random.default_rng(seed)
    per_cell = per_cell if per_cell is not None else _OBJECTS_PER_CELL[kind]
    nuclei = _nuclei(shape, rng, n_cells)
    truth = _place_objects(shape, nuclei, rng, kind, per_cell)

    image = np.zeros(shape, np.float32)
    image[nuclei > 0] = NUCLEOPLASM_FRACTION * OBJECT_AMPLITUDE
    for object_id in range(1, int(truth.max()) + 1):
        mask = truth == object_id
        if not mask.any():
            continue
        amplitude = OBJECT_AMPLITUDE * (1.0 + float(rng.uniform(-AMPLITUDE_JITTER,
                                                               AMPLITUDE_JITTER)))
        image[mask] = NUCLEOPLASM_FRACTION * OBJECT_AMPLITUDE + amplitude

    image = ndi.gaussian_filter(image, PSF_SIGMA) + CAMERA_OFFSET
    image = image + rng.normal(0, NOISE_SIGMA, shape).astype(np.float32)
    return np.clip(image, 0, 32767).astype(np.int16), truth, nuclei


# ── scoring ─────────────────────────────────────────────────────────────────────────
def union_fractions(truth_mask, predicted_mask):
    """False-negative and false-positive area, each as a percentage of the union.

    The units the comparison figures use, so a number here is directly comparable to
    one measured for CellProfiler or Squassh on the same field.
    """
    truth = np.asarray(truth_mask) > 0
    predicted = np.asarray(predicted_mask) > 0
    true_positive = int((truth & predicted).sum())
    false_positive = int((~truth & predicted).sum())
    false_negative = int((truth & ~predicted).sum())
    union = true_positive + false_positive + false_negative
    if union == 0:
        return dict(fn_frac=float('nan'), fp_frac=float('nan'), iou=float('nan'))
    return dict(fn_frac=100.0 * false_negative / union,
                fp_frac=100.0 * false_positive / union,
                iou=true_positive / union)


def per_object_rows(truth_labels, predicted_mask, detection_coverage=0.25):
    """One row per TRUE object: its radius, whether it was found, how well it was covered.

    Separating detection from coverage is the whole point. A regime can score a
    respectable false-negative fraction while missing every large object in it, because
    the small objects it does find outnumber them.
    """
    predicted = np.asarray(predicted_mask) > 0
    labels = np.asarray(truth_labels)
    components, _ = ndi.label(predicted)
    component_area = np.bincount(components.ravel())
    rows = []
    for object_id, window in enumerate(ndi.find_objects(labels), start=1):
        if window is None:
            continue
        mask = labels[window] == object_id
        area = int(mask.sum())
        if area == 0:
            continue
        overlap = mask & predicted[window]
        coverage = int(overlap.sum()) / area
        ids = np.unique(components[window][overlap])
        ids = ids[ids != 0]
        predicted_area = int(component_area[ids].sum()) if ids.size else 0
        rows.append(dict(r_true=math.sqrt(area / math.pi), area=area,
                         coverage=coverage, detected=coverage >= detection_coverage,
                         area_ratio=(predicted_area / area) if predicted_area else 0.0))
    return rows


def format_size_table(rows):
    lines = [f"    {'r_true (px)':>12}{'n':>6}{'detected %':>12}"
             f"{'coverage':>10}{'area ratio':>12}"]
    for low, high in RADIUS_BINS:
        selected = [r for r in rows if low <= r['r_true'] < high]
        if not selected:
            continue
        found = [r for r in selected if r['detected']]
        ratio = np.median([r['area_ratio'] for r in found]) if found else float('nan')
        lines.append(f"    {low:5.0f}-{high:<6.0f}{len(selected):6d}"
                     f"{100.0 * len(found) / len(selected):12.0f}"
                     f"{np.mean([r['coverage'] for r in selected]):10.2f}"
                     f"{ratio:12.2f}")
    return "\n".join(lines)


# ── running the real pipeline ───────────────────────────────────────────────────────
def segment(image, nuclei, measured_diameter_px, cell_diameter_px=CELL_DIAMETER_PX,
            min_spot_radius=2, **segmentation_kwargs):
    """Drive the condensate pipeline exactly as the GUI does, viewer-free.

    The GUI's own order, and the same derivations, so a number measured here is a
    number about PyCAT rather than about this file:

        ball_radius  = ceil(1.5 * object_radius)         DataClass.update_sizes
        window_size  = cell_diameter // 2, capped        run_pre_process_image
        pre_processed = pre_process_image(raw, ball_radius, window_size)
        CMS           = cell_mask_stretching(pre_processed, cells)
        per cell      -> segment_subcellular_objects(raw, CMS, cell, ..., ball_radius)
    """
    import pandas as pd
    from pycat.toolbox.image_processing.preprocessing import pre_process_image
    from pycat.toolbox.segmentation.morphology import cell_mask_stretching
    from pycat.toolbox.segmentation.subcellular import segment_subcellular_objects

    ball_radius = math.ceil(1.5 * (measured_diameter_px / 2.0))
    max_radius = max(4, int(min(image.shape[-2:]) * 0.05))
    ball_radius = min(ball_radius, max_radius)
    window_size = min(int(cell_diameter_px) // 2, max_radius * 2)

    pre_processed = pre_process_image(image, ball_radius, window_size)
    stretched = cell_mask_stretching(pre_processed, nuclei)

    predicted = np.zeros(image.shape, bool)
    for cell_id in np.unique(nuclei)[1:]:
        refined, _puncta = segment_subcellular_objects(
            image.copy(), stretched.copy(), nuclei == cell_id, cell_id, ball_radius,
            pd.DataFrame(), min_spot_radius=min_spot_radius, **segmentation_kwargs)
        predicted |= np.asarray(refined, dtype=bool)
    return predicted, ball_radius


def main(seeds=(0, 1, 2)):
    variants = {
        'single-scale, no re-fit': dict(multiscale=False, boundary_refit=False),
        'scale-aware + re-fit':    dict(multiscale=True, boundary_refit=True),
    }
    for regime in ('small', 'irregular', 'large', 'mixed'):
        print(f"\n{'=' * 78}\n{regime.upper()}  "
              f"(user-measured object diameter {MEASURED_DIAMETER_PX[regime]:.0f} px)")
        for name, kwargs in variants.items():
            union, rows = [], []
            for seed in seeds:
                image, truth, nuclei = make_scene(regime, seed=seed)
                predicted, ball_radius = segment(
                    image, nuclei, MEASURED_DIAMETER_PX[regime], **kwargs)
                union.append(union_fractions(truth, predicted))
                rows += per_object_rows(truth, predicted)
            print(f"\n  {name}  (ball_radius {ball_radius})"
                  f"   FN {np.mean([u['fn_frac'] for u in union]):.1f}%"
                  f"   FP {np.mean([u['fp_frac'] for u in union]):.1f}%"
                  f"   IoU {np.mean([u['iou'] for u in union]):.3f}")
            print(format_size_table(rows))


if __name__ == "__main__":
    main()
