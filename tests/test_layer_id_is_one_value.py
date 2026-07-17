"""**One layer had two ids, and they were different strings.**

`layer_tag_hook` stamps `metadata['pycat_layer_id'] = uuid4().hex` (32 chars) — what
`ObjectRef.source_layer_id` carries and what the whole brushing arc keys on. `layer_tags.layer_tag_id`
independently minted `metadata['pycat_tag_uid'] = uuid4().hex[:12]` — what `partial_volume_tools` uses
and what `tag_registry.tags_for_plot` records as a plot's `layer_tag_id`.

**Two values, one layer.** So anything matching a plot's recorded id against a ref's
`source_layer_id` could never match. Nothing did yet — which is why the 2026-07-16 audit called it a
trap rather than a bug, and why it was safe to fix by picking one: whoever wires plot-recorded ids to
selection would have hit it, and the failure would have looked like "brushing does not work" rather
than "these are different uuids".

`pycat_layer_id` wins because it is the one with consumers; `pycat_tag_uid` is kept as an alias
holding the same value, so existing readers keep working and now agree with the refs.
"""

# Third party imports
import pytest

# Local application imports
from pycat.utils.layer_tags import layer_tag_id

pytestmark = pytest.mark.core


class _Layer:
    def __init__(self, metadata=None, name='layer'):
        self.metadata = {} if metadata is None else metadata
        self.name = name


def test_the_two_keys_hold_the_SAME_value():
    """The fix, stated plainly. This is what was false."""
    layer = _Layer()
    layer_tag_id(layer)

    assert layer.metadata['pycat_layer_id'] == layer.metadata['pycat_tag_uid']


def test_it_ADOPTS_the_brushing_id_rather_than_minting_a_second():
    """A layer already stamped by `layer_tag_hook` must keep that id — it is the one refs carry."""
    layer = _Layer({'pycat_layer_id': 'a' * 32})

    assert layer_tag_id(layer) == 'a' * 32
    assert layer.metadata['pycat_tag_uid'] == 'a' * 32


def test_a_LEGACY_tag_uid_is_adopted_when_there_is_no_brushing_id():
    """An old layer carrying only `pycat_tag_uid` keeps working, and its id is promoted rather than
    replaced — a rename mid-session would break the edges the id exists to survive."""
    layer = _Layer({'pycat_tag_uid': 'legacy12'})

    assert layer_tag_id(layer) == 'legacy12'
    assert layer.metadata['pycat_layer_id'] == 'legacy12'


def test_a_STALE_tag_uid_is_replaced_by_the_brushing_id_not_honoured():
    """A layer stamped by BOTH paths carries two different values — the exact broken state. The
    brushing id wins: the stale one could not have matched anything, so keeping it would only keep
    the mismatch."""
    layer = _Layer({'pycat_layer_id': 'b' * 32, 'pycat_tag_uid': 'stale1234567'})

    assert layer_tag_id(layer) == 'b' * 32
    assert layer.metadata['pycat_tag_uid'] == 'b' * 32, 'the stale alias survived'


def test_it_is_STABLE_across_calls():
    """The id exists so an edge survives a rename. A fresh uuid per call would defeat it."""
    layer = _Layer()
    assert layer_tag_id(layer) == layer_tag_id(layer)


def test_a_layer_with_NO_usable_metadata_still_answers():
    """Falls back to the name rather than raising — the tag system must not take a viewer down."""
    layer = _Layer(name='fallback')
    layer.metadata = None
    assert layer_tag_id(layer) == 'fallback'


def test_the_id_matches_what_a_REF_would_carry():
    """The point of the whole exercise: a plot's recorded id and a ref's `source_layer_id` are now
    the same string for the same layer, so a future wiring of one to the other can actually match."""
    layer = _Layer({'pycat_layer_id': 'c' * 32})

    recorded_by_the_tag_system = layer_tag_id(layer)
    carried_by_a_ref = layer.metadata.get('pycat_layer_id')

    assert recorded_by_the_tag_system == carried_by_a_ref
