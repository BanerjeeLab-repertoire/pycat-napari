"""**Measure the CZI mosaic seam — turn a visual defect into a number.**

The reported CZI symptom is a *left-side column discontinuity*: a visible vertical seam where a mosaic
tile was assembled with an offset. Everything built since (BioFormats routing, off-thread load, the scene
stack) plausibly helps, but scene-switching correctness ≠ within-plane mosaic assembly correctness, and
nothing measured the seam — so nothing could tell whether the defect returned, or was ever gone.

A seam is a *spatial* discontinuity: pixel statistics change abruptly across one column boundary in a way
they do not across its neighbours. That is measurable without knowing the cause, and — crucially —
**across many frames**: real image structure moves frame to frame, a tile boundary does not. A boundary
anomalous on *every* frame at a *fixed* column is a seam; a one-frame spike is just image content.

This module is pure numpy (no reader dependency) so it is `core`-testable against synthetic mosaics and
reusable by ``scripts/czi_diagnostics.py`` on the real file. **It does not fix the seam; it measures it.**
"""
from __future__ import annotations

import numpy as np


def column_seam_score(frame, x, *, window=16) -> float:
    """How discontinuous is ``frame`` across the vertical boundary at column ``x`` — as a z-score of that
    boundary's step against the steps of its neighbouring boundaries.

    Normalized against NEIGHBOURS, not an absolute threshold: absolute pixel steps vary enormously with
    sample and exposure, but a tile seam stands out from the boundaries right beside it. ``>= ~5`` is a
    seam; ordinary structure does not produce that at one fixed column across many frames."""
    frame = np.asarray(frame, dtype=float)
    if frame.ndim != 2:
        frame = np.asarray(frame).reshape(-1, frame.shape[-1]) if frame.ndim > 2 else frame
    x = int(x)
    w = frame.shape[1]
    if x < 1 or x >= w:
        return 0.0
    step_at_x = float(np.abs(frame[:, x] - frame[:, x - 1]).mean())
    neighbours = [float(np.abs(frame[:, i] - frame[:, i - 1]).mean())
                  for i in range(x - window, x + window + 1)
                  if i != x and 1 <= i < w]
    if len(neighbours) < 3:
        return 0.0
    med = float(np.median(neighbours))
    sd = float(np.std(neighbours))
    return (step_at_x - med) / (sd + 1e-12)


def persistent_seam_columns(frames, *, z_threshold=5.0, frame_fraction=0.6, window=16, boundaries=None):
    """The columns that read as a seam on a MAJORITY of ``frames`` — the test that separates a seam from
    a coincidence. A column whose :func:`column_seam_score` exceeds ``z_threshold`` on at least
    ``frame_fraction`` of the frames is a persistent seam; a sharp edge that happens to fall on a boundary
    in one frame is not (real structure moves).

    ``frames`` is an iterable of 2-D arrays. ``boundaries`` limits the check to known tile boundaries;
    ``None`` scans every interior column (the stronger, boundary-agnostic assertion). Returns a sorted
    list of seam column indices (empty ⇒ no seam)."""
    frames = [np.asarray(f, float) for f in frames]
    if not frames:
        return []
    w = frames[0].shape[1]
    cols = list(boundaries) if boundaries is not None else list(range(1, w))
    need = max(1, int(np.ceil(frame_fraction * len(frames))))
    seams = []
    for x in cols:
        hits = sum(1 for f in frames if column_seam_score(f, x, window=window) >= z_threshold)
        if hits >= need:
            seams.append(int(x))
    return sorted(seams)
