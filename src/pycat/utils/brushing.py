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


def _follow_enabled(central_manager):
    """Should a plain click take the user to the object?

    **No, by default.** Clicking a point to ask what it is should not move the camera and jump the
    frame — that is the "abrupt navigation" complaint, and it is what brushing did unconditionally.
    Going there is a separate intention with its own gestures (double-click, or Reveal).

    `getattr`-defensive on purpose: plenty of callers have no manager, and a missing preference must
    read as "don't yank the view", not as a crash.
    """
    return bool(getattr(central_manager, 'follow_selection', False))


def make_pickable(figure, artist, refs, *, hub=None, on_select=None, viewer=None,
                  central_manager=None):
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

    state = {'previous': None, 'indices': []}

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

        # ── What the gesture MEANT ────────────────────────────────────────────────────────
        #
        # Every click used to do the same thing: mark it, and take you there. So a click meant to
        # ask *"what is this point?"* also moved the camera and jumped the frame, and the view you
        # were reading left. The overlay is what lets these come apart — the object is outlined
        # where it sits, so seeing which one it is no longer costs you your place.
        mouse = getattr(event, 'mouseevent', None)
        modifiers = str(getattr(mouse, 'key', '') or '')
        adding = 'shift' in modifiers
        navigate = bool(getattr(mouse, 'dblclick', False)) or _follow_enabled(central_manager)

        if adding:
            picked = [i for i in state['indices'] if i != index] + [index]
        else:
            picked = [index]
        state['indices'] = picked

        _emphasise(artist, picked, state)
        try:
            event.canvas.draw_idle()
        except Exception:
            pass

        selected_refs = [refs[i] for i in picked]

        # The things a pick can do. **None of them is required**, and a plot can want any
        # combination — which is why they are separate rather than one god-callback.
        if viewer is not None:
            try:
                if len(selected_refs) > 1:
                    from pycat.utils.selection_overlay import show_selection
                    show_selection(viewer, selected_refs)
                else:
                    resolve_in_viewer(ref, viewer, centre=navigate)
            except Exception as exc:
                debug_log('brushing: could not reveal the object in the viewer', exc)

        if on_select is not None:
            try:
                on_select(ref)
            except Exception as exc:
                debug_log('brushing: the on_select callback failed', exc)

        if hub is not None:
            hub.select(ref, source='plot')

    def _on_key(event):
        """**Escape means nothing is selected** — not "nothing happened"."""
        if str(getattr(event, 'key', '')) != 'escape':
            return
        state['indices'] = []
        overlay = state.get('overlay')
        try:
            if overlay is not None:
                overlay.set_visible(False)
            event.canvas.draw_idle()
        except Exception as exc:
            debug_log('brushing: could not clear the plot selection', exc)
        if viewer is not None:
            try:
                from pycat.utils.selection_overlay import clear_selection
                clear_selection(viewer)
            except Exception as exc:
                debug_log('brushing: could not clear the viewer overlay', exc)

    _wire_pickable(figure, _on_pick, _on_key, state, refs)
    return figure


def attach_brushing(figure, brushable, refs, **kwargs):
    """**Wire brushing for whatever ``plot_backends.scatter`` handed back as its second element.**

    A plot is either one artist that maps 1:1 to ``refs``, or — when seaborn split by hue and the split was
    *verified* — a list of ``(artist, row_positions)`` where each artist maps to a SUBSET of ``refs``. This
    dispatches on which it is, so every point ends up carrying its own ``ObjectRef`` either way and callers
    do not special-case the split. ``kwargs`` (``hub`` / ``on_select`` / ``viewer`` / ``central_manager``)
    pass straight through to :func:`make_pickable`.
    """
    if isinstance(brushable, list):
        for artist, positions in brushable:
            make_pickable(figure, artist, [refs[i] for i in positions], **kwargs)
        return figure
    return make_pickable(figure, brushable, refs, **kwargs)


def _wire_pickable(figure, on_pick, on_key, state, refs) -> None:
    """Connect the pick/key/close callbacks and stash the teardown handles on ``figure``.

    Split out of :func:`make_pickable` so the tracked-cids + close-event wiring lives in one place and
    the caller stays reviewable. Tracks the connection ids so teardown is EXACT (disconnecting what was
    actually wired, not guessing at close time), stores the refs + cids + state on the figure, and wires
    the auto-teardown to the figure's OWN close.
    """
    cids = []
    try:
        cids.append(figure.canvas.mpl_connect('pick_event', on_pick))
        cids.append(figure.canvas.mpl_connect('key_press_event', on_key))
    except Exception as exc:
        debug_log('brushing: could not connect the pick event', exc)

    # The refs travel WITH the figure, so anything downstream — an export, a saved session, a batch
    # report — can still answer "what is this point?". **Stored as they are, NOT `list(refs)`.** That
    # call rebuilt every ref the `LazyRefs` sequence exists to avoid building — measured at 3.0 s for
    # 50 000 points — so it quietly undid increment 4's lazy construction in the one function that wires
    # every brushable plot. The sequence is indexable, sized and iterable; nothing needs it to be a list.
    try:
        figure._pycat_object_refs = refs
        figure._pycat_brush_cids = cids
        figure._pycat_brush_state = state
    except Exception:      # broad-ok: a figure that rejects attributes just loses auto-teardown, not brushing
        pass

    # Wire teardown to the figure's OWN close so a closed window cleans up without the caller remembering
    # to. Uses `event.canvas.figure` rather than capturing `figure`, so the handler adds no reference
    # cycle; it does NOT `plt.close` (the figure is already closing — that would recurse). Idempotent.
    try:
        cids.append(figure.canvas.mpl_connect(
            'close_event', lambda event: _teardown_pickable(event.canvas.figure)))
    except Exception:      # broad-ok: no canvas to wire the auto-teardown onto (headless) — dispose still works
        pass


def _teardown_pickable(figure) -> None:
    """Undo everything :func:`make_pickable` wired onto ``figure`` **except closing it** — idempotent.

    Disconnects the pick/key/close canvas callbacks it connected (by their tracked cids, so teardown is
    exact), removes the one-point selection overlay artist, and drops the ``ObjectRef`` sequence the
    figure carried (releasing the ``LazyRefs`` cache). Safe to call more than once — a close signal can
    fire twice — and safe on a figure that was never made pickable (everything is a no-op then).
    """
    cids = getattr(figure, '_pycat_brush_cids', None) or []
    for cid in list(cids):
        try:
            figure.canvas.mpl_disconnect(cid)
        except Exception:      # broad-ok: a stale/twice-disconnected cid must not break teardown
            pass
    try:
        figure._pycat_brush_cids = []
    except Exception:          # broad-ok: teardown is best-effort; never raise while cleaning up
        pass

    state = getattr(figure, '_pycat_brush_state', None)
    overlay = state.get('overlay') if isinstance(state, dict) else None
    if overlay is not None:
        try:
            overlay.remove()
        except Exception:      # broad-ok: an already-removed overlay must not break teardown
            pass
        try:
            state['overlay'] = None
        except Exception:      # broad-ok: best-effort
            pass

    try:
        figure._pycat_object_refs = None       # release the LazyRef sequence the plot held
    except Exception:          # broad-ok: best-effort
        pass


def dispose_pickable(figure, *, close_figure=True) -> None:
    """**Explicit teardown for a brushable plot window** — disconnect, drop refs, and close the figure.

    The complement to :func:`make_pickable`: call this when a plot dock/dialog closes so a long session
    does not accumulate open figures, canvas callbacks and ``LazyRef`` caches (the audit's ">20 figures"
    finding). Runs :func:`_teardown_pickable` (disconnect cids, remove overlay, drop refs) and then, by
    default, ``plt.close(figure)`` so the figure count returns to baseline. Idempotent — a close signal
    can fire twice, and a second call is a no-op.

    ``close_figure=False`` tears down the wiring but leaves the figure open (e.g. a figure embedded in a
    dock that Qt will destroy itself, where ``plt.close`` on an unmanaged canvas would do nothing useful).
    """
    _teardown_pickable(figure)
    if close_figure:
        try:
            import matplotlib.pyplot as plt
            plt.close(figure)
        except Exception:      # broad-ok: closing is best-effort; a non-pyplot figure just stays as-is
            pass


def _emphasise(artist, index, state):
    """Show the selection on the plot too. A click that changes nothing visible feels broken.

    ── The old scatter branch highlighted the WRONG POINTS ────────────────────────────────

    It rewrote the marker-size array::

        sizes = np.asarray(artist.get_sizes(), ...)          # s=60 -> array([60.]), size 1
        if sizes.size == 1:
            sizes = np.repeat(sizes, len(state.get('n', [1])) or 1)   # `state` has no 'n' -> x1
        new_sizes = np.full(max(index + 1, sizes.size), base)         # length index+1 (!)
        new_sizes[index] = base * 4.0
        artist.set_sizes(new_sizes)

    A scatter built with a scalar ``s=`` reports **one** size, and ``state`` never had an ``'n'``
    key, so the repeat was a no-op and the array handed to ``set_sizes`` was ``index + 1`` long —
    **shorter than the collection.** matplotlib TILES a short size array across the points, so
    clicking point 5 of 20 enlarged points **5, 11 and 17**. *The user clicks one object and sees
    several, with nothing to say which is the real one* — the same class of failure as the
    wrong-target highlight increment 1 fixed, in the other direction.

    So the base artist is now **never modified**. The selection is a second, one-point overlay whose
    coordinates are moved: two numbers per click instead of a whole array, and — the part that
    matters — it can only ever mark the point that was actually picked.
    """
    try:
        if hasattr(artist, 'get_offsets'):                   # a scatter
            _emphasise_scatter(artist, index, state)
            return
        elif hasattr(artist, 'set_color'):                   # a line
            previous = state.get('previous')
            if previous is not None:
                previous.set(**_NORMAL_STYLE)
            artist.set(**_SELECTED_STYLE)
            state['previous'] = artist
    except Exception as exc:
        debug_log('brushing: could not emphasise the picked point', exc)


def _emphasise_scatter(artist, index, state):
    """Move the overlay onto the picked point(s). **The base scatter is not touched.**

    ``index`` may be one index or several — shift-click adds to the selection, and *k* selected
    points are still k coordinates, not a rewrite of N.

    Display-only and deliberately unpickable: ``make_pickable`` maps a click to an index on the
    BASE artist, and an overlay sitting on top that could also be picked would hand back its own
    index — which is 0, i.e. the wrong object, every time.
    """
    offsets = np.asarray(artist.get_offsets(), dtype=float)
    wanted = [index] if isinstance(index, (int, np.integer)) else list(index)
    wanted = [int(i) for i in wanted if 0 <= int(i) < len(offsets)]
    if not wanted:
        return
    points = offsets[wanted]
    x, y = points[0]

    axes = getattr(artist, 'axes', None)
    if axes is None:
        return

    overlay = state.get('overlay')
    if overlay is None or getattr(overlay, 'axes', None) is not axes:
        sizes = np.asarray(artist.get_sizes(), dtype=float)
        base = float(np.median(sizes)) if sizes.size else 36.0
        overlay = axes.scatter(
            [x], [y], s=base * 4.0, facecolor='none', edgecolor=_SELECTED_STYLE['color'],
            linewidth=2.0, zorder=(artist.get_zorder() or 2) + 1, picker=None)
        state['overlay'] = overlay
        overlay.set_offsets(points)
    else:
        overlay.set_offsets(points)         # k coordinates, whatever N is
        overlay.set_visible(True)


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
