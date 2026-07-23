"""**`get_tags()` returns a LIST. Four consumers called `.get()` on it.**

The tag store is `{'tags': [{'key': ..., 'value': ...}, ...]}` and `get_tags(layer)` hands back that
list of records. Four call sites treated it as a mapping — `(get_tags(layer) or {}).get('stack_axis',
{}).get('value')` — which raises `AttributeError: 'list' object has no attribute 'get'`. Every one of
them sits inside a bare `except Exception`, so the exception is swallowed and the branch silently
does nothing. **The layer carries the tag; the code cannot hear it.** `get_tag(layer, key)` is the
correct accessor and has been sitting two functions away the whole time.

**Why it survived:** `get_tags(layer) or {}` is fine when a layer has NO tags — `[] or {}` gives `{}`
and `.get()` works. It only breaks once a layer actually *has* tags, which is exactly when the branch
was supposed to do something.

**Why the tests did not catch it:** `test_axis_is_per_layer.py` monkeypatched `get_tags` to return a
dict-shaped fake, so it validated a data model that does not exist in production and passed while the
real path was dead. These tests drive the **real** store through `tag_layer`, so they cannot lie in
the same way.

pycat imports are inside the test bodies: `conftest.py`'s `pytest_ignore_collect` drops modules whose
module-scope imports name `pycat.file_io` when the GUI stack is absent.
"""

# Standard library imports
import sys
import types

# Third party imports
import pytest


pytestmark = pytest.mark.base


class _Layer:
    """A stand-in napari layer: the tag store only needs a `.metadata` dict and a name."""

    def __init__(self, name='layer'):
        self.name = name
        self.metadata = {}


def _tagged(**tags):
    """A layer carrying REAL tags, written through the real `tag_layer`."""
    from pycat.utils import layer_tags as LT
    layer = _Layer()
    for key, value in tags.items():
        LT.tag_layer(layer, key, value, source='user_set')
    return layer


@pytest.fixture
def captured_warnings(monkeypatch):
    """Catch `napari.utils.notifications.show_warning` without a viewer."""
    raised = []
    notifications = types.ModuleType('napari.utils.notifications')
    notifications.show_warning = lambda message: raised.append(message)
    monkeypatch.setitem(sys.modules, 'napari', types.ModuleType('napari'))
    monkeypatch.setitem(sys.modules, 'napari.utils', types.ModuleType('napari.utils'))
    monkeypatch.setitem(sys.modules, 'napari.utils.notifications', notifications)
    return raised


def test_get_tags_returns_a_LIST_and_get_tag_is_the_accessor():
    """The shape the four consumers got wrong. Pinned so the next reader does not have to
    rediscover it by writing the same bug."""
    from pycat.utils.layer_tags import get_tag, get_tags

    layer = _tagged(stack_axis='Z')

    records = get_tags(layer)
    assert isinstance(records, list), "get_tags returns a list of tag RECORDS, not a mapping"
    assert records[0]['key'] == 'stack_axis' and records[0]['value'] == 'Z'
    assert not hasattr(records, 'get'), (
        "a list has no .get — `(get_tags(layer) or {}).get(...)` raises AttributeError, and every "
        "call site wrapped it in a bare `except`, so the failure was invisible")

    assert get_tag(layer, 'stack_axis') == 'Z'
    assert get_tag(layer, 'nope', 'fallback') == 'fallback'


def test_the_LAYERS_OWN_axis_tag_beats_the_shared_session_label(captured_warnings):
    """**The bug that matters.** Open a movie and label it T; add a z-stack and label it Z — the
    second load overwrites `stack_axis_label` in the shared repository, so the session now says the
    wrong thing about the first layer. The per-layer tag exists precisely to win here.

    It never did: the layer branch raised into a bare `except` and fell through to the session
    label. So the warning named the axis of *whichever file was opened last*.
    """
    from pycat.file_io.stack_access import warn_if_assumed_axis

    layer = _tagged(stack_axis='Z')                       # this layer was labelled Z
    session_says_T = {'stack_axis_assumed': True, 'stack_axis_label': 'T'}

    warn_if_assumed_axis(session_says_T, "an MSD", layer=layer)

    assert captured_warnings, "no warning fired for a layer whose axis was assumed"
    assert "'Z'" in captured_warnings[0], (
        f"the warning used the SESSION's label instead of the layer's own tag: "
        f"{captured_warnings[0]!r}")


def test_a_layer_whose_axis_was_DECLARED_is_never_warned_about(captured_warnings):
    """The no-op half of the contract: only speak when the label really was a guess."""
    from pycat.file_io.stack_access import warn_if_assumed_axis

    warn_if_assumed_axis({}, "an MSD", layer=_Layer())
    assert not captured_warnings


def test_the_axis_warning_fires_once_per_LAYER_not_once_per_session(captured_warnings):
    """The once-only flag is keyed by layer — a shared boolean meant the second stack (the one
    actually mislabelled) never warned at all."""
    from pycat.file_io.stack_access import warn_if_assumed_axis

    dr = {'stack_axis_assumed': True, 'stack_axis_label': 'T'}
    movie = _tagged(stack_axis='T'); movie.name = 'movie'
    zstack = _tagged(stack_axis='Z'); zstack.name = 'zstack'

    for _ in range(3):
        warn_if_assumed_axis(dr, "an MSD", layer=movie)
    warn_if_assumed_axis(dr, "3-D metrics", layer=zstack)

    assert len(captured_warnings) == 2, (
        f"expected one warning per layer, got {len(captured_warnings)}")
    assert "'T'" in captured_warnings[0] and "'Z'" in captured_warnings[1]


def test_session_manifest_reads_the_real_store_for_source_and_derived_layers():
    """`_is_source_image_layer` / `_is_reconstructable` both did `(get_tags(layer) or {}).get(...)`,
    and both read tag keys that **nothing writes** (`'origin'`, `'operation'` — the vocabulary has
    `provenance` and `op`). Doubly dead: wrong accessor, wrong key.

    The name-based checks were carrying these functions entirely.
    """
    from pycat.file_io.session_manifest import _is_reconstructable, _is_source_image_layer
    from pycat.utils import layer_tags as LT

    class Image(_Layer):
        """`_is_source_image_layer` gates on `type(layer).__name__ == 'Image'` — it only ever
        considers napari Image layers, so the stand-in has to be one."""

    def _image(**tags):
        layer = Image()
        for key, value in tags.items():
            LT.tag_layer(layer, key, value, source='user_set')
        return layer

    # A freshly loaded source layer is tagged provenance='raw' by `_tag_loaded_layer`.
    loaded = _image(provenance='raw')
    loaded.name = 'something_that_does_not_match_the_stem'
    assert _is_source_image_layer(loaded, source_stem='unrelated'), (
        "a layer tagged provenance='raw' is a loaded source layer; the tag branch must see it "
        "even when the NAME does not match the file stem")

    # A derived layer must not be mistaken for a source.
    #
    # NOTE the name is deliberately stem-free. `_is_source_image_layer` is an OR: a name containing
    # the source stem still wins on its own, so a derived layer named "<stem> something" is a false
    # positive unless its name happens to hit the hardcoded `derived_markers` list. That name
    # heuristic is the fragility the tag branch was added to replace — but making the tag
    # AUTHORITATIVE (provenance='derived' vetoing the name) would change which layers the save
    # dialog pre-selects, so it is left alone here and noted rather than smuggled into a bug fix.
    derived = _image(provenance='derived')
    derived.name = 'a_processed_result'
    assert not _is_source_image_layer(derived, source_stem='unrelated')

    # `_is_reconstructable` must not raise on a tagged layer, and must not claim a plain
    # source layer is a pure interpolation.
    assert not _is_reconstructable(loaded)
