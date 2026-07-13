"""
The QC exemplar gallery — what each defect LOOKS like, and what PyCAT says about it.

Why this exists
---------------
A QC report that says *"Focus: 0.42"* teaches nobody anything. The module's whole purpose is the
shape:

    Image → Assessment → Interpretation → Recommendation

and the **Image** half was missing. A scientist looking at a "bad" verdict on their own data has
no reference for what "bad" looks like, or how bad *theirs* is by comparison.

This module generates that reference: for each defect, a **clean** frame and a **degraded** one
produced by a known, physically-parameterised corruption — and the QC verdict on each, so the
user can see the metric fire.

On simulated exemplars
----------------------
**These are simulated, and every panel says so.** That is a deliberate choice, not a shortcut:

* Real exemplars accumulate slowly. Waiting for a curated set of real bad data before shipping
  the gallery means shipping nothing.
* **A simulated exemplar is honest about what it is.** It carries the exact parameters that
  produced it (``psf_sigma_px=3.0``), so a user can reason about the degree of the defect
  rather than eyeballing a vibe.
* The interface does not change when a real example replaces a simulated one. ``source`` is a
  field, and swapping ``'simulated'`` for ``'real: 2026-08-14 Dragonfly, Zyla'`` is a one-line
  edit.

**What must NOT happen** is a gallery that quietly implies *"your data should look like this"*.
A synthetic image is not an acquisition standard. Every exemplar is labelled ``SIMULATED``, and
the caption says what it is standing in for.

The degradations are the ones from ``tests/imaging_realism.py`` — the audit's validation layer 2
— so **the gallery and the test suite are generated from the same physics.** An exemplar that
does not trip its own metric is a bug in one or the other, and ``tests/test_qc_gallery.py``
asserts exactly that.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


# ── The reference scene ─────────────────────────────────────────────────────────────────

def reference_scene(size=256, n_puncta=40, seed=0):
    """A clean field: puncta inside cells, on a dim background, as a 12-bit camera would see it.

    This is deliberately **ordinary** — the point of the gallery is that the *defect* is the only
    thing that differs between the two panels, so the scene must not be doing any work.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    img = np.full((size, size), 40.0)

    # A few cells, so vignetting and illumination have something to act on.
    for cy, cx in [(0.28, 0.28), (0.28, 0.72), (0.72, 0.28), (0.72, 0.72)]:
        cy, cx = cy * size, cx * size
        img += 180 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * (size / 9) ** 2))

    # Puncta: the objects most measurements are actually about.
    for _ in range(n_puncta):
        cy, cx = rng.integers(size // 8, size - size // 8, size=2)
        img += 900 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 2.2 ** 2))

    img = img + rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 4095).astype(np.uint16)          # a 12-bit sensor


def flat_field_scene(size=256, n_puncta=40, seed=0):
    """A scene with a UNIFORM background — for the metrics that measure the FIELD.

    **The four-cell reference scene is not usable for the vignetting exemplar**, and finding out
    why is itself worth teaching: four cells arranged in a ring **genuinely are a radial
    intensity pattern**, and ``qc_vignetting`` reads the clean scene as ``bad (0.535)`` — *"edge
    is 54 % of centre brightness"*.

    The metric is not wrong. **The scene is.** This is the same trap as the 1.5.404 bug, where a
    vignetting metric was measuring where the cells happened to sit — and it is a real caveat for
    a user with a sparse field, which the gallery caption says out loud.

    So the vignetting exemplar uses a flat background with scattered puncta, where the *only*
    radial structure is the one being demonstrated.
    """
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    img = np.full((size, size), 300.0)
    for _ in range(n_puncta):
        cy, cx = rng.integers(size // 8, size - size // 8, size=2)
        img += 900 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 2.2 ** 2))

    img = img + rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 4095).astype(np.uint16)


def reference_stack(n_frames=32, size=192, seed=0):
    """A time series of the reference scene, for the motion and bleaching exemplars."""
    rng = np.random.default_rng(seed)
    base = reference_scene(size=size, n_puncta=25, seed=seed).astype(float)
    return np.stack([
        np.clip(base + rng.normal(0, 6, base.shape), 0, 4095).astype(np.uint16)
        for _ in range(n_frames)])


# ── The defects ─────────────────────────────────────────────────────────────────────────
#
# Each entry says what the defect IS, what it COSTS downstream (measured, with the release
# that found it), and how to FIX it at the microscope. The cost is the part that matters:
# "your image is blurry" is not actionable; "your enrichment is halved" is.

def _saturate(img, ceiling):
    return np.minimum(np.asarray(img, float), ceiling).astype(np.uint16)


def _defocus(img, sigma):
    return ndi.gaussian_filter(np.asarray(img, float), sigma).astype(np.uint16)


def _add_noise(img, sd, seed=1):
    rng = np.random.default_rng(seed)
    a = np.asarray(img, float) + rng.normal(0, sd, np.shape(img))
    return np.clip(a, 0, 4095).astype(np.uint16)


def _defocus_one_frame(stack, frame, sigma):
    """Blur a SINGLE frame of a stack — a focus slip mid-acquisition, which is the real case."""
    out = np.asarray(stack, float).copy()
    out[frame] = ndi.gaussian_filter(out[frame], sigma)
    return out.astype(np.uint16)


def _vignette(img, fraction):
    a = np.asarray(img, float)
    h, w = a.shape[-2:]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - h / 2) ** 2 + (xx - w / 2) ** 2)
    r = r / max(r.max(), 1e-9)
    return (a * (1.0 - fraction * r ** 2)).astype(np.uint16)


def _bleach(stack, tau_frames):
    a = np.asarray(stack, float)
    t = np.arange(a.shape[0]).reshape(-1, 1, 1)
    return (a * np.exp(-t / tau_frames)).astype(np.uint16)


def _drift(stack, per_frame):
    a = np.asarray(stack, float)
    vy, vx = per_frame
    return np.stack([ndi.shift(a[t], (vy * t, vx * t), order=1, mode='nearest')
                     for t in range(a.shape[0])]).astype(np.uint16)


def _vibrate(stack, amplitude_px, period_frames):
    a = np.asarray(stack, float)
    return np.stack([
        ndi.shift(a[t], (amplitude_px * np.sin(2 * np.pi * t / period_frames), 0),
                  order=1, mode='nearest')
        for t in range(a.shape[0])]).astype(np.uint16)


EXEMPLARS = [
    dict(
        key='saturation',
        wiki=('Clipping (photography)', 'https://en.wikipedia.org/wiki/Clipping_(photography)'),
        cite=('Waters, J. C. (2009). Accuracy and precision in quantitative fluorescence microscopy. *J. Cell Biol.* 185, 1135–1148.', 'https://doi.org/10.1083/jcb.200903097'),
        cite_quote='"Detectors have a limited capacity to hold electrons; if this capacity is reached, the corresponding pixel will be saturated... The linearity of the detector is therefore lost, and saturated images cannot be used for quantitation of fluorescence intensity values. Choosing to crop out saturated areas is not acceptable... because it will select for the weaker intensity parts of the specimen."',
        metric='qc_saturation',
        title='Saturation — the detector clipped',
        degrade=lambda: _saturate(reference_scene(), 600),
        clean=lambda: reference_scene(),
        params='clipped at 600 counts (a gain-limited sensor); the scene peaks near 1100',
        looks_like='The brightest puncta are FLAT-TOPPED — their centres are all exactly the '
                   'same value, and the peak has been sliced off.',
        costs='**This is the one defect that cannot be undone downstream.** The intensity is '
              'gone. A partition coefficient of 655, 1500 and 4000 all read as 655 once the '
              'dense phase clips — and the number is not a lower bound on anything, because '
              'the numerator was truncated by an unknown amount (1.5.392).',
        fix='Reduce the exposure or the gain until the brightest objects sit below the '
            'ceiling. Check the histogram for a spike at the maximum. **Do not** rescue a '
            'clipped acquisition by scaling it — the information is not there.',
    ),
    dict(
        key='defocus',
        wiki=('Defocus aberration', 'https://en.wikipedia.org/wiki/Defocus_aberration'),
        cite=("North, A. J. (2006). Seeing is believing? A beginners' guide to practical pitfalls in image acquisition. *J. Cell Biol.* 172, 9–18.", 'https://doi.org/10.1083/jcb.200507103'),
        cite_quote='',
        metric='qc_focus',
        title='Defocus — the objects are blurred',
        # `qc_focus` reports 'info' on a single image ON PURPOSE — the absolute band-pass
        # energy is scene-dependent, so it only JUDGES across a stack, by comparing each frame
        # to the median. The exemplar therefore shows a stack with one defocused frame, which
        # is also the realistic case: focus drifts during an acquisition.
        degrade=lambda: _defocus_one_frame(reference_stack(), frame=16, sigma=3.0),
        clean=lambda: reference_stack(),
        params='one frame of 32 blurred with sigma = 3.0 px (a focus slip mid-acquisition)',
        is_stack=True,
        looks_like='The puncta are wider and dimmer. Their edges are soft, and faint ones have '
                   'merged into the background.',
        costs='Blur spreads each object into a **halo**, and the pixels immediately outside a '
              'mask are halo, not background. A client enrichment of 30 reads as **14.9** with '
              'a 5 px edge (1.5.460), and the dense–dilute contrast loses **22 %** (1.5.461). '
              'Small objects are lost entirely, which biases any size distribution upward.',
        fix='Refocus. If the field is only partly sharp, the coverslip is tilted or the sample '
            'is not flat. For a stack, check that the focal drift correction is on.',
    ),
    dict(
        key='low_snr',
        wiki=('Signal-to-noise ratio', 'https://en.wikipedia.org/wiki/Signal-to-noise_ratio'),
        cite=('Waters, J. C. (2009). Accuracy and precision in quantitative fluorescence microscopy. *J. Cell Biol.* 185, 1135–1148.', 'https://doi.org/10.1083/jcb.200903097'),
        cite_quote='',
        metric='qc_snr',
        title='Low SNR — the signal is buried in noise',
        degrade=lambda: _add_noise(reference_scene(), 90),
        clean=lambda: reference_scene(),
        params='additive Gaussian noise, sd = 90 counts',
        looks_like='The background is grainy and the faint puncta are hard to separate from it '
                   'by eye. The bright ones are still obvious — **which is the trap.**',
        costs='Segmentation finds objects in the noise and loses the faint real ones, so the '
              'population you measure is biased toward the bright. Every threshold becomes '
              'noise-dependent, and a measurement that is stable on clean data can swing with '
              'the noise level alone — a partition coefficient moved **323 → 22 with the noise '
              'level** when it was computed on a normalised image (1.5.424).',
        fix='Increase the exposure, the excitation power, or bin the camera. **Check for '
            'photobleaching first** — if the sample is fading, more power makes it worse.',
    ),
    dict(
        key='vignetting',
        wiki=('Vignetting', 'https://en.wikipedia.org/wiki/Vignetting'),
        cite=('Jonkman, J. et al. (2020). Tutorial: guidance for quantitative confocal microscopy. *Nat. Protoc.* 15, 1585–1611.', 'https://doi.org/10.1038/s41596-020-0313-9'),
        cite_quote='',
        metric='qc_vignetting',
        title='Vignetting — the field is unevenly lit',
        degrade=lambda: _vignette(flat_field_scene(), 0.55),
        clean=lambda: flat_field_scene(),
        params='radial fall-off, 55 % dimmer at the corners',
        looks_like='The centre is bright and the corners are dark. **Compare the same puncta at '
                   'the edge and in the middle** — they are the same objects, imaged '
                   'differently.',
        costs='Any measurement that compares objects **across the field** is comparing '
              'illumination, not biology. Cells at the edge look dimmer, so an intensity '
              'threshold selects the ones in the middle, and an enrichment measured against a '
              'global background is wrong everywhere except the centre.',
        fix='Acquire a flat-field reference (an even fluorescent slide) and divide by it. Check '
            'the lamp alignment and that the field diaphragm is opened past the camera chip.',
    ),
    dict(
        key='bleaching',
        wiki=('Photobleaching', 'https://en.wikipedia.org/wiki/Photobleaching'),
        cite=('Jost, A. P.-T. & Waters, J. C. (2019). Designing a rigorous microscopy experiment: validating methods and avoiding bias. *J. Cell Biol.* 218, 1452–1466.', 'https://doi.org/10.1083/jcb.201812109'),
        cite_quote='',
        # NOT qc_snr: a global intensity scale changes the signal AND the noise together, so
        # the SNR is (correctly) invariant to it. Bleaching is a TEMPORAL defect, and the
        # metric that sees it is the one that watches intensity across frames.
        metric='qc_photobleaching',
        title='Photobleaching — the sample fades',
        degrade=lambda: _bleach(reference_stack(), 12.0),
        clean=lambda: reference_stack(),
        params='exponential decay, tau = 12 frames',
        is_stack=True,
        looks_like='The first frame is bright and the last is dim. Scrub through the stack: the '
                   'objects do not move, they fade.',
        costs='A bleach correction divides by exp(-t/tau), so **an error in tau compounds '
              'exponentially**. On a movie a fifth of the bleach time, tau fits to 11 s against '
              'a true 50 — and the final frame is over-corrected by **96 %**, nearly doubling '
              'it (1.5.451). In FRAP, uncorrected acquisition bleaching makes the plateau sag, '
              'and the fit reads that as a **2.5× faster recovery** with a mobile fraction 31 % '
              'too low — at R² = 0.94 (1.5.455).',
        fix='Reduce the excitation power or the frame rate. For FRAP, **acquire a reference '
            'region** that the bleach pulse did not hit — it measures the acquisition '
            'bleaching directly, and PyCAT corrects with it.',
    ),
    dict(
        key='drift',
        wiki=('Image stabilization', 'https://en.wikipedia.org/wiki/Image_stabilization'),
        cite=('Jonkman, J. et al. (2020). Tutorial: guidance for quantitative confocal microscopy. *Nat. Protoc.* 15, 1585–1611.', 'https://doi.org/10.1038/s41596-020-0313-9'),
        cite_quote='',
        metric='qc_drift',
        title='Stage drift — the field slides',
        degrade=lambda: _drift(reference_stack(), (0.6, 0.35)),
        clean=lambda: reference_stack(),
        params='0.6 px/frame in y, 0.35 px/frame in x',
        is_stack=True,
        looks_like='Scrub through: everything moves together, smoothly, in one direction. The '
                   'objects do not move relative to each other.',
        costs='Drift is **ballistic** — it adds (v·tau)² to the MSD, which grows as tau² and '
              'pushes the anomalous exponent toward 2. In a viscous condensate, **50 nm/s of '
              'drift triples D and drives alpha to 1.91** — which reads as *directed, active '
              'transport*. It is the stage. And R² does not move (1.5.456).',
        fix='Let the stage and the sample come to thermal equilibrium before acquiring — most '
            'drift is thermal. Use hardware focus lock if available. PyCAT can subtract the '
            'common-mode motion, but **a correction is not a substitute for a stable stage**.',
    ),
    dict(
        key='vibration',
        wiki=('Vibration isolation', 'https://en.wikipedia.org/wiki/Vibration_isolation'),
        cite=("North, A. J. (2006). Seeing is believing? A beginners' guide to practical pitfalls in image acquisition. *J. Cell Biol.* 172, 9–18.", 'https://doi.org/10.1083/jcb.200507103'),
        cite_quote='',
        metric='qc_vibration',
        title='Vibration — a periodic source is shaking the sample',
        degrade=lambda: _vibrate(reference_stack(), 2.5, 6.0),
        clean=lambda: reference_stack(),
        params='2.5 px oscillation with a 6-frame period',
        is_stack=True,
        looks_like='Scrub through: the field oscillates back and forth at a regular rhythm — '
                   'unlike drift, it returns.',
        costs='Periodic displacement adds a spurious oscillation to every trajectory, and blurs '
              'each frame over the exposure. It is **distinct from drift**, and the fix is '
              'different: PyCAT tests for periodicity against a permutation null precisely so '
              'that random jitter and smooth drift do not send you looking for a pump that does '
              'not exist (1.5.419/420).',
        fix='Find the source: a pump, a fan, a compressor, footsteps, a nearby lift. An air '
            'table helps only if the source is floor-borne — an on-table pump needs isolating '
            'or turning off.',
    ),
]


# ── Building an exemplar ────────────────────────────────────────────────────────────────

def build_exemplar(spec):
    """Produce one gallery entry: the images, the QC verdicts, and the teaching text.

    The verdict is **computed, not written down** — the gallery calls the real metric on the
    real degraded image. If a metric stops firing on its own exemplar, the gallery says so, and
    ``tests/test_qc_gallery.py`` fails. **A teaching example that no longer matches the software
    is worse than none.**
    """
    from pycat.toolbox import data_qc_tools as qc

    clean = spec['clean']()
    degraded = spec['degrade']()
    metric = getattr(qc, spec['metric'])

    def _verdict(data):
        try:
            r = metric(data)
            return dict(status=r.get('status'), value=r.get('value'),
                        headline=r.get('headline'), how=r.get('how'))
        except Exception as exc:                       # pragma: no cover - diagnostic path
            return dict(status='error', value=None, headline=str(exc)[:80], how='')

    return dict(
        key=spec['key'],
        title=spec['title'],
        metric=spec['metric'],
        params=spec['params'],
        source='SIMULATED',                            # never implied to be real data
        # A defect the user cannot look up is a defect they cannot learn from. Each exemplar
        # carries an accessible entry point (Wikipedia) AND a primary reference from the
        # quantitative-microscopy literature — the papers a reviewer would expect to see.
        wiki=spec['wiki'],
        cite=spec['cite'],
        cite_quote=spec.get('cite_quote', ''),
        is_stack=bool(spec.get('is_stack')),
        clean=clean,
        degraded=degraded,
        clean_verdict=_verdict(clean),
        degraded_verdict=_verdict(degraded),
        looks_like=spec['looks_like'],
        costs=spec['costs'],
        fix=spec['fix'],
    )


def build_gallery():
    """Every exemplar. Used by both the in-app widget and the documentation build."""
    return [build_exemplar(s) for s in EXEMPLARS]
