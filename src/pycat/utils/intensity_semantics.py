"""
Intensity semantics: which operations destroy the relationship a measurement depends on.

The problem this exists to prevent
----------------------------------
A measurement is only valid on an image whose intensities still *mean* something. Several
routine preprocessing steps deliberately destroy that meaning — that is what they are for —
and nothing stopped their output being fed to a measurement that requires it.

Measured on a droplet field with a **true Kp of 30**:

===========================  =========  ==========  ==========
image                        I_dense    I_dilute    ratio
===========================  =========  ==========  ==========
raw counts                   3500.1     600.0       5.83
min-max normalised           —          —           **130.01**
after white top-hat          2914.4     **14.6**    **199.27**
after top-hat + LoG          48.6       **−4.1**    **−11.96**
after CLAHE                  1.000      0.015       **64.77**
===========================  =========  ==========  ==========

* **Min-max normalisation** maps the image *minimum* to zero, silently subtracting an
  uncontrolled floor — the darkest noise pixel in that field. Every ratio built on it becomes
  a function of the exposure.
* **A white top-hat removes the background**, which is its purpose. The dilute phase goes to
  ~0 and the ratio explodes.
* **A Laplacian-of-Gaussian is a signed operator centred on zero.** The dilute-phase mean goes
  *negative*, and **a ratio of two numbers straddling zero is not a physical quantity at all.**
  A negative partition coefficient.
* **CLAHE equalises the local histogram**, pushing the dense phase to the ceiling (1.000) and
  the dilute phase to 0.015. The result measures the contrast-enhancement algorithm, not the
  sample.

None of these operations is wrong. They are correct — for segmentation. The error is feeding
their output to a measurement of intensity.

The design
----------
Each operation declares what it did to the intensity semantics, as a tag on the output layer.
Each measurement declares what it requires. ``check_measurement_input`` compares them and
refuses when the requirement is violated, naming the operation that broke it.

This is deliberately **not** a heuristic that inspects the pixels. An image that has had its
background subtracted looks like an image with a dark background; a LoG output looks like an
image with a lot of noise. The information is in the *provenance*, not the pixels — so it is
recorded when the operation happens, and read when the measurement happens.

What a measurement can require
------------------------------
``ABSOLUTE``
    Values are detector counts on the original scale. The camera pedestal is present and the
    zero point is the detector's. Needed for anything that removes a floor by reference to a
    dark frame, and for optical density.

``LINEAR``
    Values are proportional to photon count, possibly with an offset and a scale factor.
    Ratios of *differences* are valid; ratios of raw values inherit the offset. Background
    subtraction lands here — it changes the zero point, but linearly.

``MONOTONIC``
    Order is preserved but the mapping is nonlinear. Thresholds and rank statistics are valid;
    intensity ratios are not.

``DESTROYED``
    The relationship to photon count is gone. Only geometry (masks, positions, shapes) survives.
"""

from __future__ import annotations

import warnings
from enum import Enum

from pycat.utils.layer_tags import get_tag, tag_layer


class MeasurementRefused(ValueError):
    """A measurement was asked to run on an image whose intensity semantics forbid it.

    Carries the reason, naming the operation responsible and what it did. Catch it if the
    caller wants to proceed anyway — but the number it would have produced is not a
    measurement of the sample.
    """


class IntensitySemantics(Enum):
    """What survives of the intensity, in decreasing order of usefulness."""

    ABSOLUTE = 'absolute'      # detector counts, original zero point
    LINEAR = 'linear'          # proportional to photons, offset/scale may have changed
    MONOTONIC = 'monotonic'    # order preserved, mapping nonlinear
    DESTROYED = 'destroyed'    # geometry only


# Ordering: a stricter requirement is satisfied by a stronger guarantee.
_RANK = {
    IntensitySemantics.ABSOLUTE: 3,
    IntensitySemantics.LINEAR: 2,
    IntensitySemantics.MONOTONIC: 1,
    IntensitySemantics.DESTROYED: 0,
}


# What each operation does to the intensity. Measured, not assumed — see the module docstring.
OPERATION_EFFECT = {
    # Geometry only: the values are untouched.
    'crop':                 IntensitySemantics.ABSOLUTE,
    'upscale':              IntensitySemantics.ABSOLUTE,
    'register':             IntensitySemantics.ABSOLUTE,
    'flat_field_correct':   IntensitySemantics.ABSOLUTE,   # multiplicative, restores linearity

    # Linear: the zero point or scale moves, but proportionality to photons survives.
    'background_subtract':  IntensitySemantics.LINEAR,
    'dark_subtract':        IntensitySemantics.LINEAR,
    'bin':                  IntensitySemantics.LINEAR,
    'gaussian_blur':        IntensitySemantics.LINEAR,
    'median_filter':        IntensitySemantics.LINEAR,

    # Monotonic: order survives, proportionality does not.
    'gamma':                IntensitySemantics.MONOTONIC,
    'log_transform':        IntensitySemantics.MONOTONIC,
    'rescale_intensity':    IntensitySemantics.MONOTONIC,

    # DESTROYED: the relationship to photon count is gone.
    #
    # `minmax_normalize` is here and not under MONOTONIC deliberately. It IS monotonic — but
    # the floor it subtracts is the darkest pixel in THAT field, which is a noise excursion.
    # So the mapping differs from image to image, and the same sample at a different exposure
    # gives a different answer. Measured: the reported partition coefficient swung from 323 to
    # 22 with the noise level alone, against a true value of 30.
    'minmax_normalize':     IntensitySemantics.DESTROYED,
    'clahe':                IntensitySemantics.DESTROYED,
    'equalize_hist':        IntensitySemantics.DESTROYED,
    'white_tophat':         IntensitySemantics.DESTROYED,
    'black_tophat':         IntensitySemantics.DESTROYED,
    'log_enhance':          IntensitySemantics.DESTROYED,   # Laplacian-of-Gaussian: SIGNED
    'dog':                  IntensitySemantics.DESTROYED,   # difference-of-Gaussians: SIGNED
    'wavelet_denoise':      IntensitySemantics.DESTROYED,
    'preprocess':           IntensitySemantics.DESTROYED,   # the whole chain
    'segment':              IntensitySemantics.DESTROYED,
    'threshold':            IntensitySemantics.DESTROYED,
    'skeletonize':          IntensitySemantics.DESTROYED,
}


_WHY = {
    'minmax_normalize': (
        "min-max normalisation maps the image MINIMUM to zero, silently subtracting an "
        "uncontrolled floor — the darkest noise pixel in that particular field. The same "
        "sample at a different exposure then gives a different answer: the reported partition "
        "coefficient swung from 323 to 22 with the noise level alone, against a true value "
        "of 30"),
    'clahe': (
        "CLAHE equalises the LOCAL histogram, which deliberately destroys the global intensity "
        "relationship. On a droplet field it pushed the dense phase to the ceiling (1.000) and "
        "the dilute phase to 0.015 — the result measures the contrast-enhancement algorithm, "
        "not the sample"),
    'white_tophat': (
        "a white top-hat REMOVES the background, which is its purpose. The dilute phase then "
        "sits at ~0 and any ratio against it explodes (measured: 199 against a true Kp of 30)"),
    'log_enhance': (
        "a Laplacian-of-Gaussian is a SIGNED operator centred on zero. The dilute-phase mean "
        "goes NEGATIVE, and a ratio of two numbers straddling zero is not a physical quantity "
        "at all — it returned a partition coefficient of −11.96"),
    'dog': (
        "a difference-of-Gaussians is a SIGNED operator centred on zero; a ratio of values "
        "straddling zero is not a physical quantity"),
    'preprocess': (
        "the preprocessing chain applies a white top-hat, a Laplacian-of-Gaussian and wavelet "
        "denoising. It is built for SEGMENTATION and is designed to destroy exactly what an "
        "intensity measurement needs: the background is removed and then a signed derivative "
        "is taken of it (measured: a partition coefficient of −11.96 against a true 30)"),
}


def mark_intensity_semantics(layer, operation):
    """Record what ``operation`` did to this layer's intensity semantics.

    Call this on the OUTPUT layer of any operation that touches pixel values. Unknown
    operations are recorded as such rather than assumed harmless — an operation nobody
    classified is exactly the one likely to break something.
    """
    effect = OPERATION_EFFECT.get(operation)
    if effect is None:
        warnings.warn(
            f"Unknown operation '{operation}' — its effect on intensity semantics has not "
            f"been classified, so measurements cannot check against it. Add it to "
            f"OPERATION_EFFECT in pycat/utils/intensity_semantics.py.",
            stacklevel=2)
        return

    # The semantics can only ever DEGRADE along a chain: a background subtraction applied to a
    # CLAHE'd image does not restore linearity.
    current = get_tag(layer, 'intensity_semantics')
    if current is not None:
        try:
            current_e = IntensitySemantics(current)
            if _RANK[effect] > _RANK[current_e]:
                effect = current_e         # keep the worse of the two
        except ValueError:
            pass

    tag_layer(layer, 'intensity_semantics', effect.value, source='derived')
    prev_ops = get_tag(layer, 'intensity_operations') or []
    if not isinstance(prev_ops, list):
        prev_ops = [prev_ops]
    tag_layer(layer, 'intensity_operations', prev_ops + [operation], source='derived')


def check_measurement_input(layer, required, measurement_name='this measurement'):
    """Is ``layer`` valid input for a measurement requiring ``required`` semantics?

    Returns ``(ok, reason)``. ``ok`` is False when the layer's recorded semantics are weaker
    than the requirement — and ``reason`` names the operation responsible and what it did, so
    the user is told *why*, not merely *no*.

    An UNTAGGED layer is allowed through with ``ok=True`` and a reason noting that its
    provenance is unknown. That is deliberate: the tag system is not yet applied everywhere, so
    refusing untagged layers would break every existing workflow. It fails open, and says so.
    """
    if not isinstance(required, IntensitySemantics):
        required = IntensitySemantics(required)

    tag = get_tag(layer, 'intensity_semantics')
    if tag is None:
        return True, (
            f"{measurement_name} requires {required.value} intensity semantics. This layer's "
            f"provenance is NOT RECORDED, so that could not be verified. If it has been "
            f"normalised, CLAHE'd, background-subtracted or preprocessed, the result will be "
            f"wrong — see pycat/utils/intensity_semantics.py for what each of those does.")

    try:
        actual = IntensitySemantics(tag)
    except ValueError:
        return True, f"unrecognised intensity_semantics tag '{tag}'"

    if _RANK[actual] >= _RANK[required]:
        return True, ""

    ops = get_tag(layer, 'intensity_operations') or []
    if not isinstance(ops, list):
        ops = [ops]
    culprits = [o for o in ops
                if _RANK.get(OPERATION_EFFECT.get(o, IntensitySemantics.ABSOLUTE), 3)
                < _RANK[required]]

    detail = ""
    for c in culprits:
        if c in _WHY:
            detail = f" The operation responsible is '{c}': {_WHY[c]}."
            break
    if not detail and culprits:
        detail = f" The operation responsible is '{culprits[0]}'."

    return False, (
        f"{measurement_name} requires {required.value} intensity semantics, but this layer is "
        f"'{actual.value}' — the relationship between pixel value and photon count has been "
        f"changed by: {', '.join(ops) if ops else 'an unrecorded operation'}.{detail} "
        f"Use the ORIGINAL image for this measurement. The processed layer is correct for "
        f"segmentation; it is not a measurement of the sample.")

def require_intensity(required, name=None):
    """Decorator: refuse to run a measurement on an image whose semantics are too weak.

    The wrapped function must accept an ``image_layer=`` keyword (the napari layer). When it
    is supplied, the layer's recorded semantics are checked against ``required``; when it is
    not, the measurement proceeds and a note is attached, because the tag system is not yet
    applied everywhere and refusing untagged input would break every existing workflow.

    ::

        @require_intensity(IntensitySemantics.ABSOLUTE, 'optical density')
        def compute_optical_density(image, ..., image_layer=None):
            ...

    On refusal the function is NOT called and a ``MeasurementRefused`` exception is raised.

    **It raises rather than returning a sentinel.** A first version returned a dict carrying
    the reason — which is fine for a function that returns a dict, and **wrong for one that
    returns an array**: ``compute_optical_density`` returns an ``ndarray``, so a caller doing
    ``od.mean()`` or ``od[mask]`` got an ``AttributeError`` on a dict. **A refusal must be
    clearer than the bug it prevents, not a different crash.** An exception carries the reason
    to the caller regardless of what the function normally returns.
    """
    import functools

    def _decorate(fn):
        label = name or fn.__name__.replace('_', ' ')

        @functools.wraps(fn)
        def _wrapped(*args, image_layer=None, **kwargs):
            if image_layer is not None:
                ok, why = check_measurement_input(image_layer, required, label)
                if not ok:
                    try:
                        from pycat.utils.notify import show_warning
                        show_warning(f"{label}: {why}")
                    except Exception:
                        warnings.warn(f"{label}: {why}")
                    raise MeasurementRefused(why)
                if why:
                    try:
                        from pycat.utils.notify import show_warning
                        show_warning(f"{label}: {why}")
                    except Exception:
                        pass
            return fn(*args, **kwargs)

        _wrapped.requires_intensity = required
        return _wrapped

    return _decorate
