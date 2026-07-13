"""
Imaging-realistic degradation — the validation layer the external audit asked for.

Why this exists
---------------
The external audit (2026-07-12) specified a three-layer validation framework, and asked every
quantitative method to declare which layer it has reached::

    Implemented → Analytically validated → Simulation validated → Experimentally validated

**Layer 2 — "imaging-realistic simulation" — is this module.** The audit named eleven
degradations that separate a clean synthetic test from a real acquisition:

    Poisson noise · sCMOS read noise and offset maps · blur and axial defocus ·
    illumination gradients · photobleaching · drift · finite exposure motion blur ·
    pixelation · saturation · object overlap · segmentation errors

**Eight of those eleven have already been shown to break a real PyCAT measurement**, each found
the hard way, one bug at a time, by asking *"what does my clean simulation assume that real data
does not?"*:

===========================  =============================================  =========
degradation                  what it broke, measured                        release
===========================  =============================================  =========
Poisson noise                N&B: the shot-noise floor is B = 1, not 0      1.5.453
sCMOS offset (pedestal)      Kp 30 → **5.8**; N&B number inflated **120×**  1.5.422/453
blur (the PSF halo)          client enrichment 30 → **14.9**                1.5.460
illumination gradient        vignetting QC measured object placement        1.5.404
photobleaching               FRAP t½ **2.5× too fast**, R² = 0.94           1.5.455
drift                        MSD α → **1.91** — reported as superdiffusion  1.5.456
saturation                   Kp of 655, 1500 and 4000 **all read 655**      1.5.392
segmentation error           over-inclusive mask: Kp 30 → **4.4**           1.5.459
===========================  =============================================  =========

**Every one of those was a real bug, and every one was invisible to a clean simulation.** That
is the argument for this module: *a method that has only been tested on clean data has not been
tested.*

Using it
--------
Each degradation is a function of the image and a **physically meaningful parameter** — not a
dimensionless knob. ``pedestal`` is in counts; ``drift`` is in µm/s; ``bleach_tau`` is in
seconds. They compose, and ``acquire()`` applies a realistic stack of them in the order a
microscope does::

    clean = my_ground_truth_scene()
    real  = acquire(clean, pedestal=500, gain=2.0, read_noise=3.0,
                    psf_sigma_px=2.0, saturate_at=65535)

    measured = my_method(real)
    assert measured == pytest.approx(truth, rel=0.1)   # ← the real bar

**The order matters and is physical:** the sample bleaches, the optics blur, the photons arrive
(Poisson), the sensor adds gain/offset/read-noise, and *then* the ADC clips. Applying the
pedestal before the Poisson draw would make the pedestal itself noisy, which is not what a
camera does.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


# ── The sample ──────────────────────────────────────────────────────────────────────────

def photobleach(stack, tau_frames, seed=None):
    """Every frame bleaches the sample a little more: I(t) = I(0)·exp(-t/τ).

    **This is why FRAP was wrong.** The recovery plateau *sags*, and the fit reads that as a
    faster recovery to a lower plateau. With a true half-time of 8 s and a typical acquisition
    bleaching constant, the fit returned **t½ = 3.2 s (2.5× too fast) and a mobile fraction of
    0.60 against a true 0.875 — with R² = 0.94, flagged identifiable** (1.5.455).

    ``tau_frames``: the bleaching time constant, in FRAMES. Set to ``None`` for no bleaching.
    """
    if tau_frames is None or not np.isfinite(tau_frames) or tau_frames <= 0:
        return np.asarray(stack, dtype=float)

    arr = np.asarray(stack, dtype=float)
    t = np.arange(arr.shape[0]).reshape((-1,) + (1,) * (arr.ndim - 1))
    return arr * np.exp(-t / float(tau_frames))


def drift(stack, velocity_px_per_frame, seed=None):
    """The stage moves: a common translation applied to every frame.

    **This is why the MSD said "superdiffusion".** Drift is *ballistic* — it contributes
    ``(v·τ)²`` to the MSD, which grows as τ² and pushes α toward 2. And **the slower the probe,
    the worse it is.** In a viscous condensate (η = 8 Pa·s, D = 0.00027 µm²/s), **50 nm/s of
    stage drift triples D and drives α to 1.91** — with R² unchanged at 0.993 (1.5.456).

    ``velocity_px_per_frame``: (dy, dx) in pixels per frame.
    """
    if velocity_px_per_frame is None:
        return np.asarray(stack, dtype=float)

    # ── Sub-pixel resampling puts a PERIODIC artefact in the trace ──────────────
    #
    # ``ndi.shift`` with a fractional displacement interpolates, and the interpolation error
    # **repeats with the fractional part of the shift**. At 0.5 px/frame the fraction cycles
    # with period 2; at 0.3 px/frame, period ~3.3.
    #
    # That is a genuine periodic signal, and ``qc_vibration`` correctly detects it — firing at
    # **p = 0.005 on a stack that is only drifting.** The metric is right and **the simulation
    # was wrong**: a real stage moving continuously does not resample its own image.
    #
    # (Measured: the recovered shift trace was -0.49 +/- 0.05 against a true 0.5 — the drift is
    # recovered correctly, and the residual is the resampling ripple.)
    #
    # Fixed by using cubic interpolation, which is far smoother across the sub-pixel boundary
    # and does not leave a periodic residue at these amplitudes.
    vy, vx = velocity_px_per_frame
    arr = np.asarray(stack, dtype=float)
    out = np.empty_like(arr)
    for t in range(arr.shape[0]):
        out[t] = ndi.shift(arr[t], (vy * t, vx * t), order=3, mode='nearest', prefilter=True)
    return out


# ── The optics ──────────────────────────────────────────────────────────────────────────

def blur(image, psf_sigma_px):
    """The PSF. Every object gets a halo, and **the halo is not the dilute phase.**

    **This is why the enrichment was halved.** The pixels immediately outside a droplet mask
    are *halo*, not dilute phase — and including them inflates the dilute reference. With a
    realistic 2.5 px edge, a **true enrichment of 30 reads as 20.7; at 5 px it reads 14.9**
    (1.5.460). It also degrades the *contrast* by 22 %, which I had been calling "exact"
    (1.5.461).
    """
    if psf_sigma_px is None or psf_sigma_px <= 0:
        return np.asarray(image, dtype=float)

    arr = np.asarray(image, dtype=float)
    sigma = [0.0] + [float(psf_sigma_px)] * (arr.ndim - 1) if arr.ndim == 3 \
        else [float(psf_sigma_px)] * arr.ndim
    return ndi.gaussian_filter(arr, sigma=sigma)


def motion_blur(stack, velocity_px_per_frame, exposure_fraction=1.0):
    """Finite exposure: the object MOVES while the shutter is open, so it smears.

    Distinct from ``drift``, which displaces a *sharp* object. Motion blur *elongates* it — and
    an elongated object has a larger apparent area, a lower peak intensity, and a biased
    centroid along the direction of travel. Anything measuring size, brightness or position on
    a moving object inherits that.

    ``exposure_fraction``: how much of the frame interval the shutter is open (1.0 = continuous
    acquisition; 0.5 = half the interval, so half the smear).
    """
    if velocity_px_per_frame is None or exposure_fraction <= 0:
        return np.asarray(stack, dtype=float)

    vy, vx = velocity_px_per_frame
    smear = float(np.hypot(vy, vx) * exposure_fraction)
    if smear < 0.5:                                   # sub-pixel: nothing to see
        return np.asarray(stack, dtype=float)

    arr = np.asarray(stack, dtype=float)
    n_steps = max(2, int(np.ceil(smear)))
    out = np.zeros_like(arr)
    for t in range(arr.shape[0]):
        acc = np.zeros_like(arr[t])
        for k in range(n_steps):
            f = (k / (n_steps - 1) - 0.5) * exposure_fraction
            acc += ndi.shift(arr[t], (vy * f, vx * f), order=1, mode='nearest')
        out[t] = acc / n_steps
    return out


def illumination_gradient(image, vignette_fraction):
    """The field is not evenly lit: a radial fall-off toward the edges.

    **This is why the vignetting QC was measuring object placement.** A metric that averages
    intensity in radial bins reads cells-in-the-centre as "bad vignetting", and cells at the
    edges would *mask* real vignetting (1.5.404). It also breaks any measurement that assumes
    a uniform background — including a global-percentile dilute phase.

    ``vignette_fraction``: how much dimmer the corners are than the centre (0.4 = 40 % fall-off).
    """
    if not vignette_fraction:
        return np.asarray(image, dtype=float)

    arr = np.asarray(image, dtype=float)
    h, w = arr.shape[-2:]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - h / 2) ** 2 + (xx - w / 2) ** 2)
    r = r / max(r.max(), 1e-9)
    field = 1.0 - float(vignette_fraction) * (r ** 2)
    return arr * field


# ── The detector ────────────────────────────────────────────────────────────────────────

def photon_noise(image, seed=0):
    """Photons arrive as a Poisson process: **var = mean.**

    **This is the shot-noise floor**, and it is why an uncalibrated N&B brightness reads
    **B = 1 for a perfectly monomeric sample, not B = 0** — a Poisson emitter's variance equals
    its mean. Calibrating that floor away is precisely what a *monomeric reference* buys you
    (1.5.453).

    The input is taken to be in PHOTONS. Convert to counts afterwards with ``sensor()``.
    """
    rng = np.random.default_rng(seed)
    arr = np.maximum(np.asarray(image, dtype=float), 0.0)
    return rng.poisson(arr).astype(float)


def sensor(photons, gain=1.0, pedestal=0.0, read_noise=0.0, seed=0):
    """The camera: counts = gain·photons + pedestal + read-noise.

    **The pedestal is the single most destructive thing in this module**, because it is
    invisible: it adds a constant that *looks like signal*.

    * **Kp** = I_dense/I_dilute — the pedestal is in **both** terms and drags the ratio toward
      1. A true Kp of 30 reads as **5.8** on a 500-count pedestal (1.5.422).
    * **N&B** — the pedestal adds to the **mean** but not the **variance**, so B = var/mean
      falls and N = mean/B is inflated **120×** (1.5.453).
    * **Transfection filtering** — a mean/background *ratio* is pedestal-dependent, so on a
      500-count sensor **every transfected cell was called untransfected** (1.5.415).
    * **Puncta SNR gates** — same failure: the gate never fired at all (1.5.416).

    ``read_noise`` is the sCMOS read-noise standard deviation, in counts. It is **additive and
    Gaussian**, unlike shot noise, and it does not scale with the signal.
    """
    rng = np.random.default_rng(seed)
    arr = np.asarray(photons, dtype=float) * float(gain) + float(pedestal)
    if read_noise:
        arr = arr + rng.normal(0.0, float(read_noise), arr.shape)
    return arr


def saturate(image, ceiling):
    """The ADC clips. **A clipped measurement is meaningless, not conservative.**

    This is the one people assume is safe — *"the value is at least this large"* — and it is
    not. With a bulk of 100 counts on a 16-bit sensor, a **true Kp of 655, 1500 and 4000 all
    read as 655** once the dense phase clips (1.5.392). The numerator has been truncated by an
    unknown amount, so the ratio is not a lower bound on anything.
    """
    if ceiling is None:
        return np.asarray(image, dtype=float)
    return np.minimum(np.asarray(image, dtype=float), float(ceiling))


def pixelate(image, bin_factor):
    """Finite sampling: the sensor integrates over a pixel, it does not sample a point.

    Under-sampling relative to the PSF (below the Nyquist limit) loses real structure and
    biases every size measurement upward — the object cannot appear smaller than one pixel.
    This is the degradation behind the partial-volume work (1.5.382–385): **a sub-resolution
    object's measured intensity depends on where it falls within a pixel.**
    """
    if not bin_factor or bin_factor <= 1:
        return np.asarray(image, dtype=float)

    arr = np.asarray(image, dtype=float)
    b = int(bin_factor)
    h, w = arr.shape[-2:]
    h2, w2 = (h // b) * b, (w // b) * b
    cropped = arr[..., :h2, :w2]
    new_shape = cropped.shape[:-2] + (h2 // b, b, w2 // b, b)
    return cropped.reshape(new_shape).mean(axis=(-3, -1))


# ── The full acquisition ────────────────────────────────────────────────────────────────

def acquire(clean, *, psf_sigma_px=None, vignette_fraction=None, bleach_tau_frames=None,
            drift_px_per_frame=None, exposure_fraction=None, gain=1.0, pedestal=0.0,
            read_noise=0.0, saturate_at=None, bin_factor=None, seed=0):
    """Apply a realistic acquisition to a clean, ground-truth scene.

    **The order is physical, and it matters:**

    1. the sample **bleaches** (over frames),
    2. the stage **drifts**, and the object **smears** during the exposure,
    3. the optics **blur**, and the illumination is **uneven**,
    4. the photons arrive — **Poisson**,
    5. the sensor applies **gain, pedestal and read noise**,
    6. the ADC **clips**,
    7. the sensor **bins**.

    Applying the pedestal *before* the Poisson draw would make the pedestal itself noisy —
    which is not what a camera does, and would understate the damage, because a noisy pedestal
    at least carries variance. **The real one does not**, and that is exactly why it destroys
    N&B and every intensity ratio.
    """
    arr = np.asarray(clean, dtype=float)
    is_stack = arr.ndim == 3

    if is_stack and bleach_tau_frames:
        arr = photobleach(arr, bleach_tau_frames)

    if is_stack and drift_px_per_frame is not None:
        if exposure_fraction:
            arr = motion_blur(arr, drift_px_per_frame, exposure_fraction)
        arr = drift(arr, drift_px_per_frame)

    if psf_sigma_px:
        arr = blur(arr, psf_sigma_px)
    if vignette_fraction:
        arr = illumination_gradient(arr, vignette_fraction)

    arr = photon_noise(arr, seed=seed)
    arr = sensor(arr, gain=gain, pedestal=pedestal, read_noise=read_noise, seed=seed + 1)
    arr = saturate(arr, saturate_at)
    if bin_factor:
        arr = pixelate(arr, bin_factor)

    return arr
