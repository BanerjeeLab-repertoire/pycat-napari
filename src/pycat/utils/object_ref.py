"""
**The identity a plot point needs to become an image again.**

The interactive case is easy and already works: a point in an MSD plot carries a ``track_id``, the
Tracks layer is in memory, and the VPT UI highlights it (``vpt_ui._select_track``). *A track_id is
enough, because the data is right there.*

**The batch case is not.** A point in a plot built over a hundred files points at an object in an
image that **is not loaded**, produced by a segmentation that **is not in memory**. *"Highlight the
layer"* is not available. What the user actually wants —

    *"select points in the resulting plot and see the data and bounded images"*

— means the point has to carry enough to **re-open the source and crop to the object**, without
re-running anything.

What that requires
------------------
========================  =================================================================
``source_path``           **which file**
``frame``                 **which frame / z-slice** in it
``object_id``             **which object** — the label value in that frame's mask
``bbox``                  **where it is** — ``(y0, x0, y1, x1)``
``tags``                  **what produced it** — the op chain, from the tag registry
========================  =================================================================

**The bbox is the one that makes this work.** With it, a batch plot can show a cropped thumbnail
by reading *that region of that file* — **no reload of the whole stack, and no re-segmentation.**
Without it, the only way back to the object is to redo the analysis.

And it is **free**: ``skimage.measure.regionprops`` gives ``prop.bbox`` at every segmentation site
in PyCAT — **25 files use regionprops, and 1 keeps the bbox.** It is being discarded everywhere.

Same mechanism, two resolvers
-----------------------------
A plot is *points + the ObjectRefs behind them*. What changes between interactive and batch is
**only the resolver**:

* **interactive** — the ObjectRef finds a live layer, and highlights it
* **batch** — the ObjectRef finds a file and a crop, and shows the image

**The plot does not know which it is.** That is what makes it extensible: a new plot supplies its
ObjectRefs and gets both behaviours for free.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections import OrderedDict
from collections.abc import Sequence

import numpy as np

from pycat.utils.general_utils import debug_log


@dataclasses.dataclass(frozen=True)
class ObjectRef:
    """**One object, identified well enough to be found again from a cold start.**

    Frozen because an identity that can be edited is not an identity.
    """

    object_id: int | None = None        # the label value in the mask
    frame: int | None = None            # frame / z index; None for a single 2-D image
    bbox: tuple | None = None           # (y0, x0, y1, x1) in PIXELS -- the crop
    source_path: str | None = None      # the file it came from
    track_id: int | None = None         # for time-series objects
    parent_id: int | None = None        # e.g. the cell a punctum belongs to
    tags: dict | None = None            # what produced it (op / role / target)
    # WHICH LAYER this object came from -- matched against `layer.metadata['pycat_layer_id']`,
    # stamped on every layer by the tag hook.
    #
    # **`object_id` is a label value, and a label value is only meaningful inside ONE mask.**
    # Label 7 exists in every segmentation that has seven objects, and they are not the same
    # object. Without this field, resolution had to guess which mask was meant, and it guessed
    # "the first one open" -- see `resolve_in_viewer`.
    #
    # Optional and defaulted: every existing `ObjectRef(...)` / `from_row(...)` call still works,
    # and a ref without it resolves exactly as before (loudly). Increment 1 makes resolution
    # HONOUR this; filling it at ref-creation time is increment 2's job.
    source_layer_id: str | None = None
    # The object's stable NAME (increment 2's `_pycat_entity_id`), when the table carried one.
    # `object_id` says which label; this says which object, across sorts, filters and reloads.
    # Optional: a legacy table has no name, and such a ref still brushes — by position, as before.
    entity_id: str | None = None

    def crop_slice(self, pad_px: int = 0):
        """The numpy slice that cuts this object out of a frame. ``None`` if there is no bbox."""
        if not self.bbox or len(self.bbox) != 4:
            return None
        y0, x0, y1, x1 = (int(v) for v in self.bbox)
        p = int(pad_px)
        return (slice(max(y0 - p, 0), y1 + p), slice(max(x0 - p, 0), x1 + p))

    def is_resolvable_offline(self) -> bool:
        """**Can this be turned back into an image WITHOUT the session that made it?**

        This is the property that decides whether batch brushing works at all. A ref with a
        ``track_id`` and nothing else is fine *interactively* and **useless in batch**.
        """
        return bool(self.source_path and self.bbox)

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row, *, source_path=None, tags=None):
        """Build a ref from a results-DataFrame row, taking whatever identity it carries.

        **The column names are the ones PyCAT already uses** — ``label``, ``track_id``,
        ``frame``, ``cell_label``, ``bbox`` — so an existing table needs no rewriting to become
        brushable, only a ``bbox`` column it should have been keeping anyway.
        """
        def _get(*names, cast=int):
            for name in names:
                if name in row and row[name] is not None:
                    try:
                        value = row[name]
                        if isinstance(value, float) and np.isnan(value):
                            continue
                        return cast(value)
                    except Exception:
                        continue
            return None

        bbox = None
        if 'bbox' in row and row['bbox'] is not None:
            try:
                bbox = tuple(int(v) for v in row['bbox'])
                if len(bbox) != 4:
                    bbox = None
            except Exception as exc:
                debug_log('ObjectRef: could not read a bbox off the row', exc)

        # A bbox may also be spread across four columns, which is how a CSV round-trip stores it.
        if bbox is None and all(k in row for k in ('bbox_y0', 'bbox_x0', 'bbox_y1', 'bbox_x1')):
            try:
                bbox = (int(row['bbox_y0']), int(row['bbox_x0']),
                        int(row['bbox_y1']), int(row['bbox_x1']))
            except Exception as exc:
                debug_log('ObjectRef: could not assemble a bbox from columns', exc)

        # ── The OBJECT's own id, and NOT its parent's ────────────────────────────
        #
        # A first version listed 'cell_label' as a fallback for object_id, and on a puncta table
        # — whose column is 'punctum_label' — **every punctum reported its CELL's id.** Four
        # different puncta all came back as object 1.
        #
        # A ref that points at the wrong object is worse than one that points at nothing: the
        # click LANDS, on the wrong thing, and nothing says so.
        #
        # So the object's own identity is looked for by every name PyCAT actually uses, and the
        # PARENT is a separate field. They are different questions.
        # The labels layer this row's object lives in, when the table carries identity. This is what
        # makes a ref resolve to its OWN layer instead of the first mask that happens to be open —
        # the wrong-target bug. Absent on a legacy table, which then resolves as it always did (and
        # says that it guessed).
        def _hidden(column):
            try:
                if column in row and row[column]:
                    value = row[column]
                    if isinstance(value, float) and np.isnan(value):
                        return None
                    return str(value)
            except Exception as exc:
                debug_log(f'ObjectRef: could not read {column} off the row', exc)
            return None

        layer_id = _hidden('_pycat_layer_id')
        entity_id = _hidden('_pycat_entity_id')

        return cls(
            object_id=_get('object_id', 'label', 'punctum_label', 'condensate_label',
                           'droplet_label', 'bead_label', 'track_id', 'cell_label'),
            frame=_get('frame', 't', 'z'),
            bbox=bbox,
            source_path=(str(source_path) if source_path
                         else (str(row['source_path']) if 'source_path' in row
                               and row['source_path'] else None)),
            track_id=_get('track_id'),
            # ``'cell label'`` — with a SPACE — is what `puncta_analysis_func` actually writes
            # (`feature_analysis_tools.py:652`), while every other producer and consumer in the
            # codebase spells it `cell_label`. So this lookup **silently missed on the one table it
            # exists for**, and every punctum ref has carried `parent_id=None`. Accepting both
            # spellings fixes the ref without renaming a column users already see in their results.
            # (The mismatch is live elsewhere too — `analysis_plots.py:1166` gates a per-cell
            # grouping on `cell_label` and never fires for this table.)
            parent_id=_get('cell_label', 'cell label', 'parent_id'),
            tags=dict(tags) if tags else None,
            source_layer_id=layer_id,
            entity_id=entity_id,
        )


class LazyRefs(Sequence):
    """**One ``ObjectRef`` per row — built on the click, not on the plot.**

    ── Measured, not assumed ──────────────────────────────────────────────────────────────

    This used to be ``for _, row in df.iterrows(): refs.append(ObjectRef.from_row(row))``, run when
    the plot was *wired*, for every row. On a 100 000-point scatter that is 100k pandas Series plus
    100k frozen objects plus their duplicated strings — **6.4 seconds, against 0.02 s for the
    scatter itself.** The refs cost ~380x the plot they decorate, and the user waits for all of it
    before seeing anything.

    **A click uses exactly one of them.** So this is a `Sequence` that materialises a ref on
    ``refs[i]`` and remembers the few most recent, which keeps every caller — ``make_pickable``'s
    ``refs[index]``, ``len(refs)``, iteration, slicing — working unchanged.

    The cache is deliberately small: the point is to avoid holding 100k objects, so remembering the
    last handful of clicks is the whole benefit and anything more re-creates the problem.
    """

    _CACHE_LIMIT = 64

    def __init__(self, df, *, source_path=None, tags=None):
        self._df = df
        self._source_path = source_path
        self._tags = tags
        self._cache: 'OrderedDict[int, ObjectRef]' = OrderedDict()

    def __len__(self):
        try:
            return len(self._df)
        except Exception:
            return 0

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]

        # **Bounds-check explicitly, before the try below.** `Sequence` iteration walks 0, 1, 2 …
        # until `__getitem__` raises IndexError — so an out-of-range index that gets swallowed by
        # the catch-all and answered with a blank ref makes `for ref in refs` loop **forever**.
        # (It did. The test that iterates caught it.)
        count = len(self)
        if index < 0:
            index += count
        if index < 0 or index >= count:
            raise IndexError(f"ref index out of range: {index}")

        hit = self._cache.get(index)
        if hit is not None:
            self._cache.move_to_end(index)
            return hit

        try:
            ref = ObjectRef.from_row(self._df.iloc[index],
                                     source_path=self._source_path, tags=self._tags)
        except Exception as exc:
            # A bare ref, exactly as the eager build did: the refs stay INDEX-ALIGNED with the
            # plot's points, and that alignment is what `make_pickable` maps a click through.
            debug_log('LazyRefs: could not build a ref for a row', exc)
            ref = ObjectRef()

        self._cache[index] = ref
        self._cache.move_to_end(index)
        while len(self._cache) > self._CACHE_LIMIT:
            self._cache.popitem(last=False)
        return ref

    @property
    def entity_ids(self):
        """The rows' stable names as a compact array — no Python object per point.

        This is what a view should key on: it is one numpy array of strings, not 100k refs.
        """
        try:
            if '_pycat_entity_id' in self._df.columns:
                return self._df['_pycat_entity_id'].to_numpy()
        except Exception as exc:
            debug_log('LazyRefs: could not read the entity id column', exc)
        return np.empty(0, dtype=object)


def refs_from_dataframe(df, *, source_path=None, tags=None):
    """One ``ObjectRef`` per row, in row order. **This is what a plot attaches to its points.**

    Returns a `LazyRefs` — a sequence that builds each ref on first access. The signature and every
    use of the result (``refs[i]``, ``len(refs)``, iteration) are unchanged; what changed is that
    opening a brushable plot no longer builds a ref for every point that will never be clicked.
    """
    return LazyRefs(df, source_path=source_path, tags=tags)


def bbox_columns_from_regionprops(prop):
    """**Keep the bbox.** ``regionprops`` hands it over free, and PyCAT throws it away.

    25 files call ``regionprops``; **one** keeps the bounding box. Every results table that
    discards it is a table whose rows **cannot be turned back into an image** — and that is the
    difference between a plot you can click and a plot you can only look at.

    Returns the four columns to merge into a results row.
    """
    try:
        y0, x0, y1, x1 = prop.bbox
        return dict(bbox_y0=int(y0), bbox_x0=int(x0), bbox_y1=int(y1), bbox_x1=int(x1))
    except Exception as exc:
        debug_log('bbox_columns_from_regionprops: no usable bbox on this prop', exc)
        return dict(bbox_y0=None, bbox_x0=None, bbox_y1=None, bbox_x1=None)


# ── The resolvers. The plot does not know which one it is talking to. ─────────────────────

def layers_for_ref(ref: ObjectRef, viewer, roles=('labels', 'mask')):
    """**The layers this object could have come from, best first** — plus why, if we had to guess.

    Returns ``(layers, note)``. ``note`` is empty when the answer is *known*; otherwise it says what
    was assumed, so a caller can degrade visibly instead of silently.

    ── Why this exists ────────────────────────────────────────────────────────────────────

    Resolution used to take **the first layer with a labels/mask role** and set
    ``selected_label = ref.object_id`` on it. **A label value is only meaningful inside one mask**:
    label 7 exists in every segmentation that has seven objects, and they are not the same object.
    So with two segmentations open, a punctum from analysis A highlighted an unrelated object in
    mask B — *and nothing about it looked wrong.* That is a scientific error, not a UX wrinkle: the
    user is shown the wrong object as if it were the right one.

    A ref that knows its own layer resolves to that layer. A ref that does not (an older one, or one
    built before increment 2 fills the field) falls back to the old behaviour — but **says so**.
    """
    from pycat.utils.layer_tags import get_tag

    candidates = [l for l in getattr(viewer, 'layers', []) if get_tag(l, 'role') in roles]
    if not candidates:
        return [], ''

    if ref.source_layer_id:
        owned = [l for l in candidates
                 if (getattr(l, 'metadata', None) or {}).get('pycat_layer_id')
                 == ref.source_layer_id]
        if owned:
            return owned, ''
        # The ref knows its layer and that layer is not open. Do NOT quietly use a different one:
        # the ref is telling us the honest answer is "not here".
        return [], (f"the layer this object came from is not open "
                    f"(layer id {ref.source_layer_id[:8]}…)")

    if len(candidates) > 1:
        return candidates, (
            f"this object does not record which layer it came from, and {len(candidates)} "
            f"labels/mask layers are open — using '{getattr(candidates[0], 'name', '?')}'. "
            f"It may not be the right one.")

    return candidates, ''


def location_from_registry(ref: ObjectRef) -> ObjectRef:
    """Refresh a ref's LOCATION from the entity registry when the registry knows this entity — so
    resolution uses the one authority for *where to show it* (which ``update_location`` keeps live) rather
    than the columns the ref was built from, which can go stale after a re-crop / layer re-add / frame
    reindex. This is the consumer side of the identity-registry contract: a view carries the id, and asks
    the registry where the object is now.

    Honest fallbacks: a ref with no ``entity_id``, or an entity the registry does not know (dataset closed /
    never registered), is returned unchanged — the ref's own last-known location is used, and a wrong
    location is never invented. Per field, a registry ``None`` leaves the ref's own value in place.
    """
    eid = getattr(ref, 'entity_id', None)
    if not eid:
        return ref
    try:
        from pycat.utils.entity_registry import default_registry
        rec = default_registry().resolve(eid)
    except Exception as exc:                 # broad-ok: a registry miss must never break resolution
        debug_log('object_ref: entity-registry lookup failed', exc)
        return ref
    if rec is None:
        return ref
    loc = rec.location
    return dataclasses.replace(
        ref,
        bbox=loc.bbox if loc.bbox is not None else ref.bbox,
        frame=loc.frame if loc.frame is not None else ref.frame,
        source_layer_id=loc.layer_id if loc.layer_id is not None else ref.source_layer_id,
        source_path=loc.source if loc.source is not None else ref.source_path)


def _source_layer_of(ref: ObjectRef, viewer):
    """The exact layer this ref was measured on, matched by ``pycat_layer_id`` — the layer whose scale and
    translate map the ref's pixel-space bbox to world coordinates. ``None`` when the ref records no source
    layer or that layer is not open. (Unlike :func:`layers_for_ref`, this is not restricted to labels/mask
    roles: the measured-on layer may be the image itself, and its scale is what the world transform needs.)"""
    lid = getattr(ref, 'source_layer_id', None)
    if not lid:
        return None
    for layer in getattr(viewer, 'layers', []) or []:
        if (getattr(layer, 'metadata', None) or {}).get('pycat_layer_id') == lid:
            return layer
    return None


def resolve_in_viewer(ref: ObjectRef, viewer, *, centre=True, pad_px=8):
    """**Interactive**: find the object in a live layer and reveal it.

    Returns True if it landed somewhere. This is what the existing VPT hub does, generalised off
    ``track_id`` so that any object identity works.
    """
    from pycat.utils.layer_tags import get_tag

    if viewer is None:
        return False

    # Resolve WHERE to show it through the entity registry — the one location authority — so navigation
    # follows the object's current place, not a stale bbox/frame baked into the ref at wiring time.
    ref = location_from_registry(ref)

    # ── `centre` now gates the FRAME too, because both are navigation ─────────────────────
    #
    # The frame step used to be ungated: every click on a plot moved the camera **and** jumped the
    # timepoint, whether or not the caller asked to be moved. That is the "abrupt navigation"
    # complaint — you click a point to see what it is, and the view you were reading leaves.
    #
    # Both are the same question ("take me there"), so they are answered by the same flag, and
    # `make_pickable` now passes the user's preference into it (default OFF — see
    # `central_manager.follow_selection`). A double-click asks explicitly and always navigates.
    #
    # The overlay is what makes this safe: the object is *shown* — outlined in place — without the
    # viewer being yanked to it. Before, marking it and going to it were the same act.
    try:
        # An object on frame 40 is not visible from frame 0 — so navigating means the frame too.
        if centre and ref.frame is not None and len(getattr(viewer, 'dims', []).point or ()) > 0:
            step = list(viewer.dims.current_step)
            step[0] = int(ref.frame)
            viewer.dims.current_step = tuple(step)
    except Exception as exc:
        debug_log('resolve_in_viewer: could not step to the frame', exc)

    if centre and ref.bbox:
        try:
            y0, x0, y1, x1 = ref.bbox
            cy, cx = (y0 + y1) / 2.0, (x0 + x1) / 2.0
            # The camera works in WORLD coordinates, so the pixel-space centre must be scaled + offset by the
            # source layer's scale/translate — otherwise a calibrated or upscaled layer centres on the wrong
            # spot (the same missing transform that mis-placed the overlay box). Fall back to pixel space
            # (scale 1, offset 0) when the source layer is not resolvable.
            sy = sx = 1.0
            ty = tx = 0.0
            layer = _source_layer_of(ref, viewer)
            if layer is not None:
                import numpy as _np
                sc = _np.asarray(getattr(layer, 'scale', (1.0, 1.0)), dtype=float).ravel()
                tr = _np.asarray(getattr(layer, 'translate', (0.0, 0.0)), dtype=float).ravel()
                if sc.size >= 2:
                    sy, sx = float(sc[-2]), float(sc[-1])
                if tr.size >= 2:
                    ty, tx = float(tr[-2]), float(tr[-1])
            viewer.camera.center = (0.0, cy * sy + ty, cx * sx + tx)
        except Exception as exc:
            debug_log('resolve_in_viewer: could not centre the camera', exc)

    # ── The highlight is an OVERLAY, not the labels layer's paint state ───────────────────
    #
    # This used to set `layer.selected_label = ref.object_id` + `show_selected_label = True`. That
    # is **napari's label-painting state**, which PyCAT's own paint tools write — so a click on a
    # plot silently changed the label the user was about to paint with, and hid every other object
    # in their mask. It also could not work for the cases brushing exists for: an object whose mask
    # is not open, a punctum (no layer carries punctum labels), or more than one object at once.
    #
    # See `pycat.utils.selection_overlay`.
    drew = False
    try:
        from pycat.utils.selection_overlay import show_selection
        drew = show_selection(viewer, [ref])
    except Exception as exc:
        debug_log('resolve_in_viewer: could not draw the selection overlay', exc)

    # Selecting the object's OWN layer is still useful — it is what makes the layer list follow the
    # user — but it no longer touches that layer's contents or its paint state.
    try:
        candidates, note = layers_for_ref(ref, viewer)
        if note:
            # Loud, not silent: the old behaviour is still available to legacy refs, but it is a
            # guess and it is recorded as one.
            debug_log(f'resolve_in_viewer: {note}', None)
        for layer in candidates:
            viewer.layers.selection = {layer}
            break
    except Exception as exc:
        debug_log('resolve_in_viewer: could not select the object', exc)

    return drew or bool(ref.bbox)


def resolve_offline(ref: ObjectRef, *, pad_px=8):
    """**Batch**: read the object's region out of the file it came from. **No session needed.**

    This is the one that makes *"select points in the resulting plot and see the bounded images"*
    work over a dataset that is not loaded — and it works **because the bbox travelled with the
    row.** Without it, the only route back is to re-run the analysis.

    Returns the cropped array, or None with a reason.
    """
    # Resolve WHERE through the registry first (it may even supply a bbox the ref lacked) — same location
    # authority as the interactive path, so a batch crop follows the current location, not a stale column.
    ref = location_from_registry(ref)
    if not ref.is_resolvable_offline():
        return None, (
            "This point cannot be turned back into an image. It carries "
            f"{'no source file' if not ref.source_path else 'no bounding box'}.\n\n"
            "**A bbox is what makes a batch plot clickable** — with it, the object's region can "
            "be read straight out of the file; without it, the only way back is to re-run the "
            "segmentation. ``regionprops`` provides it free at every segmentation site.")

    path = pathlib.Path(ref.source_path)
    if not path.exists():
        return None, f"The source file is gone: {path}"

    try:
        # ── Read the FILE directly. Do NOT go through file_io. ──────────────────
        #
        # file_io imports aicsimageio at module level, which is a heavy optional dependency —
        # and reading one crop out of one TIFF does not need it. A batch report that cannot open
        # a thumbnail because a CZI reader is missing is a batch report that does not work.
        import tifffile

        with tifffile.TiffFile(str(path)) as handle:
            series = handle.series[0]
            if ref.frame is not None and len(series.shape) >= 3:
                frame = series.asarray(key=int(ref.frame))
            else:
                frame = series.asarray()

        window = ref.crop_slice(pad_px=pad_px)
        return np.asarray(frame)[window], ''

    except Exception as exc:
        debug_log('resolve_offline: could not read the crop', exc)
        return None, f"Could not read {path.name}: {exc}"

#: The spellings a cell label answers to, in priority order. ``'cell label'`` — with a SPACE — is
#: what ``puncta_analysis_func`` writes (``feature_analysis_tools.py``); every other producer and
#: consumer in the codebase uses the underscore. See ``cell_label_column``.
CELL_LABEL_SPELLINGS = ('cell_label', 'cell label', 'parent_id')


def cell_label_column(df):
    """The column holding the cell label, whatever it is spelled. ``None`` if there is not one.

    **One producer disagrees with the whole codebase.** ``puncta_analysis_func`` writes
    ``'cell label'`` with a space; everything else writes ``cell_label``. So a plain
    ``'cell_label' in df.columns`` **silently misses on the one table it exists for** — which is
    exactly what happened twice:

    * ``ObjectRef.from_row`` looked up ``cell_label`` and every punctum ref carried
      ``parent_id=None`` (fixed 1.6.74, by accepting both);
    * ``analysis_plots`` gated a per-cell grouping on it, so multi-cell puncta data was drawn as ONE
      line connecting points *across* cells — a pooled series rendered as a trajectory — instead of
      per-cell traces plus a mean (fixed 1.6.90, by calling this).

    The column is **not renamed**, deliberately: it is user-visible in results tables and CSVs, and
    renaming it would silently change what those files say. Accepting both spellings fixes the
    readers without touching anything a user has already saved.

    This lives in one place so the next reader does not have to rediscover the split.
    """
    if df is None:
        return None
    try:
        columns = df.columns
    except AttributeError:
        return None
    for name in CELL_LABEL_SPELLINGS:
        if name in columns:
            return name
    return None


def normalise_bbox_columns(df):
    """**Rename skimage's ``bbox-0..bbox-3`` to the ``bbox_y0..bbox_x1`` an ObjectRef reads.**

    ``regionprops_table(..., properties=(..., 'bbox'))`` expands the bounding box into four
    columns named ``bbox-0``, ``bbox-1``, ``bbox-2``, ``bbox-3`` — in ``(min_row, min_col,
    max_row, max_col)`` order, which is ``(y0, x0, y1, x1)``.

    Those hyphenated names are awkward in a DataFrame (``df.bbox-0`` is a subtraction), and they
    do not survive a round-trip through a user's spreadsheet intact in anyone's memory. So they
    are renamed once, here, at the point they are produced.

    **Idempotent** — a table that has already been normalised, or never had a bbox, passes
    through untouched.
    """
    if df is None or not hasattr(df, 'columns'):
        return df

    mapping = {'bbox-0': 'bbox_y0', 'bbox-1': 'bbox_x0',
               'bbox-2': 'bbox_y1', 'bbox-3': 'bbox_x1'}
    present = {k: v for k, v in mapping.items() if k in df.columns}

    if present:
        try:
            df = df.rename(columns=present)
        except Exception as exc:
            debug_log('normalise_bbox_columns: the rename failed', exc)

    return df
