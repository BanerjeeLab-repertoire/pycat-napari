"""**Linear spectral / bleed-through unmixing — from CONTROLS, refusing to invert garbage.**

Two fluorophores whose emission spectra overlap leak into each other's channels. The honest correction is a
linear unmix: model each observed channel as a fixed linear combination of the true fluorophore abundances,
``c = M · a``, and recover ``a = M⁻¹ · c``. This is the general N-channel form of the single-coefficient
bleed-through knob in ``ratiometric_tools`` — it complements it, it does not replace it.

The science lives in the rules, and they are the same "refuse rather than lie" contract as the calibration
and pixel-size gates:

* **The mixing matrix comes from single-label CONTROLS, never from the mixed data.** Estimating crosstalk
  from the very image you are trying to unmix is circular — it fits the leak to the signal and calls the
  result clean.
* **Background is subtracted BEFORE the matrix is formed.** An un-removed camera pedestal inflates every
  crosstalk ratio (the offset is common to numerator and denominator only at zero background).
* **A singular / ill-conditioned matrix is refused with a reason**, not pseudo-inverted — inverting a
  near-degenerate matrix amplifies noise without bound and produces confident nonsense.
* **The negative fraction is reported.** After unmixing, some pixels come out slightly negative from noise;
  that is expected. A LARGE negative fraction means the model is wrong (bad controls, a third emitter, a
  non-linear detector) — so the fraction is the built-in honesty check. Negatives are **clipped for display
  only**, never fed back into a measurement.

Linear 2–4 channel crosstalk only — NOT a full spectral (lambda-stack) linear-unmixing of many narrow bands.
Pure numpy; ``core``-testable against synthetic mixtures with a known matrix.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.errors import ScientificAssumptionError


def _channel_means(control, background):
    """Per-channel mean signal of one single-label control, background-subtracted.

    ``control`` is either a length-K vector of per-channel means, or a ``(K, …)`` image whose leading axis
    is the channel. ``background`` is a scalar or a length-K vector (the camera offset per channel)."""
    arr = np.asarray(control, dtype=float)
    vec = arr if arr.ndim == 1 else arr.reshape(arr.shape[0], -1).mean(axis=1)
    return vec - np.asarray(background, dtype=float)


def estimate_mixing_matrix(single_label_controls, *, background=0.0) -> np.ndarray:
    """The linear mixing matrix ``M`` (K×K) from K single-label controls — column j from control j.

    ``single_label_controls`` is a length-K sequence; control ``j`` is a sample containing ONLY fluorophore
    ``j`` (a length-K per-channel vector, or a ``(K, …)`` image), giving that fluorophore's signal in every
    channel. ``M[i, j]`` is the fraction of fluorophore ``j`` appearing in channel ``i``, normalized so
    ``M[j, j] = 1`` — the fluorophore reads 1 in its own (j-th) channel and the off-diagonal entries are the
    crosstalk fractions.

    Rules enforced here: background is removed before the ratio; a control whose OWN channel is non-positive
    after background subtraction is refused (you cannot normalize by zero — and it means the fluorophore is
    not detectable in its assigned channel, so the channel↔fluorophore assignment is wrong)."""
    controls = list(single_label_controls)
    k = len(controls)
    if k < 2 or k > 4:
        raise ScientificAssumptionError(
            f"linear unmixing supports 2–4 channels; got {k}. For many narrow bands use a dedicated "
            "spectral (lambda-stack) unmixing, not this crosstalk correction.")
    bg = np.asarray(background, dtype=float)
    if bg.ndim and bg.shape[0] not in (k, 1):
        raise ScientificAssumptionError(
            f"background must be a scalar or a length-{k} per-channel vector, got shape {bg.shape}.")
    M = np.zeros((k, k), dtype=float)
    for j, ctrl in enumerate(controls):
        vec = _channel_means(ctrl, background)
        if vec.shape[0] != k:
            raise ScientificAssumptionError(
                f"control {j} has {vec.shape[0]} channels, expected {k} (one control per channel).")
        own = vec[j]
        if not np.isfinite(own) or own <= 0:
            raise ScientificAssumptionError(
                f"single-label control {j} has non-positive signal ({own:.4g}) in its OWN channel {j} "
                "after background subtraction — the fluorophore is not detectable where it should be "
                "brightest, so the channel/fluorophore assignment (or the background) is wrong. Refusing "
                "to build a mixing matrix by dividing by it.")
        M[:, j] = vec / own
    return M


def mixing_matrix_warnings(M) -> list:
    """Plausibility notes on a mixing matrix — an empty list means it looks sane.

    Flags (does NOT refuse — these are judgement calls the user should see): an off-diagonal crosstalk term
    ≥ 1 (more of fluorophore j lands in some OTHER channel than its own — the channel assignment is likely
    swapped), and a negative crosstalk term (a linear model cannot produce negative leak; usually an
    over-subtracted background)."""
    M = np.asarray(M, dtype=float)
    notes = []
    k = M.shape[0]
    for j in range(k):
        for i in range(k):
            if i == j:
                continue
            v = M[i, j]
            if v >= 1.0:
                notes.append(
                    f"fluorophore {j} leaks MORE into channel {i} ({v:.2f}) than it reads in its own "
                    f"channel {j} (1.00) — channel/fluorophore assignment may be swapped.")
            elif v < 0:
                notes.append(
                    f"crosstalk M[{i},{j}] = {v:.2f} is negative — a linear model has no negative leak; "
                    "the background is probably over-subtracted.")
    return notes


def unmix(channels, M, *, background=0.0, condition_limit=1.0e6) -> np.ndarray:
    """Recover true fluorophore abundances from mixed channels: ``a = M⁻¹ · c``, per pixel/object.

    ``channels`` is a ``(K, …)`` array of observed signals (leading axis = channel); ``M`` is the K×K mixing
    matrix from :func:`estimate_mixing_matrix`. ``background`` (scalar or length-K) is removed first, matching
    the matrix estimation.

    **Refuses a singular / ill-conditioned matrix** — ``cond(M) > condition_limit`` — rather than inverting
    it, because a near-degenerate matrix amplifies noise without bound. **Negatives are kept** in the result
    (they are the honesty signal; see :func:`negative_fraction`); clip only for display."""
    M = np.asarray(M, dtype=float)
    channels = np.asarray(channels, dtype=float)
    k = M.shape[0]
    if M.ndim != 2 or M.shape[1] != k:
        raise ScientificAssumptionError(f"the mixing matrix must be square; got shape {M.shape}.")
    if channels.shape[0] != k:
        raise ScientificAssumptionError(
            f"channels has {channels.shape[0]} channels but the mixing matrix is {k}×{k}.")
    cond = np.linalg.cond(M)
    if not np.isfinite(cond) or cond > condition_limit:
        raise ScientificAssumptionError(
            f"the mixing matrix is singular / ill-conditioned (condition number {cond:.3g} > "
            f"{condition_limit:.0g}) — its channels are nearly linearly dependent, so inverting it would "
            "amplify noise without bound. This usually means two channels see almost the same fluorophore "
            "(pick more separated channels/filters) or a control was mislabeled. Refusing to unmix.")
    c = channels - np.asarray(background, dtype=float).reshape(
        (-1,) + (1,) * (channels.ndim - 1)) if np.ndim(background) else channels - background
    flat = c.reshape(k, -1)
    a = np.linalg.solve(M, flat)
    return a.reshape(channels.shape)


def negative_fraction(unmixed) -> float:
    """The fraction of unmixed values that came out negative — the built-in honesty check.

    A few percent is ordinary photon noise around zero in the dilute regions. A LARGE fraction means the
    linear model is wrong (bad controls, a third emitter, a non-linear detector) and the unmix should not be
    trusted — do not silently clip it away."""
    a = np.asarray(unmixed, dtype=float)
    if a.size == 0:
        return 0.0
    return float((a < 0).sum()) / float(a.size)


def clip_for_display(unmixed) -> np.ndarray:
    """Clip negatives to 0 **for display only**. Never feed the clipped array into a measurement — clipping
    hides exactly the model error that :func:`negative_fraction` reports."""
    return np.clip(np.asarray(unmixed, dtype=float), 0.0, None)
