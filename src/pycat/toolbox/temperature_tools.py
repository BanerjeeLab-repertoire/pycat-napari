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


def entropy_turbidity_curve(
    stack: np.ndarray,
    temperatures: np.ndarray,
    subtract_first_frame: bool = True,
    correct_focal_drift: bool = True,
    bins: int = 256,
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
        bg = stack[0] - float(stack[0].min())
        work = stack - bg
    else:
        work = stack

    ent  = np.array([frame_entropy(work[i], bins) for i in range(n)])
    mean = np.array([float(work[i].mean()) for i in range(n)])
    foc  = focus_scores(stack)

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


def detect_transitions(turbidity_df: pd.DataFrame,
                       signal_column: str = 'entropy_corrected') -> dict:
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

    T_phase = _sigmoid_midpoint(heating['temperature_C'].values,
                                heating[signal_column].values) if len(heating) > 4 else np.nan
    T_clear = _sigmoid_midpoint(cooling['temperature_C'].values,
                                cooling[signal_column].values) if len(cooling) > 4 else np.nan
    hyst = (T_phase - T_clear) if (np.isfinite(T_phase) and np.isfinite(T_clear)) else np.nan

    return dict(T_phase_C=T_phase, T_clear_C=T_clear, hysteresis_C=hyst,
                loc=loc, heating_df=heating, cooling_df=cooling)


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
        except Exception as e:
            row['status'] = f'error: {e}'
        rows.append(row)
        if progress_callback:
            progress_callback(k + 1, len(tiffs))

    return pd.DataFrame(rows)

