"""
Topology tools — chromatin / nucleoplasm structural envelope and higher-order
spatial-organisation metrics for PyCAT-Napari.

Motivation
----------
The rolling-ball *background* estimate (the smoothed intensity envelope that is
normally subtracted and discarded) is itself a meaningful signal. On a nuclear
channel (e.g. DAPI) it suppresses fine puncta and leaves the large-scale
nucleoplasm intensity structure, which reads as a connected chromatin network.
This module exposes that envelope as a named output and derives principled
per-cell metrics from it.

This is the shared foundation for two downstream utilities:
  • over-segmentation sanity check (do many objects fall into one intensity
    basin of the envelope?),
  • higher-order connectedness / wetting metrics (do condensates sit on and
    bridge elevated-intensity ridges of the envelope?).

Both build on ``compute_topology_envelope`` and the metrics here rather than
recomputing the envelope.
"""

import numpy as np
import skimage as sk
import scipy.ndimage as ndi

from pycat.utils.general_utils import dtype_conversion_func
from pycat.toolbox.image_processing_tools import compute_rolling_ball_background


def compute_topology_envelope(image, ball_radius, mode='rolling_ball', smooth=True):
    """
    Compute the smoothed structural intensity envelope of an image.

    Parameters
    ----------
    image : numpy.ndarray
        Input image (any dtype). Returned envelope is float32.
    ball_radius : int
        Structural scale. For 'rolling_ball' this is the ball radius; for
        'gaussian' the low-pass sigma is set to ball_radius so the two modes
        capture structure at a comparable scale.
    mode : {'rolling_ball', 'gaussian'}
        - 'rolling_ball': morphological rolling-ball background (the envelope you
          see when the fine puncta are removed; traces chromatin/nucleoplasm
          topology). Uses the existing GPU/CPU-routed implementation.
        - 'gaussian': a Gaussian low-pass at ball_radius scale. Smoother and
          faster; a good cross-check against the rolling-ball envelope.
    smooth : bool
        If True, apply a light Gaussian smoothing (sigma = ball_radius/2) to the
        envelope so both modes yield a continuous field suitable for basin and
        connectivity analysis. The rolling-ball CPU path is otherwise slightly
        blocky.

    Returns
    -------
    numpy.ndarray
        The float32 structural envelope, same shape as ``image``.
    """
    img = dtype_conversion_func(image, output_bit_depth='float32')

    if mode == 'gaussian':
        env = ndi.gaussian_filter(img, sigma=max(1.0, float(ball_radius)))
    else:  # rolling_ball
        env = compute_rolling_ball_background(img, int(ball_radius)).astype(np.float32)

    if smooth:
        env = ndi.gaussian_filter(env, sigma=max(0.5, ball_radius / 2.0))

    return env.astype(np.float32)


def normalize_within_mask(field, mask=None):
    """
    Min–max normalise a field to [0, 1] using only the pixels inside ``mask``
    (or the whole image if mask is None). Pixels outside the mask are set to 0.
    Makes envelope *shape* comparable across cells of differing brightness.
    """
    field = field.astype(np.float32)
    if mask is None:
        vals = field.ravel()
    else:
        mask = mask.astype(bool)
        vals = field[mask]
    if vals.size == 0:
        return np.zeros_like(field)
    lo = float(vals.min()); hi = float(vals.max())
    out = np.zeros_like(field)
    if hi > lo:
        norm = (field - lo) / (hi - lo)
    else:
        norm = np.zeros_like(field)
    if mask is None:
        out = np.clip(norm, 0.0, 1.0)
    else:
        out[mask] = np.clip(norm[mask], 0.0, 1.0)
    return out.astype(np.float32)


def topology_metrics(envelope, cell_mask, connectivity_percentile=50.0,
                     min_basin_distance=None, ball_radius=None):
    """
    Per-cell metrics derived from the structural envelope.

    All metrics are computed on the envelope pixels inside ``cell_mask`` only.

    Parameters
    ----------
    envelope : numpy.ndarray
        The structural envelope (from ``compute_topology_envelope``).
    cell_mask : numpy.ndarray (bool)
        Binary mask of the cell/nucleus to analyse.
    connectivity_percentile : float
        Percentile (of in-cell envelope intensity) used as the threshold for the
        connectivity/percolation metrics. 50 = median: pixels above the median
        form the 'high-intensity network'.
    min_basin_distance : int or None
        Minimum separation (px) between envelope intensity maxima when counting
        basins. Defaults to ball_radius (or 3 if not given), so basins are counted
        at the structural scale rather than per-pixel noise.
    ball_radius : int or None
        Structural scale, used only as the default for ``min_basin_distance``.

    Returns
    -------
    dict
        Metrics:
          topo_cov                : coefficient of variation of envelope in-cell
                                    (std/mean) — how structured the field is.
          topo_roughness          : std of the normalised in-cell envelope.
          topo_n_basins           : number of distinct intensity maxima (basins)
                                    in the envelope at the structural scale.
          topo_n_components       : connected components of the >percentile mask.
          topo_largest_frac       : largest connected component as a fraction of
                                    the >percentile area (→1 = percolating network,
                                    →0 = fragmented).
          topo_high_area_frac     : fraction of cell area above the percentile
                                    threshold (how much of the cell is 'high').
    """
    mask = cell_mask.astype(bool)
    vals = envelope[mask].astype(np.float64)
    out = {
        'topo_cov': 0.0, 'topo_roughness': 0.0, 'topo_n_basins': 0,
        'topo_n_components': 0, 'topo_largest_frac': 0.0, 'topo_high_area_frac': 0.0,
    }
    if vals.size < 4:
        return out

    mean = float(vals.mean())
    std = float(vals.std())
    out['topo_cov'] = (std / mean) if mean > 0 else 0.0

    # Roughness on the mask-normalised envelope (0..1) so it's brightness-agnostic.
    norm_env = normalize_within_mask(envelope, mask)
    out['topo_roughness'] = float(norm_env[mask].std())

    # Basin count: local maxima of the envelope at the structural scale.
    if min_basin_distance is None:
        min_basin_distance = int(ball_radius) if ball_radius else 3
    min_basin_distance = max(1, int(min_basin_distance))
    try:
        # Restrict maxima to inside the cell.
        env_in = np.where(mask, envelope, 0)
        coords = sk.feature.peak_local_max(
            env_in, min_distance=min_basin_distance, labels=mask.astype(int))
        out['topo_n_basins'] = int(len(coords))
    except Exception:
        out['topo_n_basins'] = 0

    # Connectivity / percolation at the percentile threshold.
    thr = float(np.percentile(vals, connectivity_percentile))
    high = mask & (envelope >= thr)
    high_area = int(high.sum())
    out['topo_high_area_frac'] = high_area / float(mask.sum()) if mask.sum() else 0.0
    if high_area > 0:
        lbl, n = ndi.label(high)
        out['topo_n_components'] = int(n)
        if n > 0:
            sizes = ndi.sum(np.ones_like(lbl), lbl, range(1, n + 1))
            out['topo_largest_frac'] = float(sizes.max()) / float(high_area)
    return out


def run_chromatin_topology(image_layer, data_instance, viewer,
                           mode='rolling_ball', connectivity_percentile=50.0):
    """
    Compute the chromatin/nucleoplasm topology envelope for the active image and
    add it to the viewer as both a raw and a mask-normalised layer. If a
    'Labeled Cell Mask' is present, per-cell topology metrics are written into
    cell_df.

    Parameters
    ----------
    image_layer : napari.layers.Image
        The nuclear (or other) channel to analyse.
    data_instance : object
        Provides data_repository (ball_radius) and cell_df.
    viewer : napari.Viewer
        Target viewer.
    mode : {'rolling_ball', 'gaussian'}
        Envelope computation mode.
    connectivity_percentile : float
        Threshold percentile for the connectivity metrics.
    """
    import pandas as pd
    from pycat.ui.ui_utils import add_image_with_default_colormap
    from napari.utils.notifications import show_info as napari_show_info
    from napari.utils.notifications import show_warning as napari_show_warning

    image = np.asarray(image_layer.data)
    ball_radius = int(data_instance.data_repository.get('ball_radius', 15))

    envelope = compute_topology_envelope(image, ball_radius, mode=mode, smooth=True)

    # Raw envelope layer (brightness comparable across cells).
    add_image_with_default_colormap(
        envelope, viewer, name=f"Chromatin Topology {image_layer.name}")

    # Determine mask context for normalisation + metrics.
    if 'Labeled Cell Mask' in viewer.layers:
        cell_masks = np.asarray(viewer.layers['Labeled Cell Mask'].data)
        any_mask = cell_masks > 0
    else:
        cell_masks = None
        any_mask = None
        napari_show_warning(
            "No 'Labeled Cell Mask' — topology metrics need cells; adding layers only.")

    # Normalised envelope layer (shape comparable across cells).
    norm_env = normalize_within_mask(envelope, any_mask)
    add_image_with_default_colormap(
        norm_env, viewer, name=f"Chromatin Topology (norm) {image_layer.name}")

    if cell_masks is None:
        napari_show_info(f"Chromatin topology ({mode}) added as raw + normalised layers.")
        return

    # Per-cell metrics.
    cell_df = data_instance.get_data('cell_df', pd.DataFrame())
    if cell_df is None or cell_df.empty:
        napari_show_warning(
            "cell_df is empty — run Cell Analyzer first to attach topology metrics.")
        napari_show_info(f"Chromatin topology ({mode}) layers added (no metrics).")
        return

    labels = [l for l in np.unique(cell_masks) if l != 0]
    for label in labels:
        cell_mask = (cell_masks == label)
        m = topology_metrics(envelope, cell_mask,
                             connectivity_percentile=connectivity_percentile,
                             ball_radius=ball_radius)
        for k, v in m.items():
            cell_df.loc[cell_df['label'] == label, k] = v

    data_instance.data_repository['cell_df'] = cell_df
    napari_show_info(
        f"Chromatin topology ({mode}) done: raw + normalised layers added, "
        f"metrics written for {len(labels)} cell(s).")


# ---------------------------------------------------------------------------
# Chromatin void / nucleolus estimation
# ---------------------------------------------------------------------------
#
# Nucleoli and other DNA-excluding bodies appear as rounded low-intensity voids
# in the DAPI channel because they physically exclude chromatin. The raw channel
# is often too noisy / low-contrast to threshold these directly, but the smoothed
# chromatin-density envelope reveals them as coherent low basins. This estimator
# finds those basins inside the nuclear territory, classifies each as
# "nucleolus-like" (round + compact + convex) or "irregular-void", and — if a
# condensate channel is supplied — reports whether condensate signal is enriched
# INSIDE each void vs. in a surrounding ring (a weak partition/exclusion
# inference for use when no nucleolar marker channel is available).
#
# This is deliberately framed as WEAK INFERENCE: a round solid void is only
# *likely* a nucleolus; the tiered label lets downstream analysis weight the
# confidence rather than treat it as a hard call.

VOID_DETECTION_DEFAULTS = {
    'envelope_sigma_scale': 0.8,   # gaussian sigma = scale * ball_radius (density field)
    'density_percentile': 35.0,    # envelope below this percentile (in-nucleus) = void
    'min_void_area': 40,           # px; drop specks
    'circularity_min': 0.60,       # nucleolus-like roundness gate
    'solidity_min': 0.88,          # nucleolus-like convexity gate
    'border_frac_max': 0.50,       # reject voids mostly on the nuclear border
    'ring_px': 4,                  # width of the surround ring for partition test
}


def _nuclear_territory(dapi, smooth_sigma=4.0, thr_scale=0.4, min_area=800):
    """Generous hole-filled nuclear territory to bound the void search."""
    sm = ndi.gaussian_filter(dapi.astype(np.float32), smooth_sigma)
    nz = sm[sm > 0]
    if nz.size == 0:
        return np.zeros(dapi.shape, bool)
    thr = sk.filters.threshold_otsu(nz)
    terr = ndi.binary_fill_holes(sm > thr * thr_scale)
    terr = ndi.binary_closing(terr, sk.morphology.disk(7))
    terr = ndi.binary_fill_holes(terr)
    lbl, n = ndi.label(terr)
    if n == 0:
        return terr
    sizes = ndi.sum(np.ones_like(lbl), lbl, range(1, n + 1))
    keep = np.isin(lbl, 1 + np.where(sizes > min_area)[0])
    return keep


def detect_chromatin_voids(dapi, ball_radius, condensate_image=None,
                           params=None, territory=None):
    """
    Detect and classify DNA-excluding voids (nucleoli / condensate-filled holes)
    in a DAPI channel from its chromatin-density envelope.

    Parameters
    ----------
    dapi : np.ndarray
        DAPI (or other chromatin) channel.
    ball_radius : int
        Structural scale; sets the density-envelope smoothing.
    condensate_image : np.ndarray, optional
        A condensate channel. If given, per-void partition inference is added:
        mean condensate intensity inside the void vs. in a surrounding ring.
    params : dict, optional
        Overrides for VOID_DETECTION_DEFAULTS.
    territory : np.ndarray (bool), optional
        Precomputed nuclear territory mask. If None it is estimated from DAPI.

    Returns
    -------
    dict with:
        labels          : int label image of kept voids
        nucleolus_like  : bool mask of round/compact voids
        irregular       : bool mask of the other kept voids
        territory       : bool nuclear-territory mask used
        envelope        : the density envelope (float32)
        voids           : list of per-void dicts (area, circularity, solidity,
                          class, and — if condensate_image given — cond_in,
                          cond_ring, partition_ratio, partition_call)
    """
    p = dict(VOID_DETECTION_DEFAULTS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})

    dapi = dapi.astype(np.float32)
    if territory is None:
        territory = _nuclear_territory(dapi)

    # Chromatin density envelope (heavy gaussian = large-scale density field).
    sigma = max(1.0, p['envelope_sigma_scale'] * ball_radius)
    env = ndi.gaussian_filter(dapi, sigma).astype(np.float32)

    # Voids = inside territory where density is in the low tail.
    in_vals = env[territory]
    if in_vals.size == 0:
        empty = np.zeros(dapi.shape, bool)
        return {'labels': np.zeros(dapi.shape, int), 'nucleolus_like': empty,
                'irregular': empty, 'territory': territory, 'envelope': env,
                'voids': []}
    lo = np.percentile(in_vals, p['density_percentile'])
    voids = territory & (env < lo)
    voids = ndi.binary_opening(voids, sk.morphology.disk(2))
    voids = ndi.binary_fill_holes(voids)

    interior = ndi.binary_erosion(territory, sk.morphology.disk(2))

    vl, vn = ndi.label(voids)
    out_labels = np.zeros(dapi.shape, int)
    nucleolar = np.zeros(dapi.shape, bool)
    irregular = np.zeros(dapi.shape, bool)
    void_list = []
    next_id = 1
    for i in range(1, vn + 1):
        obj = vl == i
        area = int(obj.sum())
        if area < p['min_void_area']:
            continue
        border_frac = float((obj & ~interior).sum()) / area
        if border_frac > p['border_frac_max']:
            continue  # mostly on the nuclear rim — an edge concavity, not a void
        rp = sk.measure.regionprops(obj.astype(int))[0]
        perim = rp.perimeter if rp.perimeter > 0 else 1.0
        circ = float(4 * np.pi * area / (perim ** 2))
        sol = float(rp.solidity)
        is_nucleolar = (circ >= p['circularity_min'] and sol >= p['solidity_min'])

        rec = {'id': next_id, 'area': area, 'circularity': round(circ, 3),
               'solidity': round(sol, 3), 'border_frac': round(border_frac, 3),
               'class': 'nucleolus-like' if is_nucleolar else 'irregular-void'}

        if condensate_image is not None:
            ring = ndi.binary_dilation(obj, sk.morphology.disk(p['ring_px'])) & ~obj
            ring &= territory
            cond_in = float(condensate_image[obj].mean())
            cond_ring = float(condensate_image[ring].mean()) if ring.any() else np.nan
            ratio = (cond_in / cond_ring) if (cond_ring and np.isfinite(cond_ring)
                                              and cond_ring > 0) else np.nan
            if np.isfinite(ratio):
                if ratio >= 1.2:
                    call = 'partitioning'      # enriched inside the void
                elif ratio <= 0.8:
                    call = 'excluded'          # depleted inside the void
                else:
                    call = 'ambiguous'
            else:
                call = 'n/a'
            rec.update({'cond_in': round(cond_in, 2),
                        'cond_ring': round(cond_ring, 2) if np.isfinite(cond_ring) else None,
                        'partition_ratio': round(ratio, 3) if np.isfinite(ratio) else None,
                        'partition_call': call})

        out_labels[obj] = next_id
        if is_nucleolar:
            nucleolar |= obj
        else:
            irregular |= obj
        void_list.append(rec)
        next_id += 1

    return {'labels': out_labels, 'nucleolus_like': nucleolar,
            'irregular': irregular, 'territory': territory, 'envelope': env,
            'voids': void_list}


def run_chromatin_void_detection(dapi_layer, viewer, data_instance,
                                 condensate_layer=None, params=None):
    """
    Run chromatin-void / nucleolus estimation for the active DAPI layer and add
    the results as napari layers: nucleolus-like voids, irregular voids, and (if a
    condensate channel is given) a partition annotation printed to the log and, if
    cell_df + a labeled cell mask are present, per-cell void counts.

    Framed as WEAK INFERENCE — nucleolus-like = round/compact DNA-excluding void;
    the partition call (partitioning / excluded / ambiguous) is a guess to be used
    as supporting, not definitive, evidence.
    """
    import pandas as pd
    from pycat.ui.ui_utils import add_image_with_default_colormap
    from napari.utils.notifications import show_info as napari_show_info

    dapi = np.asarray(dapi_layer.data)
    cond = np.asarray(condensate_layer.data) if condensate_layer is not None else None
    ball_radius = int(data_instance.data_repository.get('ball_radius', 15))

    res = detect_chromatin_voids(dapi, ball_radius, condensate_image=cond,
                                 params=params)

    # Add label layers (Labels so they carry the per-void id).
    if res['nucleolus_like'].any():
        viewer.add_labels(res['nucleolus_like'].astype(int),
                          name=f"Nucleolus-like Voids {dapi_layer.name}")
    if res['irregular'].any():
        viewer.add_labels((res['irregular'].astype(int) * 2),
                          name=f"Irregular Voids {dapi_layer.name}")

    n_nuc = sum(1 for v in res['voids'] if v['class'] == 'nucleolus-like')
    n_irr = len(res['voids']) - n_nuc
    # Log the per-void partition inference when a condensate channel was used.
    if cond is not None:
        for v in res['voids']:
            print(f"[PyCAT Voids] id={v['id']} {v['class']} area={v['area']} "
                  f"partition={v.get('partition_call')} "
                  f"ratio={v.get('partition_ratio')}")

    # Per-cell counts into cell_df if the cell context exists.
    try:
        if 'Labeled Cell Mask' in viewer.layers:
            cell_masks = np.asarray(viewer.layers['Labeled Cell Mask'].data)
            cell_df = data_instance.get_data('cell_df', pd.DataFrame())
            if cell_df is not None and not cell_df.empty:
                for label in [l for l in np.unique(cell_masks) if l != 0]:
                    cm = cell_masks == label
                    nn = int(np.unique(res['labels'][cm & (res['nucleolus_like'])]).size)
                    ni = int(np.unique(res['labels'][cm & (res['irregular'])]).size)
                    cell_df.loc[cell_df['label'] == label, 'n_nucleolus_like_voids'] = nn
                    cell_df.loc[cell_df['label'] == label, 'n_irregular_voids'] = ni
                data_instance.data_repository['cell_df'] = cell_df
    except Exception as _e:
        from pycat.utils.general_utils import debug_log
        debug_log("void detection: writing per-cell void counts", _e)

    napari_show_info(
        f"Void detection: {n_nuc} nucleolus-like, {n_irr} irregular "
        f"(weak inference — round voids are *likely* nucleoli).")
    return res
