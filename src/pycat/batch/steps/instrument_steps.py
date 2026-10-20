"""The instrument pipelines' batch steps (VPT, FRAP, fusion, force-distance, temperature), as one table.

``batch_step_registry._STEP_MAP`` merges this in, so the registry (a ratcheted concentration point) does
not grow by a line per instrument step. 2026-10-19: VPT steps 2-4 and every FRAP, fusion, force-distance
and temperature analysis step were 'skipped in headless mode' until they were replayed here.
"""

from pycat.batch.steps.fd_curve_steps import replay_fd_load, replay_fd_rips, replay_fd_segment
from pycat.batch.steps.frap_steps import replay_frap_analysis, replay_frap_define_roi
from pycat.batch.steps.fusion_steps import replay_fusion_build_signal, replay_fusion_fit
from pycat.batch.steps.temperature_steps import replay_temperature_sync, replay_temperature_turbidity
from pycat.batch.steps.vpt_steps import (
    replay_vpt_detect_beads,
    replay_vpt_infer_host,
    replay_vpt_link_trajectories,
    replay_vpt_microrheology,
    replay_vpt_segment_host,
)

INSTRUMENT_STEPS = {
    'vpt_segment_host':         replay_vpt_segment_host,
    'vpt_infer_host':           replay_vpt_infer_host,
    'vpt_detect_beads':         replay_vpt_detect_beads,
    'vpt_link_trajectories':    replay_vpt_link_trajectories,
    'vpt_microrheology':        replay_vpt_microrheology,
    'frap_define_roi':          replay_frap_define_roi,
    'frap_analysis':            replay_frap_analysis,
    'fusion_build_signal':      replay_fusion_build_signal,
    'fusion_fit':               replay_fusion_fit,
    'fd_load':                  replay_fd_load,
    'fd_segment':               replay_fd_segment,
    'fd_rips':                  replay_fd_rips,
    'temperature_sync':         replay_temperature_sync,
    'temperature_turbidity':    replay_temperature_turbidity,
}
