"""In-vitro fluorescence droplet segmentation.

Extracted VERBATIM from ``invitro_fluor_ui``'s inline worker ``_task`` (1.6.376 Outstanding-Work C1
increment 3) so the operation is a NAMED, ``@tags_layer``-registered producer — which lets the droplet
mask it makes carry lineage (``tag_from_operation`` needs a registered op). The method dispatch
(otsu / multiotsu / sauvola / random-forest / advanced-spot) and the shared post-filter are moved
unchanged; behaviour is pinned by ``tests/test_ivf_droplet_segmentation.py``.

Import-clean (numpy / skimage / segmentation_tools only, no Qt/napari) so the tag-discovery sweep can
import it headlessly.
"""
from __future__ import annotations

import numpy as np
from skimage import filters, measure

from pycat.utils.general_utils import remove_small_objects_compat as _remove_small_objects_compat
from pycat.utils.tag_registry import tags_layer


@tags_layer('ivf_droplet_segment', role='labels', target='condensate', inputs=('image',),
            requirements=('in_vitro', 'fluorescence'),
            summary='In-vitro fluorescence droplet segmentation')
def segment_ivf_droplets(pre, raw, *, method='otsu', otsu_sensitivity=1.0, multiotsu_classes=3,
                         multiotsu_upper=False, sauvola_window=35, sauvola_k=0.0, min_radius=2.0,
                         kurtosis_threshold=-3.0, local_snr_threshold=0.8, min_area=6,
                         reject_nonround=False, rf_scribbles=None, ball_radius=15, cell_diameter=100):
    """Segment in-vitro fluorescence droplets from a pre-processed image.

    Parameters mirror the in-vitro-fluorescence panel's controls. ``pre`` is the pre-processed image the
    threshold methods run on; ``raw`` is the raw fluorescence image the advanced-spot pipeline needs.
    ``method`` selects the branch: ``'otsu'`` / ``'multiotsu'`` / ``'sauvola'`` (global / multi-level /
    local thresholds), ``'rf'`` (a trained random-forest classifier, requires ``rf_scribbles``), or the
    default advanced-spot detector (``segment_subcellular_objects`` on a whole-frame mask).

    Returns
    -------
    (labeled, unrefined) : tuple[numpy.ndarray, numpy.ndarray]
        ``labeled`` is the int32 labelled droplet mask (post-filtered by ``min_area`` /
        ``reject_nonround``); ``unrefined`` is the pre-postfilter boolean foreground (for the spot method,
        the detector's own ``unrefined`` mask).
    """
    def _postfilter(binary):
        b = np.asarray(binary) > 0
        if min_area > 0:
            b = _remove_small_objects_compat(b, int(min_area))
        lab = measure.label(b)
        if reject_nonround:
            keep = np.zeros_like(lab)
            for pr in measure.regionprops(lab):
                if pr.area >= 5 and pr.solidity >= 0.85:
                    keep[lab == pr.label] = pr.label
            lab = measure.label(keep > 0)
        return lab.astype(np.int32), b

    if method == 'otsu':
        t = filters.threshold_otsu(pre) * otsu_sensitivity
        return _postfilter(pre > t)

    if method == 'multiotsu':
        ts = filters.threshold_multiotsu(pre, classes=int(multiotsu_classes))
        cut = ts[-1] if multiotsu_upper else ts[0]
        return _postfilter(pre > cut)

    if method == 'sauvola':
        from pycat.toolbox.segmentation_tools import local_thresholding_func
        binary = local_thresholding_func(pre, window_size=int(sauvola_window),
                                         k_val=sauvola_k, mode='Sauvola')
        return _postfilter(np.asarray(binary) > 0)

    if method == 'rf':
        from pycat.toolbox.segmentation_tools import train_and_apply_rf_classifier
        # train_and_apply_rf_classifier runs CLAHE (equalize_adapthist), which requires float input in
        # [-1, 1]. The raw fluorescence image is in raw intensity units, so pass a [0,1]-normalized copy
        # or CLAHE raises "Images of type float must be between -1 and 1" -- caught by the worker and
        # surfacing as an EMPTY mask.
        _p = np.asarray(pre, dtype=np.float32)
        _lo, _hi = float(_p.min()), float(_p.max())
        _pn = (_p - _lo) / (_hi - _lo) if _hi > _lo else _p
        # Returns a LIST of refined masks, one per non-background class (the lowest painted label is
        # dropped as background inside).
        masks = train_and_apply_rf_classifier(_pn, rf_scribbles, int(cell_diameter))
        if not masks:
            return _postfilter(np.zeros(pre.shape, dtype=bool))
        # Foreground = union of all returned (non-background) class masks.
        fg = np.zeros(pre.shape, dtype=bool)
        for m in masks:
            fg |= (np.asarray(m) > 0)
        return _postfilter(fg)

    # Advanced spot detection (original pipeline).
    from pycat.toolbox.segmentation_tools import cell_mask_stretching, segment_subcellular_objects
    H, W = pre.shape
    whole = np.ones((H, W), dtype=bool); whole[:2, :2] = False
    cms = cell_mask_stretching(pre, whole.astype(int))
    refined, unrefined = segment_subcellular_objects(
        raw.copy(), cms.copy(), whole, 1, ball_radius, cell_df=None,
        min_spot_radius=min_radius, kurtosis_threshold=kurtosis_threshold,
        local_snr_threshold=local_snr_threshold, global_snr_threshold=0.8)
    lab, _ = _postfilter(refined)
    return lab, unrefined
