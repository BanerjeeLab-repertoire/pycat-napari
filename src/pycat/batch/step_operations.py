"""Batch step -> catalog operation composition -- split out of batch_step_registry (OperationService adoption)."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Batch step → operation COMPOSITION  (OperationSpec increment 3)
# ---------------------------------------------------------------------------
# A batch step and a catalog operation are DIFFERENT abstraction levels: a step
# is a workflow stage (`condensate_segmentation`), an operation is a
# layer-producing transform (`subcellular_segment`). Measured: `_STEP_MAP` and
# the op catalog have ZERO name overlap — that is correct design, not drift, so
# the two vocabularies are NOT merged. The honest relationship is *composition*:
# a step INVOKES one or more catalog/measure operations. That mapping cannot be
# inferred (a step's replay function calls whatever it calls), so it is DECLARED
# here, next to the steps, and drift-guarded against the live operation vocabulary
# (tests/navigator/test_batch_step_composition.py): rename an op and the build
# breaks instead of replay silently breaking.
#
# Each value is the tuple of operation ids the step invokes, verified against the
# step's replay function (its toolbox imports → the op that function declares).
# Only steps whose invoked ops are UNAMBIGUOUS are declared — staged population,
# the same discipline as OperationSpec increment 2. A step that also calls an
# UNtagged helper (a composite with no op id) declares only its registered ops;
# a step invoking no catalog/measure op (file I/O, save/clear, skip-stubs) is left
# out rather than declared empty. Later work raises the coverage floor.
_STEP_OPERATIONS: dict[str, tuple[str, ...]] = {
    'preprocessing':            ('preprocess',),
    'upscaling':                ('upscale',),
    'calibration_correction':   ('bg_subtract_clear', 'flatfield'),
    'auto_crop_roi':            ('multi_otsu', 'cropped'),
    'cellpose_segmentation':    ('cellpose', 'stardist'),
    'condensate_segmentation':  ('mask_stretch', 'subcellular_segment'),
    'ts_cells_time_average':    ('cell_timeseries.analyze', 'cellpose'),
    'cell_timeseries':          ('cell_timeseries.analyze', 'preprocess', 'mask_stretch', 'subcellular_segment'),
    # 'ivf_segmentation' deliberately NOT declared: a fixed op tuple misleads for 4 of its 5 recorded methods -- see test_batch_step_composition._MIN_STEPS_DECLARED for the rationale.
    'bf_condensate_segmentation': ('bf_segment',),
    'ivbf_segmentation':        ('bf_segment',),
    'ivf_size_distribution':    ('invitro.size_distribution', 'size_distribution'),   # MEASURE op + its kernel op
    'frap_analysis':             ('frap.fit_recovery',),
    'fusion_build_signal':       ('condensate_physics.fit_fusion_relaxation',),
    'fusion_fit':                ('condensate_physics.fit_fusion_relaxation',),
    'fd_segment':                ('fd_curve.segment_cycles',),
    'fd_rips':                   ('fd_curve.segment_cycles',),
    'temperature_sync':          ('temperature.turbidity',),
    'temperature_turbidity':     ('temperature.turbidity',),
    'vpt_segment_host':         ('host_segment', 'host_erode'),
    'vpt_infer_host':           ('bead_detect', 'host_erode'),
    'vpt_detect_beads':         ('bead_detect',),
    'vpt_link_trajectories':    ('vpt.microrheology',),     # vpt.link_beads, a component of it
    'vpt_microrheology':        ('vpt.microrheology', 'bead_detect', 'dynamic_spatial.link_trajectories',
                                 'condensate_physics.compute_msd', 'condensate_physics.fit_anomalous_diffusion',
                                 'viscosity'),   # INTERPRET terminal (N2b-1) and the chain it runs
    # Steps that run through OperationService: the ops each replay executes (checked against the replay's own
    # OperationService calls by tests/test_kernel_contract.py).
    'background_removal':       ('bg_removal_enhanced',),
    'bilateral_filter':         ('bilateral',),
    'peak_edge_enhancement':    ('peak_edge',),
    'morphological_gaussian':   ('morph_gaussian',),
    'local_thresholding':       ('local_threshold',),
    'pixel_colocalization':     ('colocalization', 'pixel_wise_corr.pearson_manders'),
    'client_enrichment':        ('client_enrichment',),
    'msd_analysis':             ('condensate_physics.compute_msd', 'condensate_physics.fit_anomalous_diffusion'),
    'dynamic_spatial':          ('dynamic_spatial.link_trajectories', 'dynamic_spatial.detect_merge_fission'),
    'ivf_droplet_segment':      ('ivf_droplet_segment',),
    'ivf_droplet_analysis':     ('partition_coefficient',),
    'ivf_field_summary':        ('partition_coefficient',),
    'tsivf_segment_stack':      ('ts_droplet_segment',),
    'tsivf_kinetics':           ('invitro_timeseries.kinetics', 'ts_track_relabel', 'cell_timeseries.analyze'),
    # Measurement steps (OperationService, after 1.7.005): a component op is declared as the op it is part of.
    'cell_analysis':            ('feature_analysis.cell_analysis',),
    'condensate_analysis':      ('feature_analysis.cell_analysis',),
    'sacf_analysis':            ('spatial_acf.per_cell', 'spatial_acf.whole_image'),
    'ts_cellpose_keyframe':     ('cellpose',),
    'tsivf_link_condensates':   ('invitro_timeseries.link_droplets',),
    'tsivf_object_trajectories': ('invitro_timeseries.object_trajectories',),
    'tsivf_field_trajectories': ('invitro_timeseries.field_trajectories',),
    'spatial_metrology':        ('spatial_metrology.ripley',),
    'ivf_spatial_metrology':    ('spatial_metrology.ripley',),
    'ivbf_spatial_metrology':   ('spatial_metrology.ripley',),
    'measure_binary_mask':      ('masks.measure_binary_mask',),
    'bf_preprocess':            ('bf_preprocess',),
    'ivbf_preprocess':          ('bf_preprocess',),
    'bf_condensate_analysis':   ('brightfield.condensate_metrics',),
    'bf_od_metrics':            ('brightfield.od_metrics',),
    'bf_per_cell_summary':      ('brightfield.od_metrics',),
    'ivbf_field_summary':       ('brightfield.invitro_field_summary',),
    'ivbf_size_distribution':   ('brightfield.invitro_size_distribution',),
}


def step_operations(step_name: str) -> tuple[str, ...]:
    """The catalog/measure operation ids a batch step invokes, or () if undeclared.

    The declared composition (see ``_STEP_OPERATIONS``). ``()`` means either the step invokes no
    registered operation (file I/O, save/clear, a skip-stub) or its mapping is not yet declared —
    the coverage guard, not this accessor, distinguishes the two.
    """
    return _STEP_OPERATIONS.get(step_name, ())


def all_step_operations() -> dict:
    """The whole declared step → operations composition (a copy)."""
    return {k: tuple(v) for k, v in _STEP_OPERATIONS.items()}
