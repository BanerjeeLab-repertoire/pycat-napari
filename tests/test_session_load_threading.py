"""**The session read moves off the Qt thread; layer creation stays on it.**

Loading a session froze the UI ("Python is not responding") because ``load_session`` did its slow
work — ``tifffile.imread`` per derived layer, ``pd.read_csv`` per table — on the Qt main thread. The
cure is to run that read on a worker (``pycat.utils.qt_worker.run_with_progress``) and create the
napari layers back on the caller's thread, because ``viewer.add_*`` off the main thread is a crash,
not a freeze.

That split only holds if the read half genuinely touches no viewer. These pin it:

* ``_read_session_payload`` takes **no viewer** — structurally it cannot add a layer — and returns a
  payload of decoded arrays/dataframes;
* ``_apply_session_payload`` is the only half that calls ``viewer.add_*``;
* ``load_session`` round-trips through both, so the observable result is unchanged.
"""

# Standard library imports
import inspect
import json

# Third party imports
import numpy as np
import pandas as pd
import pytest
import tifffile

from pycat.file_io import session_loader as sl

pytestmark = pytest.mark.core


class _Viewer:
    """Records the layer-creating calls — the ones that MUST stay on the caller's thread."""

    def __init__(self):
        self.images = []
        self.labels = []

    def add_image(self, arr, **kw):
        self.images.append(kw.get('name'))

    def add_labels(self, arr, **kw):
        self.labels.append(kw.get('name'))


class _DI:
    def __init__(self):
        self.data_repository = {}


def _write_session(tmp_path):
    """A minimal session folder: a manifest naming the method, one derived image, one table."""
    tifffile.imwrite(str(tmp_path / 'img_preprocessed.tiff'),
                     np.arange(16, dtype=np.float32).reshape(4, 4))
    pd.DataFrame({'track_id': [1, 2], 'y_um': [1.0, 2.0]}).to_csv(
        tmp_path / 'img_vpt_tracks.csv', index=False)
    (tmp_path / 'pycat_session.json').write_text(json.dumps({
        'manifest_version': 3, 'active_method': 'VideoParticleTrackingUI',
        'source_image': {'path': None}, 'layers': [], 'dataframes': [],
    }), encoding='utf-8')


# ── the read half is viewer-free ──────────────────────────────────────────────

def test_the_reader_takes_NO_viewer():
    """Structural guarantee: a function with no viewer parameter cannot call ``viewer.add_*`` — so it
    is safe to run on the worker. If someone adds a viewer arg, this fails and the review asks why."""
    params = inspect.signature(sl._read_session_payload).parameters
    assert 'viewer' not in params, (
        '_read_session_payload took a viewer — the worker could now touch napari off-thread (a crash)')


def test_the_reader_DECODES_into_a_payload_without_a_viewer(tmp_path):
    """It reads the image and the table into memory and reports the method — all with no viewer in
    sight."""
    _write_session(tmp_path)
    payload = sl._read_session_payload(tmp_path)

    assert payload['active_method'] == 'VideoParticleTrackingUI'
    assert 'vpt_tracks' in payload['dataframes']
    assert [l['kind'] for l in payload['layers']] == ['image']
    assert isinstance(payload['layers'][0]['array'], np.ndarray)


# ── the apply half is where the viewer is touched ─────────────────────────────

def test_the_applier_creates_the_layers_and_populates_the_repo(tmp_path):
    _write_session(tmp_path)
    payload = sl._read_session_payload(tmp_path)

    v, di = _Viewer(), _DI()
    result = sl._apply_session_payload(payload, v, di)

    assert v.images == [payload['layers'][0]['name']]
    assert di.data_repository.get('vpt_tracks') is not None
    assert result['active_method'] == 'VideoParticleTrackingUI'


# ── the whole thing still round-trips (synchronous path) ──────────────────────

def test_load_session_round_trips_through_both_halves(tmp_path):
    """``use_worker=False`` (the default, and what tests use) runs read then apply inline — the
    observable result is exactly what the one-pass loader produced."""
    _write_session(tmp_path)
    v, di = _Viewer(), _DI()

    result = sl.load_session(tmp_path, v, di)

    assert len(v.images) == 1
    assert result['loaded_dfs'].get('vpt_tracks') is not None
    assert di.data_repository.get('vpt_tracks') is not None
    assert result['active_method'] == 'VideoParticleTrackingUI'


def test_the_manifest_dataframe_READER_does_not_store(tmp_path):
    """The read-only helper the worker uses: it returns the dataframes but writes nothing into a
    repository, so it is safe off-thread. Storing is the applier's job, on the main thread."""
    from pycat.file_io import session_manifest as sm
    pd.DataFrame({'a': [1]}).to_csv(tmp_path / 'x_vpt_tracks.csv', index=False)
    manifest = {'dataframes': [{'key': 'vpt_tracks', 'file': 'x_vpt_tracks.csv'}]}

    out = sm.read_dataframes_from_manifest(manifest, tmp_path)
    assert 'vpt_tracks' in out and isinstance(out['vpt_tracks'], pd.DataFrame)
