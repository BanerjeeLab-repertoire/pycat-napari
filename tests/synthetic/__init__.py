"""**Synthetic ground truth** (Completion Spec §0.2): deterministic generators with a KNOWN answer.

Real data has no known answer; every measurement bias in the spec's Phase A was found with synthetic data and can
only be *fixed* against it. Each generator is seeded, takes its parameters in the call, writes no files, and --
the load-bearing rule -- takes a **noise** parameter and a **pedestal** parameter. Two of the Phase A biases
survived because the old golden masters generated noise-free, pedestal-free data on which the biased branch was
mathematically unreachable; a test built on these generators runs a SWEEP over noise and pedestal, not one point.

==========================  ===========================================  ==========
generator                   known truth                                  spec gate
==========================  ===========================================  ==========
``spida_field``             N, epsilon                                   A1, A1''
``partition_field``         K (dense / dilute above the pedestal)        A2a-c
``textured_object``         identical texture multiset, varying shape    A3', A3''
``lognormal_sizes``         mu, sigma, truncation radius                 A4
``bleaching_stack``         tau, fraction of object signal remaining     G2
``noise_stack``             per-frame sigma (white / row / column)       G3, G7
``drifting_stack``/``zstack`` known drift vs none                        G6
==========================  ===========================================  ==========
"""
from __future__ import annotations

import numpy as np

__all__ = ['spida_field', 'partition_field', 'textured_object', 'shape_mask', 'lognormal_sizes',
           'bleaching_stack', 'noise_stack', 'drifting_stack', 'zstack']


# ── SpIDA ──────────────────────────────────────────────────────────────────────────────────────────────────────

def spida_field(N=8.0, eps=25.0, read_sigma=0.0, pedestal=0.0, n=20000, seed=0):
    """ROI pixels for SpIDA: each pixel sees Poisson(N) molecules, each emitting Poisson(eps) photons (shot noise),
    plus Gaussian detector read noise of sd ``read_sigma`` and a constant camera ``pedestal``. Returns 1-D float."""
    rng = np.random.default_rng(seed)
    shot = rng.poisson(rng.poisson(N, n) * eps).astype(float)
    read = rng.normal(0.0, read_sigma, n) if read_sigma else 0.0
    return shot + read + float(pedestal)


# ── Partition coefficient ──────────────────────────────────────────────────────────────────────────────────────

def partition_field(K=100.0, dilute=100.0, dilute_cv=0.0, dense_cv=0.0, pedestal=0.0, dtype=np.float64,
                    H=192, W=192, r=10, seed=0):
    """An in-vitro field: 16 droplets on a uniform dilute phase. Above the ``pedestal`` the dilute phase is
    ``dilute`` and the dense phase ``K * dilute`` (so the true partition coefficient is ``K``), each with Gaussian
    RELATIVE noise (``dilute_cv`` / ``dense_cv``). An integer ``dtype`` is rounded and clipped to its range.
    Returns ``(image, labels)``."""
    rng = np.random.default_rng(seed)
    img = dilute * (1.0 + dilute_cv * rng.standard_normal((H, W)))
    lbl = np.zeros((H, W), np.int32)
    yy, xx = np.ogrid[:H, :W]
    k = 1
    step_y, step_x = H / 4.0, W / 4.0
    for gy in range(4):
        for gx in range(4):
            cy, cx = int(step_y * (gy + 0.5)), int(step_x * (gx + 0.5))
            disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
            img[disc] = K * dilute * (1.0 + dense_cv * rng.standard_normal(int(disc.sum())))
            lbl[disc] = k
            k += 1
    img = img + float(pedestal)
    dt = np.dtype(dtype)
    if np.issubdtype(dt, np.integer):
        info = np.iinfo(dt)
        img = np.clip(np.rint(img), info.min, info.max)
    return img.astype(dt), lbl


# ── Texture: same texture, different shape ─────────────────────────────────────────────────────────────────────

def shape_mask(area=4000, aspect=1.0, angle_deg=45.0, pad=6):
    """A boolean ellipse mask of EXACTLY ``area`` pixels, axis ratio ``aspect`` (1 = circle), long axis at
    ``angle_deg``: the ``area`` pixels nearest the centre in the elliptical metric, so shapes of different
    eccentricity have equal area. (At 45 degrees the bounding-box fill falls with eccentricity -- 0.79 for a circle,
    ~0.1 at 16:1 -- which is what a bbox-based statistic is sensitive to; an axis-aligned ellipse keeps pi/4.)"""
    b = np.sqrt(area / (np.pi * aspect))           # minor semi-axis
    a = aspect * b
    h = int(np.ceil(a)) + pad
    yy, xx = np.mgrid[-h:h + 1, -h:h + 1]
    th = np.deg2rad(angle_deg)
    u = xx * np.cos(th) + yy * np.sin(th)
    v = -xx * np.sin(th) + yy * np.cos(th)
    rho = (v / b) ** 2 + (u / a) ** 2
    order = np.argsort(rho, axis=None, kind='stable')[:area]
    mask = np.zeros(rho.size, bool)
    mask[order] = True
    return mask.reshape(rho.shape)


def textured_object(aspect=1.0, area=4000, texture='random', background=0.0, pedestal=0.0, noise_sigma=0.0,
                    seed=0):
    """An object whose WITHIN-MASK intensity multiset is identical for every ``aspect`` (only the shape changes),
    on a ``background`` (0 = zeroed, or a real level) plus ``pedestal``, with optional Gaussian ``noise_sigma`` on
    the background. ``texture``: ``'random'`` (a fixed seeded multiset) or ``'flat'`` (a constant). Returns
    ``(image float64, mask bool)``."""
    rng = np.random.default_rng(seed)
    mask = shape_mask(area, aspect)
    if texture == 'flat':
        values = np.full(area, 500.0)
    else:
        values = np.random.default_rng(1234).gamma(4.0, 100.0, area)   # the SAME multiset for every shape
    img = np.full(mask.shape, float(background))
    if noise_sigma:
        img = img + rng.normal(0.0, noise_sigma, mask.shape)
    img[mask] = rng.permutation(values)
    return img + float(pedestal), mask


# ── Size distribution ──────────────────────────────────────────────────────────────────────────────────────────

def lognormal_sizes(mu=np.log(0.5), sigma=0.6, d_min=0.0, n=20000, seed=0):
    """Radii drawn from lognormal(mu, sigma), observed only at or above the detection limit ``d_min`` (a radius):
    a left-TRUNCATED sample, as a segmentation with a minimum object size produces. Returns the observed radii."""
    r = np.random.default_rng(seed).lognormal(mu, sigma, n)
    return r[r >= d_min] if d_min > 0 else r


# ── Stacks (Data-QC ground truth) ──────────────────────────────────────────────────────────────────────────────

def _blobs(shape, n_objects, radius, rng):
    H, W = shape
    yy, xx = np.mgrid[:H, :W]
    img = np.zeros(shape)
    for _ in range(n_objects):
        cy, cx = rng.uniform(radius, H - radius), rng.uniform(radius, W - radius)
        img += np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2.0 * radius ** 2))
    return img


def bleaching_stack(tau=8.0, sparsity=0.02, pedestal=100.0, n_frames=30, shape=(128, 128), amplitude=1000.0,
                    noise_sigma=2.0, seed=0):
    """Sparse fluorescent objects decaying as exp(-t/tau) on a CONSTANT pedestal (the background does not bleach).
    ``sparsity`` is roughly the fraction of the field the objects cover. Returns ``(stack, fraction_remaining)``
    where ``fraction_remaining`` is the true object signal at the last frame over the first."""
    rng = np.random.default_rng(seed)
    radius = 3.0
    n_obj = max(1, int(sparsity * shape[0] * shape[1] / (np.pi * (2 * radius) ** 2)))
    base = _blobs(shape, n_obj, radius, rng) * amplitude
    t = np.arange(n_frames)
    stack = np.stack([pedestal + base * np.exp(-ti / tau) + rng.normal(0, noise_sigma, shape) for ti in t])
    return stack.astype(np.float32), float(np.exp(-(n_frames - 1) / tau))


def noise_stack(sigma=25.0, n_frames=16, axis=None, mean=1000.0, shape=(128, 128), signal=True, seed=0):
    """Frames of a fixed specimen plus noise of per-frame sd ``sigma``: white (``axis=None``), constant along each
    ROW (``'row'``: row noise, as sCMOS rows / line-scan bands), or along each COLUMN (``'col'``). The specimen is
    identical in every frame, so averaging frames reduces the noise by sqrt(n) -- the artefact G3 is about."""
    rng = np.random.default_rng(seed)
    H, W = shape
    spec = mean + (_blobs(shape, 20, 4.0, np.random.default_rng(99)) * 4 * sigma if signal else 0.0)
    frames = []
    for _ in range(n_frames):
        if axis == 'row':
            n = np.repeat(rng.normal(0, sigma, (H, 1)), W, axis=1)
        elif axis == 'col':
            n = np.repeat(rng.normal(0, sigma, (1, W)), H, axis=0)
        else:
            n = rng.normal(0, sigma, shape)
        frames.append(spec + n)
    return np.stack(frames).astype(np.float32)


def drifting_stack(dx_per_frame=1.0, n_frames=10, shape=(128, 128), pedestal=100.0, noise_sigma=2.0, seed=0):
    """A sharp specimen translated by ``dx_per_frame`` pixels per frame along x (integer roll, so the drift is
    exact). Returns ``(stack, total_drift_px)``."""
    rng = np.random.default_rng(seed)
    spec = _blobs(shape, 30, 2.5, np.random.default_rng(7)) * 1000.0
    stack = np.stack([pedestal + np.roll(spec, int(round(i * dx_per_frame)), axis=1)
                      + rng.normal(0, noise_sigma, shape) for i in range(n_frames)])
    return stack.astype(np.float32), float((n_frames - 1) * dx_per_frame)


def zstack(n_planes=21, shape=(128, 128), pedestal=100.0, noise_sigma=2.0, seed=0):
    """A through-focus series with ZERO lateral drift: the same specimen, Gaussian-defocused by |z - centre|.
    Returns the stack; its true drift is 0 by construction."""
    from scipy import ndimage as ndi
    rng = np.random.default_rng(seed)
    spec = _blobs(shape, 30, 2.0, np.random.default_rng(7)) * 1000.0
    c = (n_planes - 1) / 2.0
    planes = [pedestal + ndi.gaussian_filter(spec, 0.5 + 0.6 * abs(z - c)) + rng.normal(0, noise_sigma, shape)
              for z in range(n_planes)]
    return np.stack(planes).astype(np.float32)
