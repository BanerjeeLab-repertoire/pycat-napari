"""**At what intensity level do humans actually trace the edge of an in-cell punctum?**

Phase 1 of the 2D cellular region-selection calibration
(`docs/audits/PyCAT_2D_Cellular_Region_Selection_Spec.md`). `boundary_refit.py` places each
object's edge at ``background + level * (peak - background)`` with ``level = 0.5`` — the
half-maximum — on the stated assumption that this is "close to where a human annotator traces a
condensate". This measures that assumption against the human masks catalogued by
`benchmarks/mask_inventory.py`, object by object.

Per annotated object, on the RAW GFP image smoothed exactly as the refit smooths it:

    bg      the refit's own local background — 25th percentile of a ring of width
            max(3, 2 r_eq) around the object, every annotated object excluded
            (`boundary_refit._local_background`), so a calibrated level plugs straight in
    sigma   noise of that same ring, from the sigma-clipped `robust_cell_background`
    peak    98th percentile inside the mask (the refit's `DEFAULT_PEAK_PERCENTILE`)
    sweep   t = bg + a (peak - bg), a in [A_MIN, 1]; at each t the connected iso-region
            holding the object's brightest pixel, holes filled (as the refit does), is
            scored by IoU against the human mask. t* maximises it.

t* is then expressed three ways — A: fraction of amplitude, B: relative over background,
C: noise units — and the parameterization with the lowest spread across objects is the one
that generalizes (spec 1.2). Because every object's full IoU-vs-level curve is kept, any
candidate default (a constant in A, B or C, floored or not, or a function of object size) can
be SCORED on the whole population, not just argued for; that is what `evaluate_rules` does.

Run it
------
    python -m benchmarks.mask_level_calibration [DATA_ROOT]

Writes `benchmarks/mask_level_objects.csv` (one row per object) and figures under
`docs/audits/mask_level_calibration/`, and prints the tables the report is built from.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import scipy.ndimage as ndi
import tifffile
from PIL import Image
from scipy import stats

from benchmarks.mask_inventory import (
    CLASSES,
    DEFAULT_ROOT,
    HUMAN_ANNOTATORS,
    N_FIELDS,
    _annotator_dirs,
    _mask_path,
)
from pycat.toolbox.segmentation.boundary_refit import (
    BACKGROUND_PERCENTILE,
    DEFAULT_PEAK_PERCENTILE,
)
from pycat.toolbox.segmentation.intensity import robust_cell_background

SMOOTH_SIGMA = 1.0          # refit_object_boundaries(smooth_sigma=1.0)
A_MIN = -0.30               # sweep a little BELOW the ring background: humans may trace past it
LEVELS = np.round(np.arange(A_MIN, 1.0001, 0.01), 4)
MIN_AREA_PX = 4             # below this an IoU-vs-level curve has too few pixels to mean anything

HERE = Path(__file__).resolve().parent
FIG_DIR = HERE.parent / 'docs' / 'audits' / 'mask_level_calibration'


def _load(path):
    return np.array(Image.open(path)) if path.suffix.lower() == '.png' else tifffile.imread(path)


def _display_stats(raw):
    """Contrast limits two standard auto-scales would give this field (spec 1.3)."""
    return {'disp_min': float(raw.min()), 'disp_max': float(raw.max()),
            'disp_p1': float(np.percentile(raw, 1)), 'disp_p99': float(np.percentile(raw, 99))}


def _sweep_object(sub, human, excluded):
    """IoU and area of the level contour at every entry of LEVELS, plus the level stats."""
    area = int(human.sum())
    r_eq = np.sqrt(area / np.pi)
    ring_px = max(3, int(round(2.0 * r_eq)))
    grown = ndi.distance_transform_edt(~human) <= ring_px
    ring = grown & ~human & ~excluded
    if int(ring.sum()) < 8:
        ring = grown & ~human
    bg = float(np.percentile(sub[ring], BACKGROUND_PERCENTILE))
    _clip_base, sigma = robust_cell_background(sub[ring])
    peak = float(np.percentile(sub[human], DEFAULT_PEAK_PERCENTILE))
    if peak <= bg:
        return None
    flat = np.flatnonzero(human)
    anchor = np.unravel_index(flat[np.argmax(sub.ravel()[flat])], sub.shape)
    ious = np.zeros(LEVELS.size)
    areas = np.zeros(LEVELS.size)
    for i, a in enumerate(LEVELS):
        cand = sub >= bg + a * (peak - bg)
        lab, _ = ndi.label(cand)
        region = ndi.binary_fill_holes(lab == lab[anchor]) if lab[anchor] else np.zeros_like(cand)
        inter = int((region & human).sum())
        union = int((region | human).sum())
        ious[i] = inter / union if union else 0.0
        areas[i] = int(region.sum())
    return {'bg': bg, 'sigma': float(sigma), 'peak': peak, 'area_px': area,
            'eq_radius_px': float(r_eq), 'ious': ious, 'areas': areas,
            'mean_inside': float(sub[human].mean())}


def _best_level(ious):
    """Middle of the arg-max plateau — small objects often score equally over a range."""
    top = np.flatnonzero(ious >= ious.max() - 1e-9)
    return float(LEVELS[top[len(top) // 2]]), bool(top[0] == 0)


def _field_objects(raw, masks_by_annotator, excluded_all, meta):
    """Sweep every object of every annotator in one field."""
    img = ndi.gaussian_filter(raw.astype(np.float32), SMOOTH_SIGMA)
    rows = []
    for annotator, mask in masks_by_annotator.items():
        for index, window in enumerate(ndi.find_objects(mask.astype(np.int32)), start=1):
            if window is None:
                continue
            obj = mask[window] == index
            if int(obj.sum()) < MIN_AREA_PX:
                continue
            r = np.sqrt(obj.sum() / np.pi)
            pad = int(np.ceil(max(8.0, 3.0 * r)))
            view = tuple(slice(max(0, s.start - pad), min(dim, s.stop + pad))
                         for s, dim in zip(window, mask.shape, strict=True))
            human = mask[view] == index
            res = _sweep_object(img[view], human, excluded_all[view] & ~human)
            if res is None:
                continue
            a_star, at_floor = _best_level(res['ious'])
            t_star = res['bg'] + a_star * (res['peak'] - res['bg'])
            amp = res['peak'] - res['bg']
            rows.append({**meta, 'annotator': annotator, 'label': index,
                         'area_px': res['area_px'], 'eq_radius_px': round(res['eq_radius_px'], 3),
                         'bg': round(res['bg'], 3), 'sigma': round(res['sigma'], 3),
                         'peak': round(res['peak'], 3), 'amplitude': round(amp, 3),
                         'snr': round(amp / max(res['sigma'], 1e-6), 3),
                         'iou_max': round(float(res['ious'].max()), 4),
                         'iou_at_fwhm': round(float(res['ious'][np.argmin(abs(LEVELS - 0.5))]), 4),
                         'A_frac_amplitude': a_star,
                         'B_rel_over_bg': (t_star / res['bg'] - 1.0) if res['bg'] > 0 else np.nan,
                         'C_noise_units': (t_star - res['bg']) / max(res['sigma'], 1e-6),
                         'at_sweep_floor': at_floor,
                         '_ious': res['ious'], '_areas': res['areas']})
    return rows


def collect(root=DEFAULT_ROOT):
    root = Path(root)
    rows = []
    for cls in CLASSES:
        class_dir = root / cls
        ann_dirs = _annotator_dirs(class_dir)
        for n in range(1, N_FIELDS + 1):
            raw = _load(class_dir / f'In Cell {n}-GFP.tif').astype(np.float64)
            masks = {a: _load(_mask_path(d, a, n)) for a, d in ann_dirs.items()
                     if _mask_path(d, a, n).exists()}
            excluded = np.zeros(raw.shape, dtype=bool)
            for m in masks.values():
                excluded |= m > 0
            meta = {'size_class': cls, 'field': n, **_display_stats(raw)}
            rows.extend(_field_objects(raw, masks, excluded, meta))
            print(f'  {cls} field {n}: {len(rows)} objects so far', flush=True)
    return rows


def _level_for_rule(row, rule):
    """The amplitude fraction `a` a rule would place this object's edge at."""
    amp = row['peak'] - row['bg']
    kind, value = rule[0], rule[1]
    if kind == 'A':
        a = value(row) if callable(value) else value
    elif kind == 'B':
        a = value * row['bg'] / amp
    else:
        a = value * row['sigma'] / amp
    floor_sigma = rule[2] if len(rule) > 2 else None
    if floor_sigma is not None:
        a = max(a, floor_sigma * row['sigma'] / amp)
    return float(np.clip(a, LEVELS[0], LEVELS[-1]))


def evaluate_rules(rows, rules):
    """Mean IoU and median pred/GT area ratio each rule achieves, by radius bin."""
    bins = ((0, 2.5), (2.5, 4), (4, 6), (6, 99))
    out = {}
    for name, rule in rules.items():
        per = []
        for row in rows:
            i = int(np.argmin(abs(LEVELS - _level_for_rule(row, rule))))
            per.append((row['eq_radius_px'], row['_ious'][i], row['_areas'][i] / row['area_px']))
        per = np.array(per)
        res = {'all': (per[:, 1].mean(), np.median(per[:, 2]), len(per))}
        for lo, hi in bins:
            sel = (per[:, 0] >= lo) & (per[:, 0] < hi)
            if sel.any():
                res[f'r {lo}-{hi}'] = (per[sel, 1].mean(), np.median(per[sel, 2]), int(sel.sum()))
        out[name] = res
    return out


def _spread(values):
    v = np.asarray([x for x in values if np.isfinite(x)])
    med = np.median(v)
    q1, q3 = np.percentile(v, [25, 75])
    return {'n': v.size, 'median': med, 'q1': q1, 'q3': q3, 'mean': v.mean(), 'sd': v.std(),
            'cv': v.std() / abs(v.mean()) if v.mean() else np.inf,
            'robust_cv': (q3 - q1) / 1.349 / abs(med) if med else np.inf}


PARAMS = ('A_frac_amplitude', 'B_rel_over_bg', 'C_noise_units')


def _write_csv(rows):
    keys = [k for k in rows[0] if not k.startswith('_')]
    with open(HERE / 'mask_level_objects.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def report_tables(rows):
    human = [r for r in rows if r['annotator'] in HUMAN_ANNOTATORS]
    good = [r for r in human if r['iou_max'] >= 0.5]
    print(f'\nobjects swept: {len(rows)} (human {len(human)}; iou_max >= 0.5: {len(good)}; '
          f'best level at sweep floor: {sum(r["at_sweep_floor"] for r in human)})')
    print('\n1.2  distributions of t* (human annotators, iou_max >= 0.5)')
    for p in PARAMS:
        s = _spread(r[p] for r in good)
        print(f'  {p:18s} median {s["median"]:8.3f}  IQR [{s["q1"]:.3f}, {s["q3"]:.3f}]  '
              f'CV {s["cv"]:.3f}  robust CV {s["robust_cv"]:.3f}  n={s["n"]}')
    print('\n     by annotator (median A / B / C, iou_max median)')
    for a in list(HUMAN_ANNOTATORS) + ['consensus']:
        sel = [r for r in rows if r['annotator'] == a and r['iou_max'] >= 0.5]
        print(f'  {a:12s} n={len(sel):5d}  ' + '  '.join(
            f'{np.median([r[p] for r in sel]):.3f}' for p in PARAMS)
              + f'   iou_max {np.median([r["iou_max"] for r in sel]):.3f}')
    print('\n     by size class')
    for c in CLASSES:
        sel = [r for r in good if r['size_class'] == c]
        print(f'  {c:18s} n={len(sel):5d}  ' + '  '.join(
            f'{np.median([r[p] for r in sel]):.3f}' for p in PARAMS))
    return human, good


def dependence_tables(good):
    radius = np.array([r['eq_radius_px'] for r in good])
    print('\n1.3  size dependence: Spearman rho of t* vs equivalent radius')
    for p in PARAMS:
        v = np.array([r[p] for r in good])
        ok = np.isfinite(v)
        rho, pv = stats.spearmanr(radius[ok], v[ok])
        print(f'  {p:18s} rho {rho:+.3f}  p {pv:.2g}')
    print('     A by radius bin: ' + '  '.join(
        f'r{lo}-{hi}: {np.median([r["A_frac_amplitude"] for r in good if lo <= r["eq_radius_px"] < hi]):.3f}'
        f' (n={sum(1 for r in good if lo <= r["eq_radius_px"] < hi)})'
        for lo, hi in ((1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 8), (8, 20))))
    print('\n1.3  display dependence: Spearman rho of A vs')
    a = np.array([r['A_frac_amplitude'] for r in good])
    for name, x in {
        'field dynamic range (max-min)': [r['disp_max'] - r['disp_min'] for r in good],
        'field p99-p1': [r['disp_p99'] - r['disp_p1'] for r in good],
        'peak / field max (min-max display)': [r['peak'] / r['disp_max'] for r in good],
        'peak / field p99 (1-99% display)': [r['peak'] / max(r['disp_p99'], 1) for r in good],
        'object SNR (amplitude / sigma)': [r['snr'] for r in good],
    }.items():
        rho, pv = stats.spearmanr(x, a)
        print(f'  {name:36s} rho {rho:+.3f}  p {pv:.2g}')
    print('\n1.3  annotator dependence (Kruskal-Wallis on A across annotators)')
    groups = [[r['A_frac_amplitude'] for r in good if r['annotator'] == h] for h in HUMAN_ANNOTATORS]
    h, pv = stats.kruskal(*groups)
    print(f'  H {h:.1f}  p {pv:.2g}   medians ' + '  '.join(
        f'{n}={np.median(g):.3f}' for n, g in zip(HUMAN_ANNOTATORS, groups, strict=True)))


def fit_size_rule(good):
    """Least-squares A* = c0 + c1 ln(r_eq) over well-fitted human objects."""
    c1, c0 = np.polyfit(np.log([r['eq_radius_px'] for r in good]),
                        [r['A_frac_amplitude'] for r in good], 1)
    return float(c0), float(c1)


def size_rule(c0, c1, lo=None, hi=None):
    def level(row):
        a = c0 + c1 * np.log(row['eq_radius_px'])
        return float(np.clip(a, lo if lo is not None else -np.inf, hi if hi is not None else np.inf))
    return ('A', level)


def rule_table(human, good):
    c0, c1 = fit_size_rule(good)
    print(f'\nsize rule fitted: A(r) = {c0:.3f} {c1:+.3f} ln r')
    rules = {
        'FWHM A=0.50 (current)': ('A', 0.50),
        'A=median': ('A', float(np.median([r['A_frac_amplitude'] for r in good]))),
        'B=median': ('B', float(np.median([r['B_rel_over_bg'] for r in good]))),
        'B=0.075 (5-10% prior)': ('B', 0.075),
        'C=3.0 sigma': ('C', 3.0),
        'A(r)': size_rule(c0, c1),
        'A(r) clamp [0.25,0.75]': size_rule(c0, c1, 0.25, 0.75),
        'A(r) clamp [0.20,0.80]': size_rule(c0, c1, 0.20, 0.80),
        'oracle (per-object best)': ('A', lambda r: r['A_frac_amplitude']),
    }
    ev = evaluate_rules(human, rules)
    print('rule scores on all human objects: mean IoU / median pred:GT area ratio (n)')
    for name, res in ev.items():
        print(f'  {name:26s}' + '  '.join(f'{b}: {v[0]:.3f}/{v[1]:.2f}' for b, v in res.items()))
    print('\nleave-one-annotator-out (fit on two, score on the third):')
    for held in HUMAN_ANNOTATORS:
        h0, h1 = fit_size_rule([r for r in good if r['annotator'] != held])
        test = [r for r in human if r['annotator'] == held]
        res = evaluate_rules(test, {'A(r)': size_rule(h0, h1, 0.20, 0.80), 'FWHM': ('A', 0.5)})
        print(f'  held out {held:7s} A(r) = {h0:.3f} {h1:+.3f} ln r   ' + '  '.join(
            f'{k}: IoU {v["all"][0]:.3f} area ratio {v["all"][1]:.2f}' for k, v in res.items()))
    return (c0, c1), rules, ev


def main(root=DEFAULT_ROOT, figures=True):
    rows = collect(root)
    _write_csv(rows)
    human, good = report_tables(rows)
    dependence_tables(good)
    coef, rules, ev = rule_table(human, good)
    if figures:
        from benchmarks.mask_level_figures import draw_all
        draw_all(human, good, coef, rules, FIG_DIR)
    return rows, human, good


if __name__ == '__main__':
    main(*sys.argv[1:2])
