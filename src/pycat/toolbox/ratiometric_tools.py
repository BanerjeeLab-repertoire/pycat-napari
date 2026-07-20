"""**Two-channel intensity ratios — done correctly, because a naive `A/B` is riddled with traps.**

Ratiometric imaging reports environment-sensitive quantities (FRET-by-ratio; polarity / viscosity / pH
from environment-sensitive dyes) as a per-pixel or per-object ratio of two channels. Computing the ratio
is trivial; the value of this module is that it handles the traps that make a naive ratio wrong:

1. **Background BEFORE ratio, always.** `(N − b_N)/(D − b_D)`, not `N/D`. An offset in either channel bends
   the ratio toward 1 — the same reasoning the partition-coefficient tools already carry. Background
   subtraction is a mandatory input here, not optional polish.
2. **The low-signal denominator.** Where `D ≈ 0` the ratio explodes into meaningless spikes. Pixels/objects
   whose background-subtracted denominator is below a threshold become **NaN, not a huge number**, and the
   **fraction excluded is reported** — a map that is 60% thresholded is telling you the measurement barely
   holds.
3. **mean-of-ratio vs ratio-of-means.** `mean(Nᵢ/Dᵢ)` weights every pixel equally (noisy where D is small);
   `mean(N)/mean(D)` is the aggregate ratio (robust, but hides heterogeneity). They answer different
   questions and differ on a heterogeneous object — so **both are reported and labelled**; the default
   summary is ratio-of-means.
4. **Bleed-through.** If one channel leaks into the other the ratio is corrupted toward 1. An optional
   user-supplied linear coefficient corrects it (`D − c·N`); with none supplied the result is flagged
   `bleedthrough_corrected=False` and the caller is expected to warn. **No automatic unmixing** — a
   coefficient must come from a single-label control.
5. **Registration.** A ratio assumes the channels are pixel-aligned; a sub-pixel shift makes spurious
   ratio rings at object edges. Detecting/warning about that is the caller's (UI) job — this module
   consumes already-aligned layers.

Downstream analysis only: it consumes existing channel arrays, reimplementing no acquisition or loading.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass(frozen=True)
class RatioResult:
    """A ratio image with `NaN` where the denominator was too small to trust, plus the record of how it
    was made (the backgrounds removed, the threshold, and how much of the domain was excluded)."""
    ratio: np.ndarray
    fraction_thresholded: float
    background_num: float
    background_den: float
    threshold: float | None
    denominator_floor: float          # the effective D cutoff actually applied


def _prepare(numerator, denominator, background_num, background_den):
    """Background-subtract both channels — the mandatory first step (`(N−b_N)`, `(D−b_D)`)."""
    n = np.asarray(numerator, dtype=float) - float(background_num)
    d = np.asarray(denominator, dtype=float) - float(background_den)
    return n, d


def ratio_image(numerator, denominator, *, background_num=0.0, background_den=0.0,
                threshold=None, mask=None) -> RatioResult:
    """Per-pixel `(N−b_N)/(D−b_D)`, with `NaN` wherever the background-subtracted denominator is at or
    below ``threshold`` (default: `0` — a non-positive denominator is never divided). ``mask`` restricts
    the domain over which the ratio and the thresholded fraction are computed. Background is subtracted
    FIRST, always — that is the whole point."""
    n, d = _prepare(numerator, denominator, background_num, background_den)
    floor = 0.0 if threshold is None else float(threshold)

    domain = np.ones(d.shape, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    valid = domain & np.isfinite(n) & np.isfinite(d) & (d > floor)

    ratio = np.full(d.shape, np.nan, dtype=float)
    ratio[valid] = n[valid] / d[valid]

    n_domain = int(domain.sum())
    frac = float((domain & ~valid).sum()) / n_domain if n_domain else 0.0
    return RatioResult(ratio=ratio, fraction_thresholded=frac,
                       background_num=float(background_num), background_den=float(background_den),
                       threshold=(None if threshold is None else float(threshold)),
                       denominator_floor=floor)


def object_ratios(labels, numerator, denominator, *, background_num=0.0, background_den=0.0,
                  threshold=None, bleedthrough_coeff=None) -> pd.DataFrame:
    """Per-object ratio summaries — **both** ``ratio_of_means`` (aggregate `Σ(N−b_N)/Σ(D−b_D)`, robust) and
    ``mean_of_ratio`` (`mean(Nᵢ/Dᵢ)` over the object's above-threshold pixels, heterogeneity-sensitive) —
    with the fraction of each object's pixels excluded by the denominator threshold, and the choices that
    shaped the numbers (backgrounds, whether bleed-through was corrected). Both summaries are reported
    because they answer different questions; neither is silently chosen for you.

    ``bleedthrough_coeff`` (optional): the fraction of the numerator channel that leaks into the
    denominator; when given, the denominator is corrected as ``D − c·N`` before the ratio. With none
    supplied, ``bleedthrough_corrected`` is ``False`` — uncorrected bleed-through biases the ratio toward
    1, and the caller should warn.
    """
    lab = np.asarray(labels)
    n, d = _prepare(numerator, denominator, background_num, background_den)
    if bleedthrough_coeff is not None:
        d = d - float(bleedthrough_coeff) * n      # linear unmixing of the numerator out of the denominator
    floor = 0.0 if threshold is None else float(threshold)

    rows = []
    for lb in np.unique(lab):
        if lb == 0:
            continue
        obj = lab == lb
        nv, dv = n[obj], d[obj]
        finite = np.isfinite(nv) & np.isfinite(dv)
        above = finite & (dv > floor)
        n_px = int(obj.sum())

        denom_sum = float(dv[above].sum())
        ratio_of_means = float(nv[above].sum() / denom_sum) if denom_sum != 0 and above.any() else np.nan
        mean_of_ratio = float(np.mean(nv[above] / dv[above])) if above.any() else np.nan
        rows.append(dict(
            label=int(lb),
            ratio_of_means=ratio_of_means,
            mean_of_ratio=mean_of_ratio,
            fraction_thresholded=(1.0 - float(above.sum()) / n_px) if n_px else np.nan,
            n_pixels=n_px,
            background_num=float(background_num), background_den=float(background_den),
            bleedthrough_corrected=bool(bleedthrough_coeff is not None),
        ))
    return pd.DataFrame(rows)
