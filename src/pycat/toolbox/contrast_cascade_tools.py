"""
Contrast Cascade — visualize and analyse images with huge object-to-object
brightness swings (e.g. a very bright condensate body that grows much dimmer
fibers).

Why standard tools fail here
----------------------------
Single-threshold segmentation and the single-feature Random-Forest classifier
key on ABSOLUTE intensity. When one object (the body) is far brighter than the
structures you also care about (the fibers), no single intensity window or
threshold captures both — window for the body and the fibers disappear into the
background, window for the fibers and the body saturates.

The cascade approach
--------------------
1. VISUALISE by splitting the intensity range into a cascade of bands, each
   shown with its own contrast so bright and dim structure are visible at once.
2. ANALYSE with brightness-INVARIANT features — local-contrast normalisation
   (each pixel relative to its own surround) and ridge/tubeness filters (fiber
   detectors) — so a dim fiber and a bright body are both "signal". These feed a
   multi-feature Random Forest that can separate body / fiber / background.
3. DISTINGUISH why the fibers are dim: below-focus fibers are blurry-and-dim;
   nucleation/growth fibers are sharp-and-dim. Measuring sharpness vs intensity
   per object tells the two apart.
"""

import numpy as np

from pycat.utils.tag_registry import tags_layer
import skimage as sk
import scipy.ndimage as ndi


# ---------------------------------------------------------------------------
# Part 1 — cascade decomposition (visualisation)
# ---------------------------------------------------------------------------

def contrast_cascade_bands(image, n_bands=4, method='percentile',
                           roi_mask=None):
    """
    Split the intensity range into a cascade of bands from brightest to dimmest.

    Returns a list of dicts (brightest first), each with:
        name, low, high (the band's intensity window),
        band_image (the image with intensity clipped to [low, high] and rescaled
        0..1 for display), mask (pixels whose value falls in the band).

    method : 'percentile' splits by intensity percentiles (robust, even areas);
             'multiotsu' uses multi-Otsu thresholds (data-driven class breaks).
    """
    img = np.asarray(image, dtype=float)
    vals = img[roi_mask > 0] if roi_mask is not None else img.ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return []

    if method == 'multiotsu' and n_bands >= 2:
        try:
            thr = sk.filters.threshold_multiotsu(vals, classes=n_bands)
            edges = np.concatenate([[vals.min()], thr, [vals.max()]])
        except Exception:
            method = 'percentile'
    if method != 'multiotsu':
        qs = np.linspace(0, 100, n_bands + 1)
        edges = np.percentile(vals, qs)
    edges = np.unique(edges)

    bands = []
    # brightest band first
    for i in range(len(edges) - 1, 0, -1):
        low, high = float(edges[i - 1]), float(edges[i])
        if high <= low:
            continue
        clipped = np.clip(img, low, high)
        band_img = (clipped - low) / (high - low + 1e-12)
        mask = (img >= low) & (img <= high)
        if roi_mask is not None:
            band_img = band_img * (roi_mask > 0)
            mask = mask & (roi_mask > 0)
        bands.append(dict(name=f"band {len(edges)-i} ({low:.0f}–{high:.0f})",
                          low=low, high=high, band_image=band_img, mask=mask))
    return bands


@tags_layer('tone_map', role='preprocessed',
            summary='Tone mapping (log/gamma)')
def tone_map(image, method='log', clip_limit=0.003):
    """Compress a high-dynamic-range image into one viewable image.
    'log' = log(1+x) stretch; 'clahe' = local adaptive contrast."""
    img = np.asarray(image, dtype=float)
    img = img - np.nanmin(img)
    if method == 'clahe':
        norm = img / (np.nanmax(img) + 1e-12)
        return sk.exposure.equalize_adapthist(norm, clip_limit=clip_limit)
    out = np.log1p(img)
    return out / (out.max() + 1e-12)


# ---------------------------------------------------------------------------
# Part 2 — brightness-invariant features
# ---------------------------------------------------------------------------

@tags_layer('local_contrast', role='preprocessed',
            summary='Local contrast normalisation')
def local_contrast_normalize(image, sigma=15.0, mode='divide'):
    """Normalise each pixel relative to its LOCAL surround, so dim structures
    become as prominent as bright ones. 'divide' = image / local-mean;
    'subtract' = image − local-mean. The local mean is a large Gaussian blur
    (an estimate of the local background/envelope)."""
    img = np.asarray(image, dtype=float)
    local = ndi.gaussian_filter(img, sigma=sigma)
    if mode == 'subtract':
        return img - local
    return img / (local + np.percentile(img, 5) + 1e-6)


@tags_layer('ridge', role='preprocessed',
            summary='Ridge (Frangi/Sato) enhancement', target='fibril')
def ridge_enhance(image, scales=(1, 2, 3), black_ridges=False):
    """Tubeness / vesselness (Sato) response — highlights elongated, fiber-like
    structures independent of their absolute brightness."""
    img = np.asarray(image, dtype=float)
    img = img / (img.max() + 1e-12)
    try:
        return sk.filters.sato(img, sigmas=scales, black_ridges=black_ridges)
    except Exception:
        # fallback: max Hessian-based ridge response across scales
        out = np.zeros_like(img)
        for s in scales:
            sm = ndi.gaussian_filter(img, s)
            gxx = ndi.gaussian_filter(sm, s, order=[0, 2])
            gyy = ndi.gaussian_filter(sm, s, order=[2, 0])
            out = np.maximum(out, np.abs(gxx + gyy))
        return out


def cascade_feature_stack(image, object_diameter=20):
    """Build the (H, W, F) feature stack for the cascade RF: multi-scale
    intensity, local-contrast-normalised intensity, ridge (fiber) response, and
    gradient magnitude. Designed so a dim fiber and a bright body are both
    separable from background."""
    img = np.asarray(image, dtype=float)
    img = img / (img.max() + 1e-12)
    feats = []
    for s in (1, 2, 4, max(6, object_diameter // 3)):
        feats.append(ndi.gaussian_filter(img, s))
    feats.append(local_contrast_normalize(img, sigma=max(8, object_diameter)))
    feats.append(local_contrast_normalize(img, sigma=max(8, object_diameter),
                                          mode='subtract'))
    feats.append(ridge_enhance(img, scales=(1, 2, 3)))
    feats.append(ndi.gaussian_gradient_magnitude(img, sigma=1.5))
    return np.stack(feats, axis=-1)


def cascade_rf_segment(image, training_labels, object_diameter=20,
                       n_estimators=300):
    """Multi-feature Random-Forest segmentation for the contrast-cascade problem.
    Unlike the single-intensity RF, this trains on the full brightness-invariant
    feature stack, so it can learn body / fiber / background even across a large
    brightness swing.

    training_labels : integer scribble image (0 = unlabelled, 1..K = classes).
    Returns the predicted class-label image (same K classes)."""
    from sklearn.ensemble import RandomForestClassifier
    feats = cascade_feature_stack(image, object_diameter)
    H, W, F = feats.shape
    X = feats.reshape(-1, F)
    tl = np.asarray(training_labels)
    train_mask = tl.ravel() != 0
    if train_mask.sum() < 10 or len(np.unique(tl[tl != 0])) < 2:
        raise ValueError("Need scribbles for at least two classes "
                         "(e.g. body, fiber, background).")
    clf = RandomForestClassifier(n_estimators=n_estimators, max_depth=12,
                                 n_jobs=-1, class_weight='balanced')
    clf.fit(X[train_mask], tl.ravel()[train_mask])
    pred = clf.predict(X).reshape(H, W).astype(np.int32)
    return pred


# ---------------------------------------------------------------------------
# Part 3 — sharpness-vs-intensity: WHY are the fibers dim?
# ---------------------------------------------------------------------------

def _local_sharpness(image, mask):
    """Size- and intensity-INVARIANT sharpness = edge steepness.

    An in-focus object has a steep boundary (edge width near the diffraction
    limit); a below-focus object has a gradual, spread-out edge. We take the
    steepest intensity gradients within the object and divide by the object's
    CONTRAST against its local background (mean inside − background just outside),
    so the result reflects how sharp the edges are, independent of how bright or
    how big the object is. High = crisp / in focus, low = blurred / out of focus."""
    m = np.asarray(mask, bool)
    if m.sum() < 9:
        return np.nan
    img = np.asarray(image, dtype=float)
    # local background: a ring just outside the object
    ring = ndi.binary_dilation(m, iterations=4) & ~ndi.binary_dilation(m, iterations=1)
    bg = float(np.median(img[ring])) if ring.sum() else float(np.percentile(img, 5))
    contrast = float(img[m].mean() - bg)
    if contrast <= 1e-6:
        return 0.0
    gx = ndi.sobel(img, axis=0)
    gy = ndi.sobel(img, axis=1)
    grad = np.hypot(gx, gy)
    return float(np.percentile(grad[m], 90) / contrast)


def focus_vs_growth_diagnostic(image, labels, body_label=None,
                               dim_ratio=0.6, blur_ratio=0.65):
    """
    For each labelled object, measure mean intensity and (intensity-invariant)
    edge sharpness, then — relative to the brightest object (assumed to be the
    condensate body, treated as the in-focus reference) — classify each dim
    object as:
        'blurry_dim'  → likely BELOW FOCUS (dim because axially displaced; its
                        edges are also softer than the in-focus body's)
        'sharp_dim'   → likely NUCLEATION/GROWTH (in focus, edges as crisp as the
                        body, but genuinely less material)
        'bright'      → comparable brightness to the body (not a dim structure)

    Parameters
    ----------
    dim_ratio : intensity/body below this counts as "dim".
    blur_ratio : a dim object whose edge sharpness is below this fraction of the
        body's is called blurry (below-focus); above it, sharp (growth). This is
        a heuristic — treat the call as a suggestion, and confirm below-focus
        with a z-stack.

    Returns a DataFrame: label, area_px, mean_intensity, sharpness,
        intensity_ratio, sharpness_ratio, interpretation.
    """
    import pandas as pd
    img = np.asarray(image, dtype=float)
    lab = np.asarray(labels)
    ids = [l for l in np.unique(lab) if l != 0]
    if not ids:
        return pd.DataFrame()

    rows = []
    for l in ids:
        m = lab == l
        rows.append(dict(label=int(l), area_px=int(m.sum()),
                         mean_intensity=float(img[m].mean()),
                         sharpness=_local_sharpness(img, m)))
    df = pd.DataFrame(rows)

    if body_label is not None and body_label in df['label'].values:
        body = df[df['label'] == body_label].iloc[0]
    else:
        body = df.loc[df['mean_intensity'].idxmax()]

    df['intensity_ratio'] = df['mean_intensity'] / (body['mean_intensity'] + 1e-9)
    df['sharpness_ratio'] = df['sharpness'] / (body['sharpness'] + 1e-9)

    def _interp(r):
        if r['intensity_ratio'] > dim_ratio:
            return 'bright'
        if r['sharpness_ratio'] < blur_ratio:
            return 'blurry_dim (likely below focus)'
        return 'sharp_dim (likely nucleation/growth)'

    df['interpretation'] = df.apply(_interp, axis=1)
    df.attrs['body_label'] = int(body['label'])
    return df
