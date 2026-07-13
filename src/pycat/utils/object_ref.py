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

        return cls(
            object_id=_get('object_id', 'label', 'condensate_label', 'cell_label'),
            frame=_get('frame', 't', 'z'),
            bbox=bbox,
            source_path=(str(source_path) if source_path
                         else (str(row['source_path']) if 'source_path' in row
                               and row['source_path'] else None)),
            track_id=_get('track_id'),
            parent_id=_get('cell_label', 'parent_id'),
            tags=dict(tags) if tags else None,
        )


def refs_from_dataframe(df, *, source_path=None, tags=None):
    """One ``ObjectRef`` per row, in row order. **This is what a plot attaches to its points.**"""
    refs = []
    for _, row in df.iterrows():
        try:
            refs.append(ObjectRef.from_row(row, source_path=source_path, tags=tags))
        except Exception as exc:
            debug_log('refs_from_dataframe: could not build a ref for a row', exc)
            refs.append(ObjectRef())
    return refs


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

def resolve_in_viewer(ref: ObjectRef, viewer, *, centre=True, pad_px=8):
    """**Interactive**: find the object in a live layer and reveal it.

    Returns True if it landed somewhere. This is what the existing VPT hub does, generalised off
    ``track_id`` so that any object identity works.
    """
    from pycat.utils.layer_tags import get_tag

    if viewer is None:
        return False

    try:
        # Move to the right frame first — an object on frame 40 is not visible from frame 0.
        if ref.frame is not None and len(getattr(viewer, 'dims', []).point or ()) > 0:
            step = list(viewer.dims.current_step)
            step[0] = int(ref.frame)
            viewer.dims.current_step = tuple(step)
    except Exception as exc:
        debug_log('resolve_in_viewer: could not step to the frame', exc)

    if centre and ref.bbox:
        try:
            y0, x0, y1, x1 = ref.bbox
            viewer.camera.center = (0.0, (y0 + y1) / 2.0, (x0 + x1) / 2.0)
        except Exception as exc:
            debug_log('resolve_in_viewer: could not centre the camera', exc)

    # Select the labels layer that holds this object, if one is open.
    try:
        for layer in viewer.layers:
            if get_tag(layer, 'role') in ('labels', 'mask'):
                viewer.layers.selection = {layer}
                if ref.object_id is not None and hasattr(layer, 'selected_label'):
                    layer.selected_label = int(ref.object_id)
                    layer.show_selected_label = True
                return True
    except Exception as exc:
        debug_log('resolve_in_viewer: could not select the object', exc)

    return bool(ref.bbox)


def resolve_offline(ref: ObjectRef, *, pad_px=8):
    """**Batch**: read the object's region out of the file it came from. **No session needed.**

    This is the one that makes *"select points in the resulting plot and see the bounded images"*
    work over a dataset that is not loaded — and it works **because the bbox travelled with the
    row.** Without it, the only route back is to re-run the analysis.

    Returns the cropped array, or None with a reason.
    """
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
