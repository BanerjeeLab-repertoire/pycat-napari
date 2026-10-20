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
named('vpt.link_beads', 'pycat.toolbox.vpt.link_step:link_bead_populations', entity='bead',
      part_of='vpt.microrheology')
named('vpt.drift', 'pycat.toolbox.vpt.measured_drift:measure_drift', entity='bead', part_of='vpt.microrheology')
named('vpt.drift_corrected', 'pycat.toolbox.vpt.measured_drift:drift_corrected_for_msd', entity='bead',
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

# ── Panel-only science (until 2026-10-19 each was called by one panel, so no two routes could disagree) ─────────
# Now in the catalog like everything else: a stage of an op the Navigator already plans is part_of it; a method
# with no op yet has its own entry in ``navigator.op_catalog._measure_ops``, under an observable of its own.
named('spatial_metrology.convex_hull', f'{_SPM}:convex_hull_metrics',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.delaunay', f'{_SPM}:delaunay_metrics',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.distance_to_roi', f'{_SPM}:distance_to_roi',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.local_density', f'{_SPM}:local_object_density',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.minimum_spanning_tree', f'{_SPM}:minimum_spanning_tree',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.nearest_neighbour', f'{_SPM}:nearest_neighbour_distance',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.pair_correlation', f'{_SPM}:pair_correlation_function',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.radial_profile', f'{_SPM}:radial_localization_profile',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.null_envelope', f'{_SPM}:spatial_null_envelope',
      entity='condensate', part_of='spatial_metrology.ripley')
named('spatial_metrology.voronoi', f'{_SPM}:voronoi_metrics', entity='condensate', part_of='spatial_metrology.ripley')

_ORG = 'pycat.toolbox.organizational_metrics_tools'
named('organizational_metrics.cluster_sizes', f'{_ORG}:cluster_size_distribution',
      entity='condensate', part_of='organizational_metrics.spacing')
named('organizational_metrics.distance_to_boundary', f'{_ORG}:distance_to_boundary',
      entity='condensate', part_of='organizational_metrics.spacing')
named('organizational_metrics.pairwise_spacing', f'{_ORG}:inter_condensate_spacing',
      entity='condensate', part_of='organizational_metrics.spacing')
named('organizational_metrics.occupancy', f'{_ORG}:per_cell_occupancy',
      entity='condensate', part_of='organizational_metrics.spacing')
named('organizational_metrics.entropy', f'{_ORG}:spatial_entropy',
      entity='condensate', part_of='organizational_metrics.spacing')

_MCX = 'pycat.toolbox.morphological_complexity_tools'
named('morphological_complexity.fractal_per_cell', f'{_MCX}:fractal_dimension_per_cell',
      entity='condensate', part_of='morphological_complexity.fractal')
named('morphological_complexity.lacunarity', f'{_MCX}:lacunarity',
      entity='condensate', part_of='morphological_complexity.fractal')
named('morphological_complexity.orientation_order', f'{_MCX}:orientation_order_parameter',
      entity='condensate', part_of='morphological_complexity.fractal')
named('morphological_complexity.tortuosity', f'{_MCX}:tortuosity_per_object',
      entity='condensate', part_of='morphological_complexity.fractal')

# trajectory analyses read the linked tracks; the Bayesian and TrackMate linkers are variants of the linking
named('dynamic_spatial.cluster_lifetimes', f'{_DYN}:cluster_lifetime_analysis',
      entity='track', part_of='dynamic_spatial.link_trajectories')
named('dynamic_spatial.growth_shrinkage', f'{_DYN}:growth_shrinkage_kinetics',
      entity='track', part_of='dynamic_spatial.link_trajectories')
named('dynamic_spatial.link_bayesian', f'{_DYN}:link_trajectories_bayesian',
      entity='track', part_of='dynamic_spatial.link_trajectories')
named('dynamic_spatial.neighbourhood_persistence', f'{_DYN}:neighbourhood_persistence',
      entity='track', part_of='dynamic_spatial.link_trajectories')
named('dynamic_spatial.trajectory_metrics', f'{_DYN}:trajectory_metrics',
      entity='track', part_of='dynamic_spatial.link_trajectories')
named('dynamic_spatial.link_trackmate', 'pycat.toolbox.trackmate_bridge:run_trackmate_lap_tracking', entity='track',
      part_of='dynamic_spatial.link_trajectories')

_FRAP = 'pycat.toolbox.frap_tools'
named('frap.analysis', f'{_FRAP}:run_frap_analysis', part_of='frap.fit_recovery')
named('frap.analysis_multi', f'{_FRAP}:run_frap_analysis_multi', part_of='frap.fit_recovery')
named('frap.fit_geometry', f'{_FRAP}:frap_fit_geometry', part_of='frap.fit_recovery')

_FUS = 'pycat.toolbox.fusion_tools'
named('fusion.fit_relaxation', f'{_FUS}:fit_fusion_relaxation', part_of='condensate_physics.fit_fusion_relaxation')
named('fusion.aspect_ratio_signal', f'{_FUS}:aspect_ratio_signal', part_of='condensate_physics.fit_fusion_relaxation')
named('fusion.force_signal', f'{_FUS}:build_fusion_signal_from_forces',
      part_of='condensate_physics.fit_fusion_relaxation')
named('fusion.droplet_radii', f'{_FUS}:droplet_radii_um', part_of='condensate_physics.fit_fusion_relaxation')
named('fusion.fit_window', f'{_FUS}:suggest_fit_window', part_of='condensate_physics.fit_fusion_relaxation')

_COLOCPKG = 'pycat.toolbox.coloc'
named('coloc.li_ica', f'{_COLOCPKG}.analysis:li_ica_histogram',
      entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('coloc.pwcca_methods', f'{_COLOCPKG}.analysis:process_pwcca_methods',
      entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('coloc.spatial_null', f'{_COLOCPKG}.nulls:spatial_null_test',
      entity='coloc', part_of='pixel_wise_corr.pearson_manders')
named('coloc.manders_threshold_sensitivity', f'{_COLOCPKG}.thresholding:manders_threshold_sensitivity',
      entity='coloc', part_of='pixel_wise_corr.pearson_manders')

# brightfield
named('brightfield.texture', f'{_BF}:bf_texture_features', entity='condensate', part_of='brightfield.condensate_metrics')
named('brightfield.frame_quality', f'{_BF}:bf_analyse_frame_quality', part_of='data_qc.assess')
named('best_slice.select', f'{_BF}:select_best_slice', part_of='best_slice')

# segmentation stages and variants
named('rf_classifier.train_apply', 'pycat.toolbox.segmentation.cellpose:train_and_apply_rf_classifier',
      part_of='rf_classifier')
named('subcellular_segment.in_cells', 'pycat.toolbox.segmentation.subcellular:segment_condensates_in_cells',
      entity='condensate', part_of='subcellular_segment')
named('contrast_floor.measure', 'pycat.toolbox.segmentation.contrast_floor:measure_for_floor', entity='condensate',
      part_of='subcellular_segment')
named('contrast_floor.apply', 'pycat.toolbox.segmentation.contrast_floor:apply_floor', entity='condensate',
      part_of='subcellular_segment')
named('im2bw.threshold', 'pycat.toolbox.fft_bandpass_tools:im2bw', part_of='im2bw')
named('upscale.advise', 'pycat.toolbox.segmentation_scale_advisor:advise_upscaling', part_of='upscale')
named('upscale.object_diameter', 'pycat.toolbox.segmentation_scale_advisor:measure_object_diameter_px',
      part_of='upscale')
named('temporal_enhance.compete', 'pycat.toolbox.temporal_enhancement_tools:compete_methods', part_of='temporal_enhance')
named('spatial_acf.drawn_rect', f'{_SACF}:sacf_drawn_rect_mode', part_of='spatial_acf.whole_image')

_TOPO = 'pycat.toolbox.topology_tools'
named('topology.metrics', f'{_TOPO}:topology_metrics', entity='cell', part_of='topology_envelope')
named('topology.image_noise', f'{_TOPO}:estimate_image_noise', part_of='topology_envelope')
named('topology.normalize_within_mask', f'{_TOPO}:normalize_within_mask', part_of='topology_envelope')

# 3D: the per-object measurement of a z-stack
named('zstack.condensate_metrics_3d', 'pycat.toolbox.zstack_segmentation_tools:condensate_metrics_3d',
      entity='condensate', part_of='feature_analysis.cell_analysis')

# methods with an op of their own (navigator.op_catalog._measure_ops)
_SPIDA = 'pycat.toolbox.spida_tools'
named('spida.fit', f'{_SPIDA}:fit_spida_histogram')
named('spida.histogram', f'{_SPIDA}:build_intensity_histogram', part_of='spida.fit')
named('spida.check_assumptions', f'{_SPIDA}:check_assumptions', part_of='spida.fit')
named('spida.check_modality', f'{_SPIDA}:check_modality', part_of='spida.fit')

named('nb.number_and_brightness', 'pycat.toolbox.nb_tools:number_and_brightness')

_UNMIX = 'pycat.toolbox.unmixing_tools'
named('unmixing.unmix', f'{_UNMIX}:unmix')
named('unmixing.mixing_matrix', f'{_UNMIX}:estimate_mixing_matrix', part_of='unmixing.unmix')
named('unmixing.matrix_warnings', f'{_UNMIX}:mixing_matrix_warnings', part_of='unmixing.unmix')
named('unmixing.negative_fraction', f'{_UNMIX}:negative_fraction', part_of='unmixing.unmix')

_FD = 'pycat.toolbox.fd_curve_tools'
named('fd_curve.segment_cycles', f'{_FD}:segment_fd_cycles')
named('fd_curve.smooth_force', f'{_FD}:smooth_force', part_of='fd_curve.segment_cycles')
named('fd_curve.summarise_cycles', f'{_FD}:summarise_cycles', part_of='fd_curve.segment_cycles')
named('fd_curve.rips', f'{_FD}:detect_all_rips', part_of='fd_curve.segment_cycles')

_TEMP = 'pycat.toolbox.temperature_tools'
named('temperature.turbidity', f'{_TEMP}:entropy_turbidity_curve')
named('temperature.static_pattern_correction', f'{_TEMP}:apply_static_pattern_correction',
      part_of='temperature.turbidity')
named('temperature.detect_transitions', f'{_TEMP}:detect_transitions', part_of='temperature.turbidity')
named('temperature.clear_frame', f'{_TEMP}:guess_clear_frame', part_of='temperature.turbidity')
named('temperature.reference_subtraction', f'{_TEMP}:reference_subtraction', part_of='temperature.turbidity')
named('temperature.folder_batch', f'{_TEMP}:run_temperature_batch', part_of='temperature.turbidity')
named('temperature.sync_temperatures', f'{_TEMP}:sync_temperatures', part_of='temperature.turbidity')

_FIB = 'pycat.toolbox.fibril_tools'
named('fibril.morphometry', f'{_FIB}:fibril_morphometry', entity='fibril')
named('fibril.graph', f'{_FIB}:fibril_graph_analysis', entity='fibril', part_of='fibril.morphometry')
named('fibril.beads', f'{_FIB}:detect_beads_on_fibrils', entity='fibril', part_of='fibril.morphometry')
named('fibril.register', f'{_FIB}:register_images', part_of='fibril.morphometry')

_CC = 'pycat.toolbox.contrast_cascade_tools'
named('contrast_cascade.bands', f'{_CC}:contrast_cascade_bands')
named('contrast_cascade.focus_vs_growth', f'{_CC}:focus_vs_growth_diagnostic', entity='condensate',
      part_of='contrast_cascade.bands')

_CV = 'pycat.toolbox.control_validation'
named('control_validation.validate', f'{_CV}:validate_against_controls')
named('control_validation.recommend', f'{_CV}:recommend_parameters', part_of='control_validation.validate')
