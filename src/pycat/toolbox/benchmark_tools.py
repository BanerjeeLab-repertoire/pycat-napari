"""
Benchmarking / comparison harness for PyCAT.

One framework, three uses:

  * **Method comparison** — run several segmenters (or external/uploaded masks)
    on the same image and compare them (count, area, runtime, pairwise overlap).
  * **Parameter sensitivity** — run ONE method across a swept parameter and see
    how the results move, to find a stable operating range.
  * **Ground-truth validation** — score candidates against a hand-labelled (or
    otherwise trusted) mask: Dice/IoU and matched-detection F1.

Everything is expressed as a list of **candidates**. A candidate is either a
built-in method (an image -> labelled-mask callable, which the harness times) or
an already-computed mask (e.g. a Labels layer dragged in from another tool or a
manual annotation — no runtime, "external" provenance). Uploaded masks are
first-class candidates: they go through the same run/score/report path, and any
one candidate can be marked as the ground truth to score the others against.

Two metric families are reported **side by side, without privileging either**:

  * **Pixel-overlap metrics** (Dice, IoU) — appropriate for area-like agreement
    and for cell/body segmentation.
  * **Matched-detection metrics** (precision / recall / F1 by Hungarian centroid
    matching, plus localisation error) — appropriate for puncta / spots, where
    two tools can agree on *which* spots exist while their pixel masks differ.

The match tolerance for detection metrics can be a fixed pixel radius or a
fraction of the mean object radius (auto-scaled, the default).
"""

from __future__ import annotations

import time
import numpy as np


# ---------------------------------------------------------------------------
# Candidate + result containers
# ---------------------------------------------------------------------------

class Candidate:
    """One thing to benchmark.

    method_fn : callable(image) -> labelled mask (int array), or None if this
                candidate is a pre-computed mask.
    mask      : pre-computed labelled mask (for uploaded/external candidates).
    params    : dict describing the settings (for the report / provenance).
    external  : True for uploaded masks (no runtime is recorded).
    """
    def __init__(self, name, method_fn=None, mask=None, params=None,
                 external=False):
        self.name = name
        self.method_fn = method_fn
        self.mask = mask
        self.params = params or {}
        self.external = external


def _labelled(mask):
    """Ensure an integer labelled mask. Accepts a boolean/binary mask (labels
    connected components) or an already-labelled integer mask."""
    from scipy import ndimage as ndi
    m = np.asarray(mask)
    if m.dtype == bool or set(np.unique(m)).issubset({0, 1}):
        lab, _ = ndi.label(m > 0)
        return lab
    return m.astype(np.int32)


def run_candidate(cand, image):
    """Execute one candidate on the image, returning (labelled_mask, runtime_s).
    External (pre-computed) candidates return their stored mask and runtime=None.
    """
    if cand.external or cand.method_fn is None:
        return _labelled(cand.mask), None
    t0 = time.perf_counter()
    out = cand.method_fn(image)
    dt = time.perf_counter() - t0
    return _labelled(out), dt


# ---------------------------------------------------------------------------
# Object descriptors
# ---------------------------------------------------------------------------

def _object_table(labelled):
    """Return (centroids Nx2, areas N) for a labelled mask (background=0)."""
    from skimage.measure import regionprops
    props = regionprops(labelled)
    if not props:
        return np.empty((0, 2)), np.empty((0,))
    cent = np.array([p.centroid for p in props], dtype=np.float64)
    area = np.array([p.area for p in props], dtype=np.float64)
    return cent, area


def _mean_object_radius(areas):
    if areas.size == 0:
        return 1.0
    # radius of a circle with the median object area
    return float(np.sqrt(np.median(areas) / np.pi))


# ---------------------------------------------------------------------------
# Pixel-overlap metrics (Dice / IoU)
# ---------------------------------------------------------------------------

def pixel_overlap(mask_a, mask_b):
    """Dice and IoU between two masks (any labels; compared as foreground)."""
    a = np.asarray(mask_a) > 0
    b = np.asarray(mask_b) > 0
    inter = np.logical_and(a, b).sum()
    sa, sb = a.sum(), b.sum()
    union = np.logical_or(a, b).sum()
    dice = (2.0 * inter / (sa + sb)) if (sa + sb) > 0 else np.nan
    iou = (inter / union) if union > 0 else np.nan
    return {'dice': float(dice), 'iou': float(iou)}


# ---------------------------------------------------------------------------
# Matched-detection metrics (Hungarian centroid matching)
# ---------------------------------------------------------------------------

def matched_detection(labelled_pred, labelled_true, tolerance_px):
    """Match predicted objects to true objects by centroid distance (Hungarian
    assignment), counting a pair as a true positive only if their centroids are
    within tolerance_px. Returns precision/recall/F1, TP/FP/FN, and the mean
    localisation error of matched pairs.
    """
    from scipy.optimize import linear_sum_assignment

    cp, _ = _object_table(labelled_pred)
    ct, _ = _object_table(labelled_true)
    n_pred, n_true = len(cp), len(ct)

    if n_pred == 0 or n_true == 0:
        tp = 0
        fp = n_pred
        fn = n_true
        loc_err = np.nan
    else:
        # cost = pairwise centroid distance
        d = np.linalg.norm(cp[:, None, :] - ct[None, :, :], axis=2)
        # Hungarian on the full matrix, then keep only within-tolerance pairs.
        row, col = linear_sum_assignment(d)
        matched = [(r, c) for r, c in zip(row, col) if d[r, c] <= tolerance_px]
        tp = len(matched)
        fp = n_pred - tp
        fn = n_true - tp
        loc_err = float(np.mean([d[r, c] for r, c in matched])) if matched else np.nan

    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    if precision and recall and (precision + recall) > 0 and not (
            np.isnan(precision) or np.isnan(recall)):
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = np.nan
    return {'precision': float(precision) if precision == precision else np.nan,
            'recall': float(recall) if recall == recall else np.nan,
            'f1': float(f1) if f1 == f1 else np.nan,
            'tp': int(tp), 'fp': int(fp), 'fn': int(fn),
            'localization_error_px': loc_err}


def _resolve_tolerance(tolerance_mode, fixed_px, ref_labelled, scale_fraction):
    """Return the match tolerance in pixels. 'auto' scales to a fraction of the
    mean object radius of the reference mask; 'fixed' uses fixed_px."""
    if tolerance_mode == 'fixed':
        return float(fixed_px)
    _, areas = _object_table(ref_labelled)
    r = _mean_object_radius(areas)
    return max(1.0, float(scale_fraction) * r)


# ---------------------------------------------------------------------------
# Basic per-candidate descriptors (count / area / runtime)
# ---------------------------------------------------------------------------

def basic_metrics(labelled, runtime_s, microns_per_px=None):
    _, areas = _object_table(labelled)
    n = int(areas.size)
    total_area = float(areas.sum())
    mean_area = float(areas.mean()) if n else np.nan
    out = {'n_objects': n, 'total_area_px': total_area,
           'mean_area_px': mean_area,
           'runtime_s': (None if runtime_s is None else float(runtime_s))}
    if microns_per_px:
        a2 = microns_per_px ** 2
        out['total_area_um2'] = total_area * a2
        out['mean_area_um2'] = mean_area * a2 if n else np.nan
    return out


# ---------------------------------------------------------------------------
# The three modes, over one core
# ---------------------------------------------------------------------------

def run_benchmark(image, candidates, ground_truth_name=None,
                  tolerance_mode='auto', fixed_tolerance_px=5.0,
                  scale_fraction=0.5, microns_per_px=None, progress_cb=None):
    """Run all candidates on `image`, score them, and return a results dict.

    If `ground_truth_name` names one of the candidates, every other candidate is
    scored against it (validation mode: pixel Dice/IoU + matched-detection F1).
    Otherwise, method-comparison mode: basic metrics per candidate plus a
    pairwise-overlap matrix (no ground truth needed). Parameter-sensitivity mode
    is just method-comparison where the candidates are the same method at
    different parameter values — no special handling needed here; the caller
    builds those candidates and can read the trend from the ordered results.

    Returns a dict with 'candidates' (list of per-candidate result dicts),
    'ground_truth' (name or None), 'pairwise' (overlap matrix in comparison
    mode), and 'tolerance_px'.
    """
    # 1) Run every candidate.
    labels = {}
    runtimes = {}
    total = len(candidates)
    for i, cand in enumerate(candidates):
        lab, dt = run_candidate(cand, image)
        labels[cand.name] = lab
        runtimes[cand.name] = dt
        if progress_cb:
            progress_cb(i + 1, total)

    # 2) Resolve detection tolerance from the reference mask.
    ref_name = ground_truth_name if ground_truth_name in labels else (
        candidates[0].name if candidates else None)
    tol = _resolve_tolerance(tolerance_mode, fixed_tolerance_px,
                             labels.get(ref_name, np.zeros((1, 1), int)),
                             scale_fraction)

    results = []
    for cand in candidates:
        lab = labels[cand.name]
        row = {'name': cand.name, 'external': cand.external,
               'params': cand.params}
        row.update(basic_metrics(lab, runtimes[cand.name], microns_per_px))
        if ground_truth_name and cand.name != ground_truth_name:
            gt = labels[ground_truth_name]
            row['vs_ground_truth'] = {
                **pixel_overlap(lab, gt),
                **matched_detection(lab, gt, tol),
            }
        results.append(row)

    out = {'candidates': results, 'ground_truth': ground_truth_name,
           'tolerance_px': tol, 'tolerance_mode': tolerance_mode,
           'labels': labels}

    # 3) Comparison mode: pairwise overlap matrix (Dice) between candidates.
    if not ground_truth_name and len(candidates) > 1:
        names = [c.name for c in candidates]
        mat = {}
        for a in names:
            for b in names:
                if a < b:
                    mat[f"{a}||{b}"] = pixel_overlap(labels[a], labels[b])
        out['pairwise'] = mat
    return out


# ---------------------------------------------------------------------------
# Built-in segmenter adapters (each returns an image -> labelled-mask callable)
# ---------------------------------------------------------------------------

def builtin_method_candidates(object_diameter=30, ball_radius=15,
                              include=None):
    """Return a list of Candidate objects wrapping PyCAT's built-in segmenters.

    include : optional list of method keys to restrict to. Available keys:
        'otsu', 'multiotsu', 'sauvola', 'watershed', 'felzenszwalb', 'cellpose'.
    Each adapter is defensive: if a backend is unavailable (e.g. Cellpose not
    installed) it is simply omitted rather than raising.
    """
    import numpy as np
    from scipy import ndimage as ndi

    cands = []

    def _add(key, name, fn, params):
        if include is None or key in include:
            cands.append(Candidate(name, method_fn=fn, params=params))

    def _otsu(img):
        from skimage.filters import threshold_otsu
        a = np.asarray(img, dtype=float)
        try:
            t = threshold_otsu(a)
        except Exception:
            t = a.mean()
        return ndi.label(a > t)[0]
    _add('otsu', 'Otsu', _otsu, {'method': 'global Otsu'})

    def _multiotsu(img):
        from skimage.filters import threshold_multiotsu
        a = np.asarray(img, dtype=float)
        try:
            ts = threshold_multiotsu(a, classes=3)
            return ndi.label(a > ts[-1])[0]
        except Exception:
            return _otsu(img)
    _add('multiotsu', 'Multi-Otsu', _multiotsu, {'method': 'multi-Otsu, top class'})

    def _sauvola(img):
        from skimage.filters import threshold_sauvola
        a = np.asarray(img, dtype=float)
        try:
            t = threshold_sauvola(a, window_size=25)
            return ndi.label(a > t)[0]
        except Exception:
            return _otsu(img)
    _add('sauvola', 'Sauvola', _sauvola, {'method': 'Sauvola local', 'window': 25})

    def _felzen(img):
        from pycat.toolbox.segmentation_tools import felzenszwalb_segmentation_and_merging
        a = np.asarray(img, dtype=float)
        seg = felzenszwalb_segmentation_and_merging(a, scale=7.0, sigma=0.5, min_size=2)
        # felzenszwalb returns an oversegmentation labelling; treat non-bg as fg
        return np.asarray(seg, dtype=np.int32)
    _add('felzenszwalb', 'Felzenszwalb', _felzen, {'method': 'Felzenszwalb'})

    def _cellpose(img):
        from pycat.toolbox.segmentation_tools import cellpose_segmentation
        out = cellpose_segmentation(np.asarray(img, dtype=float),
                                    object_diameter, postprocess=True)
        # cellpose_segmentation may return (masks, ...) or masks
        masks = out[0] if isinstance(out, (tuple, list)) else out
        return np.asarray(masks, dtype=np.int32)
    # Only add Cellpose if importable.
    try:
        import cellpose  # noqa: F401
        _add('cellpose', 'Cellpose', _cellpose,
             {'method': 'Cellpose', 'diameter': object_diameter})
    except Exception:
        pass

    return cands




def to_markdown_table(results):
    """Render a benchmark result dict as a markdown table (pasteable)."""
    rows = results['candidates']
    gt = results['ground_truth']
    lines = []
    if gt:
        lines.append(f"**Validation against ground truth: `{gt}`** "
                     f"(detection tolerance = {results['tolerance_px']:.1f} px, "
                     f"{results['tolerance_mode']})\n")
        header = ("| Method | N | Dice | IoU | Precision | Recall | F1 | "
                  "Loc.err (px) | Runtime (s) |")
        sep = "|---|---|---|---|---|---|---|---|---|"
        lines += [header, sep]
        for r in rows:
            v = r.get('vs_ground_truth')
            rt = "—" if r['runtime_s'] is None else f"{r['runtime_s']:.2f}"
            if r['name'] == gt:
                lines.append(f"| **{r['name']}** (GT) | {r['n_objects']} | "
                             f"— | — | — | — | — | — | {rt} |")
            elif v:
                le = "—" if (v['localization_error_px'] is None or
                             v['localization_error_px'] != v['localization_error_px']) \
                    else f"{v['localization_error_px']:.2f}"
                lines.append(
                    f"| {r['name']} | {r['n_objects']} | {v['dice']:.3f} | "
                    f"{v['iou']:.3f} | {v['precision']:.3f} | {v['recall']:.3f} | "
                    f"{v['f1']:.3f} | {le} | {rt} |")
    else:
        lines.append("**Method comparison** (no ground truth)\n")
        header = "| Method | N objects | Total area (px) | Mean area (px) | Runtime (s) |"
        sep = "|---|---|---|---|---|"
        lines += [header, sep]
        for r in rows:
            rt = "—" if r['runtime_s'] is None else f"{r['runtime_s']:.2f}"
            ma = "—" if r['mean_area_px'] != r['mean_area_px'] else f"{r['mean_area_px']:.1f}"
            lines.append(f"| {r['name']} | {r['n_objects']} | "
                         f"{r['total_area_px']:.0f} | {ma} | {rt} |")
    return "\n".join(lines)
