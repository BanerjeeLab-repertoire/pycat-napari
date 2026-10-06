"""A minimum-contrast floor on the FINAL objects, applied by lookup so a slider can move it live.

Each object's local contrast-to-noise ratio is measured once, after segmentation, with exactly the
definition the refinement gate uses (`puncta_refinement._snr_conditions`): the mean of the RAW image
over the object (dilated by its gap), minus the robust median of a background ring scaled to the
object, in units of that ring's robust spread, with other objects excluded from the ring. The floor
then keeps or drops whole objects by that number alone — no shape or size term — through a
label -> keep lookup table, so moving it never re-runs segmentation.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage as ndi

from pycat.toolbox.segmentation.puncta_refinement import _local_ring_radii, _ring_masks, _robust_bg


def object_local_cnr(labels, raw, cell_labels=None):
    """Local CNR of every object in ``labels``, as an array indexed by label (index 0 unused, NaN).

    ``cell_labels`` (optional) gives each object's cell, so the ring radii scale with that cell's
    area as in the refinement gate; without it the whole image is one cell."""
    labels = np.asarray(labels)
    raw = np.asarray(raw, dtype=float)
    n = int(labels.max())
    cnr = np.full(n + 1, np.nan)
    if n == 0:
        return cnr
    occupied = labels > 0
    H, W = labels.shape
    for lab, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        area = int((labels[sl] == lab).sum())
        if cell_labels is not None:
            cell = int(np.bincount(np.asarray(cell_labels)[sl][labels[sl] == lab]).argmax())
            cell_area = int((np.asarray(cell_labels) == cell).sum()) if cell else labels.size
        else:
            cell_area = labels.size
        erode_r, gap_r, band_r = _local_ring_radii(area, cell_area)
        pad = gap_r + 4 * band_r + 1
        win = (slice(max(0, sl[0].start - pad), min(H, sl[0].stop + pad)),
               slice(max(0, sl[1].start - pad), min(W, sl[1].stop + pad)))
        obj = labels[win] == lab
        _interior, dilated, ring = _ring_masks(obj, erode_r, gap_r, band_r, exclude_mask=occupied[win])
        if not ring.any():
            continue
        med, sd = _robust_bg(raw[win][ring])
        cnr[lab] = (float(raw[win][dilated].mean()) - med) / (sd + np.finfo(np.float32).eps)
    return cnr


def floor_lookup(cnr, floor):
    """label -> keep (bool) for a given floor. NaN (unmeasurable) objects are kept."""
    keep = ~(np.asarray(cnr) < floor)
    keep[0] = False
    return keep


def split_by_floor(labels, cnr, floor):
    """(above, below) label images for ``floor`` — one vectorised lookup, no re-segmentation."""
    keep = floor_lookup(cnr, floor)
    labels = np.asarray(labels)
    k = keep[labels]
    return np.where(k, labels, 0), np.where(~k & (labels > 0), labels, 0)


# Calibrated 2026-10-06 (docs/audits/ring_rejection_phaseC_2026-10-06.md): Meet's two "diffuse" cells
# (small-puncta fields 5 and 7; 24 objects he did not trace) against the 1,371 objects both Meet and Gable
# traced on all 27 annotated fields. No value separates them cleanly — the populations overlap at CNR
# 0.9-2.8 — and 1.75 is the knee chosen by Gable: it removes 19/24 (79%) of Meet's objects and 10.6% of
# the agreed condensates.
CONTRAST_FLOOR_DEFAULT = 1.75


def measure_for_floor(data_instance, labels, raw, cell_labels=None):
    """Measure every object's local CNR once and keep it, with the labels, in the repository
    (``contrast_floor_state``) so any later floor is a lookup."""
    labels = np.asarray(labels)
    cnr = object_local_cnr(labels, raw, cell_labels)
    data_instance.data_repository['contrast_floor_state'] = {'labels': labels, 'cnr': cnr}
    return cnr


def apply_floor(data_instance, floor, apply=True):
    """Split the stored objects at ``floor`` (or keep all when ``apply`` is False) and record the
    result for the condensate table: ``below_floor_map`` (objects under the floor) and
    ``local_cnr_map`` (each object's CNR painted over it). Returns (above, below) label images."""
    state = data_instance.data_repository.get('contrast_floor_state')
    if state is None:
        return None, None
    labels, cnr = state['labels'], state['cnr']
    above, below = split_by_floor(labels, cnr, floor if apply else -np.inf)
    painted = np.asarray(cnr, dtype=np.float32)[labels]          # cnr[0] is NaN: background stays NaN
    data_instance.data_repository.update({'below_floor_map': below > 0, 'local_cnr_map': painted,
                                          'contrast_floor': float(floor), 'contrast_floor_on': bool(apply)})
    return above, below


def floor_counts(data_instance, floor, apply=True):
    """(kept, below) object counts for ``floor``, from the lookup table alone."""
    state = data_instance.data_repository.get('contrast_floor_state')
    if state is None:
        return 0, 0
    keep = floor_lookup(state['cnr'], floor if apply else -np.inf)
    n = len(state['cnr']) - 1
    return int(keep[1:].sum()), int(n - keep[1:].sum())
