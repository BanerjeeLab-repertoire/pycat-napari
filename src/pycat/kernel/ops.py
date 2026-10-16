"""Every registered kernel: op id -> the toolbox function that performs it (see ``operation_service``).

Most kernels are a declaration: ``passthrough(op_id, 'module:function', roles=...)`` calls the function with the
caller's inputs (by role) and params (by keyword). The few written out by hand reshape a result the function
returns in a form ``AnalysisResult`` does not take (a bare float, a (value, p) tuple) or derive an argument the
function needs from the input itself (upscale's original dimensions).
"""
from __future__ import annotations

from pycat.kernel.operation_service import (
    _result,
    named,
    passthrough,
    register_kernel,
    resolve,
)

_BG = 'pycat.toolbox.image_processing.background'
_FILTERS = 'pycat.toolbox.image_processing.filters'
_BASE = 'pycat.toolbox.image_processing._base'
_CASCADE = 'pycat.toolbox.contrast_cascade_tools'
_MORPH = 'pycat.toolbox.masks.morphology'

# ── Enhance: image -> image ──────────────────────────────────────────────────────────────────────────────────
passthrough('rolling_ball', f'{_BG}:rb_gaussian_background_removal')
passthrough('bg_removal_enhanced', f'{_BG}:rb_gaussian_bg_removal_with_edge_enhancement')
passthrough('gaussian', f'{_FILTERS}:gaussian_smooth_2d')
passthrough('dog', f'{_FILTERS}:dog_blob_enhance_2d')
passthrough('bilateral', f'{_FILTERS}:apply_bilateral_filter')
passthrough('log', f'{_FILTERS}:apply_laplace_of_gauss_filter')
passthrough('gabor', f'{_FILTERS}:gabor_filter_func')
passthrough('peak_edge', f'{_FILTERS}:peak_and_edge_enhancement_func')
passthrough('morph_gaussian', f'{_FILTERS}:morphological_gaussian_filter')
passthrough('bandpass', 'pycat.toolbox.fft_bandpass_tools:fft_bandpass')
passthrough('invert', f'{_BASE}:invert_image')
passthrough('rescale', f'{_BASE}:apply_rescale_intensity')
passthrough('ridge', f'{_CASCADE}:ridge_enhance')
passthrough('tone_map', f'{_CASCADE}:tone_map')
passthrough('local_contrast', f'{_CASCADE}:local_contrast_normalize')


def _upscale(inputs: dict, params: dict):
    """The original dimensions are the input's own (last two) dimensions, not a parameter the caller chooses."""
    img = inputs['image']
    out = resolve(f'{_BASE}:upscale_image_interp')(img, img.shape[-2], img.shape[-1], **params)
    return _result('upscale', 'image', out)


register_kernel('upscale', _upscale, wraps=f'{_BASE}:upscale_image_interp')

# ── Create: image -> mask / labels ───────────────────────────────────────────────────────────────────────────
passthrough('local_threshold', 'pycat.toolbox.segmentation.local_thresholding:local_thresholding_func',
            entity='mask', first=True)
passthrough('felzenszwalb', 'pycat.toolbox.segmentation.fz:felzenszwalb_segmentation_and_merging',
            entity='mask', first=True)
passthrough('cellpose', 'pycat.toolbox.segmentation.cellpose:cellpose_segmentation', entity='cell', first=True)

# ── Mask / label transforms ──────────────────────────────────────────────────────────────────────────────────
passthrough('binary_open', f'{_MORPH}:custom_binary_opening', roles=('mask',), entity='mask')
passthrough('binary_close', f'{_MORPH}:custom_binary_closing', roles=('mask',), entity='mask')
passthrough('binary_morph', f'{_MORPH}:binary_morph_operation', roles=('mask',), entity='mask')
passthrough('split_watershed', f'{_MORPH}:split_touching_objects', roles=('labels',), entity='mask', first=True)

# ── Measure: -> table ────────────────────────────────────────────────────────────────────────────────────────
passthrough('clean', 'pycat.toolbox.clean_spot_detection_tools:clean_detect', entity='puncta')
passthrough('condensate_physics.compute_msd', 'pycat.toolbox.condensate_physics.msd:compute_msd',
            roles=('tracks',), entity='track')
passthrough('client_enrichment', 'pycat.toolbox.partition_enrichment_tools:client_enrichment',
            roles=('image', 'dense_mask'), optional={'cell_mask': 'cell_mask', 'background_mask': 'background_mask'},
            entity='condensate')

_PEARSON = 'pycat.toolbox.coloc.metrics:pearsons_correlation'
_COLOC = 'pycat.toolbox.coloc.object_based'


def _pearson(inputs: dict, params: dict):
    """Pixel-wise Pearson within the ROI: the coefficient and its p-value as a one-row table."""
    pcc, p_value = resolve(_PEARSON)(inputs['ch1'], inputs['ch2'], inputs['roi'])
    return _result('colocalization', 'coloc', {'value': float(pcc), 'p_value': float(p_value)})


def _manders(op_id: str, function: str, first_role: str, second_role: str):
    def run(inputs: dict, params: dict):
        value = resolve(f'{_COLOC}:{function}')(inputs[first_role], inputs[second_role], inputs['roi'])
        return _result(op_id, 'coloc', {'value': float(value)})
    register_kernel(op_id, run, wraps=f'{_COLOC}:{function}', entity='coloc', part_of='obj_based_coloc.manders')


register_kernel('colocalization', _pearson, wraps=_PEARSON, entity='coloc')
_manders('coloc.manders_m1', 'manders_m1_calculation', 'masked_image_1', 'mask_2')
_manders('coloc.manders_m2', 'manders_m2_calculation', 'mask_1', 'masked_image_2')

# ── Dynamics: trajectories, MSD fits, coarsening, merge/fission, VPT ─────────────────────────────────────────
_DYN = 'pycat.toolbox.dynamic_spatial_tools'
named('dynamic_spatial.link_trajectories', f'{_DYN}:link_trajectories', entity='track')
named('dynamic_spatial.detect_merge_fission', f'{_DYN}:detect_merge_fission', entity='event')
named('condensate_physics.fit_anomalous_diffusion', 'pycat.toolbox.condensate_physics.msd:fit_anomalous_diffusion',
      entity='track')
named('coarsening_fit', 'pycat.toolbox.condensate_physics.coarsening:fit_coarsening', entity='condensate')
named('condensate_physics.fit_fusion_relaxation',
      'pycat.toolbox.condensate_physics.relaxation:fit_aspect_ratio_relaxation', entity='condensate')
named('bead_detect', 'pycat.toolbox.vpt.detection:detect_beads_stack', entity='bead')
named('viscosity', 'pycat.toolbox.vpt.viscosity:viscosity_from_diffusion', entity='condensate')
named('host_segment', 'pycat.toolbox.vpt.host:segment_host_condensate', entity='mask')
named('host_erode', 'pycat.toolbox.vpt.host:erode_host_mask', entity='mask')

# ── In-vitro, brightfield and preprocessing ───────────────────────────────────────────────────────────────────
_PRE = 'pycat.toolbox.image_processing.preprocessing'
_BF = 'pycat.toolbox.brightfield_tools'
_TSIVF = 'pycat.toolbox.timeseries_invitro_tools'
named('preprocess', f'{_PRE}:pre_process_image')
named('flatfield', f'{_PRE}:apply_flatfield_correction')
named('bg_subtract_clear', f'{_PRE}:apply_background_subtraction')
named('log_enhance', f'{_FILTERS}:apply_laplace_of_gauss_enhancement')
named('wbns', 'pycat.toolbox.image_processing.wavelet:wbns_func')
named('fg_suppress', f'{_BG}:soft_foreground_suppression')
named('dpr', 'pycat.toolbox.image_processing.deblur:deblur_by_pixel_reassignment')
named('optical_density', f'{_BF}:compute_optical_density')
named('bf_segment', f'{_BF}:segment_bf_condensates', entity='mask')
named('ivf_droplet_segment', 'pycat.toolbox.invitro.segmentation:segment_ivf_droplets', entity='mask')
named('partition_coefficient', 'pycat.toolbox.invitro.partition:partition_coefficient_local', entity='condensate')
named('size_distribution', 'pycat.toolbox.invitro.size_distribution:fit_size_distribution', entity='condensate')
named('ts_droplet_segment', f'{_TSIVF}:segment_stack_per_frame', entity='mask')
named('ts_track_relabel', f'{_TSIVF}:relabel_stack_by_track', entity='mask')
named('multi_otsu', 'pycat.toolbox.batch_roi_tools:multi_otsu_cell_mask', entity='mask')
named('mask_stretch', 'pycat.toolbox.segmentation.morphology:cell_mask_stretching', entity='mask')
named('subcellular_segment', 'pycat.toolbox.segmentation.subcellular:segment_subcellular_objects', entity='mask')
named('watershed_cv', 'pycat.toolbox.segmentation.watershed:opencv_watershed_func', entity='mask')

# ── Spatial, topology, z-stack ────────────────────────────────────────────────────────────────────────────────
_ZS = 'pycat.toolbox.zstack_segmentation_tools'
named('spatial_statistics', 'pycat.toolbox.spatial_metrology_tools:ripleys_l', entity='condensate')
named('topology_envelope', 'pycat.toolbox.topology_tools:compute_topology_envelope')
named('void_detect', 'pycat.toolbox.topology_tools:detect_chromatin_voids', entity='mask')
named('bg_subtract_3d', f'{_ZS}:bg_removal_3d')
named('cellpose_3d', f'{_ZS}:cellpose_segmentation_3d', entity='cell')
named('subcellular_segment_3d', f'{_ZS}:segment_subcellular_objects_3d', entity='mask')

# ── Measurement and analysis steps (shared by a panel and its batch replay) ──────────────────────────────────
# Plannable ops have their own curated entry in ``navigator.op_catalog._measure_ops``; a ``part_of`` op is a stage
# or variant of the catalog op it names, planned as that op.
_FEAT = 'pycat.toolbox.feature_analysis_tools'
_BFP = 'pycat.toolbox.brightfield_pipeline'
_KIN = 'pycat.toolbox.invitro.timeseries_kinetics'
_SPM = 'pycat.toolbox.spatial_metrology_tools'
_SACF = 'pycat.toolbox.spatial_acf_tools'
_CELLTS = 'pycat.toolbox.timeseries.cell_pipeline'
_CM = 'pycat.toolbox.coloc.metrics'
named('feature_analysis.cell_analysis', f'{_FEAT}:cell_analysis_func', entity='cell')
# The Navigator plans condensate measurement as feature_analysis.cell_analysis on a condensate target (the
# executor dispatches on the target), so the puncta analysis is that op's condensate variant.
named('feature_analysis.condensate_analysis', f'{_FEAT}:puncta_analysis_func', entity='condensate',
      part_of='feature_analysis.cell_analysis')
named('masks.measure_binary_mask', 'pycat.toolbox.masks.measurement:binary_mask_properties', entity='mask')
named('spatial_metrology.ripley', f'{_SPM}:run_all_spatial_metrics', entity='condensate')
named('spatial_metrology.centroids', f'{_SPM}:get_puncta_centroids', entity='condensate',
      part_of='spatial_metrology.ripley')
named('spatial_metrology.whole_field', f'{_BFP}:whole_field_spatial_metrics', entity='condensate',
      part_of='spatial_metrology.ripley')
named('spatial_acf.per_cell', f'{_SACF}:sacf_lir_mode', entity='cell')
named('spatial_acf.whole_image', f'{_SACF}:sacf_whole_image_mode')
named('coloc.manders_k1', f'{_CM}:manders_k1_calculation', entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('coloc.manders_k2', f'{_CM}:manders_k2_calculation', entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('coloc.manders_overlap', f'{_CM}:manders_overlap', entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('dynamic_spatial.frame_properties', f'{_DYN}:extract_frame_properties', entity='condensate',
      part_of='dynamic_spatial.link_trajectories')
named('auto_crop.cell_bboxes', 'pycat.toolbox.batch_roi_tools:cell_bboxes_from_mask', part_of='cropped')
named('auto_crop.foreground_bbox', 'pycat.toolbox.batch_roi_tools:multi_otsu_foreground_bbox', part_of='cropped')
named('cellpose.keyframe', 'pycat.toolbox.ts_cellpose_tools:run_keyframe_cellpose', entity='cell',
      part_of='cellpose')
named('vpt.measured_drift', 'pycat.toolbox.vpt.measured_drift:measure_and_apply_if_real', entity='bead',
      part_of='vpt.microrheology')

# brightfield
named('bf_preprocess.stages', f'{_BF}:preprocess_brightfield', part_of='bf_preprocess')
named('brightfield.condensate_metrics', f'{_BF}:bf_condensate_metrics', entity='condensate')
named('brightfield.od_metrics', f'{_BFP}:bf_od_metrics', entity='condensate')
named('brightfield.per_cell', f'{_BFP}:bf_per_cell', entity='cell', part_of='brightfield.od_metrics')
named('brightfield.invitro_field_summary', f'{_BFP}:ivbf_field_summary', entity='condensate')
named('brightfield.invitro_size_distribution', f'{_BFP}:ivbf_size_and_contact', entity='condensate')

# time series: cellular, and in-vitro droplets
named('cell_timeseries.analyze', f'{_CELLTS}:analyze_cell_timeseries', entity='cell')
# the image the cellular time series segments its cells on
named('cell_timeseries.time_average', f'{_CELLTS}:time_average', part_of='cell_timeseries.analyze')
named('invitro_timeseries.link_droplets', f'{_TSIVF}:link_condensates', entity='track')
named('invitro_timeseries.measure_droplets', f'{_KIN}:measure_droplets_multichannel', entity='condensate',
      part_of='invitro_timeseries.link_droplets')
named('invitro_timeseries.object_trajectories', f'{_TSIVF}:build_object_records', entity='track')
named('invitro_timeseries.field_trajectories', f'{_TSIVF}:field_trajectories')
named('invitro_timeseries.kinetics', f'{_KIN}:droplet_kinetics', entity='condensate')
named('invitro_timeseries.kinetics_heterogeneity', f'{_KIN}:kinetics_heterogeneity', entity='condensate',
      part_of='invitro_timeseries.kinetics')
named('invitro_timeseries.heterogeneity_over_time', f'{_KIN}:heterogeneity_over_time', entity='condensate',
      part_of='invitro_timeseries.kinetics')
