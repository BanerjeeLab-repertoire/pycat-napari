"""
PyCAT Gaussian Spot Localization
==================================
Fit a 2D or 3D Gaussian (plus background offset) to each detected spot, giving
sub-pixel localization and PSF width. Adapted from the author's
shape-based peak-finding tool, which cropped a box around each peak and fit a
Gaussian with lsqcurvefit.

Given spot coordinates (from CLEAN detection, a napari Points layer, or any
detector), this crops a window around each and fits:

  2D:  I(x,y)   = A·exp(−(x−x0)²/(2σx²) − (y−y0)²/(2σy²)) + B
  3D:  I(x,y,z) = A·exp(−(x−x0)²/(2σx²) − (y−y0)²/(2σy²) − (z−z0)²/(2σz²)) + B

Returns the sub-pixel centre (x0,y0[,z0]), the widths (σ), amplitude, and
background — useful for PSF characterisation (width, ellipticity) and precise
localization beyond the integer pixel of the detector.

Author
------
    Original tool: Gable Wadsworth (shapebased_peakfinding.m)
    PyCAT port: Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

# Via the notification shim: keeps the localization maths importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Gaussian models
# ---------------------------------------------------------------------------

def gaussian_2d_offset(coords, amplitude, x0, y0, sigma_x, sigma_y, offset):
    """2D Gaussian + constant background. `coords` = (x, y) raveled arrays."""
    x, y = coords
    g = amplitude * np.exp(-((x - x0) ** 2) / (2 * sigma_x ** 2)
                           - ((y - y0) ** 2) / (2 * sigma_y ** 2)) + offset
    return g.ravel()


def gaussian_3d_offset(coords, amplitude, x0, y0, z0, sigma_x, sigma_y, sigma_z, offset):
    """3D Gaussian + constant background. `coords` = (x, y, z) raveled arrays."""
    x, y, z = coords
    g = amplitude * np.exp(-((x - x0) ** 2) / (2 * sigma_x ** 2)
                           - ((y - y0) ** 2) / (2 * sigma_y ** 2)
                           - ((z - z0) ** 2) / (2 * sigma_z ** 2)) + offset
    return g.ravel()


# ---------------------------------------------------------------------------
# Single-spot fits
# ---------------------------------------------------------------------------

def classify_spot_fit(fit, psf_sigma_px=None, sigma_tolerance=(0.6, 1.3),
                      max_aspect=1.3):
    """Is this Gaussian fit a real single spot — or a merged pair, or a hot pixel?

    Why R² cannot answer this
    -------------------------
    ``min_r_squared`` gates which spots are kept, and in ``molecular_counting_tools`` an
    R² threshold decides whether a **molecule count is accepted at all**. But R² only asks
    "does a Gaussian describe this patch better than a flat line?" — and a Gaussian
    describes a *lot* of things reasonably well. Measured on 11x11 patches (true PSF sigma
    = 1.8 px):

    ==========================  ========  =========  ========
    patch                       R²        sigma      aspect
    ==========================  ========  =========  ========
    **real single spot**        0.983     **1.83**   1.03
    **two merged spots**        **0.980** 2.42       **1.49**
    **hot pixel**               **0.928** **0.14**   1.14
    a diagonal edge             0.672     12.24      1.09
    flat noise                  0.051     —          —
    ==========================  ========  =========  ========

    **R² cannot tell one molecule from two, or from a dead pixel.** A merged pair scores
    0.980 against a real spot's 0.983, and a hot pixel scores 0.928. An R² gate therefore
    accepts merged spots and hot pixels as valid single molecules — and in molecular
    counting, each of those is a wrong count.

    What does work
    --------------
    **The width.** A real spot has ``sigma`` at the PSF; a hot pixel is far narrower
    (sub-pixel); a merged pair is *elongated* (aspect ratio well above 1); an edge fits with
    an absurd sigma. Those separate cleanly.

    Parameters
    ----------
    psf_sigma_px : the expected PSF sigma. Without it, only the aspect ratio and the
        obviously-degenerate cases can be checked, and the verdict says so.
    sigma_tolerance : (low, high) multiples of the PSF sigma that count as a single spot.
    max_aspect : above this, the fit is elongated — usually two unresolved spots.

    The thresholds are measured, not guessed
    ----------------------------------------
    Fitting 11x11 patches (PSF sigma 1.8 px, 40 realisations each):

    ======================  ============  ==========
    case                    sigma / PSF   aspect
    ======================  ============  ==========
    single spot             **1.00**      **1.02**
    merged pair, 2 px apart 1.08          1.17
    merged pair, 3 px apart 1.21          **1.39**
    merged pair, 4 px apart 1.40          **1.77**
    ======================  ============  ==========

    Hence ``max_aspect = 1.3`` and ``sigma_tolerance = (0.6, 1.3)``.

    .. warning::

       **A pair separated by less than about 3 px is not detectable as a pair, by anyone.**
       At 2 px apart the two emitters produce sigma/PSF = 1.08 and aspect = 1.17 —
       statistically indistinguishable from a single spot. That is diffraction, not a
       shortcoming of this check: two emitters closer than the PSF cannot be separated by
       any measurement of their combined image. A ``single`` verdict therefore means "not
       distinguishable from one spot", **not** "definitely one molecule."
    """
    if not fit or not fit.get('success'):
        return dict(spot_class='fit_failed', ok=False,
                    reason="the Gaussian fit did not converge")

    sx = abs(float(fit.get('sigma_x', np.nan)))
    sy = abs(float(fit.get('sigma_y', np.nan)))
    if not (np.isfinite(sx) and np.isfinite(sy)) or min(sx, sy) <= 0:
        return dict(spot_class='degenerate', ok=False,
                    reason="the fitted width is not finite or is zero")

    aspect = max(sx, sy) / max(min(sx, sy), 1e-9)
    mean_sigma = 0.5 * (sx + sy)

    if aspect > max_aspect:
        return dict(spot_class='elongated', ok=False, aspect=aspect,
                    sigma_mean=mean_sigma,
                    reason=(f"the fit is elongated (aspect {aspect:.2f} > {max_aspect}), "
                            f"which usually means TWO unresolved spots rather than one. "
                            f"R² cannot see this — a merged pair scores essentially the "
                            f"same as a single spot."))

    if psf_sigma_px is None or not np.isfinite(psf_sigma_px) or psf_sigma_px <= 0:
        return dict(spot_class='unverified', ok=True, aspect=aspect,
                    sigma_mean=mean_sigma,
                    reason=("no PSF sigma supplied, so the width could not be checked "
                            "against the optics. A hot pixel and a merged pair both pass "
                            "an R² test; only the width distinguishes them. Supply "
                            "psf_sigma_px to enable the check."))

    lo, hi = sigma_tolerance
    ratio = mean_sigma / float(psf_sigma_px)
    if ratio < lo:
        return dict(spot_class='too_narrow', ok=False, aspect=aspect,
                    sigma_mean=mean_sigma, sigma_ratio=ratio,
                    reason=(f"the fitted width ({mean_sigma:.2f} px) is only {ratio:.2f}x "
                            f"the PSF sigma ({psf_sigma_px:.2f} px). Nothing real can be "
                            f"narrower than the PSF — this is a HOT PIXEL or a "
                            f"cosmic-ray hit, not a molecule."))
    if ratio > hi:
        return dict(spot_class='too_wide', ok=False, aspect=aspect,
                    sigma_mean=mean_sigma, sigma_ratio=ratio,
                    reason=(f"the fitted width ({mean_sigma:.2f} px) is {ratio:.2f}x the "
                            f"PSF sigma ({psf_sigma_px:.2f} px) — too broad for a single "
                            f"diffraction-limited spot. Usually an unresolved cluster, or "
                            f"a fit that has latched onto background structure."))

    return dict(spot_class='single', ok=True, aspect=aspect,
                sigma_mean=mean_sigma, sigma_ratio=ratio,
                reason="width and shape are not distinguishable from a single "
                       "diffraction-limited spot. NOTE this is not proof of a single "
                       "molecule: two emitters closer than ~3 px produce an image "
                       "indistinguishable from one, by any method.")


def fit_gaussian_2d_spot(patch: np.ndarray, sigma_guess: float = 2.0,
                         fast: bool = False) -> dict:
    """
    Fit a 2D Gaussian + offset to a small image patch centred on a spot.

    fast : if True, use an unbounded Levenberg-Marquardt fit with a tight
        iteration cap (maxfev=200). This is dramatically faster than the bounded
        Trust-Region fit (which can burn thousands of evaluations per bead on
        noisy patches) at a small precision cost — suitable for the 'fast_fit'
        quality mode. If the unbounded fit produces an out-of-range centre it is
        rejected and the bead falls back to NaN metrics.

    Returns
    -------
    dict with amplitude, x0, y0 (patch coords), sigma_x, sigma_y, offset,
    r_squared, success. Centre coords are in the patch frame (add the patch
    origin to get image coords).
    """
    p = np.asarray(patch, dtype=float)
    if p.ndim != 2:
        raise ValueError("fit_gaussian_2d_spot expects a 2D patch.")
    h, w = p.shape
    y, x = np.mgrid[0:h, 0:w]

    offset0 = float(np.median(p))
    amp0 = float(p.max() - offset0)
    tot = p.sum()
    if tot > 0:
        y0g = float((y * p).sum() / tot)
        x0g = float((x * p).sum() / tot)
    else:
        y0g, x0g = h / 2.0, w / 2.0
    p0 = [amp0, x0g, y0g, sigma_guess, sigma_guess, offset0]
    lb = [0, 0, 0, 0.3, 0.3, -np.inf]
    ub = [np.inf, w, h, w, h, np.inf]

    try:
        if fast:
            # Unbounded LM with a tight cap — much faster, mild precision cost.
            popt, _ = curve_fit(gaussian_2d_offset, (x, y), p.ravel(),
                                p0=p0, maxfev=200)
            # Reject nonsensical centres/sigmas that the unbounded fit can yield.
            if (not (0 <= popt[1] <= w and 0 <= popt[2] <= h)
                    or popt[3] <= 0 or popt[4] <= 0
                    or popt[3] > w or popt[4] > h):
                raise RuntimeError("fast fit out of range")
        else:
            popt, _ = curve_fit(gaussian_2d_offset, (x, y), p.ravel(),
                                p0=p0, bounds=(lb, ub), maxfev=10000)
        fit = gaussian_2d_offset((x, y), *popt)
        ss_res = np.sum((p.ravel() - fit) ** 2)
        ss_tot = np.sum((p.ravel() - p.mean()) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        a, x0, y0, sx, sy, off = popt
        return dict(amplitude=float(a), x0=float(x0), y0=float(y0),
                    sigma_x=float(sx), sigma_y=float(sy), offset=float(off),
                    r_squared=float(r2), success=True)
    except Exception:
        return dict(success=False)


def fit_gaussian_3d_spot(patch: np.ndarray, sigma_xy_guess: float = 2.0,
                         sigma_z_guess: float = 2.0) -> dict:
    """
    Fit a 3D Gaussian + offset to a small volume patch centred on a spot.
    `patch` axis order is (Z, Y, X). Centre coords returned in patch frame.
    """
    p = np.asarray(patch, dtype=float)
    if p.ndim != 3:
        raise ValueError("fit_gaussian_3d_spot expects a 3D (Z,Y,X) patch.")
    nz, ny, nx = p.shape
    z, y, x = np.mgrid[0:nz, 0:ny, 0:nx]

    offset0 = float(np.median(p))
    amp0 = float(p.max() - offset0)
    tot = p.sum()
    if tot > 0:
        z0g = float((z * p).sum() / tot)
        y0g = float((y * p).sum() / tot)
        x0g = float((x * p).sum() / tot)
    else:
        z0g, y0g, x0g = nz / 2.0, ny / 2.0, nx / 2.0
    p0 = [amp0, x0g, y0g, z0g, sigma_xy_guess, sigma_xy_guess, sigma_z_guess, offset0]
    lb = [0, 0, 0, 0, 0.3, 0.3, 0.3, -np.inf]
    ub = [np.inf, nx, ny, nz, nx, ny, nz, np.inf]

    try:
        popt, _ = curve_fit(gaussian_3d_offset, (x, y, z), p.ravel(),
                            p0=p0, bounds=(lb, ub), maxfev=20000)
        fit = gaussian_3d_offset((x, y, z), *popt)
        ss_res = np.sum((p.ravel() - fit) ** 2)
        ss_tot = np.sum((p.ravel() - p.mean()) ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
        a, x0, y0, z0, sx, sy, sz, off = popt
        return dict(amplitude=float(a), x0=float(x0), y0=float(y0), z0=float(z0),
                    sigma_x=float(sx), sigma_y=float(sy), sigma_z=float(sz),
                    offset=float(off), r_squared=float(r2), success=True)
    except Exception:
        return dict(success=False)


# ---------------------------------------------------------------------------
# Batch localization over many spots
# ---------------------------------------------------------------------------

def _crop_2d(image, cy, cx, half):
    y0, y1 = cy - half, cy + half + 1
    x0, x1 = cx - half, cx + half + 1
    if y0 < 0 or x0 < 0 or y1 > image.shape[0] or x1 > image.shape[1]:
        return None, None, None
    return image[y0:y1, x0:x1], y0, x0


def _crop_3d(vol, cz, cy, cx, half_xy, half_z):
    z0, z1 = cz - half_z, cz + half_z + 1
    y0, y1 = cy - half_xy, cy + half_xy + 1
    x0, x1 = cx - half_xy, cx + half_xy + 1
    if (z0 < 0 or y0 < 0 or x0 < 0 or
            z1 > vol.shape[0] or y1 > vol.shape[1] or x1 > vol.shape[2]):
        return None, None, None, None
    return vol[z0:z1, y0:y1, x0:x1], z0, y0, x0


def localize_spots(image: np.ndarray, coords: np.ndarray,
                   window: int = 9, window_z: int = 4,
                   sigma_guess: float = 2.0,
                   pixel_size_um: float = None,
                   pixel_size_z_um: float = None,
                   min_r_squared: float = 0.0,
                   psf_sigma_px: float = None) -> pd.DataFrame:
    """
    Fit a Gaussian to each spot and return sub-pixel localizations + PSF widths.

    Parameters
    ----------
    image : 2D (H,W) or 3D (Z,H,W) intensity image.
    coords : (N,2) [y,x] for 2D or (N,3) [z,y,x] for 3D — integer-ish spot
        centres (e.g. from CLEAN detection or a Points layer).
    window : half-window in xy is window//2 (patch is window×window in xy).
    window_z : full z half-window for 3D (patch z-depth = 2·window_z+1).
    sigma_guess : initial PSF sigma (px).
    pixel_size_um : if given, widths/centres also reported in µm (xy).
    pixel_size_z_um : axial pixel size (µm) for 3D width in µm.
    min_r_squared : drop fits below this R².

        **R² is a weak filter here, and it is worth knowing why.** It only asks whether a
        Gaussian beats a flat line — and a Gaussian describes a lot of things well.
        Measured on 11x11 patches (true PSF sigma 1.8 px): a real single spot scores
        R² = 0.983, **two merged spots score 0.980**, and a **hot pixel scores 0.928**.
        R² cannot tell one molecule from two, or from a dead pixel; it only rejects
        patches with no structure at all.
    psf_sigma_px : the expected PSF sigma (px). When supplied, every spot is CLASSIFIED by
        its fitted width — the quantity that actually discriminates. A hot pixel is far
        narrower than the PSF, a merged pair is elongated, an edge fits with an absurd
        sigma. The classification is added as columns (`spot_class`, `spot_ok`,
        `spot_reason`); spots are NOT silently dropped, so a suspect fit is visible rather
        than missing. See `classify_spot_fit`.

    Returns
    -------
    DataFrame with per-spot sub-pixel centre, sigma(s), amplitude, offset,
    FWHM, R². Coordinates are in full-image pixel units (patch origin added).
    """
    img = np.asarray(image, dtype=float)
    coords = np.asarray(coords)
    is_3d = img.ndim == 3
    half = window // 2
    FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0))   # sigma → FWHM factor

    rows = []
    rejected = []
    for c in coords:
        if is_3d:
            cz, cy, cx = int(round(c[0])), int(round(c[1])), int(round(c[2]))
            patch, z0, y0, x0 = _crop_3d(img, cz, cy, cx, half, window_z)
            if patch is None:
                continue
            fit = fit_gaussian_3d_spot(patch, sigma_guess, sigma_guess)
            if not fit.get('success'):
                rejected.append(dict(z=float(cz), y=float(cy), x=float(cx),
                                     spot_class='fit_failed', spot_ok=False,
                                     spot_reason="the Gaussian fit did not converge",
                                     r_squared=float('nan')))
                continue
            if fit['r_squared'] < min_r_squared:
                rejected.append(dict(z=float(cz), y=float(cy), x=float(cx),
                                     spot_class='low_r_squared', spot_ok=False,
                                     spot_reason=(f"R² {fit['r_squared']:.3f} below "
                                                  f"min_r_squared"),
                                     r_squared=float(fit['r_squared'])))
                continue
            cls = classify_spot_fit(fit, psf_sigma_px=psf_sigma_px)
            row = dict(
                z=z0 + fit['z0'], y=y0 + fit['y0'], x=x0 + fit['x0'],
                spot_class=cls['spot_class'], spot_ok=bool(cls['ok']),
                spot_reason=cls['reason'],
                sigma_x=fit['sigma_x'], sigma_y=fit['sigma_y'], sigma_z=fit['sigma_z'],
                amplitude=fit['amplitude'], offset=fit['offset'],
                fwhm_x=fit['sigma_x'] * FWHM, fwhm_y=fit['sigma_y'] * FWHM,
                fwhm_z=fit['sigma_z'] * FWHM, r_squared=fit['r_squared'])
            if pixel_size_um:
                row['sigma_x_um'] = fit['sigma_x'] * pixel_size_um
                row['sigma_y_um'] = fit['sigma_y'] * pixel_size_um
                row['fwhm_x_um'] = row['fwhm_x'] * pixel_size_um
                row['fwhm_y_um'] = row['fwhm_y'] * pixel_size_um
            if pixel_size_z_um:
                row['sigma_z_um'] = fit['sigma_z'] * pixel_size_z_um
                row['fwhm_z_um'] = row['fwhm_z'] * pixel_size_z_um
            rows.append(row)
        else:
            cy, cx = int(round(c[0])), int(round(c[1]))
            patch, y0, x0 = _crop_2d(img, cy, cx, half)
            if patch is None:
                continue
            fit = fit_gaussian_2d_spot(patch, sigma_guess)
            # A rejected spot is RECORDED, not silently dropped. Dropping it means the
            # user gets N-1 spots with no indication that one was rejected or why — and a
            # missing molecule is as wrong as a spurious one. A hot pixel, for instance,
            # is 1 px wide and often makes the Gaussian fit fail outright: it must appear
            # in the output as a rejected detection, not vanish.
            if not fit.get('success'):
                rejected.append(dict(y=float(cy), x=float(cx),
                                     spot_class='fit_failed', spot_ok=False,
                                     spot_reason=("the Gaussian fit did not converge. A "
                                                  "single-pixel spike (hot pixel, cosmic "
                                                  "ray) commonly does this — there is no "
                                                  "width to fit."),
                                     r_squared=float('nan')))
                continue
            if fit['r_squared'] < min_r_squared:
                rejected.append(dict(y=float(cy), x=float(cx),
                                     spot_class='low_r_squared', spot_ok=False,
                                     spot_reason=(f"R² {fit['r_squared']:.3f} below the "
                                                  f"min_r_squared gate "
                                                  f"({min_r_squared:.3f})"),
                                     r_squared=float(fit['r_squared'])))
                continue
            cls = classify_spot_fit(fit, psf_sigma_px=psf_sigma_px)
            row = dict(
                y=y0 + fit['y0'], x=x0 + fit['x0'],
                spot_class=cls['spot_class'], spot_ok=bool(cls['ok']),
                spot_reason=cls['reason'],
                sigma_x=fit['sigma_x'], sigma_y=fit['sigma_y'],
                amplitude=fit['amplitude'], offset=fit['offset'],
                fwhm_x=fit['sigma_x'] * FWHM, fwhm_y=fit['sigma_y'] * FWHM,
                ellipticity=(max(fit['sigma_x'], fit['sigma_y'])
                             / max(min(fit['sigma_x'], fit['sigma_y']), 1e-9)),
                r_squared=fit['r_squared'])
            if pixel_size_um:
                row['sigma_x_um'] = fit['sigma_x'] * pixel_size_um
                row['sigma_y_um'] = fit['sigma_y'] * pixel_size_um
                row['fwhm_x_um'] = row['fwhm_x'] * pixel_size_um
                row['fwhm_y_um'] = row['fwhm_y'] * pixel_size_um
            rows.append(row)

    # Rejected spots are RETURNED, flagged, rather than dropped. `spot_ok` separates them:
    #   df[df.spot_ok]    -> the spots you can use
    #   df[~df.spot_ok]   -> what was rejected, and `spot_reason` says why
    # A silent drop is indistinguishable from a spot that was never detected, and a
    # missing molecule is as wrong as a spurious one.
    out = pd.DataFrame(rows + rejected)
    if len(out) and 'spot_ok' in out.columns:
        n_bad = int((~out['spot_ok'].astype(bool)).sum())
        if n_bad:
            napari_show_warning(
                f"Spot localization: {n_bad}/{len(out)} detections were rejected "
                f"(see the 'spot_class' and 'spot_reason' columns). They are RETAINED in "
                f"the table and flagged, not dropped — filter with df[df.spot_ok] to use "
                f"only the good ones.")
    return out

def spots_to_mask(shape: tuple, df: pd.DataFrame) -> np.ndarray:
    """
    Build a binary mask from localized spot centres: the pixel containing each
    spot's (rounded) sub-pixel centre is set to 1, all others 0.

    This lets Gaussian Spot Localization emit a segmentation-style output (a
    peak mask) in addition to the sub-pixel table, so detected peaks can feed
    mask/label tools downstream.

    Parameters
    ----------
    shape : the output mask shape — (H, W) for 2D or (Z, H, W) for 3D.
    df : localization DataFrame with columns y, x (and z for 3D).

    Returns
    -------
    uint8 binary mask of the given shape.
    """
    mask = np.zeros(shape, dtype=np.uint8)
    if df is None or len(df) == 0:
        return mask
    is_3d = (len(shape) == 3) and ('z' in df.columns)
    for _, r in df.iterrows():
        if is_3d:
            zi = int(round(r['z'])); yi = int(round(r['y'])); xi = int(round(r['x']))
            if 0 <= zi < shape[0] and 0 <= yi < shape[1] and 0 <= xi < shape[2]:
                mask[zi, yi, xi] = 1
        else:
            yi = int(round(r['y'])); xi = int(round(r['x']))
            if 0 <= yi < shape[-2] and 0 <= xi < shape[-1]:
                mask[..., yi, xi] = 1
    return mask


# ---------------------------------------------------------------------------
# UI entry point (Toolbox)
# ---------------------------------------------------------------------------

def _add_gaussian_localization(ui_instance, layout=None, separate_widget=False):
    """
    Widget: sub-pixel Gaussian localization + PSF width for detected spots.

    Takes an intensity image (2D or 3D) and a Points layer of spot coordinates
    (e.g. from CLEAN detection or manual picking), fits a Gaussian to each, and
    reports sub-pixel centres, PSF widths (sigma / FWHM), and ellipticity.
    """
    import napari
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QSpinBox, QDoubleSpinBox, QPushButton,
        QProgressBar, QCheckBox, QSizePolicy)

    grp  = QGroupBox("Gaussian Spot Localization")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    desc = QLabel(
        "Fits a 2D/3D Gaussian + background to each detected spot for sub-pixel "
        "localization and PSF width (sigma, FWHM, ellipticity). Needs an image "
        "and a Points layer of spot coordinates.")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    image_dd.setToolTip("Intensity image (2D H,W or 3D Z,H,W).")
    form.addRow("Image:", image_dd)

    points_dd = ui_instance.create_layer_dropdown(napari.layers.Points)
    points_dd.setToolTip("Points layer of detected spot coordinates.")
    form.addRow("Detected spots:", points_dd)

    window_spin = QSpinBox(); window_spin.setRange(3, 51); window_spin.setValue(9)
    window_spin.setSingleStep(2)
    window_spin.setToolTip("XY fitting window (px). Should comfortably contain one spot.")
    form.addRow("XY window (px):", window_spin)

    window_z_spin = QSpinBox(); window_z_spin.setRange(1, 25); window_z_spin.setValue(4)
    window_z_spin.setToolTip("3D only: half-depth of the z fitting window (patch depth = 2N+1).")
    form.addRow("Z half-window (px):", window_z_spin)

    sigma_spin = QDoubleSpinBox(); sigma_spin.setRange(0.3, 20); sigma_spin.setValue(2.0)
    sigma_spin.setDecimals(2)
    sigma_spin.setToolTip("Initial PSF sigma guess (px).")
    form.addRow("Sigma guess (px):", sigma_spin)

    px_spin = QDoubleSpinBox(); px_spin.setRange(0.0, 100); px_spin.setValue(0.0)
    px_spin.setDecimals(4)
    px_spin.setToolTip("XY pixel size (µm/px). 0 = use stored value; widths also reported in µm.")
    form.addRow("Pixel size (µm/px):", px_spin)

    pxz_spin = QDoubleSpinBox(); pxz_spin.setRange(0.0, 100); pxz_spin.setValue(0.0)
    pxz_spin.setDecimals(4)
    pxz_spin.setToolTip("3D only: axial pixel size (µm/px).")
    form.addRow("Z pixel size (µm/px):", pxz_spin)

    r2_spin = QDoubleSpinBox(); r2_spin.setRange(0.0, 1.0); r2_spin.setValue(0.0)
    r2_spin.setDecimals(3); r2_spin.setSingleStep(0.05)
    r2_spin.setToolTip("Drop fits below this R² (0 = keep all).")
    form.addRow("Min fit R²:", r2_spin)

    add_pts = QCheckBox("Add refined sub-pixel points layer")
    add_pts.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    add_pts.setChecked(True)
    form.addRow(add_pts)

    add_mask = QCheckBox("Add binary peak mask (1 at each located spot)")
    add_mask.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    add_mask.setChecked(False)
    add_mask.setToolTip(
        "Output a binary mask with a 1 at each localized spot's pixel — a "
        "segmentation-style peak mask that can feed the Label and Mask tools.")
    form.addRow(add_mask)

    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton("▶  Localize Spots")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); form.addRow(btn)

    def _on_run():
        from napari.utils.notifications import show_info as _info, show_warning as _warn
        import numpy as _np
        iname = image_dd.currentText(); pname = points_dd.currentText()
        layers = [l.name for l in ui_instance.viewer.layers]
        if iname not in layers:
            _warn("Select a valid image layer."); return
        if pname not in layers:
            _warn("Select a valid Points layer of detected spots."); return
        img = _np.asarray(ui_instance.viewer.layers[iname].data)
        coords = _np.asarray(ui_instance.viewer.layers[pname].data)
        if len(coords) == 0:
            _warn("The Points layer has no points."); return
        if img.ndim not in (2, 3):
            _warn("Image must be 2D or 3D."); return
        if img.ndim == 3 and coords.shape[1] != 3:
            _warn("3D image needs 3D (z,y,x) point coordinates."); return
        if img.ndim == 2 and coords.shape[1] != 2:
            # allow 3D points on a 2D image by dropping the first axis
            if coords.shape[1] == 3:
                coords = coords[:, 1:]
            else:
                _warn("2D image needs 2D (y,x) point coordinates."); return

        px = px_spin.value()
        if px <= 0:
            try:
                stored = ui_instance.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
                px = float(stored) ** 0.5 if stored else None
            except Exception:
                px = None
        pxz = pxz_spin.value() or None

        prog.setVisible(True); prog.setRange(0, 0)
        try:
            df = localize_spots(
                img, coords, window=window_spin.value(),
                window_z=window_z_spin.value(), sigma_guess=sigma_spin.value(),
                pixel_size_um=px, pixel_size_z_um=pxz,
                min_r_squared=r2_spin.value())
        except Exception as e:
            prog.setVisible(False)
            _warn(f"Localization failed: {e}")
            import traceback; traceback.print_exc(); return
        prog.setVisible(False)

        if len(df) == 0:
            _warn("No spots fit successfully — widen the window or lower min R²."); return

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'gaussian_localization_df'] = df
        except Exception:
            pass
        rec = getattr(ui_instance, '_record', None)
        if callable(rec):
            rec('gaussian_localization', {
                'image_layer': iname, 'points_layer': pname,
                'n_localized': int(len(df)),
                'median_fwhm_x_px': float(df['fwhm_x'].median())})

        if add_pts.isChecked():
            if 'z' in df.columns:
                refined = df[['z', 'y', 'x']].values
            else:
                refined = df[['y', 'x']].values
            nm = f"{pname} (refined)"
            if nm in layers:
                ui_instance.viewer.layers.remove(nm)
            ui_instance.viewer.add_points(refined, name=nm, size=4,
                                          face_color='#00e5ff', border_color='white')

        if add_mask.isChecked():
            peak_mask = spots_to_mask(img.shape, df)
            mnm = f"{pname} (peak mask)"
            if mnm in layers:
                ui_instance.viewer.layers.remove(mnm)
            ui_instance.viewer.add_labels(peak_mask, name=mnm)

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            show_dataframes_dialog("Gaussian Localization",
                                   [('Per-spot fits', df.round(4))])
        except Exception:
            pass
        med = df['fwhm_x'].median()
        _info(f"Localized {len(df)} spots (median FWHM_x ≈ {med:.2f} px"
              + (f", {med*px:.3f} µm)." if px else ")."))

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QWidget, QScrollArea
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Gaussian Localization", area='right')

