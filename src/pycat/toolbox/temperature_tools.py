"""
PyCAT Temperature-Dependent Condensate Tools
==============================================
Synchronise a MicroManager time-lapse (OME-TIFF) with a temperature log CSV,
annotate temperatures onto the movie, and detect the phase-separation
(cloud, T_phase) and dissolution (clear, T_clear) transitions from an
entropy-based turbidity curve — with correction for the focal drift that
otherwise corrupts the entropy signal.

Concepts (from the manual workflow)
-----------------------------------
1. Time/temperature sync: each TIFF page carries MicroManager metadata with a
   'ReceivedTime' wall-clock stamp and an 'ElapsedTime-ms'. The wall-clock
   stamp is matched (to the second) against the temperature CSV's 'Date/Time'
   column, and the temperature is read from the 'AI0 (°C)' column.
2. Turbidity via entropy: per-frame histogram entropy −Σ p·log2(p) rises as
   condensates scatter light and the intensity histogram broadens. Plotting
   entropy vs. temperature and splitting into heating/cooling branches (at the
   max-temperature frame) gives a hysteresis loop; the transition midpoints
   are T_phase (heating) and T_clear (cooling).
3. Focal-drift correction: defocus also broadens/narrows the histogram, so raw
   entropy conflates phase separation with focus changes. We measure a focus
   score per frame (Brenner/Tenengrad) and regress it out of the entropy
   signal, so the corrected entropy reflects genuine turbidity change.

Author
------
    Gable Wadsworth, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from napari.utils.notifications import show_info as napari_show_info
from napari.utils.notifications import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# 1. CSV auto-location by TIFF date  (batch helpers from the manual script)
# ---------------------------------------------------------------------------

def to_integer(dt: datetime) -> int:
    """Date → YYYYMMDD integer (10000·year + 100·month + day)."""
    return 10000 * dt.year + 100 * dt.month + dt.day


def get_filenames_by_type(root_folder: str, file_extension: str) -> list:
    """Recursively find files with a given extension under root_folder."""
    ext = file_extension if file_extension.startswith('.') else '.' + file_extension
    found = []
    for dirpath, _dirs, filenames in os.walk(root_folder):
        for fn in filenames:
            if fn.lower().endswith(ext.lower()):
                found.append(os.path.join(dirpath, fn))
    return found


def find_subfolders_with_prefix(parent_dir: str, prefix: str) -> list:
    """Immediate subfolders of parent_dir whose name starts with prefix."""
    out = []
    try:
        for item in os.listdir(parent_dir):
            p = os.path.join(parent_dir, item)
            if os.path.isdir(p) and item.startswith(prefix):
                out.append(item)
    except Exception:
        pass
    return out


def locate_temperature_csv(tiff_path: str, temperature_root: str) -> Optional[str]:
    """
    Find the temperature CSV for a TIFF by matching the TIFF's modification
    date (YYYYMMDD) to a subfolder of `temperature_root` whose name starts
    with that date, then taking the first CSV inside it.

    Returns the CSV path, or None if nothing matches.
    """
    try:
        t = datetime.fromtimestamp(os.path.getmtime(tiff_path))
        prefix = str(to_integer(t))
        matches = find_subfolders_with_prefix(temperature_root, prefix)
        if not matches:
            return None
        csvs = get_filenames_by_type(os.path.join(temperature_root, matches[0]), 'csv')
        return csvs[0] if csvs else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2. TIFF metadata + temperature synchronisation
# ---------------------------------------------------------------------------

def read_micromanager_times(tiff_path: str) -> dict:
    """
    Read per-frame timing from a MicroManager OME-TIFF.

    Returns
    -------
    dict with:
        elapsed_ms   : (N,) ElapsedTime-ms per frame
        received     : list of 'ReceivedTime'[:19] wall-clock strings
        n_frames     : number of pages
    """
    import tifffile
    elapsed, received = [], []
    with tifffile.TiffFile(tiff_path) as tif:
        n = len(tif.pages)
        for i in range(n):
            tags = {}
            for tag in tif.pages[i].tags.values():
                tags[tag.name] = tag.value
            mm = tags.get('MicroManagerMetadata', {})
            # ElapsedTime-ms lives in the MicroManagerMetadata dict
            et = mm.get('ElapsedTime-ms', np.nan) if isinstance(mm, dict) else np.nan
            elapsed.append(float(et) if et is not None else np.nan)
            rt = mm.get('ReceivedTime', '') if isinstance(mm, dict) else ''
            received.append(rt[:19] if isinstance(rt, str) else '')
    return dict(elapsed_ms=np.array(elapsed), received=received, n_frames=n)


def sync_temperatures(
    received_times: list,
    csv_path: str,
    temp_column: str = 'AI0 (°C)',
    datetime_column: str = 'Date/Time',
    csv_header: int = 6,
) -> np.ndarray:
    """
    Match per-frame wall-clock times to a temperature CSV and return the
    per-frame temperature array.

    The MicroManager 'ReceivedTime' (parsed as %Y-%m-%d %H:%M:%S) is matched
    to the CSV 'Date/Time' column (parsed as %m/%d/%Y %I:%M:%S.%f %p) at
    one-second resolution — the format used by the lab's DAQ export.

    Returns
    -------
    (N,) array of temperatures (°C); NaN where no match was found.
    """
    tempvec = pd.read_csv(csv_path, header=csv_header)
    if temp_column not in tempvec.columns:
        # Try to find a column that looks like the temperature channel
        cand = [c for c in tempvec.columns if '°C' in c or 'AI0' in c or 'Temp' in c]
        if cand:
            temp_column = cand[0]
        else:
            raise ValueError(
                f"Temperature column '{temp_column}' not found. "
                f"Columns: {list(tempvec.columns)}")

    # Pre-parse all CSV datetimes once (to the second) for a fast lookup
    csv_dt = {}
    for m, tstr in enumerate(tempvec[datetime_column].astype(str)):
        try:
            d = datetime.strptime(tstr, '%m/%d/%Y %I:%M:%S.%f %p')
            key = d.strftime('%Y-%m-%d %H:%M:%S')
            if key not in csv_dt:          # first occurrence, matches manual break
                csv_dt[key] = m
        except (ValueError, TypeError):
            continue

    temps = np.full(len(received_times), np.nan)
    for i, rt in enumerate(received_times):
        if not rt:
            continue
        try:
            key = datetime.strptime(rt, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        idx = csv_dt.get(key)
        if idx is not None:
            temps[i] = float(tempvec[temp_column].iloc[idx])
    return temps


def elapsed_to_seconds(elapsed_ms: np.ndarray) -> np.ndarray:
    """Frame elapsed times (ms) → seconds relative to the first frame."""
    e = np.asarray(elapsed_ms, dtype=float)
    return np.round((e - e[0]) / 1000.0)


# ---------------------------------------------------------------------------
# 3. Focus metric (for drift correction)
# ---------------------------------------------------------------------------

def focus_scores(stack: np.ndarray) -> np.ndarray:
    """
    Per-frame focus score (normalised Brenner gradient). Higher = sharper.

    Reuses the brightfield Brenner metric — robust for the transmitted-light
    condensate movies these experiments use.
    """
    from pycat.toolbox.brightfield_tools import bf_focus_metric
    stack = np.asarray(stack)
    scores = np.array([bf_focus_metric(stack[i]) for i in range(stack.shape[0])])
    med = np.median(scores)
    return scores / max(med, 1e-12)


# ---------------------------------------------------------------------------
# 4. Entropy turbidity curve + focal-drift correction
# ---------------------------------------------------------------------------

def frame_entropy(frame: np.ndarray, bins: int = 256) -> float:
    """
    Shannon entropy of a frame's intensity histogram: −Σ p·log2(p).

    The image is normalised to [0,1] before histogramming so entropy is
    comparable across frames with different absolute intensity.
    """
    f = np.asarray(frame, dtype=np.float32)
    mn, mx = float(f.min()), float(f.max())
    if mx <= mn:
        return 0.0
    fn = (f - mn) / (mx - mn)
    hist, _ = np.histogram(fn, bins=bins, range=(0, 1))
    p = hist.astype(float)
    p = p[p > 0]
    p /= p.sum()
    return float(-np.sum(p * np.log2(p)))


def guess_clear_frame(stack: np.ndarray, flatness_cov_threshold: float = 0.15):
    """
    Propose the clearest reference frame and judge whether it is actually clear.

    Selection uses the per-frame COEFFICIENT OF VARIATION (std / mean), which is
    a direct, scale-independent measure of spatial flatness: a uniform/clear
    field has low CoV, while condensates create bright structure that raises it.
    The flattest frame (lowest CoV) is the candidate.

    NB: this deliberately does NOT use the normalized histogram entropy from
    ``frame_entropy`` — that normalizes each frame to [0,1] before histogramming,
    which stretches a flat noisy field to look high-entropy and makes a bimodal
    condensate field look low-entropy, i.e. the opposite of what we want here.

    "Flattest available" is not the same as "clear" — some stacks have no clear
    frame at all (condensates throughout). So the candidate is also checked
    against an ABSOLUTE flatness threshold. If it fails, ``is_clear`` is False and
    the caller should warn rather than use it as a background reference. This
    handles UCST and LCST behavior, where the clear frame (if any) may sit at the
    start OR the end of the ramp.

    Returns
    -------
    dict with keys:
        index      : int   — proposed frame index (flattest)
        is_clear   : bool  — passed the absolute flatness test
        cov        : float — coefficient of variation of the candidate frame
        threshold  : float — the CoV threshold used
    """
    stack = np.asarray(stack, dtype=np.float32)
    n = stack.shape[0]
    covs = np.empty(n, dtype=np.float64)
    for i in range(n):
        f = stack[i]
        mean = float(f.mean())
        covs[i] = (float(f.std()) / mean) if mean > 1e-9 else float('inf')
    idx = int(np.argmin(covs))
    cov = float(covs[idx])
    return {
        'index': idx,
        'is_clear': bool(cov <= flatness_cov_threshold),
        'cov': cov,
        'threshold': float(flatness_cov_threshold),
    }


def apply_static_pattern_correction(stack, reference_index=0):
    """
    Remove the static brightfield pattern (dust, scratches, fixed optical
    artifacts) captured in a reference frame, while preserving the gray baseline
    so the result still looks like brightfield rather than going toward black:

        corrected = frame - reference + mean(reference)

    Subtracting the reference cancels the fixed pattern (present in every frame);
    adding back mean(reference) restores the overall gray level. Each frame keeps
    its own noise and any real content.

    The reference frame minus itself would be flat, so it is replaced by the
    average of its already-corrected neighbours — real neighbouring noise and
    content rather than a synthetic fill — so it reads as a normal brightfield
    frame instead of a flat outlier.

    Returns a float32 (T, H, W) stack.
    """
    stack = np.asarray(stack, dtype=np.float32)
    n = stack.shape[0]
    ref = int(np.clip(reference_index, 0, n - 1))
    reference = stack[ref]
    mean_ref = float(reference.mean())

    corrected = (stack - reference + mean_ref).astype(np.float32)

    # The reference frame minus itself would be flat, so rebuild it from its
    # already-corrected neighbours — real neighbouring noise and content rather
    # than a synthetic fill. (Neighbours share the same static pattern, which is
    # removed identically, so this stays gray-preserving.)
    if n > 1:
        if 0 < ref < n - 1:
            corrected[ref] = 0.5 * (corrected[ref - 1] + corrected[ref + 1])
        elif ref == 0:
            corrected[ref] = corrected[1]
        else:  # ref == n - 1
            corrected[ref] = corrected[ref - 1]
    return corrected


def entropy_turbidity_curve(
    stack: np.ndarray,
    temperatures: np.ndarray,
    subtract_first_frame: bool = True,
    correct_focal_drift: bool = True,
    bins: int = 256,
    reference_frame_index: int = 0,
) -> pd.DataFrame:
    """
    Build the entropy-based turbidity curve for a temperature ramp.

    Parameters
    ----------
    stack : (T, H, W) image stack.
    temperatures : (T,) per-frame temperature (°C).
    subtract_first_frame : subtract the (assumed clear) first frame to remove
        static illumination pattern before computing entropy/mean.
    correct_focal_drift : regress the per-frame focus score out of the entropy
        signal. Defocus broadens the histogram just like turbidity, so raw
        entropy conflates phase separation with focus drift; the corrected
        column isolates the turbidity-driven component.
    bins : histogram bins for entropy.

    Returns
    -------
    DataFrame with columns:
        frame, temperature_C, entropy, entropy_corrected, image_mean,
        focus_score
    """
    stack = np.asarray(stack).astype(np.float32)
    n = stack.shape[0]

    if subtract_first_frame:
        ref = int(np.clip(reference_frame_index, 0, n - 1))
        work = apply_static_pattern_correction(stack, ref)
    else:
        work = stack

    ent  = np.array([frame_entropy(work[i], bins) for i in range(n)])
    mean = np.array([float(work[i].mean()) for i in range(n)])
    foc  = focus_scores(stack)

    if subtract_first_frame:
        # The reference frame is a rebuilt (interpolated) frame, so its recomputed
        # entropy can differ slightly from its neighbours. Make it inherit the
        # neighbour average directly so it never shows up as an outlier on the
        # curve or biases the transition detection.
        r = int(np.clip(reference_frame_index, 0, n - 1))
        if 0 < r < n - 1:
            ent[r]  = 0.5 * (ent[r - 1]  + ent[r + 1])
            mean[r] = 0.5 * (mean[r - 1] + mean[r + 1])
            foc[r]  = 0.5 * (foc[r - 1]  + foc[r + 1])
        elif r == 0 and n > 1:
            ent[r], mean[r], foc[r] = ent[1], mean[1], foc[1]
        elif r == n - 1 and n > 1:
            ent[r], mean[r], foc[r] = ent[r - 1], mean[r - 1], foc[r - 1]

    ent_corr = ent.copy()
    if correct_focal_drift and np.isfinite(foc).all() and foc.std() > 0:
        # Regress entropy on focus score, keep the residual + mean level.
        # This removes the component of entropy variation explained by focus
        # changes while preserving the turbidity-driven trend.
        A = np.vstack([foc, np.ones_like(foc)]).T
        try:
            coef, *_ = np.linalg.lstsq(A, ent, rcond=None)
            predicted = A @ coef
            ent_corr = ent - predicted + ent.mean()
        except Exception:
            pass

    return pd.DataFrame({
        'frame':            np.arange(n),
        'temperature_C':    np.asarray(temperatures, dtype=float),
        'entropy':          ent,
        'entropy_corrected': ent_corr,
        'image_mean':       mean,
        'focus_score':      foc,
    })


# ---------------------------------------------------------------------------
# 5. Transition detection (T_phase / T_clear) + hysteresis
# ---------------------------------------------------------------------------

def _sigmoid_midpoint(temp: np.ndarray, signal: np.ndarray,
                      n_bins: int = 30) -> float:
    """
    Estimate a transition temperature as the steepest point of signal vs.
    temperature. Robust and model-free (no assumption about transition shape).

    The signal is first averaged into evenly-spaced temperature bins (so
    repeated/entangled temperatures from a ramp don't break the derivative),
    lightly smoothed, then the temperature of maximum |d signal / d temp| is
    returned. Endpoints are excluded so noise at the branch ends is not
    mistaken for the transition.
    """
    t = np.asarray(temp, dtype=float)
    s = np.asarray(signal, dtype=float)
    good = np.isfinite(t) & np.isfinite(s)
    t, s = t[good], s[good]
    if len(t) < 6 or t.max() <= t.min():
        return np.nan

    # Bin by temperature to get a monotonic, evenly-spaced axis
    edges = np.linspace(t.min(), t.max(), n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    binned = np.full(n_bins, np.nan)
    for b in range(n_bins):
        in_bin = (t >= edges[b]) & (t < edges[b + 1] if b < n_bins - 1 else t <= edges[b + 1])
        if np.any(in_bin):
            binned[b] = np.nanmean(s[in_bin])

    # Interpolate any empty bins
    valid = np.isfinite(binned)
    if valid.sum() < 5:
        return np.nan
    binned = np.interp(centers, centers[valid], binned[valid])

    # Light smoothing
    k = max(3, n_bins // 8)
    if k % 2 == 0:
        k += 1
    kernel = np.ones(k) / k
    s_sm = np.convolve(binned, kernel, mode='same')

    # Derivative, excluding the outer 15% of the range where edge effects and
    # smoothing artifacts dominate
    ds = np.abs(np.gradient(s_sm, centers))
    margin = max(1, int(0.15 * n_bins))
    ds[:margin] = 0
    ds[-margin:] = 0
    if not np.any(ds > 0):
        return np.nan
    return float(centers[np.argmax(ds)])


def _baseline_onset(temp: np.ndarray, signal: np.ndarray,
                    frac: float = 0.12, n_bins: int = 40) -> float:
    """
    Temperature at which the signal departs from (or returns to) its baseline.

    Unlike the steepest-point midpoint, this reports the *onset* of the
    transition — where turbidity first rises above the flat baseline — which is
    where condensates begin to appear (cloud) or finish dissolving (clear).

    The branch is binned by temperature and, scanning from low temperature, the
    first crossing above ``baseline + frac * (peak - baseline)`` is returned
    (linearly interpolated between bins). Works for either branch: sorted by
    increasing temperature, both the heating and cooling branches sit low at low
    T and high at high T, so the low-T crossing is the departure (heating →
    cloud) or the return to baseline (cooling → clear).
    """
    t = np.asarray(temp, dtype=float)
    s = np.asarray(signal, dtype=float)
    good = np.isfinite(t) & np.isfinite(s)
    t, s = t[good], s[good]
    if len(t) < 6 or t.max() <= t.min():
        return np.nan

    edges = np.linspace(t.min(), t.max(), n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    binned = np.full(n_bins, np.nan)
    for b in range(n_bins):
        in_bin = (t >= edges[b]) & (t < edges[b + 1] if b < n_bins - 1 else t <= edges[b + 1])
        if np.any(in_bin):
            binned[b] = np.nanmean(s[in_bin])
    valid = np.isfinite(binned)
    if valid.sum() < 5:
        return np.nan
    binned = np.interp(centers, centers[valid], binned[valid])

    # Light smoothing to suppress single-bin noise
    k = max(3, n_bins // 10)
    if k % 2 == 0:
        k += 1
    binned = np.convolve(binned, np.ones(k) / k, mode='same')

    baseline = np.percentile(binned, 15)
    peak = np.percentile(binned, 95)
    if peak - baseline < 1e-9:
        return np.nan
    thr = baseline + frac * (peak - baseline)

    above = binned >= thr
    if not above.any():
        return np.nan
    idx = int(np.argmax(above))          # first bin above threshold (low-T side)
    if idx == 0:
        return float(centers[0])
    # Linear interpolation of the crossing between idx-1 and idx.
    s0, s1 = binned[idx - 1], binned[idx]
    t0, t1 = centers[idx - 1], centers[idx]
    if s1 == s0:
        return float(t1)
    return float(t0 + (thr - s0) / (s1 - s0) * (t1 - t0))


def detect_transitions(turbidity_df: pd.DataFrame,
                       signal_column: str = 'entropy_corrected',
                       method: str = 'baseline',
                       frac: float = 0.12) -> dict:
    """
    Split the turbidity curve into heating and cooling branches at the
    maximum-temperature frame and estimate the transition temperatures.

    Returns
    -------
    dict with:
        T_phase_C   : phase-separation (cloud) temperature — heating branch
        T_clear_C   : dissolution (clear) temperature — cooling branch
        hysteresis_C: T_phase − T_clear
        loc         : index of the maximum-temperature frame (branch split)
        heating_df, cooling_df : the two branches
    """
    df = turbidity_df.reset_index(drop=True)
    temp = df['temperature_C'].values
    sig = df[signal_column].values
    if not np.isfinite(temp).any():
        return dict(T_phase_C=np.nan, T_clear_C=np.nan, hysteresis_C=np.nan,
                    loc=None, heating_df=df.iloc[:0], cooling_df=df.iloc[:0])

    loc = int(np.nanargmax(temp))
    heating = df.iloc[:loc + 1]
    cooling = df.iloc[loc:]

    _detect = _sigmoid_midpoint if method == 'midpoint' else _baseline_onset
    _kw = {} if method == 'midpoint' else {'frac': frac}
    h_mid = _detect(heating['temperature_C'].values,
                    heating[signal_column].values, **_kw) if len(heating) > 4 else np.nan
    c_mid = _detect(cooling['temperature_C'].values,
                    cooling[signal_column].values, **_kw) if len(cooling) > 4 else np.nan

    def _rises(s):
        """True if the signal is higher at the end of the branch than the start."""
        s = np.asarray(s, dtype=float); s = s[np.isfinite(s)]
        if len(s) < 4:
            return True
        k = max(2, len(s) // 8)
        return float(np.nanmean(s[-k:])) >= float(np.nanmean(s[:k]))

    # Cloud point = where turbidity RISES (condensates appear); clear point =
    # where it FALLS (condensates dissolve). Assign by the heating branch's
    # direction so this works for both LCST (appear on heating) and UCST
    # (appear on cooling) systems, rather than assuming a fixed branch.
    if _rises(heating[signal_column].values):
        T_cloud, cloud_branch = h_mid, 'heating'
        T_clear, clear_branch = c_mid, 'cooling'
    else:
        T_clear, clear_branch = h_mid, 'heating'
        T_cloud, cloud_branch = c_mid, 'cooling'

    hyst = (abs(T_cloud - T_clear)
            if (np.isfinite(T_cloud) and np.isfinite(T_clear)) else np.nan)

    return dict(T_phase_C=T_cloud, T_clear_C=T_clear, hysteresis_C=hyst,
                loc=loc, heating_df=heating, cooling_df=cooling,
                cloud_branch=cloud_branch, clear_branch=clear_branch)


# ---------------------------------------------------------------------------
# 6. Temperature annotation layer + scale bar
# ---------------------------------------------------------------------------

def build_temperature_labels(temperatures: np.ndarray,
                             elapsed_s: np.ndarray) -> list:
    """
    Build per-frame annotation strings, e.g. "34.20 °C  |  0:12:30".
    Returned as a list of strings, one per frame, for a napari text layer.
    """
    labels = []
    for i in range(len(temperatures)):
        t = temperatures[i]
        secs = int(elapsed_s[i]) if np.isfinite(elapsed_s[i]) else 0
        tstr = str(timedelta(seconds=secs))
        tc = f"{t:.2f} °C" if np.isfinite(t) else "-- °C"
        labels.append(f"{tc}  |  {tstr}")
    return labels

# ---------------------------------------------------------------------------
# 7. Batch processing (folder of TIFFs vs. temperature-files parent folder)
# ---------------------------------------------------------------------------

def run_temperature_batch(
    tiff_root: str,
    temperature_root: str,
    subtract_first_frame: bool = True,
    correct_focal_drift: bool = True,
    temp_column: str = 'AI0 (°C)',
    csv_header: int = 6,
    progress_callback=None,
    export_mp4: bool = False,
    fps: int = 30,
    pixel_um: float = 1.0,
    scalebar_um: float = 10.0,
    export_corrected: bool = False,
) -> pd.DataFrame:
    """
    Process every TIFF under `tiff_root` (recursively — files may sit directly
    in the folder or in nested subfolders), locating each one's temperature CSV
    by date under `temperature_root`, and returning one row of transition
    results per TIFF.

    This is the headless equivalent of the interactive pipeline: for each TIFF
    it reads the MicroManager timing, syncs temperatures, builds the
    focus-corrected entropy turbidity curve, and detects T_phase / T_clear.

    Returns
    -------
    DataFrame with one row per TIFF:
        file, n_frames, n_matched, T_phase_C, T_clear_C, hysteresis_C, csv,
        status
    """
    import tifffile

    tiffs = get_filenames_by_type(tiff_root, 'tif') + get_filenames_by_type(tiff_root, 'tiff')
    tiffs = sorted(set(tiffs))
    rows = []

    for k, tiff in enumerate(tiffs):
        row = {'file': os.path.basename(tiff), 'n_frames': 0, 'n_matched': 0,
               'T_phase_C': np.nan, 'T_clear_C': np.nan, 'hysteresis_C': np.nan,
               'csv': '', 'status': 'ok'}
        try:
            csv = locate_temperature_csv(tiff, temperature_root)
            if csv is None:
                row['status'] = 'no CSV matched by date'
                rows.append(row)
                if progress_callback: progress_callback(k + 1, len(tiffs))
                continue
            row['csv'] = os.path.basename(csv)

            times = read_micromanager_times(tiff)
            temps = sync_temperatures(times['received'], csv,
                                      temp_column=temp_column, csv_header=csv_header)
            stack = tifffile.imread(tiff, key=slice(None))
            if stack.ndim != 3:
                row['status'] = f'not a (T,H,W) stack (ndim={stack.ndim})'
                rows.append(row)
                if progress_callback: progress_callback(k + 1, len(tiffs))
                continue

            df = entropy_turbidity_curve(
                stack, temps, subtract_first_frame=subtract_first_frame,
                correct_focal_drift=correct_focal_drift)
            sig = 'entropy_corrected' if correct_focal_drift else 'entropy'
            trans = detect_transitions(df, sig)

            row.update({
                'n_frames': times['n_frames'],
                'n_matched': int(np.isfinite(temps).sum()),
                'T_phase_C': trans['T_phase_C'],
                'T_clear_C': trans['T_clear_C'],
                'hysteresis_C': trans['hysteresis_C'],
            })

            # Optional annotated MP4, named from the TIFF and saved beside it.
            if export_mp4:
                try:
                    elapsed_s = elapsed_to_seconds(times['elapsed_ms'])
                    base = os.path.splitext(os.path.basename(tiff))[0]
                    mp4_path = os.path.join(os.path.dirname(tiff), f"{base}_annotated.mp4")
                    render_annotated_mp4(
                        stack, temps, elapsed_s, mp4_path, fps=fps,
                        pixel_um=pixel_um, scalebar_um=scalebar_um)
                    row['mp4'] = os.path.basename(mp4_path)
                except Exception as _me:
                    row['mp4'] = f'mp4 error: {_me}'

            # Optional pattern-corrected TIFF (auto-detect the clear frame).
            if export_corrected:
                try:
                    import tifffile as _tf
                    g = guess_clear_frame(stack)
                    corrected = apply_static_pattern_correction(stack, g['index'])
                    base = os.path.splitext(os.path.basename(tiff))[0]
                    corr_path = os.path.join(os.path.dirname(tiff), f"{base}_corrected.tif")
                    _tf.imwrite(corr_path, corrected.astype(np.float32))
                    row['corrected'] = os.path.basename(corr_path)
                except Exception as _ce:
                    row['corrected'] = f'corrected error: {_ce}'
        except Exception as e:
            row['status'] = f'error: {e}'
        rows.append(row)
        if progress_callback:
            progress_callback(k + 1, len(tiffs))

    return pd.DataFrame(rows)



def render_annotated_mp4(stack, temps, elapsed_s, out_path, fps=30,
                         pixel_um=1.0, scalebar_um=10.0, progress_callback=None):
    """
    Render a stack to an annotated MP4 with per-frame temperature/time text and
    an optional scale bar burned in. Headless (Agg backend); shared by the
    interactive export and the batch runner.

    scalebar_um <= 0 disables the scale bar.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import imageio.v3 as iio
    from datetime import timedelta

    stack = np.asarray(stack)
    n = stack.shape[0]
    vmin, vmax = float(np.percentile(stack, 1)), float(np.percentile(stack, 99))
    bar_px = (scalebar_um / pixel_um) if (pixel_um > 0 and scalebar_um > 0) else 0

    frames = []
    for i in range(n):
        fig, ax = plt.subplots(figsize=(5, 5), dpi=100)
        ax.imshow(stack[i], cmap='gray', vmin=vmin, vmax=vmax)
        ax.axis('off')
        T = temps[i] if temps is not None else np.nan
        secs = int(elapsed_s[i]) if (elapsed_s is not None and np.isfinite(elapsed_s[i])) else 0
        tc = f"{T:.2f} \u00b0C" if np.isfinite(T) else "-- \u00b0C"
        ax.set_title(f"{tc}   |   {timedelta(seconds=secs)} (h:m:s)", fontsize=12)
        if bar_px > 0:
            H, W = stack.shape[1], stack.shape[2]
            x0 = W * 0.95 - bar_px; y0 = H * 0.92
            ax.plot([x0, x0 + bar_px], [y0, y0], '-', color='white', lw=3)
            ax.text(x0 + bar_px / 2, y0 - H * 0.02, f"{scalebar_um:.0f} \u00b5m",
                    color='white', ha='center', va='bottom', fontsize=10)
        fig.canvas.draw()
        buf = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8)
        buf = buf.reshape(fig.canvas.get_width_height()[::-1] + (4,))[:, :, :3]
        frames.append(buf.copy())
        plt.close(fig)
        if progress_callback:
            progress_callback(i + 1, n)

    arr = np.stack(frames)
    # H.264 needs even dims + yuv420p for broad player compatibility.
    if arr.shape[1] % 2:
        arr = arr[:, :-1, :, :]
    if arr.shape[2] % 2:
        arr = arr[:, :, :-1, :]
    last_err = None
    for _kwargs in ({'codec': 'libx264', 'out_pixel_format': 'yuv420p'},
                    {'codec': 'libx264'},
                    {'codec': 'mpeg4'},
                    {}):
        try:
            iio.imwrite(str(out_path), arr, fps=fps, **_kwargs)
            last_err = None
            break
        except Exception as _e:
            last_err = _e
    if last_err is not None:
        raise last_err
    return str(out_path)


def plot_turbidity_transitions(df, transitions, signal_column='entropy',
                               interactive=True):
    """
    Plot the entropy-vs-temperature turbidity hysteresis with the heating branch
    (temperature rising) in RED and the cooling branch (falling) in BLUE, and
    annotate T_cloud (T_phase, on heating) and T_clear (on cooling).

    The two temperature markers are staggered — one label above with a downward
    pointer, one below with an upward pointer — so their text never overlaps even
    when the two temperatures are close.

    interactive=True shows a Qt window; False returns the Figure (headless).
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    temp = df['temperature_C'].values
    sig = df[signal_column].values
    loc = int(np.nanargmax(temp))                 # split at peak temperature
    fig, ax = plt.subplots(figsize=(6.5, 5))

    # Heating = rising T (red), cooling = falling T (blue).
    ax.plot(temp[:loc + 1], sig[:loc + 1], '-o', color='#d62728', ms=3, lw=1.2,
            label='heating (T\u2191)')
    ax.plot(temp[loc:], sig[loc:], '-o', color='#1f77b4', ms=3, lw=1.2,
            label='cooling (T\u2193)')

    y0, y1 = ax.get_ylim()
    yr = y1 - y0

    def _annotate(T, label, arrow_up, color):
        """Vertical guide at T with a directional arrow that respects the
        temperature sweep: heating (T rising) → arrow points up, cooling
        (T falling) → arrow points down."""
        if not np.isfinite(T):
            return
        ax.axvline(T, color=color, ls='--', lw=1.1, alpha=0.55)
        if arrow_up:                       # heating: temperature rising
            y_tail, y_head = y0 + 0.12 * yr, y0 + 0.34 * yr
            ytext = y0 + 0.02 * yr; va = 'bottom'
        else:                              # cooling: temperature falling
            y_tail, y_head = y1 - 0.12 * yr, y1 - 0.34 * yr
            ytext = y1 - 0.02 * yr; va = 'top'
        ax.annotate('', xy=(T, y_head), xytext=(T, y_tail),
                    arrowprops=dict(arrowstyle='-|>', color=color, lw=1.8,
                                    alpha=0.9))
        ax.text(T, ytext, f"{label}\n{T:.2f} \u00b0C", ha='center', va=va,
                fontsize=9, fontweight='bold', color='0.15',
                bbox=dict(boxstyle='round,pad=0.25', fc='white', ec=color,
                          alpha=0.92))

    _RED, _BLUE = '#d62728', '#1f77b4'
    cloud_branch = transitions.get('cloud_branch')
    clear_branch = transitions.get('clear_branch')
    cloud_color = _RED if cloud_branch == 'heating' else _BLUE
    clear_color = _RED if clear_branch == 'heating' else _BLUE
    # heating branch → arrow up (T rising), cooling branch → arrow down.
    _annotate(transitions.get('T_phase_C'), 'T$_{cloud}$',
              arrow_up=(cloud_branch == 'heating'), color=cloud_color)
    _annotate(transitions.get('T_clear_C'), 'T$_{clear}$',
              arrow_up=(clear_branch == 'heating'), color=clear_color)

    ax.set_xlabel('temperature (\u00b0C)')
    ax.set_ylabel(signal_column.replace('_', ' '))
    hy = transitions.get('hysteresis_C')
    title = 'Turbidity transition'
    if hy is not None and np.isfinite(hy):
        title += f'   (hysteresis {hy:.2f} \u00b0C)'
    ax.set_title(title)
    ax.legend(loc='best', fontsize=9, framealpha=0.9)
    fig.tight_layout()

    if interactive:
        plt.show(block=False)
    return fig


# ---------------------------------------------------------------------------
# Batch phase diagram: parse filenames → (x-variable, replicate) → boundary
# ---------------------------------------------------------------------------

import re as _re

# number + unit tokens commonly used for the swept variable
_CONC_UNITS = r'(?:mg[\s_/]*p?m?L|mg/?ml|ug/?ml|µg/?ml|mM|uM|µM|nM|M|%|wt%|v/v)'
_TOKEN_RE = _re.compile(r'([0-9]*\.?[0-9]+)\s*[_\- ]?(' + _CONC_UNITS + r')', _re.I)
_REPEAT_RE = _re.compile(r'(?:pos|rep|replicate|r|n|trial|fov)[\s_\-]?([0-9]+)', _re.I)


def parse_batch_filenames(filenames):
    """
    Parse a set of batch TIFF filenames into a swept x-axis variable and
    replicate groupings for a phase diagram.

    Strategy: extract every "<number><unit>" token from each name, keyed by
    (unit, position-within-name) so a constant buffer (e.g. 50 mM HEPES) is not
    confused with a swept salt (e.g. 150–1500 mM). The (unit, position) whose
    VALUE varies across the batch is the swept variable; if more than one varies,
    or none does, parsing fails and the caller should ask the user.

    Returns a dict:
        ok         : bool
        reason     : str (why parsing failed, if ok is False)
        x_name     : label of the swept variable (e.g. 'mM')
        per_file   : list of {file, x_value, replicate}
        candidates : varying/ambiguous tokens found (for user disambiguation)
    """
    import os
    from collections import defaultdict
    parsed = []
    for f in filenames:
        base = os.path.basename(str(f))
        toks = _TOKEN_RE.findall(base)
        d = defaultdict(list)
        for val, unit in toks:
            u = unit.lower().replace(' ', '').replace('_', '')
            d[u].append(float(val))
        rep = _REPEAT_RE.search(base)
        parsed.append({'file': base, 'tokens': dict(d),
                       'replicate': int(rep.group(1)) if rep else None})

    n = len(parsed)
    # candidate = (unit, position); collect its value per file
    col = defaultdict(dict)
    for i, p in enumerate(parsed):
        for unit, vals in p['tokens'].items():
            for pos, v in enumerate(vals):
                col[(unit, pos)][i] = v
    # a candidate is "swept" if present in most files and takes ≥2 distinct values
    varying = {k: d for k, d in col.items()
               if len(d) >= max(2, 0.5 * n) and len(set(d.values())) >= 2}

    def _label(key):
        unit, pos = key
        return unit if pos == 0 else f"{unit}#{pos + 1}"

    if not varying:
        return dict(ok=False, x_name=None, per_file=[],
                    candidates=[_label(k) for k in col],
                    reason=("Could not find a swept variable in the filenames: no "
                            "number+unit token (e.g. '150mM', '3mgpmL') varies "
                            "across the batch. Please specify the x-axis variable."))
    if len(varying) > 1:
        return dict(ok=False, x_name=None, per_file=[],
                    candidates=[_label(k) for k in varying],
                    reason=("More than one variable changes across the filenames "
                            f"({', '.join(_label(k) for k in varying)}). Please "
                            "specify which is the x-axis variable."))

    key = next(iter(varying))
    unit, pos = key
    per_file = []
    for i, p in enumerate(parsed):
        vals = p['tokens'].get(unit)
        if not vals or pos >= len(vals):
            continue
        per_file.append({'file': p['file'], 'x_value': float(vals[pos]),
                         'replicate': p['replicate']})
    if len(per_file) < 2:
        return dict(ok=False, x_name=_label(key), per_file=per_file,
                    candidates=[_label(k) for k in varying],
                    reason="Not enough files carry the swept variable to plot.")
    return dict(ok=True, reason='', x_name=_label(key), per_file=per_file,
                candidates=[_label(k) for k in varying])
