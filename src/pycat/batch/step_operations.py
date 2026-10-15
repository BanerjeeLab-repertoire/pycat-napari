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
    'auto_crop_roi':            ('multi_otsu',),
    'cellpose_segmentation':    ('cellpose', 'stardist'),
    'condensate_segmentation':  ('mask_stretch', 'subcellular_segment'),
    'ts_cells_time_average':    ('cellpose',),
    'cell_timeseries':          ('preprocess', 'mask_stretch', 'subcellular_segment'),
    # 'ivf_segmentation' deliberately NOT declared: a fixed op tuple misleads for 4 of its 5 recorded methods -- see test_batch_step_composition._MIN_STEPS_DECLARED for the rationale.
    'bf_condensate_segmentation': ('bf_segment',),
    'ivbf_segmentation':        ('bf_segment',),
    'ivf_size_distribution':    ('invitro.size_distribution',),   # a MEASURE op
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
    'pixel_colocalization':     ('colocalization',),
    'client_enrichment':        ('client_enrichment',),
    'msd_analysis':             ('condensate_physics.compute_msd', 'condensate_physics.fit_anomalous_diffusion'),
    'dynamic_spatial':          ('dynamic_spatial.link_trajectories', 'dynamic_spatial.detect_merge_fission'),
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
