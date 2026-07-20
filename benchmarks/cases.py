"""**The canonical benchmark cases — fixed, seeded, with CONSTRUCTED ground truth.**

This is a *benchmark*, so the inputs must never change silently and the ground truth must be **constructed,
not produced by a PyCAT run** — otherwise the suite measures self-consistency and would happily track a
drifting method as "stable." Each case pairs a seeded synthetic image (from `tests.fixtures_synthetic`)
with the ground-truth mask the generator itself placed, a deterministic segmentation method, and an
optional derived measurement.

**Keep the set FIXED.** Changing a case invalidates the recorded history — if a case must change, add it as
a NEW case (a new name) rather than editing the old one, so prior records stay meaningful.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional

import numpy as np


@dataclasses.dataclass(frozen=True)
class BenchmarkCase:
    name: str
    family: str
    build: Callable            # () -> (image, ground_truth_labels)  — GT is CONSTRUCTED, not from PyCAT
    method: Callable           # (image) -> predicted labels          — a deterministic segmenter
    derived: Optional[Callable] = None   # (pred_labels, image) -> {measurement: value}


def _threshold_segment(image, rel=0.5):
    """A deterministic threshold-at-relative-level segmenter — seed-stable, no learned state."""
    from scipy import ndimage as ndi
    a = np.asarray(image, dtype=float)
    lo, hi = float(a.min()), float(a.max())
    t = lo + rel * (hi - lo)
    return ndi.label(a > t)[0]


# ── Case builders (constructed ground truth) ────────────────────────────────────────────────────
def _build_puncta():
    from tests.fixtures_synthetic import synthetic_puncta_image
    image, gt = synthetic_puncta_image(shape=(128, 128), n_puncta=20, seed=0)   # gt is the generator's labels
    return np.asarray(image), np.asarray(gt)


def _build_partition():
    from tests.fixtures_synthetic import partition_scene
    image, dense_gt, _cell = partition_scene(k_true=5.0, dilute_val=100.0, seed=0)
    return np.asarray(image), np.asarray(dense_gt, dtype=np.int32)


def _partition_k(pred_labels, image):
    dense = np.asarray(pred_labels) > 0
    image = np.asarray(image, dtype=float)
    if not dense.any() or not (~dense).any():
        return {'partition_k': float('nan')}
    return {'partition_k': float(image[dense].mean() / image[~dense].mean())}


def _build_cells():
    """A field of well-separated disk 'cells' on a dark background — constructed geometry."""
    shape = (160, 160)
    img = np.full(shape, 0.05, dtype=float)
    gt = np.zeros(shape, dtype=np.int32)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    for i, (cy, cx) in enumerate([(40, 40), (40, 120), (120, 40), (120, 120), (80, 80)], start=1):
        m = (yy - cy) ** 2 + (xx - cx) ** 2 <= 18 ** 2
        img[m] = 1.0
        gt[m] = i
    return img, gt


CANONICAL_CASES = (
    BenchmarkCase('puncta_20', 'puncta', _build_puncta, _threshold_segment),
    BenchmarkCase('partition_k5', 'condensate', _build_partition, _threshold_segment, _partition_k),
    BenchmarkCase('cells_5disks', 'cells', _build_cells, _threshold_segment),
)
