"""Stability QC — photobleaching, lateral drift and vibration across a time series.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc._base import _to_float, _shift_normalise

def qc_photobleaching(stack):
    """Is the sample FADING over the acquisition?

    **This metric did not exist**, and photobleaching is one of the most common and most
    destructive defects there is. The QC module had ``qc_drift`` and ``qc_vibration`` for
    temporal *motion*, and nothing for temporal *intensity*.

    It cannot be folded into ``qc_snr``: a global intensity scale changes the signal **and** the
    noise together, so the SNR is (correctly) invariant to it. A stack that fades to a tenth of
    its brightness has the same SNR at the end as at the start — and is useless.

    What it costs, measured:

    * A **bleach correction divides by exp(-t/tau)**, so an error in tau compounds
      exponentially. On a movie a fifth of the bleach time, tau fits to 11 s against a true 50,
      and the final frame is over-corrected by **96 %** — nearly doubling it (1.5.451).
    * In **FRAP**, uncorrected acquisition bleaching makes the recovery plateau *sag*, and the
      fit reads that as a **2.5× faster recovery** with a mobile fraction 31 % too low — at
      R² = 0.94, flagged identifiable (1.5.455).
    * Any **time-series intensity measurement** (partition, enrichment, condensate growth)
      inherits a downward trend that is the lamp, not the biology.

    The measurement is the fraction of the initial signal remaining at the end, which is what
    determines whether a correction is even possible: if 90 % of the signal is gone, the last
    frames are noise and no correction recovers them.
    """
    a = _to_float(stack)
    if a.ndim != 3 or a.shape[0] < 4:
        return dict(name='Photobleaching', tier='core', status='na', value=None,
                    unit='', headline='needs a time series (≥ 4 frames)',
                    how='', good='', diag=None)

    # Median, not mean: robust to a few saturated pixels and to objects entering the field.
    per_frame = np.array([float(np.median(f)) for f in a])
    if not np.isfinite(per_frame).all() or per_frame[0] <= 0:
        return dict(name='Photobleaching', tier='core', status='na', value=None,
                    unit='', headline='intensity trace unusable', how='', good='', diag=None)

    # Fit a straight line in LOG space: an exponential decay is linear there, and the slope is
    # -1/tau. Doing it in log space also stops a few bright frames dominating the fit.
    t = np.arange(len(per_frame), dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        log_i = np.log(np.maximum(per_frame, 1e-9))
    ok = np.isfinite(log_i)
    slope = float(np.polyfit(t[ok], log_i[ok], 1)[0]) if ok.sum() >= 3 else 0.0

    # ── A tau of 5,663,342,369,728,770 frames is not a measurement ──────────────
    #
    # A stack that does not fade has a slope of ~-1e-16 (floating-point noise), and ``-1/slope``
    # turns that into **5.6e15 frames** — which the report printed, verbatim, in the headline.
    #
    # There is no decay to fit. The honest report is the fraction remaining (100 %), and tau is
    # simply not defined. A tau longer than the acquisition itself cannot be measured from it,
    # so anything beyond ~10x the stack length is reported as "no measurable decay".
    _n_frames = len(per_frame)
    tau_frames = (-1.0 / slope) if slope < 0 else float('inf')
    if not np.isfinite(tau_frames) or tau_frames > 10.0 * _n_frames:
        tau_frames = float('inf')          # no measurable decay over this acquisition
    remaining = float(per_frame[-1] / per_frame[0])

    # The thresholds are set by what a correction can actually rescue. Losing a third of the
    # signal is correctable; losing 70 % means the late frames are mostly noise.
    if remaining >= 0.85:
        status = 'good'
    elif remaining >= 0.50:
        status = 'warn'
    else:
        status = 'bad'

    return dict(
        name='Photobleaching', tier='core', status=status,
        value=remaining * 100.0, unit='% signal remaining',
        headline=(f"{remaining * 100:.0f}% of the initial signal remains at the last frame"
                  + (f" (tau ≈ {tau_frames:.0f} frames)" if np.isfinite(tau_frames) else "")),
        how="Median intensity per frame, fitted as an exponential decay in log space. The "
            "reported value is the fraction of the STARTING signal still present at the end.",
        good="Little or no fade. A bleach correction divides by exp(-t/tau), so an error in "
             "tau compounds exponentially — and if most of the signal is gone, the late frames "
             "are noise and no correction recovers them.",
        diag=dict(per_frame=per_frame, tau_frames=tau_frames, remaining=remaining))


def qc_drift(stack):
    """Lateral sample/stage drift across a (T, H, W) stack via phase
    cross-correlation to the first frame."""
    a = _to_float(stack)
    if a.ndim != 3 or a.shape[0] < 2:
        return dict(name='Drift', tier='core', status='na', value=None, unit='px',
                    headline='needs a multi-frame stack', how='', good='', diag=None)
    from skimage.registration import phase_cross_correlation
    ref = a[0]
    shifts = np.zeros((a.shape[0], 2))
    for i in range(1, a.shape[0]):
        sh = phase_cross_correlation(_shift_normalise(ref),
                                     _shift_normalise(a[i]),
                                     upsample_factor=10)[0]
        shifts[i] = sh
    mag = np.sqrt((shifts ** 2).sum(axis=1))
    total = float(mag.max())
    fov = min(a.shape[1], a.shape[2])
    frac = total / fov

    # ── Drift damage scales with OBJECT size, not sensor size ──────────────────
    #
    # The gate used to be a fraction of the FIELD OF VIEW (good < 1 %, bad ≥ 5 %). But the
    # SAME physical drift then gets a different verdict depending on the camera:
    #
    #     19 px of drift over 20 frames:
    #         128 px sensor -> 14.8 % -> "bad"
    #         512 px sensor ->  3.7 % -> "warn"
    #
    # The stage did exactly the same thing. And the FOV framing is backwards for the
    # damage that actually matters: a condensate is ~6 px across, so 19 px moves it
    # THREE DIAMETERS -- the object in the last frame does not overlap the object in the
    # first frame at all, and every per-object time-series is destroyed. On a large sensor
    # that reads as a mild 3.7 % and the QC said "warn".
    #
    # FOV-fraction is right for one failure only (objects leaving the field). For
    # misaligned time-series, broken tracking and blurred projections -- the failures that
    # matter here -- the reference is the OBJECT SIZE.
    #
    # The QC does not know the object size, but it can MEASURE it: the autocorrelation
    # half-width of the image tracks the true feature size closely (measured ratio 1.6-2.0
    # across a 8x range of object radii) and needs no mask.
    def _feature_scale(f):
        g = np.asarray(f, dtype=float)
        g = g - g.mean()
        if not np.any(g):
            return float('nan')
        F = np.fft.fft2(g)
        ac = np.fft.fftshift(np.fft.ifft2(F * np.conj(F)).real)
        mx = ac.max()
        if mx <= 0:
            return float('nan')
        ac = ac / mx
        c0, c1 = np.array(ac.shape) // 2
        prof = ac[c0, c1:]
        below = np.flatnonzero(prof < 0.5)
        return float(below[0]) if below.size else float(prof.size)

    feat = _feature_scale(a[0])
    drift_in_objects = (total / feat) if (np.isfinite(feat) and feat > 0) else float('nan')

    if np.isfinite(drift_in_objects):
        # Sub-object drift is harmless; drift of an object diameter or more breaks
        # per-object time-series and tracking outright.
        if drift_in_objects < 0.5:
            status = 'good'
        elif drift_in_objects < 1.0:
            status = 'warn'
        else:
            status = 'bad'
        headline = (f"max drift {total:.1f} px = {drift_in_objects:.1f}x the feature "
                    f"size ({frac*100:.1f}% of FOV)")
        basis = 'feature size'
    else:
        status = 'good' if frac < 0.01 else ('warn' if frac < 0.05 else 'bad')
        headline = f"max drift {total:.1f} px ({frac*100:.1f}% of FOV)"
        basis = 'field of view (feature size unavailable)'

    return dict(
        name='Drift', tier='core', status=status, value=total, unit='px',
        headline=headline,
        drift_in_features=drift_in_objects,
        fov_fraction=float(frac),
        basis=basis,
        how="Each frame is registered to the first by phase cross-correlation. The drift "
            "is judged against the IMAGE'S OWN FEATURE SIZE (autocorrelation half-width), "
            "because that is the scale on which drift does damage: a drift of one object "
            "diameter means the object no longer overlaps itself between the first and "
            "last frame. A fraction of the sensor is the wrong reference — the same stage "
            "drift would then pass or fail depending on the camera.",
        good="Drift well under half a feature size. Larger drift misaligns per-object "
             "time-series and breaks tracking — register the stack, or fix the stage.",
        diag=dict(shifts=shifts, magnitude=mag, feature_scale_px=feat))


def qc_vibration(stack):
    """Mechanical vibration: an oscillatory component in the frame-to-frame
    shift trace (advisory — needs several frames)."""
    a = _to_float(stack)
    if a.ndim != 3 or a.shape[0] < 8:
        return dict(name='Vibration', tier='advisory', status='na', value=None,
                    unit='', headline='needs ≥ 8 frames', how='', good='', diag=None)
    from skimage.registration import phase_cross_correlation
    dx = np.zeros(a.shape[0] - 1)
    dy = np.zeros(a.shape[0] - 1)
    for i in range(1, a.shape[0]):
        sh = phase_cross_correlation(_shift_normalise(a[i - 1]),
                                     _shift_normalise(a[i]),
                                     upsample_factor=10)[0]
        dy[i - 1], dx[i - 1] = sh

    # ── DETREND: a steady drift is not a vibration ──────────────────────────────
    #
    # These are FRAME-TO-FRAME shifts, so a constant stage drift appears as a **constant
    # offset** in the trace — and a constant is *maximally* concentrated at zero frequency,
    # which is exactly what the spectral test reads as a perfect periodic component.
    #
    # Measured: a stack drifting smoothly at 0.5 px/frame, with sharp (diffraction-limited)
    # objects, fired the vibration alarm at **p = 0.005, "bad"** — sending the user to hunt for
    # a pump when the problem is a drifting stage. (It only showed up once the test scene was
    # made diffraction-limited; with softer objects the shift estimate is noisy enough to mask
    # it. **The bug was always there, hidden behind a blurry test image.**)
    #
    # A vibration is an oscillation **about** the trend, not the trend itself. Removing a linear
    # fit leaves exactly that, and it is what `qc_drift` already reports separately — so the two
    # checks now measure two different things, which is the whole point of having both.
    _t = np.arange(len(dx), dtype=float)
    if len(dx) >= 3:
        try:
            dx = dx - np.polyval(np.polyfit(_t, dx, 1), _t)
            dy = dy - np.polyval(np.polyfit(_t, dy, 1), _t)
        except Exception as _exc:
            debug_log('vibration: could not detrend the shift trace', _exc)

    # ── Do NOT collapse the shift to its MAGNITUDE ─────────────────────────────
    #
    # This used to be `sig = np.hypot(dx, dy)`. A stage vibrating in a CIRCLE or ellipse
    # -- a real and common mode -- has a shift of CONSTANT magnitude, so hypot() turns it
    # into a FLAT LINE and the periodicity is destroyed before the FFT ever sees it.
    # Measured on a synthetic circular vibration: the magnitude trace was literally all
    # zeros, and the check reported "no periodic component (p = 1.00)" for a stage that
    # was vibrating throughout.
    #
    # Analyse the two axes separately and take the stronger periodicity: a linear
    # vibration shows up in one axis, a circular one in both.
    def _conc(s):
        sp = np.abs(np.fft.rfft(s - s.mean())) ** 2
        if len(sp) < 3:
            return np.nan
        return float(sp[1:].max() / max(sp[1:].sum(), 1e-12))

    axes = {'y': dy - dy.mean(), 'x': dx - dx.mean()}
    concs = {k: _conc(v) for k, v in axes.items()}
    worst_axis = max(concs, key=lambda k: (concs[k] if np.isfinite(concs[k]) else -np.inf))
    sig = axes[worst_axis]
    ratio = concs[worst_axis]
    spec = np.abs(np.fft.rfft(sig)) ** 2

    # ── The old gate measured STACK LENGTH, not vibration ───────────────────────
    #
    # The status used to be `good if ratio < 0.35 else warn if < 0.6 else bad`. But the
    # spectral concentration of a *random* jitter trace depends entirely on how many
    # frequency bins there are — i.e. on the number of frames. Measured, with NO vibration
    # present at all:
    #
    #      5 frames -> ratio 0.79  -> "bad"
    #     10 frames -> ratio 0.54  -> "warn"
    #     20 frames -> ratio 0.31  -> "good"
    #    200 frames -> ratio 0.05  -> "good"
    #
    # The same microscope on the same table got a different verdict depending on how many
    # frames were acquired. A short stack of perfectly good data was condemned; a long
    # stack could hide a real vibration.
    #
    # So reference the statistic against its own null: PERMUTE the jitter trace, which
    # destroys any periodicity while preserving the amplitude distribution exactly, and
    # ask how often a random ordering concentrates its energy as sharply as the observed
    # one. That p-value does not depend on the frame count.
    #
    # Validated: random jitter is called "no vibration" at EVERY stack length (including
    # 5 frames, where the old gate said "bad"), and real periodic vibration is detected
    # from ~20 frames upward. Below ~20 frames there are too few bins to detect anything,
    # and it says so rather than reporting "good".
    _rng = np.random.default_rng(0)
    _ps = []
    for _k, _v in axes.items():
        _c = concs[_k]
        if not np.isfinite(_c):
            continue
        _null = np.array([_conc(_rng.permutation(_v)) for _ in range(400)])
        _null = _null[np.isfinite(_null)]
        if _null.size:
            _ps.append(float((np.sum(_null >= _c) + 1) / (_null.size + 1)))
    if _ps:
        # Two axes tested -> Bonferroni. A vibration in EITHER axis is a vibration.
        p_vib = float(min(1.0, 2.0 * min(_ps)))
    else:
        p_vib = float('nan')

    n_frames = int(a.shape[0])
    if not np.isfinite(p_vib):
        status = 'na'
        headline = 'vibration could not be assessed'
    elif n_frames < 20:
        # Too few frequency bins for the test to have power. "Not assessed" is NOT "good".
        status = 'na'
        headline = (f'not assessable: {n_frames} frames (≥ 20 needed to detect a '
                    f'periodic component)')
    elif p_vib < 0.01:
        status = 'bad'
        headline = f'periodic vibration detected (p = {p_vib:.3f})'
    elif p_vib < 0.05:
        status = 'warn'
        headline = f'possible periodic vibration (p = {p_vib:.3f})'
    else:
        status = 'good'
        headline = f'no periodic component (p = {p_vib:.2f})'

    return dict(
        name='Vibration', tier='advisory', status=status, value=float(ratio),
        unit='spectral conc.',
        headline=headline,
        p_value=p_vib,
        how="Frame-to-frame shift jitter is Fourier-transformed, and its spectral "
            "concentration is compared against permutations of the SAME trace "
            "(which destroy periodicity but keep the amplitudes). The raw "
            "concentration depends strongly on the frame count; the p-value does not.",
        good="Jitter energy spread across frequencies, indistinguishable from a random "
             "reordering of the same jitter. A significant peak suggests a vibration "
             "source (pump, fan, footsteps).",
        diag=dict(spectrum=spec, p_value=p_vib, n_frames=n_frames,
                  axis=worst_axis, concentration_by_axis=concs))
