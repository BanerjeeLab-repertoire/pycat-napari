"""**"I selected two images and it loaded all eight."**

Two bugs in the session loader, both verified in the tree before being fixed:

1. **The selection was computed, used to size the progress bar, and thrown away.** `_on_load` built
   `selected_stems` from the multi-select list and then called `load_session(folder, ...)` with **no
   filter**, so it re-scanned the whole folder. The progress bar carried the tell: its maximum was
   the *selected* count while the load reported over *all* files. And `stem_filter` was a single
   SUBSTRING — it could not have expressed "these three of eight" even if it had been passed.
2. **The sessions were in subfolders, and nothing looked there.** Saving always creates its own
   `session_<stem>_<timestamp>/`, but the load dialog scans one level for loose *files*. So pointing
   at the parent directory the sessions were saved into — the obvious thing to do — reported *"No
   recognised PyCAT outputs found"*, with every session sitting in plain view underneath it.
"""

# Standard library imports
import json
from pathlib import Path

# Third party imports
import pytest


pytestmark = pytest.mark.base


def _make_session(parent, stem, n_layers=1, n_tables=1):
    from pycat.file_io.session_manifest import default_session_dir, write_manifest
    directory = default_session_dir(parent, stem)
    directory.mkdir(parents=True, exist_ok=True)
    write_manifest(
        directory, f'C:/data/{stem}.tif', {},
        [{'name': f'{stem} mask', 'layer_type': 'labels', 'file': f'{stem}_mask.tif',
          'is_3d': False}] * n_layers,
        [{'key': 'cell_df', 'file': f'{stem}_cell_df.csv'}] * n_tables)
    return directory


def test_pointing_at_the_PARENT_finds_the_sessions_underneath(tmp_path):
    """**The obvious thing to do reported "nothing here".** Saving puts each session in its own
    `session_<stem>_<timestamp>/`; the dialog's scan is one level and files-only."""
    from pycat.file_io.session_manifest import discover_sessions

    _make_session(tmp_path, 'cells_A')
    _make_session(tmp_path, 'cells_B')

    sessions = discover_sessions(tmp_path)

    assert len(sessions) == 2, (
        f"pointing at the parent found {len(sessions)} sessions — this is where the save path puts "
        f"them, and it is what the user picks")
    assert {s['name'] for s in sessions} == {'cells_A', 'cells_B'}
    assert all(s['n_layers'] == 1 and s['n_dataframes'] == 1 for s in sessions)


def test_pointing_AT_a_session_folder_still_works(tmp_path):
    """The other obvious thing to do."""
    from pycat.file_io.session_manifest import discover_sessions

    directory = _make_session(tmp_path, 'cells_A')
    sessions = discover_sessions(directory)

    assert [s['name'] for s in sessions] == ['cells_A']


def test_read_manifest_is_UNCHANGED_for_existing_folders(tmp_path):
    """Back-compat: a folder saved by an older version must still load."""
    from pycat.file_io.session_manifest import read_manifest

    directory = _make_session(tmp_path, 'legacy')
    assert read_manifest(directory) is not None
    assert read_manifest(tmp_path) is None       # the parent holds no manifest of its own


def test_a_folder_with_NO_sessions_finds_none(tmp_path):
    from pycat.file_io.session_manifest import discover_sessions

    (tmp_path / 'not_a_session').mkdir()
    assert discover_sessions(tmp_path) == []


def test_discovery_does_not_CRAWL_the_whole_drive(tmp_path):
    """One level, because that is where the save path puts them. A deep crawl of someone's data
    directory is its own bug."""
    from pycat.file_io.session_manifest import discover_sessions

    deep = tmp_path / 'a' / 'b'
    deep.mkdir(parents=True)
    _make_session(deep, 'buried')

    assert discover_sessions(tmp_path) == []


def test_the_newest_session_is_offered_FIRST(tmp_path):
    """The one you just saved is the one you most likely want back."""
    from pycat.file_io.session_manifest import MANIFEST_NAME, discover_sessions

    older = _make_session(tmp_path, 'older')
    newer = _make_session(tmp_path, 'newer')
    # The dirs are timestamped to the second, so pin the manifests' own `created` explicitly.
    for directory, created in ((older, '2026-01-01T00:00:00'), (newer, '2026-07-16T12:00:00')):
        path = directory / MANIFEST_NAME
        data = json.loads(path.read_text(encoding='utf-8'))
        data['created'] = created
        path.write_text(json.dumps(data), encoding='utf-8')

    assert [s['name'] for s in discover_sessions(tmp_path)] == ['newer', 'older']


# ── The selection ─────────────────────────────────────────────────────────────────────────

class _FakeViewer:
    def __init__(self):
        self.added = []

    def add_image(self, data, **kw):
        self.added.append(kw.get('name'))

    def add_labels(self, data, **kw):
        self.added.append(kw.get('name'))


class _FakeData:
    def __init__(self):
        self.data_repository = {}

    def set_data(self, k, v):
        self.data_repository[k] = v

    def get_data(self, k, default=None):
        return self.data_repository.get(k, default)


def _loose_outputs(folder, stems):
    """A folder of loose outputs — the pre-manifest model the dialog was built for."""
    import numpy as np
    import tifffile
    for stem in stems:
        tifffile.imwrite(folder / f'{stem}_preprocessed.tiff',
                         np.zeros((8, 8), dtype=np.uint16))
    return folder


def test_loading_HONOURS_the_selection(tmp_path):
    """**The bug, stated plainly.** Select two of eight; load two of eight."""
    pytest.importorskip("tifffile")
    from pycat.file_io.session_loader import load_session, scan_output_folder

    _loose_outputs(tmp_path, ['img_A', 'img_B', 'img_C'])
    assert set(scan_output_folder(tmp_path)) == {'img_A', 'img_B', 'img_C'}

    viewer = _FakeViewer()
    result = load_session(tmp_path, viewer, _FakeData(), stems={'img_A'})

    loaded = ' '.join(result['loaded_layers'])
    assert 'img_B' not in loaded and 'img_C' not in loaded, (
        f"selecting one image loaded others too: {result['loaded_layers']}")
    assert 'img_A' in loaded, "the selected image was not loaded"


def test_NO_selection_still_loads_everything(tmp_path):
    """Back-compat: `stems=None` means "no filter", exactly as before."""
    pytest.importorskip("tifffile")
    from pycat.file_io.session_loader import load_session

    _loose_outputs(tmp_path, ['img_A', 'img_B'])
    result = load_session(tmp_path, _FakeViewer(), _FakeData())

    loaded = ' '.join(result['loaded_layers'])
    assert 'img_A' in loaded and 'img_B' in loaded


def test_an_EMPTY_selection_loads_NOTHING_rather_than_everything(tmp_path):
    """The dangerous edge: an empty set must not read as "no filter". `if stems:` would have
    collapsed `set()` to falsey and loaded the whole folder — the original bug, restored."""
    pytest.importorskip("tifffile")
    from pycat.file_io.session_loader import load_session

    _loose_outputs(tmp_path, ['img_A', 'img_B'])
    result = load_session(tmp_path, _FakeViewer(), _FakeData(), stems=set())

    assert result['loaded_layers'] == [], (
        f"an empty selection loaded {result['loaded_layers']} — `stems=set()` was treated as "
        f"'no filter'")


def test_a_stem_selection_is_EXACT_not_a_substring(tmp_path):
    """`stem_filter` was `stem_filter.lower() in s.lower()` — so filtering for 'img_A' would also
    drag in 'img_A_control'. A set of stems means the stems in the set."""
    pytest.importorskip("tifffile")
    from pycat.file_io.session_loader import load_session

    _loose_outputs(tmp_path, ['img_A', 'img_A_control'])
    result = load_session(tmp_path, _FakeViewer(), _FakeData(), stems={'img_A'})

    loaded = ' '.join(result['loaded_layers'])
    assert 'img_A_control' not in loaded, (
        f"a substring match dragged in a stem the user did not select: {result['loaded_layers']}")
