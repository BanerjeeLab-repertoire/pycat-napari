"""The ordered steps of each fixed analysis pipeline -- the data behind the workflow checklist.

Plain data, no Qt: the batch registry, the Navigator and the headless tests read these without a GUI. The
checklist bar (``workflow_checklist``) imports them from here.
"""

# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------

CONDENSATE_PIPELINE = [
    ('open_image',              '1.  Open image(s)'),
    ('measure_line',            '2.  Measure lines'),
    ('upscaling',               '3.  Upscale  [optional]'),
    ('preprocessing',           '4.  Pre-process image'),
    ('background_removal',      '5.  Background removal'),
    ('cellpose_segmentation',   '6.  Cell segmentation'),
    ('cell_analysis',           '7.  Cell Analyzer'),
    ('condensate_segmentation', '8.  Condensate segmentation'),
    ('condensate_analysis',     '9.  Condensate analysis'),
    ('spatial_metrology',       '10. Spatial Metrology  [opt]'),
    ('morphological_complexity','11. Morphological Complexity  [opt]'),
    ('dynamic_spatial',         '12. Dynamic Spatial Phenotyping  [opt]'),
    ('organizational_metrics',  '13. Organizational Metrics  [opt]'),
    # save_and_clear is a generic, always-available action (Open/Save
    # File(s) menu) rather than a step specific to this pipeline, so it does
    # not occupy a number in the checklist -- confirmed by the user for
    # cellular_bf/zstack; applied consistently across all pipelines.
]

TIMESERIES_PIPELINE = [
    # 1.7.004: cells once on the time average, then every frame segmented as the 2D workflow does. The older
    # keyframe / zarr steps are under the panel's collapsed "Performance & advanced steps" and stay listed [adv].
    ('open_stack',              '1.  Open IMS / image stack'),
    ('measure_line',            '2.  Measure lines'),
    ('ts_cells_time_average',   '3.  Segment cells on the time average'),
    ('cell_timeseries',         '4.  Cellular time-series analysis'),
    ('set_frame_range',         '5.  Reference frame & range  [adv]'),
    ('ts_upscale_stack',        '6.  Upscale stack  [adv]'),
    ('lazy_preprocess_stack',   '7.  Preprocess stack (zarr-backed)  [adv]'),
    ('auto_crop_roi',           '7b. Auto-crop ROI  [batch, adv]'),
    ('ts_cellpose_keyframe',    '8.  Keyframe Cellpose segmentation  [adv]'),
    ('cell_analysis',           '9.  Cell Analyzer  [adv]'),
    ('timeseries_condensate_analysis', '10. Legacy time-series condensate analysis  [adv]'),
    ('export_timeseries_video', '11. Export video  [opt]'),
]

COLOC_PIPELINE = [
    ('open_image',              '1.  Open multi-channel image(s)'),
    ('measure_line',            '2.  Measure lines'),
    ('upscaling',               '3.  Upscale  [opt]'),
    ('preprocessing',           '4.  Pre-process'),
    ('background_removal',      '5.  Background removal'),
    ('cellpose_segmentation',   '6.  Cell segmentation'),
    ('cell_analysis',           '7.  Cell Analyzer'),
    ('condensate_segmentation', '8.  Condensate segmentation'),
    ('condensate_analysis',     '9.  Condensate analysis'),
    ('two_channel_condensate_coloc', '10. Two-Channel Colocalization'),
]

CELLULAR_BF_PIPELINE = [
    ('open_image',                 '1.  Open brightfield image'),
    ('bf_preprocess',              '2.  Preprocess BF'),
    ('bf_cell_segmentation',       '3.  Cell segmentation  [opt]'),
    ('bf_condensate_segmentation', '4.  Segment condensate spots'),
    ('bf_od_metrics',              '5.  OD metrics & analysis'),
    ('bf_per_cell_summary',        '6.  Per-cell summary'),
    ('bf_spatial_metrology',       '7.  Spatial Metrology  [opt]'),
    ('bf_dynamics',                '8.  Dynamics  [opt]'),
    ('bf_texture',                 '9.  Texture  [opt]'),
    ('bf_frame_qc',                '10. Frame Quality  [opt]'),
]

INVITRO_FLUOR_PIPELINE = [
    ('open_image',              '1.  Open fluorescence image'),
    ('ivf_preprocess',          '2.  Preprocess'),
    ('ivf_segmentation',        '3.  Segment droplets'),
    ('ivf_field_summary',       '4.  Field summary & partition coefficient'),
    ('ivf_size_distribution',   '5.  Size distribution fit'),
    ('ivf_spatial_metrology',   '6.  Spatial Metrology  [opt]'),
    ('ivf_phase_diagram',       '7.  Phase diagram / C_sat  [opt]'),
    # ivf_dynamics and ivf_frame_qc are time-series-only bonus panels in this
    # otherwise single-image pipeline (see invitro_fluor_ui.py) -- they don't
    # occupy a step number and are intentionally not listed here.
]

TIMESERIES_INVITRO_FLUOR_PIPELINE = [
    # The panel's optional "Preprocess" box is a layer chooser with no operation, so it is not a step (it was
    # listed with an empty key -- unrecordable, unreplayable -- until 1.7.005).
    ('open_stack',                '1.  Open time-series image'),
    ('tsivf_segment_stack',       '2.  Segment droplets (every frame)'),
    ('tsivf_link_condensates',    '3.  Link condensates (fusion-aware)'),
    ('tsivf_object_trajectories', '4.  Per-condensate trajectories'),
    ('tsivf_field_trajectories',  '5.  Field trajectories (Φ, partition, C_sat)'),
    ('tsivf_kinetics',            '6.  Droplet kinetics (linked results)'),
]

INVITRO_BF_PIPELINE = [
    ('open_image',               '1.  Open brightfield image'),
    ('ivbf_preprocess',          '2.  Preprocess'),
    ('ivbf_segmentation',        '3.  Segment droplets'),
    ('ivbf_field_summary',       '4.  OD & field summary'),
    ('ivbf_size_distribution',   '5.  Size distribution & contact angle'),
    ('ivbf_spatial_metrology',   '6.  Spatial Metrology  [opt]'),
    ('ivbf_dynamics',            '7.  Dynamics & coarsening  [opt]'),
    ('ivbf_frame_qc',            '8.  Focus Quality  [opt]'),
]

FIBRIL_PIPELINE = [
    # Every step has a key (1.7.005): the seven image-chain steps were unkeyed, so never recorded or replayed.
    ('open_image',              '1.  Open image'),
    ('measure_line',            '2.  Measure lines'),
    ('upscaling',               '3.  Upscale  [opt]'),
    ('bilateral_filter',        '4.  Bilateral filter  [opt]'),
    ('preprocessing',           '5.  Pre-process image'),
    ('background_removal',      '6.  Background removal'),
    ('peak_edge_enhancement',   '7.  Peak & edge enhancement'),
    ('morphological_gaussian',  '8.  Morphological Gaussian  [opt]'),
    ('cellpose_segmentation',   '9.  Segmentation (Random Forest / Cellpose)  [opt]'),
    ('local_thresholding',      '10. Local thresholding  [opt]'),
    ('label_binary_mask',       '11. Label connected components'),
    ('measure_binary_mask',     '12. Measure binary mask'),
    ('morphological_complexity','13. Morphological Complexity  [opt]'),
    ('organizational_metrics',  '14. Organizational Metrics  [opt]'),
]

ZSTACK_PIPELINE = [
    ('open_image',                     '1.  Open Z-stack'),
    ('zstack_bg_removal',              '2.  3D Background Removal'),
    ('zstack_cell_segmentation',       '3.  3D Cell Segmentation  [opt]'),
    ('zstack_condensate_segmentation', '4.  3D Condensate Segmentation'),
    ('zstack_3d_metrics',              '5.  3D Metrics'),
]

VPT_PIPELINE = [
    ('open_image',              '1.  Open multichannel image'),
    ('vpt_segment_host',        '2.  Segment host + erode interface'),
    ('vpt_detect_beads',        '3.  Detect beads'),
    ('vpt_link_trajectories',   '4.  Link trajectories'),
    ('vpt_microrheology',       '5.  MSD & viscosity'),
]

FRAP_PIPELINE = [
    ('open_image',       '1.  Open recovery time-series'),
    ('frap_define_roi',  '2.  Define bleach & reference ROIs'),
    ('frap_analysis',    '3.  Analyze recovery (fit τ½ & mobile fraction)'),
]

FUSION_PIPELINE = [
    ('open_image',            '1.  Load C-Trap .h5 or image stack'),
    ('fusion_build_signal',   '2.  Build fusion signal (force / aspect ratio)'),
    ('fusion_fit',            '3.  Fit relaxation → τ'),
]

TEMPERATURE_PIPELINE = [
    ('open_image',              '1.  Open OME-TIFF + temperature CSV'),
    ('temperature_sync',        '2.  Sync temperatures to frames'),
    ('temperature_turbidity',   '3.  Entropy turbidity → T_phase / T_clear'),
    ('temperature_export_video','4.  Scale bar & annotated export'),
    ('temperature_batch',       '5.  Batch (folder of TIFFs)  [opt]'),
]

FD_CURVE_PIPELINE = [
    ('fd_load',     '1.  Load Lumicks .h5 (Force / Distance)'),
    ('fd_segment',  '2.  Unfold into stretch/relax cycles'),
    ('fd_plot',     '3.  Plot FD loops (+ WLC reference)'),
    ('fd_rips',     '4.  Detect rips / unzips (G4 unfolding)'),
]

PIPELINE_DEFS = {
    'condensate':    CONDENSATE_PIPELINE,
    'timeseries':    TIMESERIES_PIPELINE,
    'coloc':         COLOC_PIPELINE,
    'cellular_bf':   CELLULAR_BF_PIPELINE,
    'invitro_fluor': INVITRO_FLUOR_PIPELINE,
    'timeseries_invitro_fluor': TIMESERIES_INVITRO_FLUOR_PIPELINE,
    'invitro_bf':    INVITRO_BF_PIPELINE,
    'fibril':        FIBRIL_PIPELINE,
    'zstack':        ZSTACK_PIPELINE,
    'vpt':           VPT_PIPELINE,
    'frap':          FRAP_PIPELINE,
    'fusion':        FUSION_PIPELINE,
    'temperature':   TEMPERATURE_PIPELINE,
    'fd_curve':      FD_CURVE_PIPELINE,
}

PIPELINE_DISPLAY_NAMES = {
    'condensate':    'Condensate',
    'timeseries':    'Time-Series',
    'coloc':         'Colocalization',
    'cellular_bf':   'Cellular BF',
    'invitro_fluor': 'In Vitro (Fluor)',
    'timeseries_invitro_fluor': 'Time-Series In Vitro (Fluor)',
    'invitro_bf':    'In Vitro (BF)',
    'fibril':        'Fibril',
    'zstack':        'Z-Stack (3D)',
    'vpt':           'Particle Tracking',
    'frap':          'FRAP',
    'fusion':        'Droplet Fusion',
    'temperature':   'Temperature-Dependent',
    'fd_curve':      'Force-Distance Curve',
}
