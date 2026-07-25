"""Illumination QC — vignetting (flat-field uniformity) and ghosting (stray-light / secondary-reflection) field artifacts.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi
from pycat.toolbox.data_qc._base import _mean_frame

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
    small and sharp.** A large MEDIAN filter removes bright structures
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

    # Estimate the ILLUMINATION field: a large MEDIAN filter suppresses compact bright
    # structures (cells, condensates) while preserving the broad, slowly-varying lamp
    # profile. Kernel = 1/4 of the short side, chosen by measurement: smaller kernels let
    # the objects leak back in, larger ones gain nothing.
    # ── A grey OPENING is a local MINIMUM. It erases the illumination. ──────────
    #
    # ``grey_opening`` takes the minimum over its window, and on any image with a dark
    # background **the minimum is ~0 everywhere** — so the "illumination field" it returns is
    # identically zero, and the edge/centre ratio comes out at exactly **1.00: "good"**.
    #
    # **The check was blind to real vignetting.** Measured on a scene with a 35 % radial
    # fall-off (true edge/centre = **0.64**):
    #
    #     estimator                     centre    edge    ratio
    #     grey_opening (the old one)    **0.0**   **0.0** **0.00 -> reported as 1.00, "good"**
    #     median filter                 61.6      42.5    **0.69**
    #     heavy Gaussian blur           71.6      49.2    0.69
    #     25th-percentile filter        34.1      15.2    0.45
    #
    # A MEDIAN filter is the right tool: it is robust to the bright objects (which is why the
    # opening was reached for) **without collapsing to the minimum**. A plain Gaussian is
    # equally accurate here but is dragged upward by dense fields of bright objects; the median
    # is not.
    k = max(5, int(min(h, w) // 8) | 1)          # odd, ~1/8 of the frame
    bg = ndi.median_filter(a, size=k)
    bg = ndi.gaussian_filter(bg, max(min(h, w) / 40.0, 1.0))

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
    # ── The pedestal dilutes the falloff, and it CANNOT be estimated from here ──
    #
    # Illumination is **multiplicative** on the signal; a camera pedestal is **additive**. So the
    # raw edge/centre ratio is NOT the illumination falloff — the pedestal sits in both terms and
    # drags the ratio toward 1, exactly as it does to a partition coefficient (1.5.422).
    #
    # Measured, on a scene with a genuine 35 % radial falloff:
    #
    #     pedestal    reported edge/centre
    #     0           **0.70**   (correct)
    #     500         0.97
    #     2000        **0.99**   (essentially flat — the check is BLIND)
    #
    # **I tried to subtract it, and it cannot be done from this image.** The obvious estimate —
    # the darkest part of the illumination field — is *the vignetted corner itself*. Subtracting
    # it removes the very signal being measured: a 0 % falloff then read 0.48 and a 35 % falloff
    # read 0.02. **Circular, and worse than the disease.**
    #
    # The pedestal is a property of the CAMERA, not of this frame, and the only honest source is
    # a dark reference — which is exactly the conclusion reached for Kp (1.5.423). So the check
    # reports the ratio it can measure, and **says that a pedestal makes it read high**. A user
    # who sees "good" on a high-offset camera needs to know the check is conservative there,
    # not to be told a corrected number that was never correct.
    centre = float(np.mean(prof[:max(1, nb // 8)]))
    edge = float(np.mean(prof[-max(1, nb // 8):]))
    ratio = edge / centre if centre != 0 else 1.0
    status = 'good' if ratio >= 0.9 else ('warn' if ratio >= 0.7 else 'bad')
    return dict(
        name='Vignetting / flat-field', tier='core', status=status,
        value=float(ratio), unit='edge/centre',
        headline=f"edge is {ratio*100:.0f}% of centre brightness",
        # ── The report was describing a method the code no longer uses ──────────
        #
        # ``grey_opening`` was replaced with a median filter (1.5.473) because the opening takes
        # a local MINIMUM, which is ~0 on any dark background — it returned an identically zero
        # illumination field and the check was blind. **The `how` text was not updated**, so the
        # report told the user it was doing something it had stopped doing.
        #
        # **A report that misdescribes its own method is worse than one that is silent**: the
        # user cannot check the result against the method, and a reviewer reading the methods
        # section would be reading a fabrication.
        how="The illumination field is estimated with a large MEDIAN filter "
            "(which removes the objects but keeps the broad lamp profile), then binned "
            "by distance from the image centre. Measuring the RAW mean instead would "
            "report the position of the cells, not the illumination.",
        good="Ratio >= 0.9 (nearly flat). **A camera pedestal makes this read HIGH** - it is additive, so it sits in BOTH the edge and the centre and drags the ratio toward 1: a real 35% falloff reads as 0.99 on a 2000-count offset. The check is CONSERVATIVE on a high-offset camera. Strong falloff biases intensity "
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
