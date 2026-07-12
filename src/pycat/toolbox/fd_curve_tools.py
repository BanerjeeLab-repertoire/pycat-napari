"""
PyCAT Force-Distance (FD) Curve Tools — DNA Tethering (C-Trap)
==============================================================
Analyse force-distance curves from a Lumicks C-Trap DNA-tethering experiment,
where two beads are joined by a molecular tether and repeated stretch/relax
cycles reveal rips (unfolding) and unzips.

The central operation is "unfolding" the continuous time-wise force trace into
individual stretch and relax segments plotted over the same distance range —
so the repeated pulls overlay as loops (the live view during the experiment)
rather than a long train of peaks and valleys. This mirrors the author's
manual FD analysis.

Capabilities
------------
- Segment a trace into stretch (extension) and relaxation half-cycles by
  detecting the turning points of the distance signal.
- Optional Savitzky-Golay smoothing of the force channel.
- A worm-like chain (WLC) reference overlay for dsDNA (extensible Odijk model).
- Per-cycle export as aligned/interleaved tables.

Author
------
    Original tools: Gable Wadsworth / Anurag / Ritika (Banerjee Lab)
    PyCAT port: Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Via the notification shim: keeps the array functions importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------

def smooth_force(force: np.ndarray, window_length: int = 5, polyorder: int = 3) -> np.ndarray:
    """Savitzky-Golay smoothing of a force trace (odd window, polyorder<window)."""
    from scipy.signal import savgol_filter
    f = np.asarray(force, dtype=float)
    w = int(window_length)
    if w % 2 == 0:
        w += 1
    w = max(w, polyorder + 1 + (1 - (polyorder + 1) % 2))
    if w > len(f):
        return f.copy()
    return savgol_filter(f, w, polyorder)


# ---------------------------------------------------------------------------
# Cycle segmentation via distance turning points
# ---------------------------------------------------------------------------

def find_turning_points(distance: np.ndarray, min_segment: int = 20,
                        smooth_window: int = 11) -> np.ndarray:
    """
    Find the indices where the distance signal reverses direction — the
    boundaries between stretch and relax half-cycles.

    The distance channel is lightly smoothed, then sign changes of its
    derivative mark turning points. Segments shorter than `min_segment` are
    merged out to suppress jitter at the reversals.

    Returns
    -------
    Sorted array of turning-point indices (including 0 and the last index).
    """
    d = np.asarray(distance, dtype=float)
    n = len(d)
    if n < 3:
        return np.array([0, n - 1])

    # Light smoothing of distance to stabilise the derivative sign
    w = smooth_window if smooth_window % 2 else smooth_window + 1
    if w < n:
        kernel = np.ones(w) / w
        ds = np.convolve(d, kernel, mode='same')
    else:
        ds = d

    diff = np.diff(ds)
    sign = np.sign(diff)
    # Treat flat spots as continuing the previous direction
    for i in range(1, len(sign)):
        if sign[i] == 0:
            sign[i] = sign[i - 1]
    turns = np.where(np.diff(sign) != 0)[0] + 1

    pts = np.concatenate([[0], turns, [n - 1]])
    pts = np.unique(pts)

    # Merge segments shorter than min_segment
    if len(pts) > 2:
        keep = [pts[0]]
        for p in pts[1:-1]:
            if p - keep[-1] >= min_segment:
                keep.append(p)
        keep.append(pts[-1])
        pts = np.array(keep)
    return pts


def segment_fd_cycles(force: np.ndarray, distance: np.ndarray,
                      min_segment: int = 20, smooth_window: int = 11) -> dict:
    """
    Split an FD trace into stretch (extension) and relax half-cycles.

    A half-cycle running from one distance turning point to the next is a
    "stretch" if distance is increasing over it, else a "relax". This unfolds
    the time-wise trace into overlapping FD loops.

    Returns
    -------
    dict with:
        stretches : list of (distance_seg, force_seg) arrays (extension)
        relaxes   : list of (distance_seg, force_seg) arrays (relaxation)
        turning_points : the segment boundary indices
    """
    f = np.asarray(force, dtype=float)
    d = np.asarray(distance, dtype=float)
    pts = find_turning_points(d, min_segment=min_segment, smooth_window=smooth_window)

    stretches, relaxes = [], []
    for i in range(len(pts) - 1):
        s, e = pts[i], pts[i + 1]
        if e - s < 2:
            continue
        d_seg, f_seg = d[s:e + 1], f[s:e + 1]
        if d_seg[-1] >= d_seg[0]:
            stretches.append((d_seg, f_seg))
        else:
            relaxes.append((d_seg, f_seg))
    return dict(stretches=stretches, relaxes=relaxes, turning_points=pts)


# ---------------------------------------------------------------------------
# Worm-like chain reference
# ---------------------------------------------------------------------------

def wlc_extensible(force_pN: np.ndarray, contour_length_um: float = 16.49,
                   persistence_length_nm: float = 50.0,
                   stretch_modulus_pN: float = 1500.0,
                   kT_pN_nm: float = 4.11) -> np.ndarray:
    """
    Extensible worm-like chain (Odijk) distance-vs-force reference curve.

        d(F) = L0 · [ 1 − 0.5·sqrt(kT / (F·Lp)) + F / K0 ]

    Parameters (defaults are typical dsDNA values matching the author's script)
    ----------
    force_pN : force values (pN) to evaluate at.
    contour_length_um : contour length L0 (µm).
    persistence_length_nm : persistence length Lp (nm).
    stretch_modulus_pN : stretch modulus K0 (pN).
    kT_pN_nm : thermal energy (pN·nm), ~4.11 at room temperature.

    Returns
    -------
    distance array (µm), same shape as force_pN.
    """
    F = np.asarray(force_pN, dtype=float)
    F = np.clip(F, 1e-6, None)
    return contour_length_um * (
        1.0 - 0.5 * np.sqrt(kT_pN_nm / (F * persistence_length_nm))
        + F / stretch_modulus_pN)


# ---------------------------------------------------------------------------
# Per-cycle tabulation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Single-stranded nucleic acid model (ssDNA / ssRNA) — extensible FJC
# ---------------------------------------------------------------------------
#
# ssDNA / ssRNA is far more flexible than dsDNA (persistence length ~1 nm vs
# ~50 nm), so it is described by an extensible freely-jointed chain (Smith,
# Cluzel & Bustamante 1996) rather than the worm-like chain:
#
#     x(F) = Lc · [ coth(F·b / kT) − kT / (F·b) ] · (1 + F / S)
#
# with Kuhn length b = 2·Lp, contour length Lc, stretch modulus S. This is the
# right reference for the TERRA (UUAGGG)_n region of a tethering construct.

# Contour-length rise per nucleotide (nm). ssDNA/ssRNA are both ~0.59 nm/nt;
# these are salt-dependent literature values and are exposed as parameters.
SSDNA_RISE_NM_PER_NT = 0.59
SSRNA_RISE_NM_PER_NT = 0.59

# Human telomeric-repeat-containing RNA (TERRA): 5'-UUAGGG-3' = 6 nt/repeat.
TERRA_REPEAT_NT = 6


def fjc_extensible_ssna(force_pN: np.ndarray, contour_length_um: float,
                        kuhn_length_nm: float = 1.5,
                        stretch_modulus_pN: float = 800.0,
                        kT_pN_nm: float = 4.11) -> np.ndarray:
    """
    Extensible freely-jointed-chain extension-vs-force for ssDNA / ssRNA.

        x(F) = Lc · [coth(F·b/kT) − kT/(F·b)] · (1 + F/S)

    Parameters
    ----------
    force_pN : force values (pN).
    contour_length_um : contour length Lc (µm).
    kuhn_length_nm : Kuhn length b (nm) = 2·persistence length. ssNA ~1.5 nm.
    stretch_modulus_pN : stretch modulus S (pN). ssNA ~800 pN.
    kT_pN_nm : thermal energy (pN·nm).

    Returns
    -------
    extension (µm), same shape as force_pN.
    """
    F = np.clip(np.asarray(force_pN, dtype=float), 1e-6, None)
    b = kuhn_length_nm
    x_b = F * b / kT_pN_nm
    langevin = (1.0 / np.tanh(x_b)) - (1.0 / x_b)     # coth(x) − 1/x
    return contour_length_um * langevin * (1.0 + F / stretch_modulus_pN)


def contour_length_from_fjc(force_pN: np.ndarray, extension_um: np.ndarray,
                            kuhn_length_nm: float = 1.5,
                            stretch_modulus_pN: float = 800.0,
                            kT_pN_nm: float = 4.11) -> np.ndarray:
    """
    Invert the extensible FJC to recover apparent contour length from measured
    (force, extension) points:

        Lc = x / { [coth(F·b/kT) − kT/(F·b)] · (1 + F/S) }

    In a contour-length transform, unfolding rips appear as upward steps in Lc.
    """
    F = np.clip(np.asarray(force_pN, dtype=float), 1e-6, None)
    x = np.asarray(extension_um, dtype=float)
    b = kuhn_length_nm
    x_b = F * b / kT_pN_nm
    langevin = (1.0 / np.tanh(x_b)) - (1.0 / x_b)
    denom = langevin * (1.0 + F / stretch_modulus_pN)
    with np.errstate(divide='ignore', invalid='ignore'):
        Lc = np.where(denom > 0, x / denom, np.nan)
    return Lc


def contour_increment_to_nucleotides(delta_Lc_um: float,
                                     rise_nm_per_nt: float = SSDNA_RISE_NM_PER_NT) -> float:
    """Convert a contour-length increment (µm) to a number of nucleotides."""
    return float(delta_Lc_um * 1000.0 / rise_nm_per_nt)


# ---------------------------------------------------------------------------
# Rip / unzip event detection (G-quadruplex unfolding)
# ---------------------------------------------------------------------------

def detect_rips(distance: np.ndarray, force: np.ndarray,
                min_force_drop_pN: float = 2.0,
                min_separation: int = 5) -> pd.DataFrame:
    """
    Detect rip (unfolding) events in a single stretch curve — the sudden force
    drops produced when a folded structure (e.g. a TERRA G-quadruplex) ruptures.

    A rip is a prominent local maximum of the force along the stretch: force
    rises as the structure resists, then drops abruptly when it unfolds. The
    prominence threshold `min_force_drop_pN` sets the smallest drop counted.

    Parameters
    ----------
    distance, force : 1D arrays for ONE stretch half-cycle (increasing distance).
    min_force_drop_pN : minimum force drop (prominence, pN) to count as a rip.
    min_separation : minimum sample separation between rips.

    Returns
    -------
    DataFrame with one row per rip:
        index, distance_um, rupture_force_pN, force_after_pN, force_drop_pN
    """
    from scipy.signal import find_peaks

    f = np.asarray(force, dtype=float)
    d = np.asarray(distance, dtype=float)
    if len(f) < 5:
        return pd.DataFrame(columns=['index', 'distance_um', 'rupture_force_pN',
                                     'force_after_pN', 'force_drop_pN'])

    peaks, props = find_peaks(f, prominence=min_force_drop_pN, distance=min_separation)
    rows = []
    for k, p in enumerate(peaks):
        # Force after the rip = the following local minimum (trough)
        nxt = peaks[k + 1] if k + 1 < len(peaks) else len(f) - 1
        trough = p + int(np.argmin(f[p:nxt + 1])) if nxt > p else p
        f_after = float(f[trough])
        rows.append({
            'index': int(p),
            'distance_um': float(d[p]),
            'rupture_force_pN': float(f[p]),
            'force_after_pN': f_after,
            'force_drop_pN': float(f[p] - f_after),
        })
    return pd.DataFrame(rows)


def analyze_stretch_rips(distance: np.ndarray, force: np.ndarray,
                         min_force_drop_pN: float = 2.0,
                         kuhn_length_nm: float = 1.5,
                         stretch_modulus_pN: float = 800.0,
                         rise_nm_per_nt: float = SSDNA_RISE_NM_PER_NT,
                         kT_pN_nm: float = 4.11) -> pd.DataFrame:
    """
    Detect rips in a stretch curve AND estimate the contour-length increment
    (ΔLc) and nucleotides released at each rip, via the FJC contour-length
    transform of the points just before and just after the rip.

    ΔLc across a rip approximates the ssNA contour released by the unfolding
    event, because the dsDNA handles' contour does not change at the rip — only
    the single-stranded insert lengthens. (This is the standard contour-length-
    transform approximation; absolute Lc still depends on the handle model.)

    Returns
    -------
    DataFrame: the detect_rips columns plus delta_Lc_um, n_nucleotides.
    """
    rips = detect_rips(distance, force, min_force_drop_pN=min_force_drop_pN)
    if rips.empty:
        rips['delta_Lc_um'] = []
        rips['n_nucleotides'] = []
        return rips

    d = np.asarray(distance, dtype=float)
    f = np.asarray(force, dtype=float)
    dLc, nnt = [], []
    for _, r in rips.iterrows():
        p = int(r['index'])
        # trough index = first local min after p
        after = f[p:]
        t_rel = int(np.argmin(after[:max(2, len(after))]))
        trough = p + t_rel
        Lc_before = contour_length_from_fjc(
            np.array([f[p]]), np.array([d[p]]),
            kuhn_length_nm, stretch_modulus_pN, kT_pN_nm)[0]
        Lc_after = contour_length_from_fjc(
            np.array([f[trough]]), np.array([d[trough]]),
            kuhn_length_nm, stretch_modulus_pN, kT_pN_nm)[0]
        delta = Lc_after - Lc_before
        dLc.append(delta)
        nnt.append(contour_increment_to_nucleotides(delta, rise_nm_per_nt)
                   if np.isfinite(delta) else np.nan)
    rips['delta_Lc_um'] = dLc
    rips['n_nucleotides'] = nnt
    return rips


def detect_all_rips(seg_result: dict, which: str = 'stretches',
                    **rip_kwargs) -> pd.DataFrame:
    """
    Run rip detection across every stretch (or relax) half-cycle and return a
    combined table tagged by cycle number.
    """
    rows = []
    for i, (d_seg, f_seg) in enumerate(seg_result.get(which, [])):
        r = analyze_stretch_rips(d_seg, f_seg, **rip_kwargs)
        if not r.empty:
            r.insert(0, 'cycle', i + 1)
            rows.append(r)
    if not rows:
        return pd.DataFrame(columns=['cycle', 'index', 'distance_um',
                                     'rupture_force_pN', 'force_after_pN',
                                     'force_drop_pN', 'delta_Lc_um', 'n_nucleotides'])
    return pd.concat(rows, ignore_index=True)


def cycles_to_dataframe(segments: list, kind: str) -> pd.DataFrame:
    """
    Pack a list of (distance, force) segments into a padded, interleaved
    DataFrame with per-cycle distance/force columns.

    kind : 'stretch' or 'relax' — used for column naming.
    """
    if not segments:
        return pd.DataFrame()
    max_len = max(len(d) for d, _ in segments)
    cols = {}
    for i, (d_seg, f_seg) in enumerate(segments):
        dp = np.pad(d_seg, (0, max_len - len(d_seg)), constant_values=np.nan)
        fp = np.pad(f_seg, (0, max_len - len(f_seg)), constant_values=np.nan)
        cols[f'{kind}_distance_um_cycle{i + 1}'] = dp
        cols[f'{kind}_force_pN_cycle{i + 1}'] = fp
    return pd.DataFrame(cols)


def summarise_cycles(seg_result: dict) -> pd.DataFrame:
    """
    One-row-per-half-cycle summary: type, n_points, distance span, force range,
    and peak (max) force — a quick overview of the unfolded loops.
    """
    rows = []
    for kind, key in [('stretch', 'stretches'), ('relax', 'relaxes')]:
        for i, (d_seg, f_seg) in enumerate(seg_result[key]):
            rows.append({
                'type': kind, 'cycle': i + 1, 'n_points': len(d_seg),
                'd_min_um': float(np.nanmin(d_seg)), 'd_max_um': float(np.nanmax(d_seg)),
                'f_min_pN': float(np.nanmin(f_seg)), 'f_max_pN': float(np.nanmax(f_seg)),
            })
    return pd.DataFrame(rows)
