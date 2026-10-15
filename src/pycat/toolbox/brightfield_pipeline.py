"""Headless computations of the brightfield panels' result steps (1.7.005), shared by the panels and batch.

The cellular-BF and in-vitro-BF panels computed these inline in their button handlers, so batch could not
replay them: ``bf_od_metrics``, ``bf_per_cell_summary``, ``ivbf_field_summary`` and ``ivbf_spatial_metrology``
were recorded and never registered (Completion Spec E2). Each function here is the handler's computation,
unchanged, returning the tables the panel shows; the panel and the batch replay both call it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _stamped(df, operation_id, source_path):
    from pycat.utils.entity_ref import finalize_entity_table
    return finalize_entity_table(df, operation_id, source_path=source_path)


def bf_od_metrics(raw, condensate_mask, cell_labels, microns_per_pixel, source_path=None):
    """Per-condensate optical-density metrics on RAW counts (OD = -log10(I / I0) cannot be taken on normalised
    data), with the cell each condensate sits in when a cell mask is given."""
    from pycat.toolbox.brightfield_tools import bf_condensate_metrics
    df = bf_condensate_metrics(np.asarray(raw, dtype=np.float64), np.asarray(condensate_mask),
                               None if cell_labels is None else np.asarray(cell_labels), microns_per_pixel)
    return _stamped(df, 'bf_condensate_metrics', source_path)


def bf_per_cell(condensate_df, cell_labels, microns_per_pixel, source_path=None):
    """The per-condensate OD table aggregated to per-cell statistics."""
    from pycat.toolbox.brightfield_tools import bf_per_cell_summary
    return _stamped(bf_per_cell_summary(condensate_df, np.asarray(cell_labels), microns_per_pixel),
                    'bf_per_cell_summary', source_path)


def ivbf_field_summary(raw, droplet_mask, microns_per_pixel, source_path=None):
    """The in-vitro brightfield field summary: ``(summary dict, summary table, per-droplet OD table)``.

    OD = -log10(I / I0) with I0 the 90th percentile of the background (the transmitted light); the summary
    table carries the 2D-projection caveat (``annotate_summary_table``)."""
    from pycat.toolbox.brightfield_tools import bf_condensate_metrics
    from pycat.toolbox.condensate_modes import annotate_summary_table
    from pycat.toolbox.invitro_tools import field_summary
    raw = np.asarray(raw, dtype=np.float64)
    mask = np.asarray(droplet_mask)
    i0 = float(np.percentile(raw[mask == 0], 90)) if (mask == 0).any() else float(raw.max())
    od_proxy = -np.log10(np.clip(raw, 1e-6, None) / max(i0, 1e-6))
    summary = field_summary(mask, od_proxy, microns_per_pixel)
    per_drop = _stamped(bf_condensate_metrics(raw, mask, None, microns_per_pixel), 'ivbf_droplet_metrics',
                        source_path)
    return summary, annotate_summary_table(pd.DataFrame([summary]), mask), per_drop


def whole_field_spatial_metrics(object_mask, microns_per_pixel):
    """Spatial metrology over the whole field as one region: ``{1: run_all_spatial_metrics(...)}``, or ``{}`` with
    fewer than two objects. (The field label leaves a 2x2 corner unlabelled, as the panels always have.)"""
    from pycat.toolbox.spatial_metrology_tools import (
        get_puncta_centroids,
        run_all_spatial_metrics,
    )
    mask = np.asarray(object_mask)
    field = np.ones(mask.shape[:2], dtype=np.int32)
    field[:2, :2] = 0
    coords_df = get_puncta_centroids(mask, field, microns_per_pixel)
    results = {}
    if coords_df.empty:
        return results
    for label in [c for c in coords_df['cell_label'].unique() if c != 0]:
        coords = coords_df.loc[coords_df['cell_label'] == label, ['y_um', 'x_um']].values
        if len(coords) >= 2:
            results[label] = run_all_spatial_metrics(coords, field == label, microns_per_pixel)
    return results


def ivbf_size_and_contact(droplet_mask, raw_source, microns_per_pixel, n_bins=30, min_area_px=None, contact_label=0):
    """The in-vitro BF size-distribution fit (left-truncated at the segmentation's own detection limit) and the
    contact angle of one droplet (``contact_label`` 0 = the largest): ``(fit dict, fit table, contact table or
    None)``. ``None`` fit when the mask holds no droplets."""
    import skimage as sk

    from pycat.toolbox.invitro_tools import (
        detection_limit_for_radii,
        estimate_contact_angle,
        fit_size_distribution,
    )
    mask = np.asarray(droplet_mask)
    props = sk.measure.regionprops(mask.astype(np.int32))
    if not props:
        return None, None, None
    radii = np.array([np.sqrt(p.area * microns_per_pixel ** 2 / np.pi) for p in props])
    fit = fit_size_distribution(radii, n_bins,
                                xmin=detection_limit_for_radii(radii, min_area_px, microns_per_pixel))
    fit_df = pd.DataFrame([{k: v for k, v in fit.items() if not hasattr(v, '__len__')}])
    contact = None
    if raw_source is not None:
        label = int(contact_label) or max(props, key=lambda p: p.area).label
        ca = estimate_contact_angle(raw_source, mask, droplet_label=label)
        if ca.get('fit_success'):
            contact = pd.DataFrame([{k: v for k, v in ca.items() if not hasattr(v, '__len__')}])
    return fit, fit_df, contact
