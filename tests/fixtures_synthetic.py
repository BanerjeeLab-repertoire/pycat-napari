"""
Shared synthetic-data fixtures for PyCAT analysis regression tests.

These generators produce deterministic (seeded) synthetic images/masks/curves
with KNOWN properties, so tests can assert either:
  - a closed-form correct answer (known-answer tests), or
  - a stable "golden" value captured from the current trusted implementation
    (characterization tests — the value to lock in is left as a TODO for the
    maintainer to fill from validated real/synthetic data).

Nothing here depends on napari or the GUI — pure numeric fixtures.
"""

import numpy as np


def synthetic_puncta_image(shape=(256, 256), n_puncta=40, radius=4,
                           amplitude=500, background=100, noise_sigma=5,
                           seed=0):
    """A field of Gaussian-blob 'puncta' on a noisy background.

    Returns (image float32, labels int). Deterministic for a given seed, so the
    object count and geometry are fixed — suitable for a segmentation
    golden-master test.
    """
    rng = np.random.default_rng(seed)
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W]
    img = rng.normal(background, noise_sigma, shape).astype(np.float32)
    labels = np.zeros(shape, dtype=int)
    placed = 0
    lid = 1
    attempts = 0
    while placed < n_puncta and attempts < n_puncta * 20:
        attempts += 1
        cy, cx = rng.integers(radius + 2, H - radius - 2), rng.integers(radius + 2, W - radius - 2)
        blob = amplitude * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * radius ** 2)))
        core = blob > amplitude * 0.5
        if labels[core].any():
            continue  # avoid overlap so the object count is well-defined
        img += blob
        labels[core] = lid
        lid += 1
        placed += 1
    return img.astype(np.float32), labels


def synthetic_frap_curve(mobile_fraction=0.7, half_time_s=5.0, i0=0.2,
                         n=60, t_max=40.0, noise_sigma=0.0, seed=0):
    """A normalized FRAP recovery curve with KNOWN mobile fraction and half-time.

    Built directly from the recovery model I(t) = (a + b·x)/(1 + x), x = t/τ½,
    with a = i0 (immediate post-bleach) and b = i0 + mobile_fraction (plateau),
    so mobile_fraction = b − a exactly. Use to check that fit_frap_recovery
    recovers these inputs.

    Returns (time, intensity).
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0, t_max, n)
    a = i0
    b = i0 + mobile_fraction
    x = t / half_time_s
    y = (a + b * x) / (1.0 + x)
    if noise_sigma > 0:
        y = y + rng.normal(0, noise_sigma, size=y.shape)
    return t, y.astype(float)


def two_channels(kind='identical', shape=(128, 128), seed=0):
    """Two channels with a KNOWN Pearson relationship.

    kind='identical'   -> perfectly correlated (Pearson == 1.0)
    kind='anticorr'    -> perfectly anti-correlated (Pearson == -1.0)
    kind='independent' -> uncorrelated noise (Pearson ~ 0)
    Returns (ch1, ch2, roi_mask) all same shape; roi_mask is all-True.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(1000, 200, shape).astype(np.float32)
    roi = np.ones(shape, dtype=bool)
    if kind == 'identical':
        return base, base.copy(), roi
    if kind == 'anticorr':
        return base, (-base + 2 * base.mean()).astype(np.float32), roi
    if kind == 'independent':
        other = rng.normal(1000, 200, shape).astype(np.float32)
        return base, other, roi
    raise ValueError(kind)


def partition_scene(k_true=5.0, dense_val=None, dilute_val=100.0,
                    shape=(128, 128), seed=0):
    """A two-phase scene with a KNOWN partition coefficient K.

    A central 'dense' disk at intensity dense_val on a 'dilute' background at
    dilute_val, with no camera offset (background=0), so
    K = dense / dilute = k_true exactly.

    Returns (client_image, dense_mask, cell_mask).
    """
    if dense_val is None:
        dense_val = k_true * dilute_val
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W]
    img = np.full(shape, dilute_val, dtype=np.float32)
    dense = np.sqrt((xx - W // 2) ** 2 + (yy - H // 2) ** 2) < min(H, W) * 0.15
    img[dense] = dense_val
    cell = np.ones(shape, dtype=bool)
    return img.astype(np.float32), dense, cell
