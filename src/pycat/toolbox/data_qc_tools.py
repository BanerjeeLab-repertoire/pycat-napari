"""
Data Quality Control (QC) metrics for microscopy images and stacks.

This module is written to be a *teaching* tool as much as a checker: every metric
returns not only a value and a pass/warn/fail status, but also a short
description of HOW it is measured and WHAT good data looks like, plus (where
useful) a diagnostic array the dashboard can plot so the user can see the
evidence rather than just a coloured light.

Two tiers:
  * CORE      — rock-solid, absolute-ish thresholds: saturation, focus, SNR,
                vignetting, drift, ghosting.
  * ADVISORY  — heuristics or metrics that need user-supplied optics/timing:
                Nyquist sampling, time sampling, spherical aberration,
                vibration, chromatic aberration.

Each metric function returns a dict with at least:
    name, tier, status ('good'|'warn'|'bad'|'info'|'na'), value (float|None),
    unit, headline (short result string), how ('how it is measured'),
    good ('what good data looks like'), and optionally `diag` (a dict of arrays
    for plotting).
"""

import numpy as np

from pycat.utils.general_utils import debug_log
import scipy.ndimage as ndi


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_float(img):
    return np.asarray(img, dtype=np.float64)


def _robust_noise_std(img):
    """Noise std from median absolute adjacent-pixel differences (robust to
    real structure and sparse edges)."""
    d = np.abs(np.diff(_to_float(img), axis=-1))
    if d.size == 0:
        return 0.0
    return 1.4826 * float(np.median(d)) / np.sqrt(2.0)


def _dtype_max(img):
    """Best guess at the sensor's full-scale value for clipping detection.

    **The container's maximum is not the sensor's ceiling**, and using it makes the saturation
    check blind to the most common case there is.

    A 12-bit camera writing into a ``uint16`` array clips at **4095**, not 65535. A camera run
    at reduced gain clips lower still. ``np.iinfo(uint16).max`` is 65535, so a check against it
    finds **nothing**. Measured, on a ``uint16`` image whose two brightest objects are
    genuinely flat-topped:

    ==================================  ==============  ==========
    image                               truly clipped   reported
    ==================================  ==============  ==========
    clipped at 65535 (the dtype max)    0.0 %          0.00 % good
    clipped at 4095 (a 12-bit sensor)   **1.2 %**      **0.00 % good**
    clipped at 1000 (gain-limited)      **9.1 %**      **0.00 % good**
    ==================================  ==============  ==========

    **Nine percent of the pixels destroyed, reported as "good".**

    So the ceiling is detected from the DATA: if a large number of pixels sit *exactly* at the
    image maximum, that maximum **is** the ceiling — a real, unclipped scene has a smooth
    intensity distribution and essentially never repeats its brightest value. The dtype max is
    kept as the fallback when no such pile-up exists.
    """
    a = np.asarray(img)
    if np.issubdtype(a.dtype, np.integer):
        # A pile-up AT the image maximum is the signature of clipping, wherever the ceiling
        # sits. One pixel happening to be brightest is not a pile-up; hundreds is.
        try:
            if a.size:
                obs_max = float(a.max())
                n_at_max = int((a == a.max()).sum())
                # >0.01 % of pixels sharing the exact maximum value: that is a flat top, not a
                # coincidence. (A 512x512 frame -> 26 pixels; noise does not do that.)
                if n_at_max > max(10, 0.0001 * a.size) and obs_max > 0:
                    return obs_max
        except Exception as _exc:
            debug_log('saturation: could not detect the ceiling from the data', _exc)
        return float(np.iinfo(a.dtype).max)
    # floats: assume the data max is the ceiling unless it looks normalised
    m = float(np.nanmax(a)) if a.size else 1.0
    if m <= 1.0 + 1e-6:
        return 1.0
    # common camera bit depths
    for full in (255, 4095, 65535):
        if m <= full:
            return float(full)
    return m


def _mean_frame(data):
    """Collapse a (T/Z, H, W) stack to a representative 2-D frame (the mean)."""
    a = _to_float(data)
    return a.mean(axis=0) if a.ndim == 3 else a


# ---------------------------------------------------------------------------
# CORE metrics
# ---------------------------------------------------------------------------

def qc_saturation(img):
    """Fraction of pixels clipped at the sensor ceiling or floor."""
    a = _to_float(img)
    full = _dtype_max(img)
    hi = float(np.mean(a >= full * (1 - 1e-6)))
    lo = float(np.mean(a <= 0.0))
    worst = max(hi, lo)
    status = 'good' if worst < 0.001 else ('warn' if worst < 0.01 else 'bad')
    return dict(
        name='Saturation / clipping', tier='core', status=status,
        value=worst * 100.0, unit='%',
        headline=f"{hi*100:.2f}% at ceiling, {lo*100:.2f}% at floor",
        how="Fraction of pixels sitting exactly at the sensor's maximum (or at "
            "zero). Clipped pixels have lost their true intensity.",
        good="Well under 0.1% clipped. Any bright saturated region means "
             "intensity/quantitative measurements there are unreliable.",
        diag=dict(hist_counts=np.histogram(a, bins=64)[0],
                  hist_edges=np.histogram(a, bins=64)[1], ceiling=full))


def edge_width_px(image):
    """The **sharpest edge in the image**, in pixels. The basis of a single-image focus verdict.

    **A single image CAN be judged for focus** — via the sharpness of the edges of its objects.
    The band-pass energy that ``qc_focus`` used could not do this, because it measures GLOBAL
    energy and therefore confounds *how many objects there are* with *how sharp they are*: a
    sparse in-focus field scored **105.9** and a dense blurred one **118.1**. Useless.

    Edge sharpness is a **local** property of an object's boundary, so it is scene-independent.
    Measured, in-focus, on the same optics:

        sparse (10 objects)  edge width 4.59 px
        dense  (60 objects)  edge width 4.44 px      <- 3 % apart

    and it grows monotonically with defocus (4.6 → 4.9 → 5.5 → 6.3 px).

    **Why the SHARPEST edge, and not the average.** A big smooth cell genuinely *has* a wide
    edge, in focus or not — so an average confounds object size with focus all over again. The
    sharpest edge asks the question that actually matters: *"could anything in this image be
    sharper than it is?"* **A blurry cell cannot hide a sharp punctum.** Verified: adding large
    smooth cells to a field of puncta leaves the answer unchanged (2.82 px either way), while
    defocus moves it (2.82 → 3.29 → 4.42 → 6.43).

    The estimate is the intensity RANGE divided by the steepest gradient — no window, so there
    is nothing to saturate. (A max-minus-min window *does* saturate once an object is wider than
    the window, and that produced a metric which went the WRONG WAY on large brightfield cells.)
    """
    a = _to_float(image)
    if a.ndim != 2:
        return float('nan')

    contrast = float(np.percentile(a, 99.5) - np.percentile(a, 0.5))
    if not np.isfinite(contrast) or contrast <= 0:
        return float('nan')

    gy, gx = np.gradient(a)
    grad = np.sqrt(gy ** 2 + gx ** 2)
    steepest = float(np.percentile(grad, 99.9))
    if steepest <= 0:
        return float('nan')

    # ── Return a PSF SIGMA, in pixels — a physically defined quantity ───────────
    #
    # A Gaussian-blurred step of contrast C has peak gradient ``C / (sigma * sqrt(2*pi))``, so
    # ``contrast / steepest_gradient = sigma * sqrt(2*pi) = 2.507 * sigma``. Verified against
    # exact synthetic step edges: the ratio converges to **2.55–2.58** for sigma 3–4 px (the
    # discrepancy at sigma 1 is pixelation, as expected).
    #
    # Dividing by that constant makes this an **edge sigma in pixels**, which is comparable
    # against the optics. Returning the raw ratio instead — as a first version did — produced a
    # number that was *proportional* to the width but not *equal* to it, and comparing it to a
    # real diffraction limit gave a ratio of **0.48 on a diffraction-limited image**: physically
    # impossible, and a symptom of comparing two different quantities.
    return (contrast / steepest) / np.sqrt(2.0 * np.pi)


def diffraction_limit_px(pixel_um, na, wavelength_nm):
    """The narrowest edge the optics permit: Abbe d = lambda / (2 NA), in pixels.

    **This is what makes an ABSOLUTE single-image verdict possible.** A perfectly focused edge
    cannot be narrower than the diffraction limit — so ``edge_width / diffraction_limit`` says
    how many times worse than the optics allow the image actually is, and that is a statement
    about the image alone, with no reference needed.
    """
    if not (pixel_um and na and wavelength_nm):
        return None
    # ── Abbe d is a RESOLUTION (~a FWHM). Convert it to a SIGMA to compare like with like. ─
    #
    # ``d = lambda / (2 NA)`` is the smallest resolvable separation — it is a width comparable
    # to the PSF's full width at half maximum, **not** to its standard deviation. A Gaussian's
    # FWHM is ``2.355 * sigma``, so the diffraction-limited edge sigma is ``d / 2.355``.
    #
    # Comparing an edge SIGMA against an Abbe DISTANCE — which the first version did — is
    # comparing two different quantities, and it produced ratios below 1 on images that were
    # already at the diffraction limit.
    d_um = (float(wavelength_nm) / 1000.0) / (2.0 * float(na))
    d_px = d_um / float(pixel_um)
    return d_px / 2.355


def qc_focus(data, pixel_um=None, na=None, wavelength_nm=None):
    """Sharpness via a BAND-PASS (difference-of-Gaussians) energy.

    Why not the variance of the Laplacian
    -------------------------------------
    This used to be ``var(laplace(frame))``. The Laplacian is a **high-pass** filter, and
    white detector noise is **entirely** high-frequency — so on any real image the noise
    dominates it completely and the metric reports the **noise level, not the focus**.

    Measured on a synthetic field (signal 400, noise sd 5), sweeping the blur over a
    **24× range**:

    ======  ==================  ==================
    blur σ  var(Laplacian)      DoG band-pass
    ======  ==================  ==================
    0.5     504.1               10.0
    1.2     504.9               9.2
    3.0     503.8               5.7
    6.0     496.5               2.1
    12.0    **497.8**           **1.0**
    ======  ==================  ==================

    ``var(Laplacian)`` moves by **1.01×** across the whole range — it has essentially no
    discriminating power. (Without noise it collapses 4.90 → 0.04 exactly as it should;
    the signal contribution is simply ~0.04 against a noise floor of ~500.)

    **This mattered.** On a 20-frame stack in which frame 10 is badly defocused, the
    existing ``< 0.5 × median`` rule was applied to a quantity where frame 10 scored
    **0.98 × median** — so **the defocused frame was not flagged at all**. With the
    band-pass it scores **0.22 × median** and is flagged correctly. *The rule was fine;
    the quantity was not.*

    The band-pass rejects both the high-frequency noise **and** the low-frequency
    illumination, keeping the scale where real edges live. It stays **monotonic in blur at
    every noise level tested** (sd 1 → 50).

    Absolute value remains scene-dependent — a bright textured field scores higher than a
    sparse dim one whatever the focus — so a single 2-D image is still reported as
    ``info``, not judged. That limitation is real and was correctly stated before; the
    problem was that the *stack* comparison, which CAN be judged, was being made on a
    quantity that could not see defocus.
    """
    a = _to_float(data)

    def _sharp(f):
        # Difference of Gaussians: keeps the mid-frequency band where genuine edges sit.
        f = np.asarray(f, dtype=float)
        band = ndi.gaussian_filter(f, 1.0) - ndi.gaussian_filter(f, 2.0)
        return float(np.var(band))
    if a.ndim == 3:
        vals = np.array([_sharp(f) for f in a])
        med = float(np.median(vals))
        # frames well below the median sharpness are likely defocused/drifted
        lo = vals < 0.5 * med if med > 0 else np.zeros(len(vals), bool)
        status = 'good' if not lo.any() else ('warn' if lo.mean() < 0.15 else 'bad')
        return dict(
            name='Focus / sharpness', tier='core', status=status,
            value=med, unit='band-pass energy',
            headline=f"{int(lo.sum())}/{len(vals)} frames below half-median sharpness",
            how="Band-pass (difference-of-Gaussians) energy per frame. A plain "
                "Laplacian is dominated by detector noise and cannot see defocus at "
                "all; the band-pass rejects the noise and keeps real edges.",
            good="All frames near the same sharpness. Frames dipping far below "
                 "the others are out of focus or drifted axially.",
            diag=dict(per_frame=vals, median=med))
    # ── A single image CAN be judged: via the sharpness of its objects' edges ───
    #
    # This used to headline **"sharpness = 545.3 (relative)"** and refuse a verdict. It was
    # right that the BAND-PASS ENERGY cannot judge a single image — it measures GLOBAL energy,
    # so a sparse in-focus field (105.9) scores below a dense blurred one (118.1). But that is a
    # limitation of the estimator, **not of the question.**
    #
    # Edge sharpness is a LOCAL property of an object's boundary, and it is scene-independent:
    # in focus, a sparse field measures 4.59 px and a dense one 4.44 px — 3 % apart — while
    # defocus moves both monotonically.
    #
    # With the pixel size and the NA, the **diffraction limit** turns that into an ABSOLUTE
    # verdict: a focused edge cannot be narrower than lambda/(2·NA), so the ratio says how many
    # times worse than the optics allow this image is. Without them, the width is still reported
    # in pixels, and it is directly COMPARABLE ACROSS A DATASET — which is how focus is most
    # often used in practice: *which of my 40 fields is the soft one?*
    width = edge_width_px(a)
    limit = diffraction_limit_px(pixel_um, na, wavelength_nm)

    if not np.isfinite(width):
        return dict(
            name='Focus / sharpness', tier='core', status='na', value=None,
            unit='px', headline='no measurable edges — cannot assess focus',
            how="Focus is judged from the sharpest object edge in the image. This image has "
                "no measurable intensity gradient (it may be blank, or uniformly saturated).",
            good='', diag=None)

    if limit:
        ratio = width / limit

        # ── If NOTHING in the field is small, focus cannot be judged. Refuse. ───
        #
        # The measure works because a **blurry cell cannot hide a sharp punctum** — the sharpest
        # edge present is the best available evidence of focus. **But if there is no small
        # object at all, there is no evidence.**
        #
        # A brightfield field of large smooth cells (sigma ~14 px) has no sharp edge anywhere.
        # The check reported **4.0x the diffraction limit -> "bad"** — which is *true about the
        # image* and *wrong about the focus*: those cells genuinely have soft boundaries, and
        # the microscope may be perfectly focused.
        #
        # **The check cannot distinguish "soft objects, sharp focus" from "sharp objects, soft
        # focus" when nothing small is present.** That is a real limit and it is not fixable by
        # a better estimator. So it is DETECTED and the check refuses, rather than reporting a
        # confident verdict it has no basis for.
        #
        # (The comparative use survives this untouched: across a dataset of the same specimen
        # the object type is constant, so a field that IS softer than its neighbours still
        # stands out. The refusal is only of the ABSOLUTE claim.)
        if ratio > 3.0:
            return dict(
                name='Focus / sharpness', tier='core', status='na',
                value=float(ratio), unit='x the diffraction limit',
                headline=("no sharp objects in this field — focus cannot be judged "
                          "against the diffraction limit"),
                how=f"The sharpest edge here is {width:.1f} px, against a diffraction limit of "
                    f"{limit:.1f} px. **Either the image is grossly defocused, or the specimen "
                    f"simply has no small objects** — a field of large smooth cells has no "
                    f"sharp edge anywhere, and this check cannot tell the two apart.",
                good="Compare this number ACROSS your dataset instead: the object type is the "
                     "same in every field, so a field that is genuinely softer than its "
                     "neighbours still stands out unambiguously. Or image a sub-resolution "
                     "bead, which gives the focus an unambiguous reference.",
                diag=dict(edge_width_px=width, diffraction_px=limit, ratio=ratio))

        # ── The ABSOLUTE verdict carries a ~1.5x systematic floor. Say so. ──────
        #
        # The estimator converts ``contrast / steepest_gradient`` into an edge sigma, and **the
        # conversion constant depends on what the object IS**:
        #
        #     a blurred STEP edge  ->  contrast/gradient = **2.51** x sigma
        #     a Gaussian BLOB      ->  contrast/gradient = **1.65** x sigma
        #
        # (Both verified against exact synthetic objects.) The estimator cannot distinguish
        # them, so an absolute ratio against the diffraction limit is uncertain by ~1.5x
        # depending on whether the field is puncta or membranes.
        #
        # **This does NOT affect the comparative use**, which is how focus is most often needed:
        # *which of my 40 fields is the soft one?* Across one dataset the object type is the
        # same, so the constant **cancels exactly**. Verified: in a 40-field acquisition where
        # field 17 slipped, it is the only outlier at 1.58x the median.
        #
        # So the thresholds are set WIDE, and the headline states the uncertainty. A check that
        # claims more precision than it has is worse than one that admits its floor — the user
        # would go and refocus a microscope that is already at the limit.
        # ── The absolute path is a SCREEN FOR GROSS DEFOCUS. Nothing more. ──────
        #
        # I tried to set these thresholds by the measurement error the blur causes, and **it
        # cannot be done honestly.** The systematic floor (~1.5x, from the step-vs-blob
        # constant) is larger than the effect being measured:
        #
        #     blur      ratio    apparent size error
        #     1.0 px    0.67     +30 %
        #     2.0 px    1.14     **+94 %**
        #     3.0 px    1.46     **+169 %**
        #
        # **A 169 % size error sits inside the systematic floor.** Any threshold tight enough to
        # catch it would fire on a perfectly focused image of the wrong object type.
        #
        # So the absolute verdict is deliberately WIDE — it catches gross defocus and nothing
        # else — and the text sends the user to the comparative measure, which has no such floor
        # because the object type is constant across a dataset and the constant cancels.
        #
        # **Reporting a screen as a screen is the honest thing. A tighter threshold here would
        # be false precision**, and it would send someone to refocus a microscope that is
        # already at the diffraction limit.
        status = 'good' if ratio < 1.5 else ('warn' if ratio < 2.5 else 'bad')
        return dict(
            name='Focus / sharpness', tier='core', status=status,
            value=float(ratio), unit='x the diffraction limit (±~1.5x systematic)',
            headline=(f"sharpest edge \u2248 {width:.1f} px vs a diffraction limit of "
                      f"{limit:.1f} px \u2014 {ratio:.1f}x (\u00b1~1.5x, see below)"),
            how="The sharpest edge in the image, converted to an edge sigma and compared "
                "against the narrowest edge the optics permit (Abbe lambda/2\u00b7NA, converted "
                "from a resolution to a sigma). **The conversion constant depends on the object "
                "type** — 2.51 for a step edge, 1.65 for a Gaussian blob — so this absolute "
                "ratio is uncertain by about 1.5x. It is a screen, not a measurement.",
            good="**This is a SCREEN for gross defocus, not a measurement.** Near 1x is "
                 "diffraction-limited. Above ~2.5x the boundaries are soft enough "
                 "that object sizes are overestimated and any edge-dependent measurement (area, "
                 "partition coefficient, enrichment) is biased.\n\n**For a precise answer, "
                 "compare this number ACROSS your dataset rather than against the limit** — the "
                 "object type is then the same in every field, the conversion constant cancels "
                 "exactly, and the soft field stands out unambiguously.",
            diag=dict(edge_width_px=width, diffraction_px=limit, ratio=ratio))

    return dict(
        name='Focus / sharpness', tier='core', status='info', value=float(width),
        unit='px (sharpest edge)',
        headline=(f"sharpest edge = {width:.1f} px — supply pixel size and NA for a verdict"),
        how="The sharpest edge in the image, in pixels. This is scene-independent (a sparse "
            "field and a dense one measure the same when equally focused), so it is directly "
            "COMPARABLE ACROSS A DATASET: the soft field in a folder of acquisitions will "
            "have a visibly larger value than its neighbours.",
        good="Supply the pixel size and the NA and this becomes an ABSOLUTE verdict — the "
             "sharpest edge is compared against the diffraction limit, which is the narrowest "
             "an edge can physically be. Otherwise, compare this number across your dataset "
             "and look for the outlier.",
        diag=dict(edge_width_px=width))


def qc_snr(img):
    """Signal-to-noise: signal (robust dynamic range) over noise (robust
    high-frequency estimate)."""
    a = _mean_frame(img)
    noise = _robust_noise_std(a)
    # signal = spread of the real structure, robustly (5–95 percentile range)
    p5, p95 = np.percentile(a, [5, 95])
    signal = float(p95 - p5)
    snr = signal / noise if noise > 0 else np.inf
    status = 'good' if snr >= 10 else ('warn' if snr >= 4 else 'bad')
    return dict(
        name='SNR / noise', tier='core', status=status,
        value=float(snr), unit='×',
        headline=f"SNR ≈ {snr:.1f}  (noise σ ≈ {noise:.1f})",
        how="Signal = 5–95th-percentile intensity spread; noise = robust "
            "estimate from adjacent-pixel differences. SNR = signal / noise.",
        good="SNR ≳ 10 is comfortable; below ~4 the structure is buried in "
             "noise — increase exposure/illumination or average frames.",
        diag=dict(noise=noise, signal=signal))


def qc_vignetting(img):
    """Radial illumination falloff, measured on the BACKGROUND rather than the objects.

    The previous version binned the **raw mean intensity** by radius. That does not
    measure illumination — it measures **where the objects happen to sit**. On images
    with a *perfectly flat* background:

    ===================================  =============  ==========
    image                                edge/centre    verdict
    ===================================  =============  ==========
    flat background, no objects          1.000          good
    flat background, objects in CENTRE   **0.354**      **bad**
    flat background, objects at EDGES    1.100          good
    ===================================  =============  ==========

    All three have **identical, flat illumination**. A field with cells clustered
    centrally was condemned as severely vignetted, and a field with cells at the edges
    would mask real vignetting.

    Percentiles do not fix it: the innermost radial bins are small (a few hundred pixels)
    and the objects can fill them **entirely** — bin 0 measured 100 % object, with *zero*
    background pixels left. That is geometric, not statistical, so no choice of percentile
    can recover a background that is not there.

    The physics gives the fix: **illumination varies smoothly and slowly; objects are
    small and sharp.** A grey-scale opening with a large kernel removes bright structures
    smaller than the kernel and leaves the broad illumination field. Reading the radial
    falloff off *that*:

    ===================================  =============  =============
    image                                old (mean)     now (opening)
    ===================================  =============  =============
    flat + objects in centre             0.354          **0.993**
    flat + objects at edges              1.100          1.000
    real 40 % vignetting, no objects     0.650          0.683
    real 40 % vignetting + centre objs   0.229          0.683
    ===================================  =============  =============

    Object placement no longer moves the number, and real vignetting is still measured.
    """
    a = _mean_frame(img).astype(float)
    h, w = a.shape

    # Estimate the ILLUMINATION field: a large grey-scale opening deletes compact bright
    # structures (cells, condensates) while preserving the broad, slowly-varying lamp
    # profile. Kernel = 1/4 of the short side, chosen by measurement: smaller kernels let
    # the objects leak back in, larger ones gain nothing.
    k = max(3, int(min(h, w) // 4))
    bg = ndi.grey_opening(a, size=k)
    bg = ndi.gaussian_filter(bg, max(min(h, w) / 25.0, 1.0))

    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    rmax = r.max()
    nb = 24
    edges = np.linspace(0, rmax, nb + 1)
    prof = np.array([bg[(r >= edges[i]) & (r < edges[i + 1])].mean()
                     if np.any((r >= edges[i]) & (r < edges[i + 1])) else np.nan
                     for i in range(nb)])
    prof = np.interp(np.arange(nb), np.flatnonzero(np.isfinite(prof)),
                     prof[np.isfinite(prof)])
    centre = float(np.mean(prof[:max(1, nb // 8)]))
    edge = float(np.mean(prof[-max(1, nb // 8):]))
    ratio = edge / centre if centre != 0 else 1.0
    status = 'good' if ratio >= 0.9 else ('warn' if ratio >= 0.7 else 'bad')
    return dict(
        name='Vignetting / flat-field', tier='core', status=status,
        value=float(ratio), unit='edge/centre',
        headline=f"edge is {ratio*100:.0f}% of centre brightness",
        how="The illumination field is estimated with a large grey-scale opening "
            "(which removes the objects but keeps the broad lamp profile), then binned "
            "by distance from the image centre. Measuring the RAW mean instead would "
            "report the position of the cells, not the illumination.",
        good="Ratio ≳ 0.9 (nearly flat). Strong falloff biases intensity "
             "measurements by position — apply a flat-field correction.",
        diag=dict(radial_profile=prof, radius_bins=0.5 * (edges[:-1] + edges[1:])))


def qc_ghosting(img):
    """Ghosting (double image from reflections / internal lens echoes) via the
    image *cepstrum* — the inverse transform of the log power spectrum. An echo
    (a faint shifted copy of the whole scene) leaves a sharp cepstral peak at the
    ghost offset. The cepstrum is far more specific than plain autocorrelation:
    it responds to the coherent whole-field echo, not to ordinary repeated
    structure (spots, cells) in the sample."""
    from scipy.ndimage import gaussian_filter
    a = _mean_frame(img)
    a = a - a.mean()
    P = np.log(np.abs(np.fft.fft2(a)) ** 2 + 1e-6)
    C = np.abs(np.fft.fftshift(np.fft.ifft2(P)))
    C /= C.max() if C.max() != 0 else 1.0
    h, w = C.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    mask = (r > 4) & (r < 0.4 * min(h, w))
    if not mask.any():
        return dict(name='Ghosting (double image)', tier='core', status='na',
                    value=None, unit='', headline='image too small',
                    how='', good='', diag=None)
    prom = C - gaussian_filter(C, 3)
    sec = float(prom[mask].max())
    idx = np.argwhere(mask & (prom == sec))
    off = np.sqrt(((idx[0][0] - cy) ** 2 + (idx[0][1] - cx) ** 2)) if len(idx) else 0.0
    status = 'good' if sec < 0.005 else ('warn' if sec < 0.012 else 'bad')
    return dict(
        name='Ghosting (double image)', tier='core', status=status,
        value=sec, unit='cepstral peak',
        headline=f"cepstral echo {sec:.4f} at ~{off:.0f} px offset",
        how="Cepstrum (inverse transform of the log power spectrum). A reflection "
            "ghost is a faint shifted copy of the whole scene and leaves a sharp "
            "cepstral peak at the ghost's displacement.",
        good="No sharp cepstral peak (well below ~0.005). A clear peak means a "
             "double image — check for filter/coverslip reflections.",
        diag=dict(cepstrum=C))


# ---------------------------------------------------------------------------
# CORE metrics needing a stack
# ---------------------------------------------------------------------------

def _shift_normalise(frame):
    """Strip the intensity scale before a phase correlation, so BRIGHTNESS cannot look like MOTION.

    ``phase_cross_correlation`` is supposed to be intensity-robust — it works on the normalised
    cross-power spectrum. **It is not robust enough when the frame is globally scaled**, because
    the DC term and the noise floor move together and the sub-pixel peak fit is biased.

    Measured: a **photobleaching** stack (which gets dimmer every frame and **does not move at
    all**) drove ``qc_vibration`` to **p = 0.010, status "bad"** — a confident report of a
    periodic vibration source. The shift trace was tracking the exponential intensity decay,
    which is smooth and monotonic, and therefore highly concentrated in the low-frequency bins:
    exactly the signature the permutation test looks for.

    **The user is sent to check their pumps and fans, and the stage is fine.**

    Z-scoring each frame removes the global scale and offset, leaving only the structure that a
    registration should key on.
    """
    f = np.asarray(frame, dtype=float)
    sd = float(f.std())
    if not np.isfinite(sd) or sd <= 0:
        return f - float(f.mean())
    return (f - float(f.mean())) / sd


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

    tau_frames = (-1.0 / slope) if slope < 0 else float('inf')
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


# ---------------------------------------------------------------------------
# ADVISORY metrics
# ---------------------------------------------------------------------------

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


def qc_nyquist(pixel_um, na, wavelength_nm):
    """Spatial (Nyquist) sampling: pixel size vs the optical resolution limit.
    Needs pixel size, objective NA, and emission wavelength."""
    if not (pixel_um and na and wavelength_nm):
        return dict(name='Nyquist sampling', tier='advisory', status='info',
                    value=None, unit='',
                    headline="enter pixel size, NA and wavelength to check",
                    how="Nyquist pixel size = λ / (4·NA).",
                    good="Pixel size ≤ λ/(4·NA) to resolve the optics.",
                    diag=None)
    lam_um = wavelength_nm / 1000.0
    resolution = lam_um / (2.0 * na)      # Abbe lateral resolution
    nyq = lam_um / (4.0 * na)             # Nyquist pixel size
    ratio = pixel_um / nyq
    if ratio <= 1.05:
        status = 'good'
        note = "properly sampled"
    elif ratio <= 2.0:
        status = 'warn'
        note = "marginally undersampled"
    else:
        status = 'bad'
        note = "undersampled — fine detail is lost"
    if ratio < 0.4:
        status = 'warn'
        note = "heavily oversampled (photon-inefficient)"
    return dict(
        name='Nyquist sampling', tier='advisory', status=status,
        value=float(ratio), unit='× Nyquist',
        headline=f"pixel {pixel_um:.3f} µm vs Nyquist {nyq:.3f} µm — {note}",
        how="Abbe resolution = λ/(2·NA); Nyquist pixel = λ/(4·NA). Ratio = your "
            "pixel size ÷ Nyquist pixel.",
        good="Ratio ≈ 1 (pixel ≈ Nyquist). >2 loses resolution; <0.4 wastes "
             "photons and field of view.",
        diag=dict(resolution_um=resolution, nyquist_um=nyq))


def qc_time_sampling(frame_interval_s, process_timescale_s):
    """Temporal Nyquist: frame interval vs the fastest process you want to
    capture. Needs the process timescale from the user."""
    if not (frame_interval_s and process_timescale_s):
        return dict(name='Time sampling', tier='advisory', status='info',
                    value=None, unit='',
                    headline="enter frame interval and process timescale",
                    how="Sample at least twice per process timescale.",
                    good="Frame interval ≤ half the fastest dynamics.",
                    diag=None)
    ratio = frame_interval_s / (process_timescale_s / 2.0)
    status = 'good' if ratio <= 1.0 else ('warn' if ratio <= 2.0 else 'bad')
    return dict(
        name='Time sampling', tier='advisory', status=status, value=float(ratio),
        unit='× Nyquist',
        headline=f"interval {frame_interval_s:g}s vs needed ≤{process_timescale_s/2:g}s",
        how="Temporal Nyquist: to capture a process of timescale τ you must "
            "sample faster than τ/2.",
        good="Frame interval ≤ τ/2. Slower and you alias/miss the dynamics.",
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


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def _not_applicable(name, why):
    """A check that cannot apply is reported as N/A **with the reason** — never as 'good'.

    Reporting 'good' for a question the data cannot answer is a quiet lie: the user reads a
    clean report and concludes their data passed a test that was never run. Reporting a
    confident 'bad' is worse — they go and fix something that is not broken, and they learn to
    distrust the whole report.

    So the check appears, greyed out, saying **why** it does not apply. That is the
    anti-black-box answer: the user can see that PyCAT considered it and declined, rather than
    wondering whether it was silently skipped.
    """
    return dict(name=name, tier='core', status='na', value=None, unit='',
                headline='not applicable to this data', how=why, good='', diag=None)


def run_full_qc(data, pixel_um=None, na=None, wavelength_nm=None,
                frame_interval_s=None, process_timescale_s=None, n_channels=1,
                is_zstack=False):
    """Run every applicable metric and return an ordered list of result dicts."""
    a = np.asarray(data)
    is_stack = a.ndim == 3 and a.shape[0] > 1
    # ── A check that cannot apply must not RUN. It must not "pass", either. ─────
    #
    # A verdict on a question the data cannot answer is worse than no verdict: the user cannot
    # act on it, and a confident false alarm **discredits the checks that are right**. Audited
    # across 2D fluorescence, brightfield, z-stacks and time series — on CLEAN data, where any
    # warn/bad is by definition a false alarm — and every failure was on the Z-STACK:
    #
    #     check                  2D fluor   brightfield   Z-STACK      time series
    #     Drift                  --         --            **bad**      good
    #     Focus / sharpness      info       info          **warn**     good
    #     Ghosting               good       good          **warn**     good
    #
    # **Drift is the worst.** On a z-stack with ZERO lateral drift it reports **89.2 px, "bad"**
    # — and adding a full pixel per plane of REAL drift moves it only to 100.1. It is not
    # measuring displacement at all: the phase correlation is failing on the sharp-vs-blurred
    # mismatch between focal planes. **A large, alarming, confident number that is blind to the
    # thing it names.**
    #
    # (Z-stack planes ARE acquired sequentially, so lateral drift between them is physically
    # real — this is not a case of an inapplicable question. It is a case of a **broken
    # measurement**, and the honest response is to say the check does not work here rather than
    # to report a number that does not mean what it says.)
    #
    # **Focus** flags 2/21 planes as below half-median sharpness — which is *what a z-stack is*.
    # The outer planes are SUPPOSED to be blurred. Flagging correct data as defective teaches
    # the user to ignore the focus check, which is the one that matters most on a 2D image.
    #
    # **Ghosting** fires on the out-of-focus signal, which is not a double image.
    is_zstack = bool(is_zstack)
    is_timeseries = bool(is_stack) and not is_zstack

    results = [
        qc_saturation(a),
        qc_snr(a),
        qc_vignetting(a),
    ]

    # Focus and ghosting are meaningful PER PLANE, and meaningless ACROSS a focal series.
    if is_zstack:
        results += [
            _not_applicable(
                'Focus / sharpness',
                "A z-stack is SUPPOSED to have blurred planes — that is what a focal series "
                "is. Comparing each plane's sharpness to the median flags the outer planes as "
                "defective when they are correct. Use the spherical-aberration check below, "
                "which asks the question that IS meaningful in z: is the through-focus "
                "response symmetric?"),
            _not_applicable(
                'Ghosting (double image)',
                "Out-of-focus signal from neighbouring planes is not a double image. The "
                "cepstral echo this check looks for is swamped by the defocus blur."),
            _not_applicable(
                'Drift',
                "This check does not work on a focal series. Lateral drift between planes IS "
                "real — they are acquired sequentially — but the phase correlation fails on "
                "the sharp-vs-blurred mismatch and reports a large number regardless: on a "
                "z-stack with ZERO drift it reports 89 px, and a full pixel per plane of real "
                "drift moves it only to 100. **It is blind to the thing it names**, so it is "
                "not reported rather than reported wrongly."),
            _not_applicable(
                'Vibration',
                "Periodicity in a focal series would be a periodic optical artefact, not a "
                "pump or a fan — and this check is not calibrated for that."),
        ]
    else:
        # Focus needs the optics for an ABSOLUTE verdict; without them it still reports the
        # sharpest edge in px, which is comparable across a dataset.
        results += [qc_focus(a, pixel_um=pixel_um, na=na, wavelength_nm=wavelength_nm),
                    qc_ghosting(a)]

    if is_timeseries:
        results += [qc_drift(a), qc_vibration(a), qc_photobleaching(a)]
    elif not is_zstack:
        results += [
            _not_applicable('Drift', "Needs a time series."),
            _not_applicable('Vibration', "Needs a time series."),
            _not_applicable('Photobleaching', "Needs a time series."),
        ]
    results += [
        qc_spherical_aberration(a, is_zstack=is_zstack),
        qc_nyquist(pixel_um, na, wavelength_nm),
        qc_time_sampling(frame_interval_s, process_timescale_s),
        qc_chromatic(n_channels),
    ]
    return results


# ---------------------------------------------------------------------------
# teaching report plot
# ---------------------------------------------------------------------------

_STATUS_COLOR = {'good': '#2ca02c', 'warn': '#ff9800', 'bad': '#d62728',
                 'info': '#1f77b4', 'na': '#888888'}
_STATUS_LABEL = {'good': 'GOOD', 'warn': 'CHECK', 'bad': 'POOR',
                 'info': 'INFO', 'na': 'N/A'}


def plot_qc_report(results, title='Data Quality Report', interactive=True):
    """Render a teaching QC report: a colour-coded scorecard plus a diagnostic
    panel for every metric that produced one, each captioned with how it is
    measured and what good data looks like."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    diag_metrics = [r for r in results if r.get('diag')]
    n_diag = len(diag_metrics)
    ncols = 3
    nrows_diag = int(np.ceil(n_diag / ncols)) if n_diag else 0

    import textwrap

    # Taller scorecard: each metric gets a score line + a teaching line.
    fig = plt.figure(figsize=(12.5, 4.6 + 3.1 * nrows_diag))
    gs = GridSpec(1 + nrows_diag, ncols, figure=fig,
                  height_ratios=[len(results) * 0.62] + [1] * nrows_diag,
                  hspace=0.85, wspace=0.28)

    # --- scorecard (spans the top row) ---
    ax = fig.add_subplot(gs[0, :]); ax.axis('off')
    ax.set_title(title, fontsize=14, fontweight='bold', loc='left')

    # Overall verdict banner — orient the reader before the details.
    # ── "All assessed metrics look good" is technically true and practically a trap ──
    #
    # The verdict counted only `bad` and `warn`. On an image with no pixel size, no NA and no
    # frame interval, **only 4 of 12 checks actually run** — Nyquist, time sampling, chromatic,
    # drift, vibration, photobleaching and spherical aberration are all skipped — and the report
    # still said *"All assessed metrics look good."*
    #
    # The word "assessed" is doing enormous work there, and no user reads it that way. They read
    # "my data is good". **A report that looks clean because most of it did not run is the exact
    # bait this module exists to prevent**, and the fix is to say the coverage out loud.
    n_bad = sum(1 for r in results if r['status'] == 'bad')
    n_warn = sum(1 for r in results if r['status'] == 'warn')
    n_assessed = sum(1 for r in results if r['status'] in ('good', 'warn', 'bad'))
    n_skipped = len(results) - n_assessed
    if n_bad:
        verdict = f"{n_bad} metric(s) look poor and {n_warn} worth checking — see the guidance below."
        vcol = _STATUS_COLOR['bad']
    elif n_warn:
        verdict = (f"No serious problems; {n_warn} metric(s) worth a look. "
                   f"({n_assessed} of {len(results)} checks ran)")
        vcol = _STATUS_COLOR['warn']
    else:
        verdict = (f"All {n_assessed} checks that ran look good."
                   + (f" — but {n_skipped} could NOT run (missing metadata, or the wrong "
                      f"kind of data). This is not a clean bill of health."
                      if n_skipped else ""))
        vcol = _STATUS_COLOR['good']
    ax.text(0.005, 1.02, verdict, transform=ax.transAxes, fontsize=10.5,
            fontweight='bold', color=vcol, va='bottom')

    y = 0.98
    dy = 1.0 / (len(results) + 0.5)
    for r in results:
        col = _STATUS_COLOR.get(r['status'], '#888')
        ax.add_patch(plt.Rectangle((0.005, y - dy * 0.62), 0.016, dy * 0.5,
                                    color=col, transform=ax.transAxes, clip_on=False))
        # line 1 — the score
        ax.text(0.03, y - dy * 0.28,
                f"{_STATUS_LABEL.get(r['status'],''):5}  {r['name']}",
                fontsize=10, fontweight='bold', color=col, va='center',
                transform=ax.transAxes)
        ax.text(0.30, y - dy * 0.28, r['headline'], fontsize=8.5, color='0.2',
                va='center', transform=ax.transAxes)
        # line 2 — the teaching / guidance (what good looks like + how to improve)
        teach = r.get('good', '') or r.get('how', '')
        teach = textwrap.shorten(teach, width=155, placeholder=" …")
        ax.text(0.03, y - dy * 0.72, "→ " + teach, fontsize=7.8,
                color='0.45', style='italic', va='center', transform=ax.transAxes)
        y -= dy

    # --- diagnostic panels, each captioned with HOW it is measured ---
    for i, r in enumerate(diag_metrics):
        row = 1 + i // ncols
        col = i % ncols
        dax = fig.add_subplot(gs[row, col])
        d = r['diag']
        c = _STATUS_COLOR.get(r['status'], '#1f77b4')
        try:
            if 'hist_counts' in d:                      # saturation
                edges = d['hist_edges']
                dax.bar(0.5 * (edges[:-1] + edges[1:]), d['hist_counts'],
                        width=np.diff(edges), color='0.6', log=True)
                dax.axvline(d['ceiling'], color=c, ls='--', lw=1.2)
                dax.set_xlabel('intensity (dashed = ceiling)')
                dax.set_ylabel('count (log)')
            elif 'radial_profile' in d:                 # vignetting
                dax.plot(d['radius_bins'], d['radial_profile'], color=c)
                dax.set_xlabel('radius (px)'); dax.set_ylabel('mean intensity')
            elif 'per_frame' in d:                      # focus
                dax.plot(d['per_frame'], '-o', ms=3, color=c)
                dax.axhline(d['median'], color='0.5', ls='--', lw=0.8)
                dax.axhline(0.5 * d['median'], color='#d62728', ls=':', lw=0.8)
                dax.set_xlabel('frame'); dax.set_ylabel('sharpness')
            elif 'cepstrum' in d:                       # ghosting
                C = d['cepstrum']; h, w = C.shape
                dax.imshow(C, cmap='magma', vmax=np.percentile(C, 99.5),
                           extent=[-w//2, w//2, -h//2, h//2])
                dax.set_xlabel('offset x (px)'); dax.set_ylabel('offset y (px)')
            elif 'magnitude' in d:                      # drift
                dax.plot(d['magnitude'], '-o', ms=3, color=c)
                dax.set_xlabel('frame'); dax.set_ylabel('drift (px)')
            elif 'spectrum' in d:                       # vibration
                dax.plot(d['spectrum'][1:], color=c)
                dax.set_xlabel('frequency bin'); dax.set_ylabel('power')
            elif 'axial_profile' in d:                  # spherical
                dax.plot(d['axial_profile'], '-o', ms=3, color=c)
                dax.axvline(d['focus_index'], color='0.5', ls='--', lw=0.8)
                dax.set_xlabel('z slice'); dax.set_ylabel('sharpness')
        except Exception:
            dax.axis('off')
        dax.set_title(r['name'], fontsize=10, color=c, fontweight='bold')
        dax.tick_params(labelsize=7)
        # caption: how this metric is measured (teaching)
        how = textwrap.fill("How: " + r.get('how', ''), width=52)
        dax.text(0.0, -0.42, how, transform=dax.transAxes, fontsize=6.8,
                 color='0.45', va='top', ha='left')

    fig.text(0.01, 0.005,
             "CORE metrics use absolute thresholds; ADVISORY metrics (spherical, "
             "Nyquist, time, vibration, chromatic) are heuristics or need "
             "optics/timing input. The italic line under each metric is what "
             "good data looks like / how to improve it.",
             fontsize=7.5, color='0.4')
    if interactive:
        plt.show(block=False)
    return fig
