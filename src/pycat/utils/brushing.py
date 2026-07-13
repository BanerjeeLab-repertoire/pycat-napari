"""
**Plot → object → image.** One hub, and a new plot joins it by supplying its refs.

The extensibility requirement
-----------------------------
Gable: *"I want this extensible so that as I write more plots this can be extended easily to
them."*

A complete three-way brushing hub **already exists** — in ``vpt_ui``, keyed on ``track_id``, wiring
plot ↔ image ↔ table with a re-entrancy guard. It is well built, and **it is welded to VPT.** Two
of PyCAT's fifteen plots are pickable; the other thirteen are pictures.

So the hub is lifted out and keyed on an **``ObjectRef``** instead of a ``track_id``. A new plot
becomes brushable by doing exactly one thing:

::

    fig, ax = plt.subplots()
    points = ax.scatter(df.area_um2, df.partition_coeff, picker=5)

    make_pickable(fig, points, refs_from_dataframe(df, source_path=path))

**That is the whole integration.** No hub edit, no registration, no callback plumbing — the plot
supplies the identity behind its points and the hub does the rest.

Interactive and batch are the same mechanism
--------------------------------------------
What the user asked for in batch —

    *"batch a data set and select points in the resulting plot and see the data and bounded
    images"*

— is **the same click**, resolved differently:

* a **live session**: the ref finds the layer, and the viewer reveals the object
* a **batch plot** over files that are not loaded: the ref finds the **file and the crop**, and a
  thumbnail of that object is shown

**The plot does not know which.** It hands over an ``ObjectRef``; the hub decides what is
available. That is what makes one implementation serve both.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.utils.object_ref import ObjectRef, resolve_in_viewer, resolve_offline


# ── The hub. One selection, many views. ──────────────────────────────────────────────────

class SelectionHub:
    """**One object is selected. Every view that cares hears about it.**

    Lifted from ``vpt_ui``'s three-way hub, which had the design right: each view calls
    ``select()`` with a ``source`` tag, and the hub updates the **other** views. The re-entrancy
    guard is what stops the highlight the hub triggers in view B from firing B's own emit and
    looping back — *without it, a click oscillates.*

    The difference is that this one is keyed on an ``ObjectRef``, not a ``track_id``, so it works
    for a condensate, a punctum, a cell or a bead **without knowing which**.
    """

    def __init__(self):
        self._selected: ObjectRef | None = None
        self._views: dict[str, callable] = {}
        self._busy = False

    def register_view(self, name, on_select):
        """A view that wants to hear about selections. ``on_select(ref)``."""
        self._views[str(name)] = on_select
        return self

    def select(self, ref: ObjectRef, source=None):
        """Select an object everywhere **except** the view that initiated it."""
        if ref is None or self._busy:
            return
        self._busy = True
        try:
            self._selected = ref
            for name, callback in self._views.items():
                if name == source:
                    continue          # a view never re-highlights from its own action
                try:
                    callback(ref)
                except Exception as exc:
                    debug_log(f'brushing: the "{name}" view failed to handle a selection', exc)
        finally:
            self._busy = False

    @property
    def selected(self):
        return self._selected


# ── Making a plot pickable. This is the whole integration. ────────────────────────────────

_SELECTED_STYLE = dict(color='#ff8c00', alpha=1.0, linewidth=2.2, zorder=5)
_NORMAL_STYLE = dict(color='#4c72b0', alpha=0.18, linewidth=0.8, zorder=2)


def make_pickable(figure, artist, refs, *, hub=None, on_select=None, viewer=None):
    """**Attach identity to the points of a plot, and make clicking one mean something.**

    Parameters
    ----------
    artist : the scatter/line collection whose elements correspond 1:1 with ``refs``. It must have
        been created with ``picker=`` set, or matplotlib will not emit a pick event for it.
    refs : one ``ObjectRef`` per point, **in the same order**.
    hub : an optional ``SelectionHub``. If given, a pick propagates to every other registered view.
    on_select : an optional ``callback(ref)`` for a plot that just wants the ref.
    viewer : if given, a pick **reveals the object in napari** — the interactive resolver.

    **A plot that supplies refs gets brushing. It does not need to know how.**
    """
    if artist is None or not refs:
        return figure

    state = {'previous': None}

    def _on_pick(event):
        if event.artist is not artist:
            return

        try:
            indices = np.atleast_1d(event.ind)
            if not len(indices):
                return
            index = int(indices[0])
            if not (0 <= index < len(refs)):
                return
            ref = refs[index]
        except Exception as exc:
            debug_log('brushing: could not identify the picked point', exc)
            return

        _emphasise(artist, index, state)
        try:
            event.canvas.draw_idle()
        except Exception:
            pass

        # The three things a pick can do. **None of them is required**, and a plot can want any
        # combination — which is why they are separate rather than one god-callback.
        if viewer is not None:
            try:
                resolve_in_viewer(ref, viewer)
            except Exception as exc:
                debug_log('brushing: could not reveal the object in the viewer', exc)

        if on_select is not None:
            try:
                on_select(ref)
            except Exception as exc:
                debug_log('brushing: the on_select callback failed', exc)

        if hub is not None:
            hub.select(ref, source='plot')

    try:
        figure.canvas.mpl_connect('pick_event', _on_pick)
    except Exception as exc:
        debug_log('brushing: could not connect the pick event', exc)

    # The refs travel WITH the figure, so anything downstream — an export, a saved session, a
    # batch report — can still answer "what is this point?".
    try:
        figure._pycat_object_refs = list(refs)
    except Exception:
        pass

    return figure


def _emphasise(artist, index, state):
    """Show the selection on the plot too. A click that changes nothing visible feels broken."""
    try:
        if hasattr(artist, 'get_sizes'):                     # a scatter
            sizes = np.asarray(artist.get_sizes(), dtype=float)
            if sizes.size == 1:
                sizes = np.repeat(sizes, len(state.get('n', [1])) or 1)
            base = float(np.median(sizes)) if sizes.size else 36.0
            new_sizes = np.full(max(index + 1, sizes.size), base)
            new_sizes[index] = base * 4.0
            artist.set_sizes(new_sizes)
        elif hasattr(artist, 'set_color'):                   # a line
            previous = state.get('previous')
            if previous is not None:
                previous.set(**_NORMAL_STYLE)
            artist.set(**_SELECTED_STYLE)
            state['previous'] = artist
    except Exception as exc:
        debug_log('brushing: could not emphasise the picked point', exc)


# ── The batch resolver: a point becomes an IMAGE, with no session ─────────────────────────

def crop_for_ref(ref: ObjectRef, *, viewer=None, pad_px=8):
    """**Turn a picked point back into an image.** Live layer if there is one; the file if not.

    This is the function a batch report calls. It is deliberately the *only* place that decides
    between the two worlds, so a plot never has to.

    Returns ``(array, message)``. The array is None when the point cannot be resolved — and the
    message says **why**, because *"nothing happened"* is the worst possible answer to a click.
    """
    # A live layer, if one holds this object.
    if viewer is not None:
        try:
            from pycat.utils.layer_tags import get_tag
            for layer in viewer.layers:
                if get_tag(layer, 'role') not in ('image', 'labels', 'mask'):
                    continue
                data = np.asarray(layer.data)
                frame = data[int(ref.frame)] if (ref.frame is not None and data.ndim >= 3) else data
                window = ref.crop_slice(pad_px=pad_px)
                if window is not None:
                    return frame[window], ''
        except Exception as exc:
            debug_log('crop_for_ref: could not crop from a live layer', exc)

    # No session, or nothing open: read it out of the file.
    return resolve_offline(ref, pad_px=pad_px)
