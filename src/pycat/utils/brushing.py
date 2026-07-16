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

    An ``ObjectRef``-shaped face on `selection_service.SelectionService`, kept because this is the
    API ``make_pickable`` already speaks.

    ── It used to be a second implementation, and it had lost the guard that matters ──────

    This was written as a lift of ``vpt_ui``'s three-way hub — its docstring said so, and the design
    was right: each view calls ``select()`` with a ``source`` tag and the hub updates the *others*,
    keyed on an ``ObjectRef`` rather than a ``track_id`` so it works for a condensate, a punctum, a
    cell or a bead without knowing which.

    But the lift ended in ``finally: self._busy = False`` — **a synchronous release**, which is
    precisely the bug VPT's dispatcher documents having fixed:

        *"Several of those emit Qt/napari signals ASYNCHRONOUSLY — they fire after this method has
        already returned — so a synchronous busy-flag that resets in `finally` does NOT cover them,
        and the queued signals re-enter here and cascade."*

    So this hub would have oscillated the first time a real Qt view was wired to it. **It never
    was** — zero production callers, one test — which is the only reason that was never discovered.
    A copy that drifts is bad; a copy that drifts *and is unused* is a trap with a passing test.

    It is now a thin adapter, so there is one dispatcher and the generic path inherits VPT's guards.
    """

    def __init__(self, service=None):
        from pycat.utils.selection_service import SelectionService
        self._service = service if service is not None else SelectionService()
        self._refs: dict[str, ObjectRef] = {}
        self._selected: ObjectRef | None = None

    @property
    def service(self):
        return self._service

    def register_view(self, name, on_select):
        """A view that wants to hear about selections. ``on_select(ref)``."""
        def _adapter(selection, _cb=on_select, _hub=self):
            ref = _hub._refs.get(selection.primary_id) or _hub._selected
            if ref is not None:
                _cb(ref)

        # The adapter is a closure, so the hub must own it or it dies on the service's weak ref.
        self._adapters = getattr(self, '_adapters', {})
        self._adapters[str(name)] = _adapter
        self._service.subscribe(str(name), _adapter)
        return self

    def select(self, ref: ObjectRef, source=None):
        """Select an object everywhere **except** the view that initiated it."""
        if ref is None:
            return
        from pycat.utils.selection_service import Selection

        key = self._key_for(ref)
        self._refs[key] = ref
        self._selected = ref
        selection = Selection(entity_ids=(key,), primary_id=key, mode='selected',
                              source_view=str(source) if source is not None else '',
                              generation=self._service.next_generation())
        self._service.select(selection)

    @staticmethod
    def _key_for(ref: ObjectRef) -> str:
        """The ref's stable name if it has one (increment 2), else something unique to it.

        A legacy ref genuinely has no name, and the hub must still dispatch it — so the fallback is
        the ref's identity, which is stable for as long as the object exists.
        """
        entity = getattr(ref, 'entity_id', None)
        return str(entity) if entity else f"objectref/{id(ref)}"

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

def _crop_from_layer(layer, ref: ObjectRef, pad_px):
    """**Index, slice, THEN materialize.** The 8-px crop, or None if this layer cannot give it.

    ── The order is the entire fix ────────────────────────────────────────────────────────

    This was ``data = np.asarray(layer.data)`` and *then* a slice. On a lazy TIFF/IMS/CZI/dask
    layer that is the ``np.asarray(layer.data)`` materialization trap, in the brushing path:
    **one click asked for the whole acquisition in order to take an 8-pixel crop.**

    And on the current tree it is worse than slow. Every lazy wrapper's ``__array__`` now *refuses*
    (``refuse_implicit_full_read``), so the eager read did not freeze — it **raised**, the
    surrounding ``except`` abandoned the live-layer path entirely, and the click fell through to
    re-reading the file. With the file moved or gone the user was told *"The source file is gone"*
    **while the layer sat open in the viewer.**

    So: index to the plane (lazily — the wrappers' ``__getitem__`` is the fast per-plane path),
    slice the crop window out of that plane, and only then coerce the tiny crop to an array.
    ``np.asarray`` never touches ``layer.data``.

    If a wrapper cannot be indexed lazily this raises, the caller's ``except`` catches it, and the
    fallback is ``resolve_offline`` — **never** a whole-stack read.
    """
    window = ref.crop_slice(pad_px=pad_px)
    if window is None:
        return None

    lazy = layer.data
    if ref.frame is not None and getattr(lazy, 'ndim', 2) >= 3:
        plane = lazy[int(ref.frame)]      # one plane, lazily — not the whole stack
    else:
        plane = lazy

    crop = plane[window]                  # slice the (still-lazy) plane
    return np.asarray(crop)               # materialize ONLY the crop


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
            from pycat.utils.object_ref import layers_for_ref
            candidates, note = layers_for_ref(ref, viewer, roles=('image', 'labels', 'mask'))
            if note:
                debug_log(f'crop_for_ref: {note}', None)
            for layer in candidates:
                crop = _crop_from_layer(layer, ref, pad_px)
                if crop is not None:
                    return crop, ''
        except Exception as exc:
            debug_log('crop_for_ref: could not crop from a live layer', exc)

    # No session, or nothing open: read it out of the file.
    return resolve_offline(ref, pad_px=pad_px)
