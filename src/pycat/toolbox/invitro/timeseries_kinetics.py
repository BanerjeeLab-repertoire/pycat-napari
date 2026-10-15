"""**Per-droplet, multi-channel measurement and reaction kinetics for in-vitro time series (1.7.002).**

Built against an Andor spinning-disk movie of a condensate reaction (180 frames x 60 s): a dense droplet field
that is present from the first frame (clear in the substrate channel and in brightfield), a reaction channel
that starts at background and lights droplet by droplet over the first half hour -- some early, many late --
and a substrate channel that only bleaches. Three things the time-series panel could not do:

* **Measure every channel in the SAME droplets.** Droplets are segmented once, on whichever channel shows them
  throughout (the substrate here; the reaction channel is blank at t = 0), and every channel is measured in
  those masks.
* **Subtract each droplet's own dilute phase.** A raw mean carries the camera offset (~500 counts on this
  EMCCD) and any slow field drift. The background is the median of a ring ``gap_px``..``gap_px+ring_px`` outside
  the droplet, built with ``expand_labels`` so every ring pixel belongs to its NEAREST droplet and no ring
  enters another droplet -- the condition that matters in a dense field. ``net = mean - background``.
* **Quantify the kinetics per droplet** -- baseline, plateau, onset / half / 90 % times, maximum rate, and
  whether the droplet responded at all -- so the heterogeneity is a distribution, not an impression.

The kinetics are MODEL-FREE (crossing times of a lightly smoothed trace), not a fitted curve: the onset
behaviour of a heterogeneous reaction is exactly what a single fitted model would average away.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

#: Ring geometry for the local dilute-phase background (px): skip ``gap`` px of the droplet's blurred edge,
#: then take the next ``ring`` px.
BACKGROUND_GAP_PX = 2
BACKGROUND_RING_PX = 4
#: A ring with fewer pixels than this (a droplet boxed in by neighbours) uses the frame's global dilute median.
MIN_RING_PX = 15
#: A droplet "responded" when its amplitude exceeds this many times its own frame-to-frame noise.
RESPONSE_K = 5.0


def channel_column(name: str) -> str:
    """A column-safe form of a channel/layer name: 'C0-488' -> 'C0_488'."""
    return re.sub(r'[^0-9A-Za-z]+', '_', str(name)).strip('_') or 'ch'


def _frame_measurements(lab, frame_images, mpp, gap_px, ring_px, min_ring_px):
    """One frame: geometry + per-channel mean / local background / net for every droplet."""
    from scipy import ndimage as ndi
    from skimage import measure
    from skimage.segmentation import expand_labels

    props = measure.regionprops(lab)
    if not props:
        return []
    ids = np.array([p.label for p in props])
    near = expand_labels(lab, gap_px)
    far = expand_labels(lab, gap_px + ring_px)
    ring_lab = np.where(near == 0, far, 0)
    ring_n = np.bincount(ring_lab.ravel(), minlength=int(lab.max()) + 1)[ids]
    dilute = near == 0
    rows = [{
        'object_id': int(p.label),
        'y_px': float(p.centroid[0]), 'x_px': float(p.centroid[1]),
        'y_um': float(p.centroid[0]) * mpp, 'x_um': float(p.centroid[1]) * mpp,
        'area_px': float(p.area), 'area_um2': float(p.area) * mpp * mpp,
        'equiv_radius_um': float(np.sqrt(p.area / np.pi)) * mpp,
        'eccentricity': float(p.eccentricity), 'solidity': float(p.solidity),
        'ring_px': int(n),
    } for p, n in zip(props, ring_n)]
    for name, img in frame_images.items():
        img = np.asarray(img, dtype=np.float64)
        col = channel_column(name)
        mean = np.asarray(ndi.mean(img, lab, ids))
        bg = np.asarray(ndi.median(img, ring_lab, ids)) if (ring_n > 0).any() else np.full(len(ids), np.nan)
        glob = float(np.median(img[dilute])) if dilute.any() else np.nan
        bg = np.where(ring_n >= min_ring_px, bg, glob)
        for r, m, b in zip(rows, mean, bg):
            r[f'mean_{col}'] = float(m)
            r[f'bg_{col}'] = float(b)
            r[f'net_{col}'] = float(m - b)
        rows[0].setdefault('_dilute', {})[col] = glob
    return rows


def measure_droplets_multichannel(label_stack, channels: Dict[str, np.ndarray], microns_per_pixel: float = 1.0,
                                  gap_px: int = BACKGROUND_GAP_PX, ring_px: int = BACKGROUND_RING_PX,
                                  min_ring_px: int = MIN_RING_PX,
                                  progress_callback: Optional[Callable[[int, int], None]] = None):
    """Per (frame, droplet) geometry plus, for EVERY channel in ``channels`` ({name: (T, Y, X) stack}), the mean,
    the local dilute-phase background and ``net = mean - background``.

    Returns ``(per_object_df, dilute_df)``. ``per_object_df`` has the columns ``link_condensates`` needs
    (frame, object_id, y_um, x_um, area_um2, equiv_radius_um, ...) plus ``mean_<ch>``, ``bg_<ch>``, ``net_<ch>``
    per channel (``<ch>`` = :func:`channel_column` of the name). ``mean_intensity`` / ``integrated_intensity``
    are filled from the FIRST channel for the existing trajectory builders. ``dilute_df`` is the per-frame
    global dilute-phase median of each channel.
    """
    from concurrent.futures import ThreadPoolExecutor

    mpp = float(microns_per_pixel) if microns_per_pixel else 1.0
    T = int(label_stack.shape[0])
    rows, dilute = [], []
    first = channel_column(next(iter(channels))) if channels else None

    def _one(lab, imgs):
        return _frame_measurements(lab, imgs, mpp, gap_px, ring_px, min_ring_px)

    # The COMPUTE runs in threads (numpy/scipy release the GIL), but every frame is READ here, on this one
    # thread: a lazy IMS layer reads through imaris_ims_file_reader, which is not thread-safe -- concurrent reads
    # kill the process outright, with no traceback. A bounded window keeps the movie streamed.
    n_workers = _n_workers()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = []
        for t in range(T):
            lab = np.asarray(label_stack[t]).astype(np.int32)
            imgs = {n: np.asarray(s[t]) for n, s in channels.items()}
            futures.append(ex.submit(_one, lab, imgs))
            while sum(not f.done() for f in futures) > 2 * n_workers:
                futures[next(i for i, f in enumerate(futures) if not f.done())].result()
        per_frame = [f.result() for f in futures]
    for t, frame_rows in enumerate(per_frame):
        glob = frame_rows[0].pop('_dilute', {}) if frame_rows else {}
        dilute.append({'frame': t, **{f'dilute_{c}': v for c, v in glob.items()}})
        for r in frame_rows:
            r['frame'] = t
            if first is not None:
                r['mean_intensity'] = r[f'mean_{first}']
                r['integrated_intensity'] = r[f'mean_{first}'] * r['area_px']
        rows.extend(frame_rows)
        if progress_callback is not None:
            progress_callback(t + 1, T)
    df = pd.DataFrame(rows)
    if len(df):
        lead = ['frame', 'object_id']
        df = df[lead + [c for c in df.columns if c not in lead]]
    return df, pd.DataFrame(dilute)


def _n_workers():
    import os
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _crossing_time(t, frac, level):
    """First time the normalised trace reaches ``level``, linearly interpolated between frames; NaN if never."""
    above = np.nonzero(frac >= level)[0]
    if not len(above):
        return np.nan
    i = int(above[0])
    if i == 0:
        return float(t[0])
    f0, f1 = frac[i - 1], frac[i]
    return float(t[i - 1] + (level - f0) / (f1 - f0) * (t[i] - t[i - 1])) if f1 != f0 else float(t[i])


def _track_kinetics(t, y, smooth_frames, response_k):
    # The smoothing window is capped at a third of the trace: on a short trace (a decimated movie, every n-th
    # frame) a 5-frame median flattened a real change below the response gate -- an 8-frame dissolution from
    # 0.31 to 0 read as amplitude -0.16, "not responded" (real-data harness, 1.7.005). Unchanged for >= 15 frames.
    smooth_frames = max(1, min(int(smooth_frames), len(y) // 3))
    s = pd.Series(y).rolling(smooth_frames, center=True, min_periods=1).median().to_numpy()
    k = max(3, len(s) // 10)
    baseline, plateau = float(np.median(s[:k])), float(np.median(s[-k:]))
    amp = plateau - baseline
    resid = y - s
    noise = float(1.4826 * np.median(np.abs(resid - np.median(resid)))) or float(np.std(resid)) or 1e-9
    out = {'baseline': baseline, 'plateau': plateau, 'amplitude': amp, 'noise': noise,
           'snr': abs(amp) / noise, 'responded': bool(abs(amp) >= response_k * noise),
           'direction': 'rise' if amp > 0 else 'fall'}
    frac = (s - baseline) / amp if amp != 0 else np.zeros_like(s)
    for name, level in (('t_onset_s', 0.1), ('t_half_s', 0.5), ('t_90_s', 0.9)):
        out[name] = _crossing_time(t, frac, level) if out['responded'] else np.nan
    rate = np.gradient(s, t) if len(s) > 1 else np.array([np.nan])
    out['max_rate_per_s'] = float(np.nanmax(rate) if amp > 0 else np.nanmin(rate)) if out['responded'] else np.nan
    out['fraction_remaining'] = float(plateau / baseline) if baseline > 0 else np.nan
    return out


def droplet_kinetics(per_object_df: pd.DataFrame, channel: str, frame_interval_s: float = 1.0,
                     smooth_frames: int = 5, min_frames: int = 10, response_k: float = RESPONSE_K) -> pd.DataFrame:
    """Model-free kinetics of one channel's background-subtracted signal for every TRACK (needs ``track_id``).

    One row per track with at least ``min_frames`` frames: ``baseline`` / ``plateau`` (median of the first / last
    10 % of the smoothed trace), ``amplitude``, ``noise`` (robust frame-to-frame scatter), ``snr``, ``responded``
    (amplitude >= ``response_k`` x noise), ``direction`` (rise / fall), the times the trace first reaches 10 / 50 /
    90 % of its change (``t_onset_s`` / ``t_half_s`` / ``t_90_s``, from the start of the MOVIE), the maximum rate
    (``max_rate_per_s``, signal units per second), ``fraction_remaining`` (plateau / baseline -- the bleach
    fraction for a decaying channel), plus the track's first / last frame, length and mean area. Crossing times
    are NaN for a droplet that did not respond.
    """
    col = f'net_{channel_column(channel)}'
    if per_object_df is None or 'track_id' not in per_object_df or col not in per_object_df:
        return pd.DataFrame()
    dt = float(frame_interval_s) if frame_interval_s else 1.0
    rows = []
    for tid, g in per_object_df[per_object_df['track_id'] >= 0].groupby('track_id'):
        g = g.sort_values('frame')
        if len(g) < min_frames:
            continue
        t = g['frame'].to_numpy(dtype=float) * dt
        rec = {'track_id': int(tid), 'first_frame': int(g['frame'].iloc[0]), 'last_frame': int(g['frame'].iloc[-1]),
               'n_frames': int(len(g)), 'mean_area_um2': float(g['area_um2'].mean())}
        rec.update(_track_kinetics(t, g[col].to_numpy(dtype=float), smooth_frames, response_k))
        rows.append(rec)
    return pd.DataFrame(rows)


def kinetics_heterogeneity(kin_df: pd.DataFrame) -> dict:
    """Field-level summary of the per-droplet kinetics: how many droplets responded, and how SPREAD their timing
    is (median / IQR / coefficient of variation of the onset and half times) -- the heterogeneity as numbers."""
    out = {'n_tracks': int(len(kin_df))}
    if not len(kin_df):
        return out
    resp = kin_df[kin_df['responded']]
    out['n_responded'] = int(len(resp))
    out['fraction_responded'] = float(len(resp) / len(kin_df))
    for c in ('t_onset_s', 't_half_s', 't_90_s', 'amplitude', 'max_rate_per_s'):
        v = resp[c].dropna().to_numpy(dtype=float) if c in resp else np.array([])
        if len(v):
            q1, med, q3 = np.percentile(v, (25, 50, 75))
            out[f'{c}_median'] = float(med)
            out[f'{c}_iqr'] = float(q3 - q1)
            out[f'{c}_cv'] = float(np.std(v) / abs(np.mean(v))) if np.mean(v) else np.nan
    # Does timing depend on droplet SIZE? (On the HHrz movie small droplets react first.) Rank correlation, so a
    # few very large droplets cannot drive it.
    both = resp[['t_half_s', 'mean_area_um2']].dropna()
    if len(both) >= 10:
        out['spearman_t_half_vs_area'] = float(both['t_half_s'].rank().corr(both['mean_area_um2'].rank()))
    return out


def heterogeneity_over_time(per_object_df: pd.DataFrame, kin_df: pd.DataFrame, channel: str) -> pd.DataFrame:
    """The heterogeneity as a CURVE, one row per frame, over the tracked droplets that have kinetics:

    * ``net_median`` / ``net_iqr_over_median`` -- the spread of the background-subtracted signal itself;
    * ``progress_median`` / ``progress_iqr`` -- each droplet's signal as a fraction of its OWN change
      ((net - baseline) / amplitude), so droplets of different brightness are comparable: an IQR near 0 means
      the field is in step, a large IQR means some droplets are far ahead of others;
    * ``fraction_activated`` -- the share past half their own change.

    On the HHrz movie the progress IQR is ~0.1 at the start, peaks at ~0.6 at 20-30 min and collapses to 0.04 by
    two hours: heterogeneous early, homogeneous late.
    """
    col = f'net_{channel_column(channel)}'
    cols = ['frame', 'n_tracked', 'net_median', 'net_iqr_over_median', 'progress_median', 'progress_iqr',
            'fraction_activated']
    if not len(kin_df) or col not in per_object_df:
        return pd.DataFrame(columns=cols)
    k = kin_df.set_index('track_id')
    d = per_object_df[per_object_df['track_id'].isin(k.index)]
    amp = d['track_id'].map(k['amplitude'])
    progress = (d[col] - d['track_id'].map(k['baseline'])) / amp.where(amp != 0)
    responded = d['track_id'].map(k['responded']).astype(bool)
    rows = []
    for f, idx in d.groupby('frame').groups.items():
        net = d.loc[idx, col].to_numpy(dtype=float)
        pr = progress.loc[idx].dropna().to_numpy(dtype=float)
        q = np.percentile(net, (25, 50, 75))
        rows.append({'frame': int(f), 'n_tracked': int(len(idx)), 'net_median': float(q[1]),
                     'net_iqr_over_median': float((q[2] - q[0]) / abs(q[1])) if q[1] else np.nan,
                     'progress_median': float(np.median(pr)) if len(pr) else np.nan,
                     'progress_iqr': float(np.subtract(*np.percentile(pr, (75, 25)))) if len(pr) else np.nan,
                     'fraction_activated': float(((progress.loc[idx] >= 0.5) & responded.loc[idx]).mean())})
    return pd.DataFrame(rows, columns=cols).sort_values('frame').reset_index(drop=True)


def brush_ready_kinetics(linked: pd.DataFrame, kin_df: pd.DataFrame, channel: str, frame_interval_s: float = 1.0,
                         microns_per_pixel: float = 1.0, source_path=None):
    """The kinetics as linked tables (1.7.004): ``droplets`` (one row per tracked droplet with kinetics: the
    `droplet_kinetics` row + its first-frame position and bbox) and ``droplet_frames`` (one row per droplet
    per frame: ``time_s`` and every measured column -- the traces). Both carry the droplet's entity id
    (``droplet_label`` = track_id + 1, the value of the tracked-labels layer), so selecting a droplet anywhere
    selects its trace too."""
    from pycat.utils.entity_ref import stamp_entity_ids
    if kin_df is None or not len(kin_df) or linked is None or 'track_id' not in linked:
        return {'droplets': pd.DataFrame(), 'droplet_frames': pd.DataFrame()}
    frames = linked[linked['track_id'].isin(kin_df['track_id'])].sort_values(['track_id', 'frame']).copy()
    frames['time_s'] = frames['frame'] * float(frame_interval_s or 1.0)
    frames['droplet_label'] = frames['track_id'].astype(int) + 1
    first = frames.groupby('track_id').first()
    r_px = first['equiv_radius_um'] / float(microns_per_pixel or 1.0)
    pos = pd.DataFrame({'track_id': first.index.astype(int), 'y_px': first['y_px'].to_numpy(),
                        'x_px': first['x_px'].to_numpy(),
                        'bbox_y0': np.floor(first['y_px'] - r_px).clip(lower=0).astype(int).to_numpy(),
                        'bbox_x0': np.floor(first['x_px'] - r_px).clip(lower=0).astype(int).to_numpy(),
                        'bbox_y1': np.ceil(first['y_px'] + r_px + 1).astype(int).to_numpy(),
                        'bbox_x1': np.ceil(first['x_px'] + r_px + 1).astype(int).to_numpy()})
    droplets = kin_df.merge(pos, on='track_id', how='left')
    droplets['droplet_label'] = droplets['track_id'].astype(int) + 1
    droplets['frame'] = droplets['first_frame']
    droplets['channel'] = channel
    for df in (droplets, frames):
        stamp_entity_ids(df, entity_type='droplet_track', label_column='droplet_label', parent_column='_none_',
                         frame=None, source_path=source_path, operation_id='tsivf_kinetics')
    return {'droplets': droplets, 'droplet_frames': frames}


def short_channel_name(layer_name):
    """'sub-HHrz-Trial-4 C0-488 Stack' -> 'C0-488': the channel part of a PyCAT stack layer name, so result
    columns read ``net_C0_488`` instead of ``net_sub_HHrz_Trial_4_C0_488_Stack``. The full name if none.
    (Here, not in the panel, so batch replay can name channels the way the panel does.)"""
    m = re.search(r'(?:^|\s)(C\d+-[^\s\[\]]+)', str(layer_name))
    return m.group(1) if m else str(layer_name)
