"""The recorded steps batch cannot replay, declared (Completion Spec E1/E2).

Each entry RAISES ``StepNotImplemented`` with its reason, so the batch runner records the step as skipped, runs
the remaining steps, and lists it in the run report. These used to be ``lambda ...: print(...)`` stubs inside the
registry: a run over a pipeline with stubbed steps produced a results folder that read as complete. Closing one
means writing its replay in ``pycat.batch.steps`` and moving its key into ``batch_step_registry._STEP_MAP``.
"""
from __future__ import annotations


def _not_replayed(step: str, reason: str):
    """A registry entry that raises ``StepNotImplemented(step, reason)`` instead of running."""
    def _stub(state, image_path, params, output_dir):
        from pycat.utils.errors import StepNotImplemented
        raise StepNotImplemented(step, reason)
    _stub.__name__ = f'not_replayed_{step}'
    _stub.not_replayed = True
    _stub.reason = reason
    return _stub


def _advice_only(state, image_path, params, output_dir):
    """``upscale_advice`` only shows the user a recommendation; it produces no data, so there is nothing to replay."""


NOT_REPLAYED = {
    'lazy_preprocess_stack': _not_replayed('lazy_preprocess_stack',
                         'TS preprocessing skipped in headless mode (zarr cache required).'),
    'organizational_metrics': _not_replayed('organizational_metrics',
                         'organizational_metrics skipped.'),
    'export_timeseries_video': _not_replayed('export_timeseries_video',
                         'Video export skipped in headless mode.'),
    'timeseries_condensate_analysis': _not_replayed('timeseries_condensate_analysis',
                         'TS condensate analysis skipped in headless mode.'),
    'two_channel_condensate_coloc': _not_replayed('two_channel_condensate_coloc',
                         'Two-channel colocalization skipped in headless mode.'),
    'ivf_dynamics': _not_replayed('ivf_dynamics',
                         'IVF dynamics skipped (time-series; not a per-image batch step).'),
    'ivf_phase_diagram': _not_replayed('ivf_phase_diagram',
                         'IVF phase diagram skipped (dilution series; manual multi-condition input).'),
    'ivf_frame_qc': _not_replayed('ivf_frame_qc',
                         'IVF frame QC skipped (time-series; not a per-image batch step).'),
    'zstack_bg_removal': _not_replayed('zstack_bg_removal',
                         'Z-stack background removal skipped in headless mode (batch file loading only extracts single 2D planes — 3D volume batch processing is not yet supported; run this step interactively in the Z-Stack Condensate Analysis dock).'),
    'zstack_cell_segmentation': _not_replayed('zstack_cell_segmentation',
                         'Z-stack cell segmentation skipped in headless mode (requires a 3D volume — not available via batch file loading).'),
    'zstack_condensate_segmentation': _not_replayed('zstack_condensate_segmentation',
                         'Z-stack condensate segmentation skipped in headless mode (requires a 3D volume — not available via batch file loading).'),
    'measure_region_props': _not_replayed('measure_region_props',
                         'the measured properties are chosen in a dialog and not recorded'),
    'spatial_randomness': _not_replayed('spatial_randomness',
                         'spatial_randomness skipped in headless mode (interactive image/ROI selection step).'),
    'temperature_export_video': _not_replayed('temperature_export_video',
                         'Temperature annotated export skipped in headless mode.'),
    'fd_plot': _not_replayed('fd_plot',
                         'FD plot skipped in headless mode.'),
    'fd_export': _not_replayed('fd_export',
                         'FD export skipped in headless mode.'),
    'molecular_counting': _not_replayed('molecular_counting',
                         'Molecular counting skipped in headless mode (interactive stack/mask selection).'),
    'gaussian_localization': _not_replayed('gaussian_localization',
                         'Gaussian localization skipped in headless mode (interactive image/points selection).'),
    'intensity_profile': _not_replayed('intensity_profile',
                         'Intensity profile skipped in headless mode (interactive shapes/points selection).'),
    'morphological_complexity': _not_replayed('morphological_complexity',
                         'Morphological complexity skipped in headless mode (interactive mask selection).'),
    'bf_spatial_metrology': _not_replayed('bf_spatial_metrology',
                         'per-cell BF spatial metrology has no batch replay yet'),
    'bf_dynamics': _not_replayed('bf_dynamics',
                         'time-series BF dynamics: not a per-image batch step'),
    'bf_texture': _not_replayed('bf_texture',
                         'BF texture panel has no batch replay yet'),
    'bf_frame_qc': _not_replayed('bf_frame_qc',
                         'time-series BF frame quality: not a per-image batch step'),
    'ivbf_dynamics': _not_replayed('ivbf_dynamics',
                         'time-series IVBF dynamics: not a per-image batch step'),
    'ivbf_frame_qc': _not_replayed('ivbf_frame_qc',
                         'time-series IVBF focus quality: not a per-image batch step'),
    'zstack_3d_metrics': _not_replayed('zstack_3d_metrics',
                         'needs a 3D volume -- batch loads 2D planes'),
    'ts_upscale_stack': _not_replayed('ts_upscale_stack',
                         'time-series stack upscaling has no batch replay yet'),
    'ts_transfection_filter': _not_replayed('ts_transfection_filter',
                         'legacy keyframe transfection filter has no batch replay'),
    'temperature_batch': _not_replayed('temperature_batch',
                         'the temperature panel runs its own folder batch'),
    'acf_analysis': _not_replayed('acf_analysis',
                         'interactive general-analysis tool (layer/ROI chosen at run time)'),
    'coloc_time_trace': _not_replayed('coloc_time_trace',
                         'interactive time-series colocalization trace'),
    'condensate_coloc_time_trace': _not_replayed('condensate_coloc_time_trace',
                         'interactive time-series condensate colocalization trace'),
    'chromatin_topology': _not_replayed('chromatin_topology',
                         'interactive diagnostics tool'),
    'chromatin_void_detection': _not_replayed('chromatin_void_detection',
                         'interactive diagnostics tool'),
    'bleach_correction': _not_replayed('bleach_correction',
                         'general stack tool has no batch replay yet'),
    'detrend_stack': _not_replayed('detrend_stack',
                         'general stack tool has no batch replay yet'),
    'frame_quality_qc': _not_replayed('frame_quality_qc',
                         'general stack QC tool has no batch replay yet'),
    'image_registration': _not_replayed('image_registration',
                         'general stack registration has no batch replay yet'),
    'motion_scale_estimate': _not_replayed('motion_scale_estimate',
                         'advisory estimate on an interactively chosen stack'),
    'partial_volume_measure': _not_replayed('partial_volume_measure',
                         'interactive partial-volume measurement'),
}
