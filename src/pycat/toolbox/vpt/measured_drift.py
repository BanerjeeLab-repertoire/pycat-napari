"""VPT **measured drift** (1.6.476): bead-anchored, tested, and applied only when real.

``drift_correct_com`` CHAINS the mean frame-to-frame step of whatever tracks span each pair of frames, and
it was applied unconditionally, writing the corrected positions over the detected ones. This module
measures drift DIRECTLY instead: every bead has one home position and every frame one shared offset, and
both are solved jointly (a robust least-squares fit of ``position = home + drift``), so a frame's drift is
anchored to every bead seen in it rather than to a running sum. On the 1000-frame G3BP1 bead movie it
agrees with whole-frame image registration to 0.2 px RMS.

Whether the drift is REAL is then tested, not assumed. The beads are split into two independent random
halves and the drift is measured from each: real drift is common to both, so their disagreement is the
measurement noise. The drift must also be larger than the localization precision, or correcting it
cannot change the MSD.

The measurement never touches the detected positions. ``attach_drift`` stores it BESIDE them, and
``drift_corrected_for_msd`` builds a separate analysis copy for the MSD / viscosity when the user applies it.
The tracks that are stored, exported and drawn keep the bead's intensity centroid.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Tracks shorter than this do not anchor the drift (a home position needs enough frames to be stable).
DRIFT_MIN_TRACK_FRAMES = 50
#: The two independent halves of the beads must agree on the drift this many times above their disagreement.
DRIFT_MIN_AGREEMENT = 3.0
#: ... and the drift must exceed this many localization precisions. Below ~2 sigma its contribution to the
#: MSD (P^2) is under the MSD's own static-noise floor (4 sigma^2 in 2-D), so correcting it changes nothing.
DRIFT_MIN_PRECISIONS = 2.0


def _solve_drift(frames, track_ids, pos_px, n_frames, robust_iters=5):
    """Robust (Huber IRLS) least-squares fit of ``pos = home[track] + drift[frame]`` for one axis.
    Frame 0's drift is fixed at 0 (the gauge). Returns the drift per frame (NaN where no bead anchors it)."""
    import scipy.sparse as sp
    from scipy.sparse.linalg import lsqr

    tids, ti = np.unique(track_ids, return_inverse=True)
    n, n_tracks = len(pos_px), len(tids)
    rows = np.arange(n)
    later = frames > 0
    A = sp.hstack([
        sp.csr_matrix((np.ones(n), (rows, ti)), shape=(n, n_tracks)),
        sp.csr_matrix((np.ones(int(later.sum())), (rows[later], frames[later] - 1)),
                      shape=(n, max(1, n_frames - 1))),
    ]).tocsr()
    w = np.ones(n)
    sol = np.zeros(A.shape[1])
    for _ in range(robust_iters):
        sw = np.sqrt(w)
        sol = lsqr(sp.diags(sw) @ A, sw * pos_px, atol=1e-10, btol=1e-10, iter_lim=5000)[0]
        res = pos_px - A @ sol
        scale = 1.4826 * np.median(np.abs(res)) + 1e-12
        u = np.abs(res) / (1.345 * scale)
        w = np.where(u <= 1.0, 1.0, 1.0 / np.maximum(u, 1e-12))
    drift = np.concatenate([[0.0], sol[n_tracks:]])[:n_frames]
    anchored = np.bincount(frames, minlength=n_frames) > 0
    drift[~anchored] = np.nan
    return drift


def _fill_gaps(curve):
    """Linear interpolation over frames no bead anchored (edges held flat)."""
    out = curve.copy()
    ok = np.isfinite(out)
    if not ok.any():
        return np.zeros_like(out)
    idx = np.arange(len(out))
    out[~ok] = np.interp(idx[~ok], idx[ok], out[ok])
    return out


def _localization_precision_px(ref):
    """Static localization precision from the step autocovariance: for diffusion plus white position
    noise, <step_i * step_{i+1}> = -sigma^2 per axis (the diffusive part is uncorrelated)."""
    g = ref.sort_values(['track_id', 'frame'])
    contiguous = g.groupby('track_id')['frame'].diff() == 1
    covs = []
    for col in ('y', 'x'):
        step = g.groupby('track_id')[col].diff().where(contiguous)
        nxt = step.groupby(g['track_id']).shift(-1)
        both = step.notna() & nxt.notna()
        if both.sum() > 10:
            covs.append(-float(np.mean(step[both] * nxt[both])))
    return float(np.sqrt(max(0.0, float(np.mean(covs))))) if covs else 0.0


def _reference_tracks(tracks_df, mpx, min_track_frames):
    """The long tracks that anchor the drift, in pixels, frames re-based to 0. Always the detected
    centroid: if a legacy correction ran, the ``*_raw`` columns hold it."""
    ycol = 'y_um_raw' if 'y_um_raw' in tracks_df else 'y_um'
    xcol = 'x_um_raw' if 'x_um_raw' in tracks_df else 'x_um'
    tr = tracks_df[tracks_df['track_id'] >= 0]
    lens = tr.groupby('track_id')['frame'].transform('size')
    min_len = int(min_track_frames)
    ref = tr[lens >= min_len]
    while ref['track_id'].nunique() < 20 and min_len > 5:      # short movies: relax the anchor length
        min_len = max(5, min_len // 2)
        ref = tr[lens >= min_len]
    f0 = int(tracks_df['frame'].min())
    return pd.DataFrame({'track_id': ref['track_id'].to_numpy(),
                         'frame': ref['frame'].to_numpy().astype(int) - f0,
                         'y': ref[ycol].to_numpy(float) / mpx, 'x': ref[xcol].to_numpy(float) / mpx})


def _verdict(real, significant, peak, mpx, agreement, precision):
    if real:
        return (f"Real drift: up to {peak:.2f} px ({peak * mpx * 1000:.0f} nm); two independent halves of the "
                f"beads agree {agreement:.0f}× above their noise.")
    if not significant:
        return (f"No real drift: the {peak:.2f} px peak is not consistent between independent halves of the "
                f"beads (agreement {agreement:.1f}×, needs {DRIFT_MIN_AGREEMENT:g}×).")
    return (f"Drift of {peak:.2f} px is consistent but below {DRIFT_MIN_PRECISIONS:g}× the localization "
            f"precision ({precision:.2f} px) — too small to change the MSD.")


def measure_drift(tracks_df: pd.DataFrame, microns_per_pixel: float,
                  min_track_frames: int = DRIFT_MIN_TRACK_FRAMES, seed: int = 0) -> dict:
    """Measure stage/sample drift directly from the beads, and say whether it is real.

    Returns a dict:
      ``frames``, ``dy_px``, ``dx_px`` — the drift curve (first frame = 0), one value per frame;
      ``peak_px`` / ``peak_um`` — the largest excursion; ``net_px`` — (dy, dx) at the last frame;
      ``noise_px`` — RMS disagreement of two independent halves of the beads (the measurement noise);
      ``agreement`` — RMS drift / noise; ``precision_px`` — localization precision;
      ``n_ref_tracks``; ``real`` (bool) and ``verdict`` (a sentence for the panel).
    """
    empty = dict(frames=np.array([], int), dy_px=np.array([]), dx_px=np.array([]), peak_px=0.0,
                 peak_um=0.0, net_px=(0.0, 0.0), noise_px=float('nan'), agreement=0.0, precision_px=0.0,
                 n_ref_tracks=0, real=False)
    if tracks_df is None or tracks_df.empty or 'track_id' not in tracks_df:
        return dict(empty, verdict="No tracks — drift not measured.")
    mpx = float(microns_per_pixel) if microns_per_pixel else 1.0
    f0 = int(tracks_df['frame'].min())
    n_frames = int(tracks_df['frame'].max()) - f0 + 1
    ref = _reference_tracks(tracks_df, mpx, min_track_frames)
    if ref['track_id'].nunique() < 4:
        return dict(empty, frames=np.arange(f0, f0 + n_frames), dy_px=np.zeros(n_frames),
                    dx_px=np.zeros(n_frames), verdict="Too few long tracks to measure drift — not applied.")

    def _curve(sub):
        return np.column_stack([
            _solve_drift(sub['frame'].to_numpy(), sub['track_id'].to_numpy(), sub[c].to_numpy(), n_frames)
            for c in ('y', 'x')])

    full = _curve(ref)
    ids = ref['track_id'].unique()
    half = set(np.random.default_rng(seed).permutation(ids)[: len(ids) // 2].tolist())
    in_a = ref['track_id'].isin(half)
    a, b = _curve(ref[in_a]), _curve(ref[~in_a])
    both = np.isfinite(a).all(1) & np.isfinite(b).all(1) & np.isfinite(full).all(1)
    full = np.column_stack([_fill_gaps(full[:, 0]), _fill_gaps(full[:, 1])])
    mag = np.hypot(full[:, 0], full[:, 1])
    noise = float(np.sqrt(np.mean(np.sum(((a - b)[both] / 2.0) ** 2, axis=1)))) if both.any() else float('nan')
    rms = float(np.sqrt(np.mean(mag[both] ** 2))) if both.any() else 0.0
    if np.isfinite(noise) and noise > 0:
        agreement = rms / noise
    else:
        agreement = float('inf') if rms > 0 else 0.0
    precision = _localization_precision_px(ref)
    peak = float(mag.max()) if len(mag) else 0.0
    significant = agreement >= DRIFT_MIN_AGREEMENT
    real = bool(significant and peak >= DRIFT_MIN_PRECISIONS * precision)
    return dict(frames=np.arange(f0, f0 + n_frames), dy_px=full[:, 0], dx_px=full[:, 1], peak_px=peak,
                peak_um=peak * mpx, net_px=(float(full[-1, 0]), float(full[-1, 1])), noise_px=noise,
                agreement=float(agreement), precision_px=precision,
                n_ref_tracks=int(ref['track_id'].nunique()), real=real,
                verdict=_verdict(real, significant, peak, mpx, agreement, precision))


def attach_drift(tracks_df: pd.DataFrame, drift: dict, microns_per_pixel: float) -> pd.DataFrame:
    """Store the measured drift BESIDE the positions (``drift_y_um``/``drift_x_um`` per row). The detected
    positions are not changed: they stay the bead's intensity centroid."""
    out = tracks_df.copy()
    mpx = float(microns_per_pixel) if microns_per_pixel else 1.0
    frames = np.asarray(drift.get('frames', []), int)
    if not len(frames):
        out['drift_y_um'] = 0.0
        out['drift_x_um'] = 0.0
        return out
    pos = np.clip(out['frame'].to_numpy().astype(int) - frames[0], 0, len(frames) - 1)
    out['drift_y_um'] = np.asarray(drift['dy_px'])[pos] * mpx
    out['drift_x_um'] = np.asarray(drift['dx_px'])[pos] * mpx
    out.attrs['drift'] = {k: v for k, v in drift.items() if k not in ('frames', 'dy_px', 'dx_px')}
    return out


def drift_corrected_for_msd(tracks_df: pd.DataFrame) -> pd.DataFrame:
    """The ANALYSIS copy of the tracks: positions minus the attached drift, for the MSD / viscosity only.
    The centroids are kept in ``y_um_raw``/``x_um_raw``, so anything drawn from this copy still lands on the
    beads. Never stored back as the tracks."""
    if tracks_df is None or 'drift_y_um' not in tracks_df:
        return tracks_df
    out = tracks_df.copy()
    if 'y_um_raw' not in out:
        out['y_um_raw'] = out['y_um']
        out['x_um_raw'] = out['x_um']
    out['y_um'] = out['y_um_raw'] - out['drift_y_um']
    out['x_um'] = out['x_um_raw'] - out['drift_x_um']
    out.attrs['drift_mode'] = 'measured'
    return out


def centroid_offsets_px(image, yx_px, radius=3):
    """For each drawn position, the distance (px) to the local intensity centroid of ``image`` around it
    (background-subtracted, one iteration re-centred). The display guarantee is checked with this: a
    marker drawn off its bead shows up as a large offset."""
    img = np.asarray(image, float)
    H, W = img.shape[-2:]
    out = np.full(len(yx_px), np.nan)
    r = int(radius)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = (yy ** 2 + xx ** 2) <= r * r
    for i, (y, x) in enumerate(np.asarray(yx_px, float)):
        cy, cx = y, x
        for _ in range(2):
            iy, ix = int(round(cy)), int(round(cx))
            if iy - r < 0 or ix - r < 0 or iy + r >= H or ix + r >= W:
                break
            win = img[iy - r:iy + r + 1, ix - r:ix + r + 1]
            wv = np.clip(win - np.median(win), 0, None) * disk
            s = wv.sum()
            if s <= 0:
                break
            cy, cx = iy + (wv * yy).sum() / s, ix + (wv * xx).sum() / s
        else:
            out[i] = float(np.hypot(cy - y, cx - x))
    return out


def measure_and_apply_if_real(tracks_df: pd.DataFrame, microns_per_pixel: float):
    """Headless/batch: measure the drift, attach it beside the centroids, and build the MSD copy with it
    subtracted only when it is real. Returns ``(tracks, msd_tracks, drift)``."""
    drift = measure_drift(tracks_df, microns_per_pixel)
    tracks = attach_drift(tracks_df, drift, microns_per_pixel)
    return tracks, (drift_corrected_for_msd(tracks) if drift['real'] else tracks), drift
