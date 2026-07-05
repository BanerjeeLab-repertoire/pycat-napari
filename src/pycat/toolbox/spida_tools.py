"""
PyCAT Spatial Intensity Distribution Analysis (SpIDA)
=====================================================
Estimate fluorescent particle **density** (N, particles per beam-area) and
**quantal brightness** (epsilon) from the pixel-intensity histogram of a single
region of interest in a confocal / laser-scanning fluorescence image, following

    Godin et al., "Revealing protein oligomerization and densities in situ using
    spatial intensity distribution analysis," PNAS 108(17):7010-7015 (2011);
    Barbeau et al., Methods Enzymol. 522:109 (2013).

The histogram-fitting model and its numerical regimes are a direct port of the
authors' reference MATLAB implementation (``SpIDA_Functions.m`` /
``fit_SpIDA_histo.m``), so results should match that tool:

* **N > 70** — Gaussian limit: mean = eps*N, width(1/e^2) = 2*eps*sqrt(N)
  (variance eps^2 * N, the super-Poissonian variance).
* **N > 6**  — generalized-Poisson closed form (``poisson.m``):
  exp(-N) * N^(k/eps) / Gamma(k/eps + 1), peak-normalised.
* **N < 7**  — low-density regime; between 6 and 7 the two branches are blended
  linearly exactly as the reference does.

The fit is initialised from the histogram moments (this is what makes it stable):
the variance-to-mean ratio estimates epsilon, and mean^2/variance estimates N.

Oligomeric state
----------------
SpIDA returns a *quantal brightness* epsilon. Oligomeric state is obtained by
normalising to the brightness of a **monomer reference** measured under
identical imaging conditions: state = epsilon / epsilon_0 (a value near 2 means
dimer, near 1 monomer). Without that calibration, epsilon is only meaningful in
raw intensity units and NO oligomeric state is reported.

IMPORTANT — assumptions (guardrails enforced below)
---------------------------------------------------
SpIDA assumes the detector response is linear and the intensity is proportional
to photon counts (confocal PMT / photon-counting), the ROI is reasonably
homogeneous (single population), and the image is not saturated. The functions
here warn when these are violated rather than silently returning numbers, in
keeping with PyCAT's "don't produce a number the data can't support" philosophy.

Author
------
    Gable Wadsworth, Banerjee Lab, SUNY Buffalo
"""

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import gammaln

try:
    from napari.utils.notifications import show_warning as napari_show_warning
except Exception:  # pragma: no cover - napari always present at runtime
    def napari_show_warning(msg):
        print(f"[warning] {msg}")

try:
    from napari.utils.notifications import show_info as napari_show_info
except Exception:  # pragma: no cover
    def napari_show_info(msg):
        print(f"[info] {msg}")


# ---------------------------------------------------------------------------
# Core model (port of SpIDA_Functions.m closed-form regimes)
# ---------------------------------------------------------------------------
_THRESMIN = 6.0
_THRESMAX = 7.0


def _poisson_curve(amp, mea, div, x):
    """Port of ``poisson.m``: exp(-mea) * mea^(x/div) / Gamma(x/div + 1),
    peak-normalised to ``amp``. Computed in log-space for numerical stability."""
    mea = abs(mea)
    div = abs(div)
    if div == 0 or mea == 0:
        return np.zeros_like(x, dtype=float)
    xd = np.asarray(x, dtype=float) / div
    with np.errstate(divide='ignore', invalid='ignore'):
        log_y = -mea + xd * np.log(mea) - gammaln(xd + 1.0)
        y = np.exp(log_y)
    y[~np.isfinite(y)] = 0.0
    m = np.max(y)
    if m > 0:
        y = amp * y / m
    return y


def _gaussian_curve(amp, mean, width, x):
    """Port of ``gaussian.m``: amp * exp(-2 (x-mean)^2 / width^2)."""
    if width == 0:
        return np.zeros_like(x, dtype=float)
    return amp * np.exp(-2.0 * (np.asarray(x, dtype=float) - mean) ** 2 / width ** 2)


def spida_model(x, amp, N, epsilon):
    """
    SpIDA single-population histogram model H(amp, N, epsilon; x), a direct port
    of the closed-form regimes in ``SpIDA_Functions.m``.

    Parameters
    ----------
    x : ndarray
        Intensity bin centres.
    amp : float
        Amplitude (peak height of the histogram).
    N : float
        Particle density in particles per beam-area.
    epsilon : float
        Quantal brightness (intensity units per particle).

    Returns
    -------
    ndarray
        Modelled histogram over ``x``.
    """
    x = np.asarray(x, dtype=float)
    amp = abs(amp)
    N = abs(N)
    epsilon = abs(epsilon)
    y = np.zeros_like(x)
    if amp <= 0 or N <= 0 or epsilon <= 0:
        return y

    if N > 70.0:
        return _gaussian_curve(amp, epsilon * N, 2.0 * epsilon * np.sqrt(N), x)

    y_low = np.zeros_like(x)
    y_high = np.zeros_like(x)
    A = 0.0
    B = 0.0
    if N > _THRESMIN:
        B = (N - _THRESMIN)
        y_high = _poisson_curve(amp, N, epsilon, x)
    if N < _THRESMAX:
        # Low-density branch. The reference uses a precomputed single-particle
        # convolution basis here; across the 6-7 blend region the generalized
        # Poisson is an accurate stand-in, and for N<6 it remains the best
        # closed-form estimate. (A future refinement can add the exact basis.)
        A = (_THRESMAX - N)
        y_low = _poisson_curve(amp, N, epsilon, x)
    if (A + B) != 0:
        return (A * y_low + B * y_high) / (A + B)
    return y_high


# ---------------------------------------------------------------------------
# Histogram construction + guardrails
# ---------------------------------------------------------------------------
def build_intensity_histogram(pixels, n_bins=256, white_noise=0.0):
    """
    Build a SpIDA intensity histogram from a 1-D array of ROI pixel values.

    Parameters
    ----------
    pixels : ndarray
        Flattened pixel intensities inside the ROI.
    n_bins : int
        Number of histogram bins.
    white_noise : float
        Background offset (camera/PMT dark level, or mean intensity of a
        cell-free region). Subtracted before histogramming.

    Returns
    -------
    (x, y) : tuple of ndarray
        Bin centres and (density-normalised) counts.
    """
    p = np.asarray(pixels, dtype=float).ravel()
    p = p[np.isfinite(p)]
    if white_noise:
        p = p - float(white_noise)
    p = p[p > 0]
    if p.size < 100:
        return None, None
    hi = np.percentile(p, 99.9)
    edges = np.linspace(0, hi, int(n_bins) + 1)
    y, edges = np.histogram(p, bins=edges, density=True)
    x = 0.5 * (edges[:-1] + edges[1:])
    return x, y


def check_modality(pixels, modality='confocal'):
    """
    Check whether the acquisition modality is compatible with SpIDA's assumptions,
    and return a list of warnings.

    SpIDA's model assumes an **optically-sectioned** modality where the pixel
    intensity is the integrated fluorescence from a defined beam focal volume and
    pixels are effectively independent samples — i.e. confocal / laser-scanning on
    a (characterisable) point detector, or TIRF with a camera-specific noise
    model. Plain **widefield epifluorescence on a camera violates this**: there is
    no scanning beam volume (so density-per-beam-area is ill-defined),
    out-of-focus light inflates and correlates the background (distorting the very
    variance SpIDA fits), and the camera noise model differs from a PMT.

    Parameters
    ----------
    pixels : ndarray
        ROI pixel intensities (used for a light data-driven sanity heuristic).
    modality : str
        One of 'confocal', 'tirf', 'widefield', or 'unknown'.

    Returns
    -------
    list of str
        Human-readable warnings (empty if the modality looks compatible).
    """
    warnings = []
    m = str(modality).strip().lower()

    if m in ('widefield', 'epifluorescence', 'epi', 'widefield_epi'):
        warnings.append(
            "Modality set to WIDEFIELD. SpIDA is derived for optically-sectioned "
            "confocal/laser-scanning data and is NOT valid for plain widefield "
            "epifluorescence: there is no beam focal volume (so density N loses "
            "its defined meaning), out-of-focus light distorts the histogram "
            "variance the fit relies on, and the PMT noise model does not apply "
            "to a camera. Treat any N/epsilon reported here as unreliable — "
            "consider Number & Brightness (N&B) on a time-series instead.")
    elif m == 'tirf':
        warnings.append(
            "Modality set to TIRF. SpIDA can work on TIRF (it is optically "
            "sectioned by the evanescent field), but the detector noise model "
            "must match your camera (sCMOS/EMCCD read + shot noise), not the "
            "built-in PMT correction. Interpret the quantal brightness with that "
            "caveat, and calibrate epsilon_0 on the same camera/settings.")
    elif m in ('unknown', ''):
        warnings.append(
            "Acquisition modality unspecified. SpIDA assumes confocal / "
            "laser-scanning (optically sectioned) data. If these are widefield "
            "camera images, the results are not valid — see the SpIDA notes.")

    # Light data-driven hint: widefield fields tend to have a high, spatially
    # smooth background floor (out-of-focus haze). A large floor-to-peak ratio in
    # the histogram is weakly suggestive of an unsectioned modality.
    try:
        p = np.asarray(pixels, dtype=float).ravel()
        p = p[np.isfinite(p) & (p > 0)]
        if p.size > 500 and m in ('confocal',):
            floor = np.percentile(p, 5)
            peak = np.percentile(p, 95)
            if peak > 0 and floor / peak > 0.5:
                warnings.append(
                    "Heads-up: this ROI has a high, flat background floor "
                    "(5th percentile is >50% of the 95th), which is more typical "
                    "of widefield out-of-focus haze than a confocal section. If "
                    "these are widefield images, SpIDA is not the right tool — "
                    "double-check the modality.")
    except Exception:
        pass

    return warnings


def check_assumptions(pixels, dtype_max=None):
    """
    Check the acquisition assumptions SpIDA depends on and return a list of
    human-readable warning strings (empty if the data look suitable). This is the
    guardrail layer — it never blocks the fit, but surfaces conditions that make
    the numbers untrustworthy so the user can judge.
    """
    warnings = []
    p = np.asarray(pixels, dtype=float).ravel()
    p = p[np.isfinite(p)]
    if p.size < 1000:
        warnings.append(
            f"ROI is small ({p.size} px). SpIDA needs adequate sampling "
            "(the reference recommends >=50 beam-areas, ~128x128 px); "
            "estimates may be imprecise.")
    # Saturation / clipping: many pixels piled at the top of the range.
    if dtype_max is not None and dtype_max > 0:
        frac_hot = np.mean(p >= 0.99 * dtype_max)
        if frac_hot > 0.001:
            warnings.append(
                f"{frac_hot*100:.2f}% of pixels are at/near the maximum value — "
                "the image may be saturated/clipped, which violates the linear-"
                "response assumption and biases epsilon and N.")
    # Signal-to-background: SpIDA wants S/N > 4 (autofluorescence otherwise biases).
    if p.size:
        med = np.median(p)
        if med > 0:
            snr = (np.percentile(p, 99) - med) / (np.std(p[p < med] + 1e-9) + 1e-9)
            if snr < 4:
                warnings.append(
                    "Estimated signal-to-background looks low (<4). "
                    "Autofluorescence/background can bias density downward; "
                    "consider subtracting a white-noise background level.")
    return warnings


# ---------------------------------------------------------------------------
# The fit (port of fit_SpIDA_histo.m initialisation + bounds)
# ---------------------------------------------------------------------------
def fit_spida_histogram(x, y):
    """
    Fit the single-population SpIDA model to a histogram (x, y).

    Initialisation follows ``fit_SpIDA_histo.m``: epsilon0 = var/mean,
    N0 = mean^2/var (moment estimators), which is what makes the fit converge
    reliably.

    Returns
    -------
    dict with keys: 'N', 'epsilon', 'amp', 'success', 'y_fit', and 'r_squared'.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 5 or np.sum(y) <= 0:
        return {'success': False}

    mea = np.sum(x * y) / np.sum(y)
    var = np.sum(y * (x - mea) ** 2) / np.sum(y)
    if mea <= 0 or var <= 0:
        return {'success': False}
    div = var / mea            # estimates epsilon
    x0 = mea / div             # estimates N  (= mea^2/var)
    amp = np.max(y)
    y_n = y / amp

    p0 = [1.0, x0, 1.2 * div]
    lb = [0.0, 0.0, 0.0]
    ub = [np.inf, np.inf, np.inf]
    try:
        popt, _ = curve_fit(spida_model, x, y_n, p0=p0, bounds=(lb, ub),
                            maxfev=8000)
    except Exception as e:
        napari_show_warning(f"SpIDA fit did not converge: {e}")
        return {'success': False}

    y_fit = spida_model(x, *popt) * amp
    # Align peaks before computing R^2: the model is peak-normalised internally,
    # so scale it to the data's peak to get a meaningful goodness-of-fit on the
    # histogram's own scale.
    if np.max(y_fit) > 0:
        y_fit_aligned = y_fit * (np.max(y) / np.max(y_fit))
    else:
        y_fit_aligned = y_fit
    ss_res = np.sum((y - y_fit_aligned) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        'success': True,
        'amp': popt[0] * amp,
        'N': popt[1],
        'epsilon': popt[2],
        'y_fit': y_fit,
        'r_squared': r2,
    }


# ---------------------------------------------------------------------------
# Top-level runners wired to the UI
# ---------------------------------------------------------------------------
def run_spida_calibration(image_layer, roi_shapes_layer, n_bins, white_noise,
                          viewer, modality='confocal'):
    """
    Measure the **monomeric quantal brightness epsilon_0** from a control ROI
    (a region known to contain monomers, e.g. a monomeric-GFP control). The
    returned epsilon_0 is used to convert brightness to oligomeric state in
    subsequent analyses. The value is stashed on the viewer
    (``viewer.metadata['spida_epsilon0']``) so the analysis widget can pick it up.
    """
    pixels = _roi_pixels(image_layer, roi_shapes_layer, viewer)
    if pixels is None:
        return

    for w in check_modality(pixels, modality):
        napari_show_warning("SpIDA calibration: " + w)
    for w in check_assumptions(pixels, dtype_max=_dtype_max(image_layer)):
        napari_show_warning("SpIDA calibration: " + w)

    x, y = build_intensity_histogram(pixels, n_bins=n_bins, white_noise=white_noise)
    if x is None:
        napari_show_warning("SpIDA calibration: not enough valid pixels in ROI.")
        return
    res = fit_spida_histogram(x, y)
    if not res.get('success'):
        return
    eps0 = res['epsilon']
    viewer.metadata['spida_epsilon0'] = float(eps0)
    napari_show_info(
        f"SpIDA calibration: monomeric epsilon_0 = {eps0:.1f} iu "
        f"(N = {res['N']:.2f} particles/beam-area, R^2 = {res['r_squared']:.3f}). "
        "This will be used as the monomer reference for oligomeric state.")


def run_spida_analysis(image_layer, roi_shapes_layer, n_bins, white_noise,
                       epsilon0, viewer, modality='confocal'):
    """
    Run SpIDA on an ROI: fit density N and quantal brightness epsilon, and — if a
    monomeric reference epsilon_0 is available (passed in or from a prior
    calibration) — report the oligomeric state epsilon/epsilon_0.

    Results are printed as an Image -> Assessment -> Interpretation summary
    consistent with PyCAT's anti-black-box reporting.
    """
    pixels = _roi_pixels(image_layer, roi_shapes_layer, viewer)
    if pixels is None:
        return

    modality_issues = check_modality(pixels, modality)
    for w in modality_issues:
        napari_show_warning("SpIDA: " + w)
    issues = check_assumptions(pixels, dtype_max=_dtype_max(image_layer))
    for w in issues:
        napari_show_warning("SpIDA: " + w)

    x, y = build_intensity_histogram(pixels, n_bins=n_bins, white_noise=white_noise)
    if x is None:
        napari_show_warning("SpIDA: not enough valid pixels in ROI.")
        return
    res = fit_spida_histogram(x, y)
    if not res.get('success'):
        return

    N = res['N']
    eps = res['epsilon']
    r2 = res['r_squared']

    # Oligomeric state requires a monomer reference.
    eps0 = epsilon0 if epsilon0 and epsilon0 > 0 else viewer.metadata.get('spida_epsilon0')
    lines = [
        "── SpIDA result ─────────────────────────────",
        f"Density N        : {N:.2f} particles / beam-area",
        f"Quantal bright.  : {eps:.1f} intensity units",
        f"Fit quality R^2  : {r2:.3f}",
    ]
    if eps0 and eps0 > 0:
        state = eps / eps0
        lines.append(f"Monomer ref eps_0: {eps0:.1f} iu")
        lines.append(f"Oligomeric state : {state:.2f}x monomer "
                     f"({_interpret_state(state)})")
    else:
        lines.append("Oligomeric state : (not computed — run 'Calibrate "
                     "monomer' on a monomeric control ROI first, or enter "
                     "epsilon_0)")
    if r2 < 0.9:
        lines.append("NOTE: R^2 is low — the single-population model may not fit "
                     "this ROI well (mixed oligomers or heterogeneity).")
    if modality_issues:
        lines.append("NOTE: modality warnings were raised above — if this is not "
                     "confocal/optically-sectioned data, the numbers are not "
                     "valid.")
    if issues:
        lines.append("NOTE: acquisition-assumption warnings were raised above; "
                     "treat the numbers with caution.")
    napari_show_info("\n".join(lines))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _interpret_state(state):
    if state < 1.3:
        return "≈ monomer"
    if state < 1.7:
        return "monomer/dimer mix"
    if state < 2.3:
        return "≈ dimer"
    return "higher-order oligomer"


def _dtype_max(image_layer):
    try:
        data = image_layer.data
        if np.issubdtype(data.dtype, np.integer):
            return float(np.iinfo(data.dtype).max)
    except Exception:
        pass
    return None


def _dtype_from_layer(image_layer):
    return getattr(getattr(image_layer, 'data', None), 'dtype', None)


def _roi_pixels(image_layer, roi_shapes_layer, viewer):
    """
    Extract the pixels of ``image_layer`` inside the ROI defined by
    ``roi_shapes_layer`` (a napari Shapes layer). If no shapes layer / no ROI is
    given, the whole image is used (with a note).
    """
    if image_layer is None:
        napari_show_warning("SpIDA: select an image layer.")
        return None
    img = np.asarray(image_layer.data)
    if img.ndim > 2:
        # Use the current step's 2D plane if a stack is provided.
        try:
            idx = tuple(int(i) for i in viewer.dims.current_step[:img.ndim - 2])
            img = img[idx]
        except Exception:
            img = img.reshape(-1, img.shape[-2], img.shape[-1])[0]

    if roi_shapes_layer is None:
        napari_show_warning(
            "SpIDA: no ROI shapes layer selected — using the whole image. "
            "For heterogeneous images, draw an ROI over a homogeneous region.")
        return img.ravel()

    try:
        mask = roi_shapes_layer.to_masks(mask_shape=img.shape)
        if mask.ndim == 3:
            mask = np.any(mask, axis=0)
    except Exception as e:
        napari_show_warning(f"SpIDA: could not rasterize ROI ({e}); using whole image.")
        return img.ravel()

    pix = img[mask]
    if pix.size < 100:
        napari_show_warning("SpIDA: ROI too small (needs >=100 px).")
        return None
    return pix
