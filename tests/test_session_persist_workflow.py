"""**A saved session carries the user's entered workflow parameters — and its manual calibration.**

`session_persist_settings` Part 2: user-entered workflow parameters (thresholds, radii, method choices)
were not written to the session manifest, so a reloaded session could not reproduce the analysis the user
set up. The fix reuses the ONE parameter record that already exists — the batch processor's recorded
config — and travels it with the manifest, restoring it into the processor on load (available for replay
and inspection in "Recorded Steps"). These pin the round-trip, the back-compat, and — as a regression
guard on Part 1, which already works — that a user-entered pixel size survives a save/reload.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core

from pycat.file_io import session_manifest as sm
from pycat.file_io.session_loader import _read_session_payload, _apply_session_payload


def _sample_config():
    return {
        'pycat_config_version': 1,
        'created': '2026-07-20T10:00:00',
        'steps': [
            {'step': 'segment_cells',
             'params': {'threshold': 0.42, 'min_size': 30, 'method': 'otsu'},
             'timestamp': '2026-07-20T10:00:01'},
            {'step': 'detect_puncta',
             'params': {'ball_radius': 5, 'sigma': 1.5},
             'timestamp': '2026-07-20T10:00:02'},
        ],
    }


# ── the Qt-free serialize / deserialize pair ──────────────────────────────────────────────────────
def test_workflow_to_extra_carries_the_recorded_steps():
    extra = sm.workflow_to_manifest_extra(_sample_config())
    assert set(extra) == {sm.WORKFLOW_KEY}
    assert extra[sm.WORKFLOW_KEY]['steps'][0]['params']['threshold'] == 0.42


def test_workflow_to_extra_is_empty_when_nothing_recorded():
    assert sm.workflow_to_manifest_extra(None) == {}
    assert sm.workflow_to_manifest_extra({}) == {}
    assert sm.workflow_to_manifest_extra({'steps': []}) == {}, \
        "a recording with no steps must not add an (empty) workflow block — keeps old-manifest parity"


def test_workflow_from_manifest_backward_compatible():
    assert sm.workflow_from_manifest({}) is None, "a pre-feature manifest has no workflow — must load"
    assert sm.workflow_from_manifest({'workflow': {'steps': []}}) is None
    assert sm.workflow_from_manifest(None) is None


def test_workflow_round_trips_through_a_real_manifest(tmp_path):
    """write_manifest(extra=…) → read_manifest → workflow_from_manifest reproduces the entered params."""
    sdir = tmp_path / "session_wf"
    cfg = _sample_config()
    sm.write_manifest(sdir, None, {}, layer_entries=[], dataframe_entries=[],
                      extra=sm.workflow_to_manifest_extra(cfg))
    restored = sm.workflow_from_manifest(sm.read_manifest(sdir))
    assert restored is not None
    assert [s['step'] for s in restored['steps']] == ['segment_cells', 'detect_puncta']
    assert restored['steps'][1]['params']['ball_radius'] == 5


# ── the loader payload carries the workflow, and applying it repopulates the processor ─────────────
def test_read_payload_surfaces_the_recorded_workflow(tmp_path):
    sdir = tmp_path / "session_read"
    sm.write_manifest(sdir, None, {}, layer_entries=[], dataframe_entries=[],
                      extra=sm.workflow_to_manifest_extra(_sample_config()))
    payload = _read_session_payload(sdir)
    assert payload['workflow'] is not None
    assert payload['workflow']['steps'][0]['step'] == 'segment_cells'


def test_read_payload_workflow_is_none_for_a_pre_feature_session(tmp_path):
    sdir = tmp_path / "session_old"
    sm.write_manifest(sdir, None, {}, layer_entries=[], dataframe_entries=[])   # no workflow block
    assert _read_session_payload(sdir)['workflow'] is None


class _FakeProcessor:
    def __init__(self):
        self.config = {'pycat_config_version': 1, 'created': '', 'steps': []}


class _FakeDataInstance:
    def __init__(self):
        self.data_repository = {}


class _FakeCM:
    def __init__(self, processor):
        self._pycat_batch_processor = processor
        self.file_io = None


def test_applying_the_payload_restores_the_workflow_into_the_processor():
    proc = _FakeProcessor()
    cm = _FakeCM(proc)
    di = _FakeDataInstance()
    payload = {'source_path': None, 'source_missing': None, 'acquisition': {},
               'dataframes': {}, 'layers': [], 'skipped': [], 'active_method': None,
               'workflow': _sample_config()}
    result = _apply_session_payload(payload, viewer=object(), data_instance=di, central_manager=cm)
    assert len(proc.config['steps']) == 2, "the reloaded session did not repopulate the recorded workflow"
    assert proc.config['steps'][0]['params']['method'] == 'otsu'
    assert result['workflow'] is not None


def test_applying_a_pre_feature_payload_leaves_the_processor_untouched():
    proc = _FakeProcessor()
    payload = {'source_path': None, 'source_missing': None, 'acquisition': {},
               'dataframes': {}, 'layers': [], 'skipped': [], 'active_method': None, 'workflow': None}
    _apply_session_payload(payload, viewer=object(), data_instance=_FakeDataInstance(),
                           central_manager=_FakeCM(proc))
    assert proc.config['steps'] == [], "no recorded workflow means the processor's config is left alone"


# ── Part 1 regression guard: a user-entered pixel size survives save → reload ──────────────────────
def test_user_entered_pixel_size_round_trips(tmp_path):
    """Part 1 already persists the manual pixel size; this pins it so a reload never silently drops the
    calibration the user typed (which would recompute every physical-unit measurement wrong)."""
    sdir = tmp_path / "session_px"
    dr = {'microns_per_pixel_sq': 0.067 ** 2,
          'pixel_size_from_metadata': False,      # user-entered, not from the file
          'pixel_size_confirmed': True}
    sm.write_manifest(sdir, None, dr, layer_entries=[], dataframe_entries=[])
    acq = _read_session_payload(sdir)['acquisition']
    assert abs(acq['microns_per_pixel_sq'] - 0.067 ** 2) < 1e-12
    assert acq['pixel_size_from_metadata'] is False, "a restored manual scale must stay manual, not metadata"
    assert acq['pixel_size_confirmed'] is True
