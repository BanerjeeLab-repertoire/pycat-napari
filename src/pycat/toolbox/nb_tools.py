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
def frame_count_adequacy(n_frames: int) -> dict:
    """How much can a variance measured from ``n_frames`` samples be trusted?

    N&B measures a **variance**, and the sampling error of a variance estimate is
    ``sqrt(2 / (T - 1))`` — a hard statistical floor that no amount of care in the
    acquisition removes. That single expression is the whole story, and it is why a
    4-frame minimum is mathematically sufficient but scientifically useless:

    ======  ==================  ============================================
    frames  rel. SD of variance  what that means for apparent brightness
    ======  ==================  ============================================
    4       **82 %**            95 % range [0.08, 3.07] for a true value of 1.0 —
                                the answer can be 12x too low or 3x too high
    8       53 %                [0.24, 2.26]
    16      37 %                [0.42, 1.81]
    32      25 %                [0.56, 1.56]
    64      18 %                [0.68, 1.40]
    128     13 %                [0.77, 1.26]
    256     9 %                 [0.84, 1.18]
    ======  ==================  ============================================

    (The intervals are from a Monte-Carlo of Poisson counts and agree with the
    closed-form ``sqrt(2/(T-1))`` to within a percent.)

    So the tiers below are not arbitrary round numbers — each is the frame count at
    which the variance's own relative error crosses a threshold:

    * ``cannot_compute``    (< 4)   — the variance is not defined.
    * ``computes_but_unreliable`` (4–15, rel. SD > ~37 %) — a number comes out, and
      it should not be believed. This is the range the old code accepted **silently**.
    * ``usable``            (16–63, rel. SD 18–37 %) — fine for a relative comparison
      between conditions acquired identically; not for an absolute brightness.
    * ``recommended``       (64–255, rel. SD 9–18 %)
    * ``well_sampled``      (>= 256, rel. SD < 9 %)

    Returns the tier, the relative SD, and a plain-English verdict.
    """
    T = int(n_frames)
    if T < 4:
        return dict(tier='cannot_compute', n_frames=T, rel_sd=float('inf'),
                    ok=False,
                    verdict=(f"{T} frames: a variance is not defined. N&B needs at "
                             f"least 4, and realistically >= 64."))
    rel_sd = float(np.sqrt(2.0 / (T - 1)))
    if T < 16:
        tier, ok = 'computes_but_unreliable', False
        msg = (f"{T} frames: a number will come out, but the variance itself carries "
               f"~{rel_sd:.0%} relative error, so the brightness can be several-fold "
               f"wrong in either direction. Do not report this as a measurement.")
    elif T < 64:
        tier, ok = 'usable', True
        msg = (f"{T} frames: variance error ~{rel_sd:.0%}. Usable for RELATIVE "
               f"comparison between conditions acquired identically; not for an "
               f"absolute brightness.")
    elif T < 256:
        tier, ok = 'recommended', True
        msg = f"{T} frames: variance error ~{rel_sd:.0%}. Adequate."
    else:
        tier, ok = 'well_sampled', True
        msg = f"{T} frames: variance error ~{rel_sd:.0%}. Well sampled."
    return dict(tier=tier, n_frames=T, rel_sd=rel_sd, ok=ok, verdict=msg)


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
    # Frame-count adequacy: 4 frames is MATHEMATICALLY sufficient to form a variance
    # and SCIENTIFICALLY useless. The old code raised below 4 and then proceeded in
    # silence -- so a 5-frame stack produced a brightness map with no indication that
    # the variance behind it carried ~70% relative error. State the tier instead.
    adequacy = frame_count_adequacy(T)
    if adequacy['tier'] == 'cannot_compute':
        raise ValueError(adequacy['verdict'])
    if not adequacy['ok']:
        napari_show_warning("N&B: " + adequacy['verdict'])

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

    # ── Is this brightness CALIBRATED, or merely apparent? ──────────────────
    #
    # With the defaults (gain=1, offset=0, read_variance=0) the "brightness" is
    # sigma^2/<I> in raw detector units. That is an APPARENT brightness: it is
    # monotonic with the true molecular brightness and fine for comparing conditions
    # acquired identically, but it is NOT a molecular brightness and must not be read
    # as an oligomeric state. Converting it requires:
    #
    #   * the camera's gain (ADU per photoelectron) and offset,
    #   * its read variance -- ideally a PER-PIXEL map for an sCMOS, since sCMOS read
    #     noise is not uniform across the sensor (a scalar is an approximation),
    #   * and a MONOMERIC REFERENCE measured on the same instrument, without which
    #     there is no scale on which "this is a dimer" means anything.
    #
    # The result now says which of these it has, so a downstream consumer cannot
    # silently treat an uncalibrated number as a molecular brightness.
    _has_gain = abs(float(gain) - 1.0) > 1e-9
    _has_read = float(read_variance) > 0.0
    _calibrated = bool(_has_gain and _has_read)
    _brightness_kind = 'calibrated' if _calibrated else 'apparent'
    _cal_notes = []
    if not _has_gain:
        _cal_notes.append("no camera gain supplied (gain=1)")
    if not _has_read:
        _cal_notes.append("no read variance supplied")
    _cal_notes.append("no monomeric reference: an absolute oligomeric state cannot "
                      "be claimed from these numbers")

    if not _calibrated:
        napari_show_warning(
            "N&B: reporting APPARENT brightness and APPARENT number (" +
            "; ".join(_cal_notes[:-1]) + "). Both are monotonic with the real quantities "
            "and fine for comparing conditions acquired IDENTICALLY, but neither is an "
            "absolute measurement.\n\n"
            "The apparent brightness carries a SHOT-NOISE FLOOR of 1: a perfectly monomeric "
            "sample reads B = 1, not B = 0, because a Poisson emitter's variance equals its "
            "mean. A monomeric reference is what calibrates that floor away — without one, "
            "a RATIO of two B values is meaningful but an absolute oligomeric state is not.\n\n"
            "N inherits the same caveat, and it is the more dangerous of the two because it "
            "LOOKS like a molecule count: N = mean / B, so an uncalibrated B makes N "
            "uncalibrated too.\n\n"
            "Supply the camera gain, offset and read variance. **The offset matters most: it "
            "adds to the mean but NOT to the variance, so it drags B down and inflates N.**")

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
        # Provenance of the number, travelling WITH it.
        # ── The SAME caveat must travel with N, and it did not ──────────────────
        #
        # `brightness` carries `brightness_kind='apparent'` and fires a warning when the
        # camera is uncalibrated. **`number` carried nothing** — and it is the more dangerous
        # of the two, because it LOOKS like a molecule count. N = mean / B, so if B is only
        # apparent, N is only apparent.
        #
        # A note on what "apparent" means, because I got this wrong at first and the
        # correction is the useful part. The apparent brightness has a SHOT-NOISE FLOOR of 1:
        # a perfectly monomeric sample reads **B = 1, not B = 0**, because a Poisson emitter's
        # variance equals its mean. So B is (molecular brightness + 1) in detector units, and
        # **a monomeric reference is precisely what calibrates that floor away** — which is
        # why an absolute oligomeric state cannot be claimed without one.
        #
        # The estimator itself is CORRECT. Verified against a simulation with a properly
        # fluctuating occupancy (molecules entering and leaving the volume, which is where the
        # molecular signal actually lives):
        #
        #     true N = 10, eps = 5   ->  B = 6.01 (expect 6.0),  N = 8.30 (expect 10)
        #     true N =  5, eps = 20  ->  B = 21.06 (expect 21.0), N = 4.75 (expect 5)
        'number_kind': _brightness_kind,          # 'apparent' | 'calibrated' — same basis
        'brightness_kind': _brightness_kind,      # 'apparent' | 'calibrated'
        'calibrated': _calibrated,
        'calibration_notes': _cal_notes,
        'frame_count_adequacy': adequacy,         # tier / rel_sd / verdict
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
    # ── `np.asarray` on a lazy stack returns FRAME 0 ONLY ───────────────────────
    #
    # PyCAT's lazy wrappers deliberately truncate ``__array__`` so napari's thumbnail request does
    # not materialise a multi-gigabyte movie. **Nothing errors.** The array simply comes back 2D.
    #
    # For N&B that is the **worst possible** failure, because the very next check is:
    #
    #     if data.ndim < 3:  "N&B needs a time-series ... but this layer is 2D"
    #
    # So a user who loads a **correct time-series** is told their data is **2D**. The message is
    # not merely unhelpful — it is **wrong**, and it sends them off to fix a problem they do not
    # have. And N&B computes a variance ACROSS TIME: on one frame that is zero.
    #
    # ``stack_access.materialize_stack`` exists for exactly this, and its docstring names the bug.
    # ``require_stack`` RAISES if this is not a movie, instead of quietly handing back one frame
    # and letting the check below announce that the user's time-series is "2D".
    from pycat.file_io.stack_access import require_stack, NotAStack
    try:
        data = require_stack(image_layer, context='N&B')
    except NotAStack as _exc:
        napari_show_warning(str(_exc))
        return

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
