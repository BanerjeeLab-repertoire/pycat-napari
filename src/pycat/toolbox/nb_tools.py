"""
PyCAT Number & Brightness (N&B) Analysis
========================================
Estimate molecular **number** (n) and **brightness** (epsilon) per pixel from the
*temporal* fluctuations of a fluorescence image **time-series**, following

    Digman, Dalal, Horwitz & Gratton, "Mapping the number of molecules and
    brightness in the laser scanning microscope," Biophys. J. 94:2320 (2008).

N&B is the camera / time-series counterpart to SpIDA. Where SpIDA reads the
*spatial* intensity histogram of one optically-sectioned image, N&B reads the
*temporal* variance at each pixel across many frames. This makes it the right
tool for **camera-based and widefield/TIRF data** (e.g. sCMOS), where SpIDA's
confocal/PMT assumptions do not hold — provided the molecules exchange (move,
diffuse, bind/unbind) between frames so that the temporal fluctuations carry the
particle statistics.

Model
-----
For each pixel, over T frames, let ⟨I⟩ be the temporal mean and σ² the temporal
variance. For an ideal detector:

    apparent brightness  B = σ² / ⟨I⟩
    apparent number      N = ⟨I⟩² / σ²

The **true** molecular brightness and number require the detector's gain and
noise. With analog gain S (intensity units per particle-event), read/offset
subtracted:

    epsilon (true brightness) = (σ² − σ²_read) / (⟨I⟩ − offset) − 1 ... [S=1 form]
    n       (true number)     = (⟨I⟩ − offset) / epsilon

We use the standard analog form with scalar gain S, offset and read-variance:

    ε = ( σ² − σ²_read ) / ( S · (⟨I⟩ − offset) )
    n = S · (⟨I⟩ − offset) / ε = S² (⟨I⟩ − offset)² / (σ² − σ²_read)

For a photon-counting-like detector (S = 1, σ²_read = 0) these reduce to the
apparent forms above. **Scalar gain/offset are supported now; a per-pixel
variance/offset map (proper sCMOS correction) is left as a documented hook.**

IMPORTANT — assumptions
-----------------------
* The series must be a genuine time-series where molecules **exchange between
  frames**. A static/fixed sample has no meaningful temporal fluctuation and N&B
  will report noise, not molecules.
* **Photobleaching and drift bias the variance** and must be removed first
  (detrending). A simple per-pixel linear/boxcar detrend is provided and on by
  default.
* Brightness is only comparable across data taken at the **same gain/offset and
  exposure**.

Author
------
    Gable Wadsworth, Banerjee Lab, SUNY Buffalo
"""

import numpy as np

try:
    from napari.utils.notifications import show_warning as napari_show_warning
    from napari.utils.notifications import show_info as napari_show_info
except Exception:  # pragma: no cover
    def napari_show_warning(msg):
        print(f"[warning] {msg}")

    def napari_show_info(msg):
        print(f"[info] {msg}")


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------
def detrend_timeseries(stack, method='boxcar', window=8):
    """
    Remove slow bleaching/drift that would otherwise inflate the temporal variance
    and corrupt N&B.

    IMPORTANT: N&B detrending must remove the *global* intensity trend (the
    spatially-averaged bleaching curve) by **rescaling each frame**, NOT by
    subtracting a per-pixel moving average — the latter also removes the genuine
    per-pixel fluctuations that carry the molecular statistics, biasing brightness
    low. Here we compute the frame-mean trace, smooth it, and divide each frame by
    the normalized trend so the mean level is held constant while per-pixel
    fluctuations are preserved (the standard Trautmann/Hillesheim bleaching
    correction).

    Parameters
    ----------
    stack : ndarray, shape (T, H, W)
    method : {'boxcar', 'linear', 'none'}
        'boxcar' smooths the global trace with a moving average; 'linear' fits a
        line to it; 'none' disables correction.
    window : int
        Boxcar window length in frames (for method='boxcar').

    Returns
    -------
    ndarray
        Bleaching-corrected stack (float32), same shape.
    """
    s = np.asarray(stack, dtype=np.float32)
    T = s.shape[0]
    if method == 'none' or T < 4:
        return s

    # Global bleaching trace: mean intensity of each frame.
    trace = s.reshape(T, -1).mean(axis=1)
    overall = float(trace.mean())
    if overall <= 0:
        return s

    if method == 'linear':
        t = np.arange(T, dtype=np.float32)
        A = np.vstack([t, np.ones_like(t)]).T
        slope, intercept = np.linalg.lstsq(A, trace, rcond=None)[0]
        trend = slope * t + intercept
    else:  # boxcar
        w = max(2, int(window))
        pad = w // 2
        csum = np.cumsum(np.concatenate([[0.0], trace]))
        trend = np.empty(T, dtype=np.float32)
        for ti in range(T):
            lo = max(0, ti - pad)
            hi = min(T, ti - pad + w)
            trend[ti] = (csum[hi] - csum[lo]) / (hi - lo)

    trend = np.where(trend <= 0, overall, trend)
    # Rescale each frame so the global level is flat, preserving per-pixel
    # fluctuations. (Multiplicative correction, standard for N&B.)
    factor = (overall / trend).astype(np.float32)
    out = s * factor[:, None, None]
    return out


def number_and_brightness(stack, gain=1.0, offset=0.0, read_variance=0.0,
                          detrend='boxcar', detrend_window=8):
    """
    Compute per-pixel apparent/true number and brightness maps from a time-series.

    Parameters
    ----------
    stack : ndarray, shape (T, H, W)
        Fluorescence time-series (molecules must exchange between frames).
    gain : float
        Detector gain S (intensity units per particle-event). 1.0 = photon-count-
        like. For an sCMOS in e-, pass the e-/ADU conversion (or leave 1.0 to work
        in ADU and get apparent brightness).
    offset : float
        Camera offset / dark level (ADU), subtracted from the mean.
    read_variance : float
        Scalar read-noise variance (ADU²), subtracted from the temporal variance.
        (Per-pixel variance-map correction is a future extension — see module doc.)
    detrend : {'boxcar','linear','none'}
        Photobleaching/drift removal before computing variance.
    detrend_window : int
        Boxcar window in frames.

    Returns
    -------
    dict with per-pixel maps: 'mean', 'variance', 'brightness' (epsilon),
        'number' (n), and scalar bookkeeping.
    """
    s = np.asarray(stack, dtype=np.float32)
    if s.ndim != 3:
        raise ValueError(f"N&B needs a 3D (T,H,W) stack; got shape {s.shape}.")
    T = s.shape[0]
    if T < 4:
        raise ValueError(f"N&B needs several frames (got {T}); >=~20 recommended.")

    s = detrend_timeseries(s, method=detrend, window=detrend_window)

    mean = s.mean(axis=0)
    var = s.var(axis=0, ddof=1)

    mean_corr = mean - float(offset)
    var_corr = var - float(read_variance)

    eps = np.zeros_like(mean)          # brightness
    num = np.zeros_like(mean)          # number
    valid = (mean_corr > 1e-6) & (var_corr > 1e-6)
    g = float(gain) if gain else 1.0
    # ε = (σ² − σ²_read) / (S·(⟨I⟩−offset)) ; n = S·(⟨I⟩−offset)/ε
    eps[valid] = var_corr[valid] / (g * mean_corr[valid])
    num[valid] = (g * mean_corr[valid]) / np.maximum(eps[valid], 1e-12)

    return {
        'mean': mean,
        'variance': var,
        'brightness': eps,
        'number': num,
        'valid': valid,
        'n_frames': T,
        'gain': g,
        'offset': float(offset),
        'read_variance': float(read_variance),
    }


# ---------------------------------------------------------------------------
# Runners wired to the UI
# ---------------------------------------------------------------------------
def run_nb_analysis(image_layer, gain, offset, read_variance, detrend_window,
                    viewer, epsilon0=0.0, roi_shapes_layer=None):
    """
    Run N&B on a time-series layer, add per-pixel brightness and number maps to the
    viewer, and print an ROI (or whole-frame) summary. If a monomeric reference
    brightness epsilon_0 is supplied, report oligomeric state.
    """
    if image_layer is None:
        napari_show_warning("N&B: select a time-series image layer.")
        return
    data = np.asarray(image_layer.data)

    # Minimalist axis handling: N&B needs (T,H,W). If the array has more than 3
    # dims (e.g. T,Z,C,H,W from lazy loading), collapse leading non-spatial axes
    # to a single time axis by taking the first index of every axis except the
    # last two (H,W) and the largest leading axis as time.
    if data.ndim < 3:
        napari_show_warning(
            "N&B needs a time-series (a stack of frames), but this layer is 2D. "
            "Load a time-lapse so molecules are sampled across frames.")
        return
    if data.ndim > 3:
        # pick the largest leading axis as the time axis, index 0 on the others
        lead = data.shape[:-2]
        t_axis = int(np.argmax(lead))
        # move time axis to front, then index 0 on remaining leading axes
        data = np.moveaxis(data, t_axis, 0)
        while data.ndim > 3:
            data = data[:, 0]
        napari_show_warning(
            "N&B: layer had >3 dimensions; used the largest stacked axis as time "
            "and the first plane of the others. For Z or multi-channel data, "
            "extract the intended 2D+time series explicitly for best results.")

    try:
        res = number_and_brightness(
            data, gain=gain, offset=offset, read_variance=read_variance,
            detrend='boxcar', detrend_window=detrend_window)
    except ValueError as e:
        napari_show_warning(f"N&B: {e}")
        return

    # Add maps as layers.
    viewer.add_image(res['brightness'], name=f"N&B brightness ({image_layer.name})",
                     colormap='viridis')
    viewer.add_image(res['number'], name=f"N&B number ({image_layer.name})",
                     colormap='magma')

    # ROI (or whole-frame) summary.
    b = res['brightness']; n = res['number']; valid = res['valid']
    mask = valid
    if roi_shapes_layer is not None:
        try:
            rm = roi_shapes_layer.to_masks(mask_shape=b.shape)
            if rm.ndim == 3:
                rm = np.any(rm, axis=0)
            mask = valid & rm
        except Exception:
            pass

    if not np.any(mask):
        napari_show_warning("N&B: no valid pixels to summarise "
                            "(check offset/read-variance and that the series has "
                            "real temporal fluctuation).")
        return

    med_b = float(np.median(b[mask]))
    med_n = float(np.median(n[mask]))
    lines = [
        "── Number & Brightness ─────────────────────",
        f"Frames analysed  : {res['n_frames']}",
        f"Median brightness: {med_b:.3g} (intensity-units per molecule)",
        f"Median number    : {med_n:.3g} molecules / pixel",
        f"Gain S = {res['gain']:g}, offset = {res['offset']:g}, "
        f"read-var = {res['read_variance']:g}",
    ]
    if epsilon0 and epsilon0 > 0:
        state = med_b / epsilon0
        lines.append(f"Monomer ref eps_0: {epsilon0:.3g}")
        lines.append(f"Oligomeric state : {state:.2f}x monomer")
    else:
        lines.append("Oligomeric state : (enter a monomer-reference brightness "
                     "eps_0 to compute)")
    if res['gain'] == 1.0 and res['read_variance'] == 0.0:
        lines.append("NOTE: gain=1 and read-variance=0 → these are APPARENT "
                     "brightness/number (detector-uncorrected). Enter your "
                     "camera's gain/offset/read-noise for true values.")
    lines.append("NOTE: N&B assumes molecules exchange between frames and that "
                 "photobleaching/drift were removed (a boxcar detrend was "
                 "applied). A static sample yields noise, not molecules.")
    napari_show_info("\n".join(lines))
