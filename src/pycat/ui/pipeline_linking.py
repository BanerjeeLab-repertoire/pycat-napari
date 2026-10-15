"""**Which pipelines' results are linked, and why the rest are not** (Completion Spec Phase F acceptance).

"Linking on all methods" means all methods whose results are OBJECTS. Every pipeline in
``workflow_checklist.PIPELINE_DEFS`` is declared here as either

* ``Linked`` -- its object tables carry ``_pycat_entity_id`` (the declared ``operations`` stamp them) and the
  ``display`` modules show them on the one ``SelectionService`` (a ``BrushableWorkspace`` or
  ``show_dataframes_dialog(..., service=...)``), or
* ``NotApplicable`` -- its rows are time points or fit parameters, not objects, with the reason.

``tests/test_pipeline_linking_contract.py`` holds every pipeline to its declaration, so a new pipeline cannot
arrive unlinked and undeclared, and a declared link cannot silently rot (a renamed operation id or a display
that stops passing the service fails the test).
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Linked:
    operations: tuple          # operation ids whose stamped tables the pipeline shows
    display: tuple             # src-relative modules that put those tables on the selection bus


@dataclasses.dataclass(frozen=True)
class NotApplicable:
    reason: str


PIPELINE_LINKING = {
    'condensate': Linked(('cell_analysis', 'puncta_analysis'), ('toolbox/feature_analysis_tools.py',)),
    'timeseries': Linked(('cell_timeseries', 'timeseries_condensate_analysis'),
                         ('toolbox/cell_ts_ui.py', 'toolbox/timeseries/ui.py')),
    # Coloc's object tables are the condensate pipeline's (steps 7/9 are shared); its own two-channel step has a
    # per-cell table. Its pixel/object-based summary tables are per METHOD / per METRIC aggregates, not objects.
    'coloc': Linked(('cell_analysis', 'puncta_analysis', 'two_channel_condensate_coloc'),
                    ('toolbox/feature_analysis_tools.py', 'toolbox/two_channel_coloc_tools.py')),
    'cellular_bf': Linked(('bf_condensate_metrics', 'bf_per_cell_summary'), ('toolbox/brightfield_ui.py',)),
    'invitro_fluor': Linked(('condensate_analysis',), ('toolbox/invitro_fluor_ui.py',)),
    'timeseries_invitro_fluor': Linked(('tsivf_kinetics',), ('toolbox/tsivf_kinetics_ui.py',)),
    'invitro_bf': Linked(('ivbf_droplet_metrics',), ('toolbox/invitro_bf_ui.py',)),
    'fibril': Linked(('fibril_morphology', 'measure_region_props'),
                     ('toolbox/morphological_complexity_tools.py', 'toolbox/advanced_analysis_ui.py',
                      'toolbox/masks/measurement.py')),
    'zstack': Linked(('zstack_condensate_metrics',), ('toolbox/zstack_segmentation_ui.py',)),
    'vpt': Linked(('vpt_tracks',), ('toolbox/vpt/table_adapter.py',)),
    'frap': NotApplicable('rows are recovery time points and fit parameters of one bleached region, not objects'),
    'fusion': NotApplicable('rows are one relaxation fit and its time points, not objects'),
    'temperature': NotApplicable('rows are per-frame temperature / turbidity values, not objects'),
    'fd_curve': NotApplicable('rows are force-distance samples and rip events along one tether, not objects'),
}
