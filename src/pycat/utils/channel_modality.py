"""Classify a channel's imaging modality from its PIXELS when metadata is silent.

Many acquisitions (camera-only MicroManager, exported OME-TIFFs) carry NO fluorophore name,
emission wavelength, or channel label — so metadata-based identification falls all the way to a
position guess ("C0-Blue"). But the pixels themselves carry the modality: fluorescence looks nothing
like a transmitted-light image, and the transmitted modes (brightfield / DIC / phase) have distinct
optical signatures. This measures a frame and names the modality, so the layer can be
"sample-brightfield" instead of a meaningless "C0-Blue".

Honesty over bravado
--------------------
Fluorescence-vs-transmitted is robust. The finer transmitted split (brightfield / DIC / phase) is
genuinely hard from pixels alone and the signatures overlap, so each finer call carries a confidence
and DEGRADES GRACEFULLY: an uncertain transmitted image is labelled "transmitted", never a confident
wrong "DIC". A wrong specific label is worse than an honest generic one.

Signatures used
---------------
* **Fluorescence**: sparse. Most of the field is near-zero background with a small bright fraction;
  histogram is heavily right-skewed, low fraction of mid-intensity pixels, background is the mode and
  sits near the low end. High skew + low background-mean-fraction.
* **Transmitted (any)**: filled. The background is bright and occupies most of the field; objects are
  darker (absorption) or edge-relief. Background mode sits high, histogram not right-skewed.
  - **Brightfield**: plain absorption — smooth bright background, objects darker, LOW directional
    gradient asymmetry, no strong edge halos.
  - **DIC**: pseudo-relief shadow-cast — a preferred illumination azimuth makes the gradient
    DIRECTIONALLY ASYMMETRIC (bright on one side of an edge, dark on the opposite). Measured as
    asymmetry of the signed directional-derivative distribution.
  - **Phase**: bright/dark HALOS around edges — high local gradient magnitude concentrated at edges
    with symmetric over/undershoot, without the directional bias of DIC.
"""

from __future__ import annotations

import numpy as np


def _first_frame_2d(arr):
    """Reduce whatever we're handed to a single 2-D frame for measurement."""
    a = np.asarray(arr)
    while a.ndim > 2:
        a = a[0]                       # take the first plane along leading axes
    return a.astype(np.float64, copy=False)


def _robust_norm(a):
    """Scale to [0, 1] by the 1–99th percentile, clipping outliers so a few hot
    pixels don't dominate the statistics."""
    lo, hi = np.percentile(a, (1, 99))
    if hi <= lo:
        return np.zeros_like(a)
    return np.clip((a - lo) / (hi - lo), 0.0, 1.0)


def classify_channel_from_pixels(frame):
    """Return ``(modality, confidence)`` for a single channel's pixels.

    modality   : 'fluorescence' | 'brightfield' | 'dic' | 'phase' | 'transmitted' | None
    confidence : float in [0, 1]; None modality means "couldn't tell".

    The label is meant to feed a layer name, so it stays coarse and honest: an uncertain
    transmitted image resolves to 'transmitted' rather than a guessed sub-type.
    """
    try:
        a = _first_frame_2d(frame)
        if a.size < 64 or not np.isfinite(a).any():
            return None, 0.0
        a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        n = _robust_norm(a)

        # ── Fluorescence vs transmitted: is the field mostly dark with sparse
        # bright signal, or mostly bright/filled? ──
        hist, _ = np.histogram(n, bins=64, range=(0.0, 1.0))
        hist = hist.astype(float)
        mode_bin = int(np.argmax(hist))
        mode_level = mode_bin / 63.0             # where the most common intensity sits
        bright_frac = float(np.mean(n > 0.5))    # fraction of clearly-bright pixels
        mean_level = float(np.mean(n))
        # skew: fluorescence is strongly right-skewed (dark bg, sparse bright tail)
        std = float(np.std(n)) or 1e-9
        skew = float(np.mean(((n - mean_level) / std) ** 3))

        # Fluorescence: background mode is low, mean is low, strong positive skew.
        fluor_score = 0.0
        if mode_level < 0.35:
            fluor_score += 0.4
        if mean_level < 0.4:
            fluor_score += 0.3
        if skew > 0.8:
            fluor_score += 0.3
        # Transmitted: background mode is high / mid, field is filled (high mean),
        # weak or negative skew.
        trans_score = 0.0
        if mode_level > 0.45:
            trans_score += 0.4
        if mean_level > 0.45:
            trans_score += 0.3
        if skew < 0.5:
            trans_score += 0.3

        if fluor_score >= trans_score and fluor_score >= 0.5:
            return 'fluorescence', min(1.0, fluor_score)

        if trans_score < 0.5:
            # neither picture is clear
            return None, 0.0

        # ── Transmitted sub-type: brightfield / DIC / phase ──
        # Directional derivatives for DIC's shadow-cast asymmetry.
        gy, gx = np.gradient(n)
        # signed directional derivative along the dominant illumination azimuth:
        # find the azimuth maximising |mean signed gradient| (DIC has a net bias).
        mean_gx, mean_gy = float(np.mean(gx)), float(np.mean(gy))
        # asymmetry of the signed-gradient distribution along the biased axis
        gmag = np.hypot(gx, gy)
        edge = gmag > np.percentile(gmag, 90)    # edge pixels
        if edge.sum() < 16:
            return 'transmitted', min(1.0, trans_score)

        # DIC signature: at edges the SIGNED directional derivative is skewed
        # (bright-then-dark relief), i.e. |skew of directional deriv| is high AND
        # there is a net directional bias.
        # project gradient onto the dominant bias direction
        bias_mag = np.hypot(mean_gx, mean_gy)
        if bias_mag > 1e-6:
            ux, uy = mean_gx / bias_mag, mean_gy / bias_mag
            dir_deriv = gx[edge] * ux + gy[edge] * uy
        else:
            dir_deriv = gx[edge]
        dd_std = float(np.std(dir_deriv)) or 1e-9
        dd_skew = abs(float(np.mean(((dir_deriv - np.mean(dir_deriv)) / dd_std) ** 3)))

        # Phase signature: strong SYMMETRIC edge halos — high edge-gradient
        # magnitude relative to the interior, low directional bias.
        edge_grad = float(np.mean(gmag[edge]))
        interior_grad = float(np.mean(gmag[~edge])) or 1e-9
        halo_ratio = edge_grad / interior_grad

        # Decide, conservatively. Thresholds are deliberately cautious; when
        # nothing is clearly indicated we return the honest generic 'transmitted'.
        dic_score = 0.0
        if bias_mag > 2e-3:
            dic_score += 0.4
        if dd_skew > 0.7:
            dic_score += 0.4

        phase_score = 0.0
        if halo_ratio > 6.0:
            phase_score += 0.4
        if bias_mag < 1e-3:
            phase_score += 0.2

        bf_score = 0.0
        if halo_ratio < 4.0:
            bf_score += 0.3
        if bias_mag < 1e-3 and dd_skew < 0.5:
            bf_score += 0.3

        scores = {'dic': dic_score, 'phase': phase_score, 'brightfield': bf_score}
        best = max(scores, key=scores.get)
        best_score = scores[best]
        # Require a clear margin before committing to a sub-type; else be honest.
        others = sorted(scores.values(), reverse=True)
        margin = others[0] - (others[1] if len(others) > 1 else 0.0)
        if best_score >= 0.6 and margin >= 0.2:
            return best, min(1.0, 0.5 + best_score / 2.0)
        return 'transmitted', min(1.0, trans_score)
    except Exception:
        return None, 0.0
