"""
PyCAT Spatial Randomness Tools
===============================
Quantify how far the intensity distribution of a (possibly low-contrast)
image or ROI departs from spatial randomness — to justify treating faint,
transient intensity "clusters" as real spatial structure rather than the
pixel noise inherent to imaging (e.g. intrinsic autofluorescence of a
condensate).

The problem
-----------
A noisy image with no true structure has spatially *independent* pixel
intensities: knowing one pixel tells you nothing about its neighbours beyond
what the global histogram says. Real clustering introduces spatial
*autocorrelation* — nearby pixels are more similar than expected by chance.
These tools measure that departure several complementary ways and, crucially,
calibrate it against a null distribution built by randomly permuting the same
pixels (destroying spatial structure while preserving the intensity
histogram exactly). The permutation null is the direct analogue of the manual
"compare against np.random" approach, done rigorously.

Statistics computed
--------------------
1. Moran's I         — global spatial autocorrelation (+1 clustered, 0 random,
                       −1 dispersed/checkerboard).
2. Join-count / local variance ratio — ratio of local (neighbourhood) variance
                       to global variance; <1 indicates smoothing/clustering.
3. Spatial entropy deficit — how much lower the joint neighbour-intensity
                       entropy is than the shuffled (random) expectation.
4. Autocorrelation length — 1/e decay distance of the radial autocorrelation
                       (0 for pure noise; grows with cluster size).

Each is reported with a permutation-based z-score and empirical p-value:
z = (observed − mean_shuffled) / std_shuffled, so a large positive z on
Moran's I means "far more clustered than random."

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

from typing import Optional

import warnings
import numpy as np
import pandas as pd
import scipy.ndimage as ndi

# Via the notification shim: keeps the statistics importable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Core statistics
# ---------------------------------------------------------------------------

def morans_I_headroom(image, mask=None):
    """Can Moran's I move at all on this image? Returns the headroom and a verdict.

    Moran's I is a **blend** of the signal's autocorrelation and the noise's::

        I  ~=  (signal variance fraction)  x  I_signal

    ``I_signal`` is close to 1 for any *extended* object — every pixel inside a droplet
    looks like its neighbour regardless of where the droplet sits. So on a bright image of
    extended objects, I is pinned near 1 and **has no room left to respond to anything**.
    Rearranging the objects entirely cannot move it.

    This was measured across 63 (object size, SNR) combinations, comparing an image of
    dispersed objects with one of the *same* objects aggregated into a clump:

    ==================  ===  ==========  =========
    headroom (1 − I)    n    median gap  max gap
    ==================  ===  ==========  =========
    **< 0.02**          6    0.0043      **0.0093**
    0.02 – 0.15         18   0.018–0.041 0.158
    > 0.15              39   0.0853      0.297
    ==================  ===  ==========  =========

    Below a headroom of 0.02 the difference between *dispersed* and *aggregated* never
    exceeded 0.009 — the statistic is dead, and any value it reports is a property of the
    optics and the object size, not of the arrangement.

    **The threshold is on headroom, not on object size.** An earlier attempt to give a size
    rule ("useless above ~2 px") was wrong: the same object size flips between usable and
    saturated depending on SNR, because noise is what dilutes I away from 1 and gives it
    room to move. Headroom captures size and SNR together, and it is measurable from the
    single image in hand with no ground truth.
    """
    I = morans_I(image, mask)
    headroom = 1.0 - float(I)
    if not np.isfinite(I):
        return dict(morans_I=np.nan, headroom=np.nan, usable=False,
                    verdict="Moran's I could not be computed.")
    if headroom < 0.02:
        usable, verdict = False, (
            f"Moran's I = {I:.3f} (headroom {headroom:.3f}). **SATURATED — this value "
            f"carries no information about spatial arrangement.** Across 63 tested "
            f"conditions, an image with this little headroom never moved by more than "
            f"0.009 even when its objects were rearranged from fully dispersed to fully "
            f"aggregated. What you are reading is the size of the objects and the "
            f"brightness of the image, not their arrangement.")
    elif headroom < 0.15:
        usable, verdict = False, (
            f"Moran's I = {I:.3f} (headroom {headroom:.3f}). Close to saturation: it may "
            f"still respond to arrangement, but weakly and unreliably. Do not draw "
            f"conclusions from small changes in this value.")
    else:
        usable, verdict = True, (
            f"Moran's I = {I:.3f} (headroom {headroom:.3f}). The statistic has room to "
            f"respond to spatial arrangement.")
    return dict(morans_I=float(I), headroom=float(headroom),
                usable=bool(usable), verdict=verdict)


def _phase_randomised_surrogate(image, rng):
    """A surrogate with the SAME spatial autocorrelation but NO real structure.

    Keeps the amplitude spectrum (and therefore, by Wiener-Khinchin, the autocorrelation
    exactly) while replacing the **phases** — which is where spatial structure actually
    lives. So the surrogate has the microscope's blur and none of the biology.

    The phases are taken from the FFT of a random *real* field, which guarantees the
    Hermitian symmetry an inverse-real transform requires. **Do not "enforce" symmetry by
    averaging a uniform phase array with its own reversal** — that was tried, and it
    produces surrogates with a kurtosis of ~650 against the data's ~0, i.e. a wildly
    biased null that makes every test fire. It was caught only by checking the surrogate's
    own statistics against the data's.
    """
    amp = np.abs(np.fft.fft2(image))
    phase = np.angle(np.fft.fft2(rng.normal(0.0, 1.0, image.shape)))
    return np.fft.ifft2(amp * np.exp(1j * phase)).real


def structure_beyond_optics(image, mask=None, n_permutations=100, seed=0):
    """Is there spatial structure BEYOND what the microscope's blur imposes?

    Why the existing permutation test cannot answer this
    ----------------------------------------------------
    ``measure_spatial_randomness`` compares Moran's I against a **pixel-shuffled** null.
    That is the correct null for the question *"is this image autocorrelated at all, versus
    spatially independent noise?"* — and it is kept, because that question is legitimate.

    But it is not the question a microscopist has. **Every image from a real microscope is
    autocorrelated, because the PSF guarantees it.** Measured: pure noise passed through a
    PSF scores Moran's I = 0.88, z = 160 against a pixel-shuffled null — "highly
    structured", with no biology in the field at all.

    Worse, Moran's I cannot separate the cases even in principle:

    ===========================  ==========
    image                        Moran's I
    ===========================  ==========
    EMPTY field (noise + optics)   0.255
    faint condensates              0.253
    clear condensates              0.262
    bright condensates             0.260
    ===========================  ==========

    An empty field and a field full of condensates score the same. And no change of *null*
    can rescue it: **Moran's I is a function of the autocorrelation**, so any null that
    preserves the autocorrelation preserves Moran's I by construction. Against the
    phase-randomised null below it has 4 % false positives (correctly calibrated) but
    **0–12 % power** — it is blind, not merely miscalibrated. The statistic is wrong for
    this question, not just its null.

    What works
    ----------
    **Kurtosis against a phase-randomised null.** Kurtosis is sensitive to *phase* — to the
    fact that bright pixels are *concentrated* rather than spread — which is exactly what a
    condensate is, and exactly what blur alone cannot manufacture.

    Characterised on synthetic fields with condensates at a controlled SNR (100
    realisations per point):

    ======  =================
    SNR     detected
    ======  =================
    0       **0 %**  ← false-positive rate; target is ~5 %
    1       0 %
    2       10 %
    3       53 %
    4       **100 %**
    ≥ 5     **100 %**
    ======  =================

    Calibrated on an empty field, and reliable from about SNR 4 upward. Below SNR 3 it will
    not see the structure — and it says so rather than guessing.

    Returns
    -------
    dict: observed kurtosis, the null mean/SD, z, an empirical p-value, and a verdict.
    """
    img = np.asarray(image, dtype=float)
    if mask is not None:
        m = np.asarray(mask) != 0
        if not m.any():
            return dict(p_value=np.nan, verdict="Empty mask: nothing to test.")
        # Fill outside the mask with the in-mask mean so the FFT is not dominated by an
        # artificial edge; the statistic itself is still evaluated inside the mask only.
        img = np.where(m, img, float(img[m].mean()))
    else:
        m = None

    from scipy import stats as _st

    def _kurt(a):
        v = a[m] if m is not None else a.ravel()
        return float(_st.kurtosis(v))

    rng = np.random.default_rng(seed)
    obs = _kurt(img)
    null = np.array([_kurt(_phase_randomised_surrogate(img, rng))
                     for _ in range(int(n_permutations))])
    null = null[np.isfinite(null)]
    if null.size == 0 or not np.isfinite(obs):
        return dict(p_value=np.nan, verdict="Surrogate null could not be computed.")

    mu, sd = float(null.mean()), float(null.std())
    z = (obs - mu) / max(sd, 1e-12)
    p = float((np.sum(np.abs(null - mu) >= abs(obs - mu)) + 1) / (null.size + 1))

    if p < 0.05:
        verdict = (f"Kurtosis {obs:.2f} vs {mu:.2f} in phase-randomised surrogates "
                   f"(z = {z:.1f}, p = {p:.3f}). There IS structure beyond what the "
                   f"optics impose: the bright pixels are concentrated, not merely "
                   f"blurred.")
    else:
        verdict = (f"Kurtosis {obs:.2f} vs {mu:.2f} in phase-randomised surrogates "
                   f"(z = {z:.1f}, p = {p:.3f}). **No structure beyond what the point "
                   f"spread function alone would produce.** Note this test is reliable "
                   f"only from about SNR 4 upward \u2014 a negative result at low SNR "
                   f"means 'not detected', not 'not there'.")

    return dict(kurtosis=obs, null_mean=mu, null_sd=sd, z_score=float(z),
                p_value=p, n_permutations=int(null.size),
                structured=bool(p < 0.05), verdict=verdict)


def morans_I(image: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    """
    Global Moran's I spatial autocorrelation using a rook (4-neighbour)
    contiguity weight.

    I = (N / W) · Σ_ij w_ij (x_i − x̄)(x_j − x̄) / Σ_i (x_i − x̄)²

    +1 = perfect clustering (neighbours identical), 0 ≈ spatial randomness,
    −1 = perfect dispersion (checkerboard). Only pixels inside `mask` are used;
    neighbour pairs where either pixel is outside the mask are dropped.
    """
    img = np.asarray(image, dtype=float)
    if mask is None:
        mask = np.ones_like(img, dtype=bool)
    else:
        mask = np.asarray(mask) > 0

    vals = img[mask]
    n = vals.size
    if n < 4:
        return np.nan
    mean = vals.mean()
    denom = np.sum((vals - mean) ** 2)
    if denom == 0:
        return np.nan

    # Sum over rook-neighbour pairs (right and down, each pair counted once)
    dev = img - mean
    num = 0.0
    W = 0.0
    # Horizontal neighbours
    m_h = mask[:, :-1] & mask[:, 1:]
    num += np.sum(dev[:, :-1][m_h] * dev[:, 1:][m_h])
    W += np.count_nonzero(m_h)
    # Vertical neighbours
    m_v = mask[:-1, :] & mask[1:, :]
    num += np.sum(dev[:-1, :][m_v] * dev[1:, :][m_v])
    W += np.count_nonzero(m_v)

    if W == 0:
        return np.nan
    # Each pair contributes symmetrically (w_ij = w_ji), so multiply by 2
    return (n / (2.0 * W)) * (2.0 * num) / denom


def local_variance_ratio(image: np.ndarray, mask: Optional[np.ndarray] = None,
                         window: int = 3) -> float:
    """
    Ratio of mean local (windowed) variance to global variance.

    For spatially-random pixels, local variance ≈ global variance → ratio ≈ 1.
    Clustering makes neighbourhoods more homogeneous → local variance < global
    → ratio < 1. Values well below 1 indicate real spatial structure.
    """
    img = np.asarray(image, dtype=float)
    if mask is None:
        mask = np.ones_like(img, dtype=bool)
    else:
        mask = np.asarray(mask) > 0
    vals = img[mask]
    global_var = vals.var()
    if global_var == 0:
        return np.nan
    mean_f = ndi.uniform_filter(img, size=window)
    mean_sq = ndi.uniform_filter(img ** 2, size=window)
    local_var = np.clip(mean_sq - mean_f ** 2, 0, None)
    return float(local_var[mask].mean() / global_var)


def autocorrelation_length(image: np.ndarray,
                           mask: Optional[np.ndarray] = None) -> float:
    """
    Characteristic autocorrelation length: the radial distance (px) at which
    the normalised spatial autocorrelation drops to 1/e.

    Pure noise decorrelates in one pixel (≈0); real clusters give a longer
    correlation length that scales with cluster size.
    """
    img = np.asarray(image, dtype=float)
    if mask is not None:
        m = np.asarray(mask) > 0
        img = np.where(m, img - img[m].mean(), 0.0)
    else:
        img = img - img.mean()

    F = np.fft.fft2(img)
    acf = np.fft.fftshift(np.real(np.fft.ifft2(F * np.conj(F))))
    if acf.max() <= 0:
        return np.nan
    acf /= acf.max()

    cy, cx = np.array(acf.shape) // 2
    # Radial profile
    y, x = np.indices(acf.shape)
    r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2).astype(int)
    tbin = np.bincount(r.ravel(), acf.ravel())
    nr = np.bincount(r.ravel())
    with np.errstate(invalid='ignore', divide='ignore'):
        radial = tbin / nr
    # First radius where the profile falls below 1/e
    below = np.where(radial < (1.0 / np.e))[0]
    return float(below[0]) if below.size else float(len(radial))


def _spatial_entropy(image: np.ndarray, mask: np.ndarray, bins: int = 16) -> float:
    """
    Joint entropy of adjacent-pixel intensity pairs (a co-occurrence entropy).
    Lower than the shuffled expectation → neighbouring pixels are more
    predictable from each other → spatial structure.
    """
    img = np.asarray(image, dtype=float)
    m = np.asarray(mask) > 0
    vals = img[m]
    if vals.size < 4 or vals.max() == vals.min():
        return np.nan
    lo, hi = vals.min(), vals.max()
    digit = np.clip(((img - lo) / (hi - lo) * (bins - 1)).astype(int), 0, bins - 1)

    pairs = []
    m_h = m[:, :-1] & m[:, 1:]
    pairs.append((digit[:, :-1][m_h], digit[:, 1:][m_h]))
    m_v = m[:-1, :] & m[1:, :]
    pairs.append((digit[:-1, :][m_v], digit[1:, :][m_v]))
    a = np.concatenate([p[0] for p in pairs])
    b = np.concatenate([p[1] for p in pairs])
    if a.size == 0:
        return np.nan
    joint = np.histogram2d(a, b, bins=bins, range=[[0, bins], [0, bins]])[0]
    p = joint / joint.sum()
    p = p[p > 0]
    return float(-np.sum(p * np.log2(p)))


# ---------------------------------------------------------------------------
# Permutation null + orchestration
# ---------------------------------------------------------------------------

def measure_spatial_randomness(
    image: np.ndarray,
    mask: Optional[np.ndarray] = None,
    n_permutations: int = 200,
    window: int = 3,
    entropy_bins: int = 16,
    random_seed: Optional[int] = None,
) -> dict:
    """
    Measure departure from spatial randomness and calibrate every statistic
    against a permutation null.

    The null is built by randomly shuffling the pixel intensities within the
    mask `n_permutations` times. Shuffling destroys all spatial structure
    while preserving the intensity histogram exactly — so any statistic that
    differs from its shuffled distribution reflects genuine spatial
    organisation, not the intensity distribution itself. This is the rigorous
    form of comparing an image against `np.random`.

    Parameters
    ----------
    image : 2D intensity image.
    mask : optional boolean ROI. If None, the whole image is used.
    n_permutations : number of shuffles for the null distribution.
    window : neighbourhood size (px) for the local variance ratio.
    entropy_bins : intensity bins for the co-occurrence entropy.
    random_seed : seed for reproducibility.

    Returns
    -------
    dict with, for each statistic, the observed value, the null mean/std,
    a z-score, and an empirical two-sided p-value; plus a plain-language
    verdict on whether the ROI is distinguishable from spatial noise.
    """
    rng = np.random.default_rng(random_seed)
    img = np.asarray(image, dtype=float)
    if img.ndim != 2:
        raise ValueError("Spatial randomness analysis requires a 2D image or ROI.")
    if mask is None:
        mask = np.ones_like(img, dtype=bool)
    else:
        mask = np.asarray(mask) > 0

    if np.count_nonzero(mask) < 16:
        raise ValueError("ROI too small (need at least 16 pixels).")

    # Observed statistics
    obs = {
        'morans_I':            morans_I(img, mask),
        'local_variance_ratio': local_variance_ratio(img, mask, window),
        'autocorr_length_px':  autocorrelation_length(img, mask),
        'spatial_entropy':     _spatial_entropy(img, mask, entropy_bins),
    }

    # Permutation null: shuffle intensities within the mask
    idx = np.where(mask)
    vals = img[idx].copy()
    null = {k: np.empty(n_permutations) for k in obs}
    for p in range(n_permutations):
        shuffled = img.copy()
        perm = rng.permutation(vals)
        shuffled[idx] = perm
        null['morans_I'][p]            = morans_I(shuffled, mask)
        null['local_variance_ratio'][p] = local_variance_ratio(shuffled, mask, window)
        null['autocorr_length_px'][p]  = autocorrelation_length(shuffled, mask)
        null['spatial_entropy'][p]     = _spatial_entropy(shuffled, mask, entropy_bins)

    rows = []
    for k in obs:
        nd = null[k][np.isfinite(null[k])]
        o = obs[k]
        if nd.size < 2 or not np.isfinite(o):
            rows.append({'statistic': k, 'observed': o, 'null_mean': np.nan,
                         'null_std': np.nan, 'z_score': np.nan, 'p_value': np.nan})
            continue
        mu, sd = nd.mean(), nd.std()
        z = (o - mu) / sd if sd > 0 else np.nan
        # Two-sided empirical p: fraction of null at least as extreme
        p_emp = (np.count_nonzero(np.abs(nd - mu) >= abs(o - mu)) + 1) / (nd.size + 1)
        rows.append({'statistic': k, 'observed': round(float(o), 5),
                     'null_mean': round(float(mu), 5), 'null_std': round(float(sd), 5),
                     'z_score': round(float(z), 3) if np.isfinite(z) else np.nan,
                     'p_value': round(float(p_emp), 4)})

    results_df = pd.DataFrame(rows)

    # ── Verdict ────────────────────────────────────────────────────────────────
    #
    # Moran's I against a PIXEL-SHUFFLED null answers exactly one question: "is this
    # image autocorrelated at all, versus spatially independent noise?" That question is
    # legitimate, and the test is correct for it.
    #
    # It is NOT the question a microscopist has. Every image from a real microscope is
    # autocorrelated, because the PSF guarantees it: pure noise through a PSF scores
    # Moran's I = 0.88, z = 160 against this null -- "highly structured", with no biology
    # in the field at all. And Moran's I cannot separate the cases even in principle --
    # an EMPTY field (0.255) and one full of condensates (0.260) score the same.
    #
    # So the verdict below is worded for what it actually establishes, and the real
    # question ("is there structure BEYOND the optics?") is answered separately by
    # `structure_beyond_optics`, which uses a phase-randomised null and kurtosis.
    # Is Moran's I even ABLE to say anything about this image? (see morans_I_headroom)
    headroom_info = morans_I_headroom(image, mask)
    if not headroom_info['usable']:
        napari_show_warning("Spatial randomness: " + headroom_info['verdict'])

    mi_row = results_df[results_df['statistic'] == 'morans_I'].iloc[0]
    mi_z = mi_row['z_score']
    if not np.isfinite(mi_z):
        verdict = "Inconclusive \u2014 statistic could not be computed."
    elif mi_z > 3 and mi_row['p_value'] < 0.05:
        verdict = (f"Spatially autocorrelated (Moran's I z={mi_z:.1f}, "
                   f"p={mi_row['p_value']:.3g}) \u2014 neighbouring pixels are more "
                   f"similar than a random rearrangement of the same intensities. "
                   f"NOTE: this is true of essentially EVERY microscope image, because "
                   f"the point spread function alone produces it. It is not evidence of "
                   f"biological structure. See the 'structure beyond optics' result.")
    elif mi_z > 2:
        verdict = (f"Weakly autocorrelated (Moran's I z={mi_z:.1f}) \u2014 suggestive "
                   f"but not strongly beyond a random rearrangement of the same "
                   f"intensities.")
    else:
        verdict = (f"Consistent with spatially independent noise (Moran's I "
                   f"z={mi_z:.1f}) \u2014 not distinguishable from a random "
                   f"rearrangement of the same pixel intensities. For a real microscope "
                   f"image this is unusual, and may indicate the ROI is background only.")

    # The question the user actually has: is there structure the OPTICS cannot fake?
    try:
        beyond = structure_beyond_optics(image, mask, n_permutations=min(100, n_permutations))
    except Exception as _exc:
        warnings.warn(f"structure_beyond_optics failed ({_exc}).")
        beyond = dict(p_value=float('nan'),
                      verdict="Structure-beyond-optics test unavailable.")

    # ── DEMOTION ───────────────────────────────────────────────────────────────
    #
    # Moran's I is no longer the primary structure indicator. It is saturated on any
    # bright image of extended objects (headroom < 0.02 -> it cannot move by more than
    # ~0.009 even when the objects go from fully dispersed to fully aggregated), and it
    # is blind to structure-beyond-optics by construction. It is retained because it is
    # genuinely informative on SMLM / near-point data, where it has headroom and a real
    # discriminating gap (0.117, versus ~0.002 on condensate images).
    #
    # The headline verdict now comes from structure_beyond_optics. Moran's I is reported
    # WITH its headroom, so a saturated value cannot be mistaken for a finding.
    # See docs/source/usage/spatial_randomness.rst.
    primary = beyond.get('verdict', '') or verdict
    if not headroom_info['usable']:
        primary = (primary + "  [Moran's I is SATURATED on this image and carries no "
                   "information about arrangement — see the spatial-randomness guide.]")

    return dict(observed=obs, null=null, results_df=results_df,
                verdict=primary,                 # structure_beyond_optics leads
                morans_verdict=verdict,          # the old one, kept but demoted
                n_permutations=n_permutations,
                structure_beyond_optics=beyond,
                morans_I_headroom=headroom_info)

# ---------------------------------------------------------------------------
# UI entry point (Toolbox → Spatial Metrology)
# ---------------------------------------------------------------------------

def _build_spatial_randomness_form(ui_instance):
    """Build the Spatial Randomness input form.

    Returns the group box plus every input widget the run callback needs, in
    the same construction order they were created inline.
    """
    import napari
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QSpinBox, QPushButton, QProgressBar,
        QComboBox, QSizePolicy)

    grp  = QGroupBox("Spatial Randomness (departure from noise)")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4)
    form.setSpacing(5)

    desc = QLabel(
        "Tests whether faint intensity clustering in an image or ROI is real "
        "spatial structure or just imaging noise, by comparing spatial "
        "statistics against a permutation null (shuffled pixels).")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    image_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    image_dd.setToolTip("Intensity image (2D, or one frame of a stack) to test.")
    form.addRow("Image:", image_dd)

    roi_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    roi_dd.setToolTip("Optional ROI/labels mask. 'None' uses the whole image.")
    form.addRow("ROI mask (optional):", roi_dd)

    frame_spin = QSpinBox()
    frame_spin.setRange(0, 100000); frame_spin.setValue(0)
    frame_spin.setToolTip("If the image is a stack, which frame index to test.")
    form.addRow("Frame (if stack):", frame_spin)

    perm_spin = QSpinBox()
    perm_spin.setRange(20, 5000); perm_spin.setValue(200)
    perm_spin.setToolTip(
        "Number of pixel-shuffle permutations for the null distribution. "
        "More = more precise p-values but slower. 200 is usually enough.")
    form.addRow("Permutations:", perm_spin)

    window_spin = QSpinBox()
    window_spin.setRange(2, 25); window_spin.setValue(3)
    window_spin.setToolTip("Neighbourhood window (px) for the local variance ratio.")
    form.addRow("Local window (px):", window_spin)

    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton("▶  Measure Departure from Randomness")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); form.addRow(btn)

    return grp, image_dd, roi_dd, frame_spin, perm_spin, window_spin, prog, btn


def _add_spatial_randomness(ui_instance, layout=None, separate_widget=False):
    """
    Widget to measure departure-from-randomness of an image or ROI.

    Uses the (ui_instance, layout, separate_widget) convention so it slots
    into the Toolbox menu with {'separate_widget': True} or into a pipeline
    dock by passing a layout.
    """
    (grp, image_dd, roi_dd, frame_spin, perm_spin, window_spin,
     prog, btn) = _build_spatial_randomness_form(ui_instance)

    def _on_run():
        from napari.utils.notifications import (
            show_info as _info, show_warning as _warn)
        import numpy as _np

        iname = image_dd.currentText()
        if iname == "None" or iname not in [l.name for l in ui_instance.viewer.layers]:
            _warn("Select a valid image layer."); return
        img = _np.asarray(ui_instance.viewer.layers[iname].data)
        if img.ndim == 3:
            fi = min(frame_spin.value(), img.shape[0] - 1)
            img = img[fi]
        elif img.ndim != 2:
            _warn("Image must be 2D or a 3D (T,H,W) stack."); return

        mask = None
        rname = roi_dd.currentText()
        if rname != "None" and rname in [l.name for l in ui_instance.viewer.layers]:
            mask = _np.asarray(ui_instance.viewer.layers[rname].data) > 0
            if mask.ndim == 3:
                mask = mask[min(frame_spin.value(), mask.shape[0]-1)]

        prog.setVisible(True); prog.setRange(0, 0)
        try:
            result = measure_spatial_randomness(
                img, mask=mask, n_permutations=perm_spin.value(),
                window=window_spin.value())
        except Exception as e:
            prog.setVisible(False)
            _warn(f"Spatial randomness analysis failed: {e}")
            import traceback; traceback.print_exc(); return
        prog.setVisible(False)

        # Store + record
        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'spatial_randomness_df'] = result['results_df']
        except Exception:
            pass
        rec = getattr(ui_instance, '_record', None)
        if callable(rec):
            mi = result['results_df'].iloc[0]
            rec('spatial_randomness', {
                'image_layer': iname, 'roi_layer': rname,
                'n_permutations': perm_spin.value(),
                'morans_I_z': float(mi['z_score']) if mi['z_score']==mi['z_score'] else None})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            verdict_df = pd.DataFrame([{'verdict': result['verdict']}])
            show_dataframes_dialog(
                "Spatial Randomness",
                [('Statistics vs. permutation null', result['results_df']),
                 ('Verdict', verdict_df)])
        except Exception:
            pass
        _info(result['verdict'])

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QWidget, QScrollArea
        from PyQt5.QtWidgets import QSizePolicy as _QSP
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(_QSP.Expanding, _QSP.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Spatial Randomness", area='right')

