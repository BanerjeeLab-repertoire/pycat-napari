"""Cellular time series: per-cell condensate intensities and kinetics on RAW counts (1.7.004).

Built on the opto-dissolution movie (``dissolution.tif``: 60 frames x 10 s, 561 nm, 13 cells of very
different expression). Three things the time axis requires that a 2D image does not:

* **Intensities stay connected in time.** Nothing is rescaled per frame or per cell. Every number is raw
  counts minus that frame's camera background (the median outside the cells), so a cell's signal at frame 40
  is directly comparable with its signal at frame 0. Optogenetic condensates move material between the
  condensates and the dilute phase; on the dissolution movie a cell's total signal is roughly constant while
  its dilute level rises 3 -> 9 counts as the condensates dissolve -- per-frame normalisation erases exactly
  that.

* **Cells differ in expression** (transient transfection). Absolute signals cannot be compared between
  cells, so each cell is described by ratios of its own signal: ``condensed_fraction`` (the signal its
  condensates hold above the dilute level / its total signal) and ``partition`` (condensate / dilute
  intensity). Expression itself (``expression``, the cell's median net intensity over the movie) is kept as
  the covariate: on the dissolution movie low-expression cells dissolve with half-times of 35-95 s and
  high-expression cells with 100-170 s.

* **A second, segmentation-free measure** (``condensed_fraction_mask_free``: signal more than 3 robust SD
  above the cell's dilute level / total) is reported beside the mask-based one. It needs no condensate mask,
  so it catches condensates too dim to segment (cell 6 of the dissolution movie) and does not count static
  bright texture as a response (cell 13). On that movie the two agree at r = 0.97 per cell-frame.

Segmentation is per frame with the 2D settings (`segmentation.subcellular.segment_condensates_in_cells`);
measured there, averaging neighbouring frames before segmenting made every score worse (moving puncta blur,
fewer found, more merged), so the time axis enters through the measurements and the tracks instead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.ndimage as ndi
import skimage as sk

# The minimum change in condensed fraction (of the cell's total signal) a cell must show to count as
# responding, on top of the noise test. Static texture that segments late in a dim cell moved the fraction
# by 0.013 on the dissolution movie; every real response there was >= 0.046.
MIN_RESPONSE_FRACTION = 0.02


def background_level(frame, cell_labels, pad=15):
    """(median, robust SD) of the frame outside the cells, ``pad`` px clear of them -- the camera
    background. Falls back to the dimmest 5 % of the frame when the cells leave too little outside."""
    frame = np.asarray(frame, dtype=np.float32)
    outside = ~ndi.binary_dilation(np.asarray(cell_labels) > 0, iterations=int(pad)) if pad else np.asarray(cell_labels) == 0
    px = frame[outside]
    if px.size < 500:
        px = np.sort(frame.ravel())[:max(1, frame.size // 20)]
    med = float(np.median(px))
    return med, float(1.4826 * np.median(np.abs(px - med)))


def _trimmed_mean(x, lo=5, hi=95):
    """Mean of the values between the lo-th and hi-th percentiles. Raw counts are integers, and on a dim
    cell the dilute phase spans only a few of them, so a median moves in whole-count steps (3, 4, 9 ...);
    the trimmed mean does not, and still ignores undetected puncta and dark nucleoli."""
    if x.size == 0:
        return np.nan
    a, b = np.percentile(x, (lo, hi))
    sel = x[(x >= a) & (x <= b)]
    return float(sel.mean()) if sel.size else float(np.mean(x))


def _cell_row(r, cm, p, guard_px):
    """One cell's measurements from its background-subtracted crop ``r``, cell mask ``cm`` and condensate
    mask ``p`` (both crop-sized)."""
    near = ndi.binary_dilation(p, iterations=guard_px) if p.any() else p
    dil = cm & ~near
    dpx = r[dil] if dil.sum() >= 20 else r[cm]
    d = _trimmed_mean(dpx)
    dsd = float(1.4826 * np.median(np.abs(dpx - np.median(dpx)))) if dpx.size else np.nan
    tot = float(r[cm].sum())
    cpx = r[p]
    exc = float((cpx - d).sum()) if cpx.size else 0.0
    free = float(np.clip(r[cm] - d - 3.0 * dsd, 0, None).sum()) if np.isfinite(dsd) else np.nan
    area = int(cm.sum())
    return {'cell_area_px': area, 'total_net': tot, 'mean_net': tot / area if area else np.nan,
            'dilute_net': d, 'dilute_sd': dsd,
            'condensate_area_px': int(p.sum()),
            'condensate_mean_net': float(cpx.mean()) if cpx.size else np.nan,
            'condensed_excess': exc,
            'condensed_fraction': exc / tot if tot > 0 else np.nan,
            'condensed_fraction_mask_free': free / tot if tot > 0 else np.nan,
            'partition': float(cpx.mean()) / d if cpx.size and d > 0 else np.nan}


def _condensate_rows(r, cl, d, mpp, off, frame, cell):
    """Per-condensate rows for one cell: ``cl`` are the cell's condensate labels in the crop, ``d`` its
    dilute level, ``off`` the crop origin (so positions are in the full frame)."""
    rows = []
    for prop in sk.measure.regionprops(cl, intensity_image=r):
        px = r[prop.slice][prop.image]
        y, x = prop.centroid
        y0, x0, y1, x1 = prop.bbox
        rows.append({'frame': frame, 'cell': cell, 'label': int(prop.label),
                     'y_px': y + off[0], 'x_px': x + off[1], 'y_um': (y + off[0]) * mpp, 'x_um': (x + off[1]) * mpp,
                     'bbox_y0': y0 + off[0], 'bbox_x0': x0 + off[1], 'bbox_y1': y1 + off[0], 'bbox_x1': x1 + off[1],
                     'area_px': int(prop.area), 'area_um2': prop.area * mpp * mpp,
                     'equiv_radius_um': float(np.sqrt(prop.area / np.pi)) * mpp,
                     'mean_net': float(px.mean()), 'max_net': float(px.max()),
                     'excess': float((px - d).sum()),
                     'partition': float(px.mean()) / d if d > 0 else np.nan})
    return rows


def measure_frame(raw, cell_labels, condensate_labels, frame=0, microns_per_pixel=1.0, guard_px=2, bg_pad=15):
    """Every cell's (and every condensate's) intensities in one frame, on raw counts.

    ``condensate_labels`` is this frame's labelled condensate mask (0 = none). Returns
    ``(cell_rows, condensate_rows)`` (lists of dicts). The dilute phase is the cell minus its condensates
    grown by ``guard_px`` (so their blurred rims are not counted as dilute).
    """
    raw = np.asarray(raw, dtype=np.float32)
    cell_labels = np.asarray(cell_labels)
    condensate_labels = np.asarray(condensate_labels)
    bg, bg_sd = background_level(raw, cell_labels, bg_pad)
    cell_rows, cond_rows = [], []
    for cell, sl in enumerate(ndi.find_objects(cell_labels), start=1):
        if sl is None:
            continue
        cm = cell_labels[sl] == cell
        r = raw[sl] - bg
        cl = np.where(cm, condensate_labels[sl], 0)
        row = {'frame': int(frame), 'cell': int(cell), 'background': bg, 'background_sd': bg_sd,
               **_cell_row(r, cm, cl > 0, guard_px)}
        row['n_condensates'] = int(len(np.unique(cl)) - 1)
        cell_rows.append(row)
        cond_rows += _condensate_rows(r, cl, row['dilute_net'], microns_per_pixel,
                                      (sl[0].start, sl[1].start), int(frame), int(cell))
    return cell_rows, cond_rows


def add_bleach_and_expression(per_cell: pd.DataFrame, baseline_frames=3) -> pd.DataFrame:
    """Per-cell ``expression`` (median net intensity over the movie) and ``signal_retained`` (total signal /
    its mean over the first ``baseline_frames`` frames -- photobleaching plus any material leaving the mask).
    Returns a new frame."""
    df = per_cell.sort_values(['cell', 'frame'])
    first = df.groupby('cell')['total_net'].transform(lambda s: s.iloc[:baseline_frames].mean())
    return df.assign(expression=df.groupby('cell')['mean_net'].transform('median'),
                     signal_retained=df['total_net'] / first.where(first > 0)).reset_index(drop=True)


def link_condensate_tracks(per_condensate: pd.DataFrame, max_gap_frames=1, min_track_frames=1):
    """Link condensates frame to frame (`timeseries_invitro_tools.link_condensates`: Bayesian/Hungarian,
    size-scaled search radius, fusion-aware) and add ``track_id`` and ``track_frames`` (how many frames the
    track was detected in -- its persistence). A detection seen in fewer than ``min_track_frames`` frames is
    dropped (1 = keep all). Returns (linked, fusions)."""
    from pycat.toolbox.timeseries_invitro_tools import link_condensates
    if per_condensate is None or not len(per_condensate):
        return per_condensate, pd.DataFrame()
    df = per_condensate.reset_index(drop=True).assign(object_id=lambda d: np.arange(len(d)))
    linked, fusions = link_condensates(df, max_gap_frames=int(max_gap_frames))
    linked = linked.assign(track_frames=linked.groupby('track_id')['frame'].transform('nunique'))
    if min_track_frames > 1:
        linked = linked[linked['track_frames'] >= int(min_track_frames)]
    return linked.reset_index(drop=True), fusions


def cell_kinetics(per_cell: pd.DataFrame, metric='condensed_fraction', frame_interval_s=1.0, smooth_frames=5,
                  response_k=5.0, min_response=MIN_RESPONSE_FRACTION) -> pd.DataFrame:
    """Model-free kinetics of ``metric`` for every cell (`invitro.timeseries_kinetics._track_kinetics`, the
    in-vitro time series' own estimator): baseline / plateau, amplitude, noise, direction (rise / fall),
    onset / half / 90 % times, max rate -- plus the cell's ``expression``.

    ``responded`` needs the change to exceed ``response_k`` x the trace's noise AND ``min_response`` in
    absolute terms (fractions are bounded, and a trace that sits at exactly 0 has no noise to compare with).
    """
    from pycat.toolbox.invitro.timeseries_kinetics import _track_kinetics
    dt = float(frame_interval_s) if frame_interval_s else 1.0
    rows = []
    for cell, g in per_cell.sort_values('frame').groupby('cell'):
        t = g['frame'].to_numpy(dtype=float) * dt
        y = g[metric].fillna(0.0).to_numpy(dtype=float)
        if len(y) < 4:
            continue
        k = _track_kinetics(t, y, smooth_frames, response_k)
        k['noise'] = max(k['noise'], 1e-3 * max(1.0, abs(k['baseline'])))
        k['snr'] = abs(k['amplitude']) / k['noise']
        ok = abs(k['amplitude']) >= response_k * k['noise'] and abs(k['amplitude']) >= min_response
        if not ok:
            k.update(responded=False, t_onset_s=np.nan, t_half_s=np.nan, t_90_s=np.nan, max_rate_per_s=np.nan)
        rows.append({'cell': int(cell), 'metric': metric,
                     'expression': float(g['expression'].iloc[0]) if 'expression' in g else np.nan, **k})
    return pd.DataFrame(rows)


def field_summary(kin: pd.DataFrame) -> dict:
    """The field's kinetics in numbers: how many cells responded, their half-times (median / IQR), whether
    the timing depends on expression (Spearman), and the expression range separating responders from
    non-responders -- a bracket on the concentration a cell needs to form condensates at all."""
    out = {'n_cells': int(len(kin))}
    if not len(kin):
        return out
    resp = kin[kin['responded']]
    out['n_responded'] = int(len(resp))
    out['direction'] = (resp['direction'].mode().iloc[0] if len(resp) else '')
    th = resp['t_half_s'].dropna()
    if len(th):
        q1, med, q3 = np.percentile(th, (25, 50, 75))
        out.update(t_half_s_median=float(med), t_half_s_iqr=float(q3 - q1))
    both = resp[['t_half_s', 'expression']].dropna()
    if len(both) >= 4:
        out['spearman_t_half_vs_expression'] = float(both['t_half_s'].rank().corr(both['expression'].rank()))
    non = kin[~kin['responded']]['expression'].dropna()
    if len(resp) and len(non):
        out['expression_lowest_responder'] = float(resp['expression'].min())
        out['expression_highest_nonresponder'] = float(non.max())
    return out
