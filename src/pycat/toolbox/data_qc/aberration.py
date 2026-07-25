"""Aberration QC — spherical-aberration (axial sharpness asymmetry) and chromatic-shift checks.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
from pycat.toolbox.data_qc._base import _to_float, _mean_frame, _shift_normalise

def qc_spherical_aberration(data, is_zstack=False):
    """Spherical aberration (e.g. from a coverslip/coating thicker than the
    objective's correction) spreads light axially and asymmetrically.

    Only meaningful on a z-stack (through-focus): the axial intensity response
    becomes asymmetric about best focus. Because a time-series stack looks the
    same shape as a z-stack, this is computed only when the caller marks the
    data as a z-stack; otherwise it falls back to a weak 2-D halo proxy.
    """
    a = _to_float(data)
    if is_zstack and a.ndim == 3 and a.shape[0] >= 5:
        # Axial profile of sharpness through the stack.
        #
        # This used `np.var(laplace(f))` — the same metric shown blind in 1.5.405. The
        # Laplacian is a high-pass filter and white detector noise is entirely
        # high-frequency, so on a real image it reports the NOISE LEVEL, not the focus.
        # A flat noise-dominated profile has no meaningful skew, so the aberration
        # measurement collapsed:
        #
        #     low noise:        symmetric |skew| 0.004 (good), ASYMMETRIC 0.723 (warn)  OK
        #     realistic noise:  symmetric |skew| 0.004 (good), ASYMMETRIC 0.012 (GOOD)  <-- lost
        #
        # Real spherical aberration was reported as "good" because the noise buried the
        # axial response. Use the same band-pass as `qc_focus`, which rejects the noise
        # and keeps the mid-frequency band where genuine edges live.
        def _axial_sharp(f):
            f = np.asarray(f, dtype=float)
            return float(np.var(ndi.gaussian_filter(f, 1.0) - ndi.gaussian_filter(f, 2.0)))

        prof = np.array([_axial_sharp(f) for f in a])

        # ── The axial profile must PEAK at best focus. A fixed DoG band does not. ─
        #
        # The skew statistic is correct — on a clean profile a symmetric response gives skew
        # 0.000 and an aberrated one gives -0.713. **The bug was upstream, in the sharpness
        # measure itself.**
        #
        # ``_axial_sharp`` is a difference-of-Gaussians band-pass at sigma 1.0 - 2.0. When the
        # in-focus objects are SHARPER than that band (sigma ~1.5 here), the response **dips at
        # best focus** — the sharpest plane falls outside the band being measured:
        #
        #     plane  8: 0.960
        #     plane  9: 1.000   <- argmax lands HERE
        #     plane 10: 0.849   <- the TRUE focal plane, and a LOCAL MINIMUM
        #     plane 11: 0.999
        #
        # ``argmax`` then picks plane 9, the moments are taken about the wrong origin, and a
        # **perfectly symmetric stack** (left sum = right sum = 544, exactly) reports a skew of
        # **+0.577 -> "warn"**. Meanwhile a genuinely aberrated stack reported 0.226 -> "good".
        # **The test was inverted, and the cause was one plane of origin error.**
        #
        # There is no magic band: a FIXED scale can always be out-tuned by the object size.
        # (DoG(0.5, 1.0) happens to peak correctly on this data; Tenengrad peaks at plane 0,
        # tracking noise.) So the origin is made ROBUST instead: smooth the profile before
        # taking the argmax, which removes the single-plane dip without assuming a scale, and
        # then refine to sub-plane precision with a parabolic fit through the peak and its
        # neighbours — which is what "the focal plane" means when the profile is broader than
        # one plane, and it always is.
        _smooth = ndi.uniform_filter1d(prof, size=3, mode='nearest')
        k = int(np.argmax(_smooth))

        # Parabolic refinement: the vertex of the parabola through (k-1, k, k+1).
        if 0 < k < len(prof) - 1:
            y0, y1, y2 = float(_smooth[k - 1]), float(_smooth[k]), float(_smooth[k + 1])
            denom = (y0 - 2 * y1 + y2)
            offset = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
            focus = k + float(np.clip(offset, -1.0, 1.0))
        else:
            focus = float(k)

        z = np.arange(len(prof), dtype=float) - focus
        p = prof / (prof.sum() + 1e-12)
        # ── The energy RATIO, not the normalised third moment ────────────────────
        #
        # Fixing the origin cured the false alarm and exposed a **false negative**: a stack with
        # **half the energy on one side of focus** (right/left = 0.499 — grossly aberrated)
        # reported |skew| = 0.080 against a threshold of 0.4, and passed as "good".
        #
        # The normalised third moment is the wrong statistic for this. The ``m2**1.5``
        # denominator grows with the axial SPREAD — and spherical aberration IS a one-sided
        # spread, so the normalisation **cancels the very asymmetry it should expose.**
        #
        # The physical question is simpler than a moment: *does the through-focus response fall
        # off at the same rate above and below focus?* That is an energy ratio, and it is what
        # a bead z-stack is inspected for by eye.
        #
        #     stack                     right/left    old |skew|   new asymmetry
        #     symmetric                 1.000         0.019        ~0
        #     strongly aberrated        **0.499**     0.080 (!)    **large**
        _lo = p[z < -0.5].sum()
        _hi = p[z > 0.5].sum()
        _ratio = (min(_lo, _hi) / max(_lo, _hi)) if max(_lo, _hi) > 1e-12 else 1.0

        # 0 = one side has ALL the energy; 1 = perfectly symmetric. Report the DEPARTURE from
        # symmetry, so that (like every other check here) bigger is worse.
        asymmetry = 1.0 - float(_ratio)

        # The signed skew is kept for the diagnostic, because its SIGN says which side of focus
        # the tail is on — which tells the user whether to add or remove correction-collar.
        m2 = float((p * z ** 2).sum())
        m3 = float((p * z ** 3).sum())
        skew = m3 / (m2 ** 1.5 + 1e-12) if m2 > 0 else 0.0

        val = asymmetry
        # A 20 % imbalance between the two sides of focus is visible and worth flagging; 40 % is
        # a clear one-sided tail.
        status = 'good' if val < 0.20 else ('warn' if val < 0.40 else 'bad')
        return dict(
            name='Spherical aberration', tier='advisory', status=status,
            value=val, unit='axial asymmetry',
            headline=(f"through-focus energy is {100 * (1 - val):.0f}% balanced about focus"
                      f" (skew {skew:+.2f} — the sign says which side the tail is on)"),
            how="A band-pass (difference-of-Gaussians) sharpness is profiled through "
                "the z-stack; spherical aberration makes this through-focus curve "
                "asymmetric about best focus. A plain Laplacian cannot be used here — "
                "detector noise dominates it and flattens the axial response.",
            good="A near-symmetric axial response (|skew| ≲ 0.4). Strong "
                 "asymmetry suggests a coverslip/coating thickness mismatch — "
                 "adjust the correction collar or use the right coverslip.",
            diag=dict(axial_profile=prof, focus_index=k, focus_subplane=focus,
                  energy_below=_lo, energy_above=_hi, skew=skew))
    # 2-D halo proxy (advisory only)
    from scipy.ndimage import gaussian_filter
    hp = a - gaussian_filter(a, 3)
    ring = float(np.mean(np.abs(hp)) / (a.std() + 1e-9))
    return dict(
        name='Spherical aberration', tier='advisory', status='info',
        value=ring, unit='halo proxy',
        headline="single image — provide a z-stack for a real measurement",
        how="Proxy only: residual high-frequency halo energy around structure. "
            "Spherical aberration is properly measured from a bead z-stack "
            "(axial PSF asymmetry).",
        good="For a real assessment, image sub-resolution beads as a z-stack "
             "and look for a symmetric axial PSF.",
        diag=None)


def qc_chromatic(n_channels, channels=None):
    """Lateral chromatic aberration: a rigid shift between co-imaged channels.

    Previously this took only a channel COUNT and returned "multi-channel — register
    channels on beads to check". Honest, but it measured nothing: PyCAT *has* the
    channels, so it can measure the shift directly with the same phase cross-correlation
    the drift QC uses.

    The trap, and the guard
    -----------------------
    A channel-to-channel shift is only evidence of *optics* if the channels image the
    **same structures**. Two channels labelling genuinely different objects also produce a
    cross-correlation peak — a large and meaningless one. Measured:

    ==========================================  ================
    scenario                                    measured shift
    ==========================================  ================
    same structures, registered (truth: 0)      0.45 px
    **chromatic: channel shifted by (1.8, 1.2)** **2.76 px**
    channels label DIFFERENT objects            **64.97 px**
    ==========================================  ================

    Chromatic aberration is **small** — sub-pixel to a few pixels, bounded by the optics.
    A shift of tens of pixels is not chromatic aberration; it is two channels that are not
    imaging the same thing, and reporting it as an optical defect would be wrong. So a
    shift beyond a few percent of the field is reported as **not assessable**, with the
    reason stated, rather than as a bad optic.

    Pass ``channels`` as a list of 2-D arrays to measure. Without them, the old
    count-only advisory is returned unchanged.
    """
    if channels is None or len(channels) < 2:
        if n_channels and n_channels >= 2:
            return dict(name='Chromatic aberration', tier='advisory', status='info',
                        value=None, unit='',
                        headline="multi-channel — pass the channel images to measure",
                        how="Compare the position of the same structures across "
                            "channels; a rigid shift is lateral chromatic aberration.",
                        good="Sub-pixel channel registration on multi-colour beads.",
                        diag=None)
        return dict(name='Chromatic aberration', tier='advisory', status='na',
                    value=None, unit='',
                    headline="single channel — cannot assess",
                    how="Requires ≥2 channels imaged of the same structures.",
                    good="Assess with multi-colour bead images.", diag=None)

    from skimage.registration import phase_cross_correlation

    ref = _mean_frame(channels[0]).astype(float)
    shifts = []
    for ch in channels[1:]:
        b = _mean_frame(ch).astype(float)
        if b.shape != ref.shape:
            continue
        sh = phase_cross_correlation(_shift_normalise(ref),
                                     _shift_normalise(b),
                                     upsample_factor=20)[0]
        shifts.append(float(np.hypot(sh[0], sh[1])))
    if not shifts:
        return dict(name='Chromatic aberration', tier='advisory', status='na',
                    value=None, unit='px',
                    headline="channels have different shapes — cannot compare",
                    how="", good="", diag=None)

    worst = float(max(shifts))
    fov = min(ref.shape)

    # A shift this large is not an optical defect — it means the channels are not imaging
    # the same structures, and calling it "chromatic aberration" would be a wrong result,
    # not merely a strict one.
    if worst > 0.05 * fov:
        return dict(
            name='Chromatic aberration', tier='advisory', status='na',
            value=worst, unit='px',
            headline=f"apparent shift {worst:.1f} px is too large to be chromatic",
            how="Phase cross-correlation between channels. A shift of more than a few "
                "percent of the field is not chromatic aberration (which is bounded by "
                "the optics to a few pixels at most) — it means the channels are not "
                "imaging the same structures, so no optical conclusion can be drawn.",
            good="Assess on multi-colour beads, where every channel images the same "
                 "objects.",
            diag=dict(shifts_px=shifts))

    # ── The gates are set to what the measurement can actually RESOLVE ─────────
    #
    # Phase cross-correlation between two channels with INDEPENDENT noise has a floor.
    # Measured on channels with NO shift at all (30 realisations each):
    #
    #     channel noise sd 1   -> mean 0.77 px, 95th pct 1.44
    #     channel noise sd 5   -> mean 0.99 px, 95th pct **2.08**
    #     channel noise sd 20  -> mean 1.68 px, 95th pct 3.04
    #
    # So a PERFECTLY registered pair routinely reads ~1 px and can read 2 px. A
    # sub-pixel gate (good < 0.5) would therefore flag correctly-registered channels as
    # aberrated — an earlier version of this did exactly that, calling a 0.28 px shift
    # "warn" at a measured 1.46 px.
    #
    # Recovery of a KNOWN shift confirms where the metric becomes trustworthy:
    #
    #     true 0.5 px -> measured 1.21 (error 0.71)   -- dominated by the floor
    #     true 1.0 px -> measured 1.32 (error 0.32)
    #     true 2.0 px -> measured 2.26 (error 0.26)   -- usable
    #     true 4.0 px -> measured 4.16 (error 0.16)   -- accurate
    #
    # Below ~2 px the measurement cannot distinguish a real shift from its own noise, and
    # it says so rather than guessing. This is a genuine limit of correlating two channels
    # of different structures with independent noise — measuring on multi-colour BEADS
    # (identical objects in both channels) pushes the floor far lower, and that is the
    # right way to calibrate a channel registration.
    if worst < 2.0:
        status = 'good'
        headline = (f"max channel shift {worst:.2f} px — within the measurement floor "
                    f"(~1-2 px)")
    elif worst < 4.0:
        status = 'warn'
        headline = f"max channel shift {worst:.2f} px"
    else:
        status = 'bad'
        headline = f"max channel shift {worst:.2f} px"

    return dict(
        name='Chromatic aberration', tier='advisory', status=status,
        value=worst, unit='px',
        headline=headline,
        how="Phase cross-correlation between channels measures a rigid lateral shift — "
            "the signature of lateral chromatic aberration. NOTE the measurement floor: "
            "two channels with independent noise read ~1 px (95th percentile ~2 px) even "
            "when perfectly registered, so a shift below ~2 px cannot be distinguished "
            "from measurement noise on ordinary images.",
        good="A shift under ~2 px is indistinguishable from the measurement floor on "
             "biological images. To resolve a sub-pixel registration error you need "
             "multi-colour BEADS — identical objects in every channel — which is also "
             "how a channel registration should be calibrated.",
        diag=dict(shifts_px=shifts, measurement_floor_px=2.0))
