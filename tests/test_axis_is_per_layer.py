"""
**Open a movie, label it T. Add a z-stack, label it Z. The movie is now labelled Z.**

``stack_axis_label`` lives in ``data_repository`` — **one dict shared by every layer.** And PyCAT
can add a file *without* clearing:

* **"Open Image (Add)"** — an explicit menu action, *"for side-by-side comparison"*;
* **multi-select in the file dialog** — which loads *"each subsequent file with
  ``clear_first=False``"*.

So the second load **overwrites the first's axis label**. An MSD on the movie then reads ``'Z'``, and
``warn_if_assumed_axis`` warns about the wrong thing — *on the layer the user labelled correctly.*

***T and Z load identically.*** There is nothing on screen to reveal it, and every rate that comes
out — a diffusion coefficient, a coarsening rate, a recovery half-time — is a rate **per frame**. If
those frames are Z-slices, the rate is a fiction.

── And the warning only ever fired ONCE ────────────────────────────────────────────────

The once-per-session flag was ``dr['_axis_warned'] = True``, set on the **shared** repository. So the
first stack spent the session's single warning and ***the second stack never warned at all*** — the
one that was actually mislabelled.

The flag is now a **set, keyed by layer**.
"""

import sys
import types

import pytest

from pycat.file_io.stack_access import warn_if_assumed_axis


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


class _Layer:
    """A napari layer carrying a PyCAT tag store."""

    def __init__(self, name, axis=None):
        self.name = name
        self.metadata = {}
        if axis is not None:
            self.metadata['pycat_tags'] = {
                'stack_axis': {'value': axis, 'source': 'user_set'}
            }


@pytest.fixture
def tags_from_the_layer(monkeypatch):
    """`get_tags` must read the layer it is handed — not a global."""
    import pycat.utils.layer_tags as layer_tags
    monkeypatch.setattr(
        layer_tags, 'get_tags',
        lambda layer: getattr(layer, 'metadata', {}).get('pycat_tags', {}))


@pytest.mark.core
def test_each_LAYER_is_warned_about_ITS_OWN_axis(captured_warnings, tags_from_the_layer):
    """**The bug.** The session remembers only the *last* file's answer."""
    # The session's label is 'Z' — because the z-stack was loaded SECOND, and overwrote it.
    repository = {'stack_axis_assumed': True, 'stack_axis_label': 'Z'}

    movie = _Layer('movie.tif', axis='T')       # the user labelled THIS one 'T'
    z_stack = _Layer('zstack.tif', axis='Z')

    warn_if_assumed_axis(repository, 'MSD', layer=movie)

    assert captured_warnings, "no warning was raised at all"
    assert "'T'" in captured_warnings[0], (
        f"the movie was warned about the SESSION's axis, not its own.\n"
        f"  said: {captured_warnings[0]}\n\n"
        "**The user labelled this layer 'T'.** The session says 'Z' only because a z-stack was "
        "added afterwards and overwrote the shared `data_repository` entry. *T and Z load "
        "identically — nothing on screen reveals it.*"
    )

    warn_if_assumed_axis(repository, '3-D metrics', layer=z_stack)
    assert "'Z'" in captured_warnings[1]


@pytest.mark.core
def test_the_SECOND_stack_is_warned_about_AT_ALL(captured_warnings, tags_from_the_layer):
    """***The one that was actually mislabelled was the one that never warned.***

    The flag was ``dr['_axis_warned'] = True`` on the **shared** repository. The first stack spent
    the session's single warning; the second got nothing.
    """
    repository = {'stack_axis_assumed': True, 'stack_axis_label': 'T'}

    first = _Layer('first.tif', axis='T')
    second = _Layer('second.tif', axis='Z')

    warn_if_assumed_axis(repository, 'MSD', layer=first)
    warn_if_assumed_axis(repository, 'MSD', layer=second)

    assert len(captured_warnings) == 2, (
        f"only {len(captured_warnings)} warning(s) for two stacks.\n\n"
        "The once-per-session flag meant **the second stack never warned** — and with the label "
        "overwritten by the second load, the second stack is exactly the one at risk."
    )


@pytest.mark.core
def test_the_same_layer_is_warned_about_only_ONCE(captured_warnings, tags_from_the_layer):
    """Per-layer, not per-call. *A warning on every button press is a warning nobody reads.*"""
    repository = {'stack_axis_assumed': True, 'stack_axis_label': 'T'}
    layer = _Layer('movie.tif', axis='T')

    for _ in range(5):
        warn_if_assumed_axis(repository, 'MSD', layer=layer)

    assert len(captured_warnings) == 1, (
        f"the same layer warned {len(captured_warnings)} times"
    )


@pytest.mark.core
def test_a_DECLARED_axis_is_never_warned_about(captured_warnings, tags_from_the_layer):
    """**Safe no-op when the file said so.** The warning is for a *guess*, not for metadata."""
    repository = {}          # nothing was assumed — the file declared its axes

    warn_if_assumed_axis(repository, 'MSD', layer=_Layer('declared.ome.tif'))

    assert not captured_warnings, (
        "a stack whose axis came from the file's own metadata was warned about. This warning "
        "exists for the undeclared multipage TIFF the user had to LABEL BY HAND."
    )


@pytest.mark.core
def test_a_caller_that_passes_NO_layer_still_works(captured_warnings):
    """**Additive.** Every existing call site passes no layer and must behave exactly as before.

    *The layer argument is optional precisely so that fixing the store did not require rewriting
    nine analysis handlers in the same change.*
    """
    repository = {'stack_axis_assumed': True, 'stack_axis_label': 'T'}

    warn_if_assumed_axis(repository, 'MSD')

    assert captured_warnings and "'T'" in captured_warnings[0], (
        "the old, layer-less call path broke. It must keep falling back to the repository."
    )
