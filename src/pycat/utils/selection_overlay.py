"""**Show the selection without taking anything over.**

── Why not `selected_label` ────────────────────────────────────────────────────────────────

Brushing used to highlight an object by setting ``layer.selected_label = ref.object_id`` and
``show_selected_label = True`` on the analytical labels layer. That is not a highlight — it is
**napari's label-painting state**, and PyCAT's own paint tools write it (``ui_modules.py``,
``invitro_fluor_ui.py`` both set ``selected_label = 1`` to pick the brush's label). So clicking a
point on a plot silently changed which label the user was about to paint with, and
``show_selected_label`` hid every other object in their mask. *Borrowing a widget's state to mean
something else is how two features quietly break each other.*

It also cannot work for the objects that need it most:

* an object whose mask is **not open** (a batch plot, a closed layer) has no ``selected_label`` to
  set — and that is precisely the case brushing exists to serve;
* a **punctum** has no layer whose label values are punctum ids (see increment 2 —
  "Cell Labeled Puncta Mask" is painted with *cell* labels), so the highlight would land on the
  wrong object;
* **several** selected objects cannot be shown at all: ``selected_label`` holds one integer.

So the selection gets its own layers: a ``Shapes`` rectangle for the bbox and a ``Points`` marker
for the centre. They are **reused, never accumulated** — the old resolver added an
``object <N>`` image layer per distinct object clicked, so exploring a plot slowly filled the layer
list with crops.

── The overlay must carry the SOURCE layer's scale ─────────────────────────────────────────

A bbox is measured in the **pixel grid of the layer the object came from**. The overlay layer,
created at the default scale 1.0, would then draw that box in raw pixel indices while the source
image sits at its own ``scale`` — so on any calibrated layer (PyCAT sets ``layer.scale =
pixel_size``) or upscaled layer the box lands in the wrong place, and the camera (which works in
WORLD coordinates) centres on the wrong spot. Both symptoms — "doesn't highlight it" and "doesn't
zoom to it" — are the one missing transform. So the overlay resolves the object's source layer (by
the recorded ``pycat_layer_id``) and is drawn with **that layer's scale and translate**; when the
source layer is not open, we report the honest miss rather than draw an unverified box.

The overlay is display-only: it owns no analysis state, nothing reads it back, and deleting it costs
nothing.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log

BBOX_LAYER = 'Selection'
CENTRE_LAYER = 'Selection centre'

_EDGE = '#ff8c00'          # the same orange the plot's selection overlay uses

#: Which image layers PyCAT temporarily hid during a selection, per viewer (``id(viewer) -> {names}``). We
#: hide only VISIBLE mismatched-grid image layers, so restoring = turn these back on; a layer the user had
#: already hidden was never recorded here, so the restore never overrides their arrangement.
_HIDDEN_BY_PYCAT: dict = {}


def _rect_for(ref, ndim):
    """The bbox as a rectangle, in the layer's coordinate space. ``None`` if it has no bbox."""
    if not ref.bbox or len(ref.bbox) != 4:
        return None
    y0, x0, y1, x1 = (float(v) for v in ref.bbox)
    corners = [(y0, x0), (y0, x1), (y1, x1), (y1, x0)]

    # A 3-D+ viewer needs the leading coordinate or the rectangle floats across every slice. A ref
    # with no frame is genuinely 2-D — leave it on whatever slice is showing rather than guess 0.
    if ndim >= 3 and ref.frame is not None:
        lead = [float(int(ref.frame))] * (ndim - 2)
        return np.array([lead + [y, x] for y, x in corners], dtype=float)
    return np.array(corners, dtype=float)


def _centre_for(ref, ndim):
    if not ref.bbox or len(ref.bbox) != 4:
        return None
    y0, x0, y1, x1 = (float(v) for v in ref.bbox)
    point = [(y0 + y1) / 2.0, (x0 + x1) / 2.0]
    if ndim >= 3 and ref.frame is not None:
        return np.array([float(int(ref.frame))] * (ndim - 2) + point, dtype=float)
    return np.array(point, dtype=float)


def _viewer_ndim(viewer):
    try:
        return int(getattr(viewer.dims, 'ndim', 2) or 2)
    except Exception:
        return 2


def _resolve_source_layer(refs, viewer):
    """The layer these refs were measured on, matched by ``pycat_layer_id``. Returns ``(layer, missing)``:

    * ``(layer, False)`` — the recorded source layer is open; draw the overlay in ITS coordinate space.
    * ``(None, False)`` — the refs record no source (legacy / genuinely 2-D unit-scale); draw unscaled.
    * ``(None, True)`` — the refs DO record a source, but it is not open: the honest answer is "not here",
      so draw nothing and report rather than place a box at a scale we cannot verify.
    """
    ids = {getattr(r, 'source_layer_id', None) for r in refs}
    ids.discard(None)
    if not ids:
        return None, False
    for layer in getattr(viewer, 'layers', []) or []:
        if (getattr(layer, 'metadata', None) or {}).get('pycat_layer_id') in ids:
            return layer, False
    return None, True


def _overlay_transform(layer, ndim):
    """The ``(scale, translate)`` to draw the overlay with so it matches ``layer``, reconciled to the
    overlay's ``ndim``. A 2-D source layer in a 3-D viewer keeps unit scale/0 offset on the leading axes and
    the layer's on Y/X. ``(None, None)`` when there is no layer (draw at unit scale, as before)."""
    if layer is None:
        return None, None
    try:
        sc = np.asarray(getattr(layer, 'scale', None), dtype=float).ravel()
        tr = np.asarray(getattr(layer, 'translate', None), dtype=float).ravel()
    except Exception:      # broad-ok: ui_cleanup — an unreadable layer transform falls back to unit scale
        return None, None
    if sc.size == 0:
        return None, None

    def _fit(values, fill):
        if values.size == ndim:
            return values.tolist()
        if values.size >= 2:                      # keep the trailing (Y, X), pad leading axes with `fill`
            tail = values[-2:].tolist()
            return [fill] * (ndim - 2) + tail
        return None

    scale = _fit(sc, 1.0)
    translate = _fit(tr if tr.size else np.zeros_like(sc), 0.0)
    return scale, translate


def _replace_layer(viewer, name, add, data, scale=None, translate=None):
    """Re-use the overlay layer if it is open, else make it. **Never accumulate.**"""
    existing = None
    try:
        if name in viewer.layers:
            existing = viewer.layers[name]
    except Exception:
        existing = None

    if existing is not None:
        try:
            existing.data = data
            if scale is not None:
                existing.scale = scale
            if translate is not None:
                existing.translate = translate
            existing.visible = True
            return existing
        except Exception as exc:
            # A shape count/ndim change can be rejected; rebuild rather than leave a stale box.
            debug_log('selection overlay: could not update the layer, rebuilding', exc)
            try:
                viewer.layers.remove(name)
            except Exception:
                pass

    kwargs = {'name': name}
    if scale is not None:
        kwargs['scale'] = scale
    if translate is not None:
        kwargs['translate'] = translate
    return add(data, **kwargs)


def _spatial_shape(layer):
    """The trailing (Y, X) pixel-grid shape of a layer's data, or ``None``. Compared to decide a grid
    mismatch — NOT the world extent (correctly-scaled layers share a world extent while differing in grid)."""
    try:
        shape = tuple(int(s) for s in getattr(layer.data, 'shape', ()) or ())
    except Exception:      # broad-ok: ui_cleanup — an unreadable shape → unknown grid, skip the hide check
        return None
    return shape[-2:] if len(shape) >= 2 else None


def _is_image(layer):
    """True for a napari Image layer (its ``_type_string`` is ``'image'``); labels/points/shapes/tracks and
    the overlay layers themselves are never hidden."""
    return getattr(layer, '_type_string', '') == 'image'


def _hide_mismatched_images(viewer, target):
    """Hide VISIBLE image layers whose pixel grid differs from ``target``'s, for the duration of the
    selection — so the canvas is not rendering two images at different resolutions under one highlight.
    Records what it hid (per viewer) so :func:`_restore_hidden` turns back on exactly those. Never hides the
    target, never a non-image layer, and never everything: if hiding would leave no visible image, hides
    nothing."""
    if target is None:
        return
    tshape = _spatial_shape(target)
    if tshape is None:
        return
    try:
        images = [l for l in viewer.layers if _is_image(l)]
    except Exception:      # broad-ok: ui_cleanup — can't enumerate layers → leave the canvas untouched
        return

    to_hide = [l for l in images
               if l is not target and getattr(l, 'visible', True)
               and _spatial_shape(l) is not None and _spatial_shape(l) != tshape]
    if not to_hide:
        return
    # Never leave the canvas with no visible image.
    remaining = [l for l in images if getattr(l, 'visible', True) and l not in to_hide]
    if not remaining:
        return

    hidden = _HIDDEN_BY_PYCAT.setdefault(id(viewer), set())
    for layer in to_hide:
        try:
            layer.visible = False
            hidden.add(getattr(layer, 'name', None))
        except Exception as exc:      # broad-ok: ui_cleanup — a layer that won't hide must not break brushing
            debug_log('selection overlay: could not hide a mismatched layer', exc)


def _restore_hidden(viewer):
    """Turn back on exactly the layers PyCAT hid for the selection (by name); a layer the user had already
    hidden was never recorded, so their arrangement is left as they set it."""
    names = _HIDDEN_BY_PYCAT.pop(id(viewer), None)
    if not names:
        return
    for name in names:
        try:
            if name in viewer.layers:
                viewer.layers[name].visible = True
        except Exception as exc:      # broad-ok: ui_cleanup — a layer that won't restore must not break clear
            debug_log(f'selection overlay: could not restore {name}', exc)


def show_selection(viewer, refs):
    """Draw ``refs`` as the selection overlay, in the SOURCE layer's coordinate space. Returns True if
    anything was drawn.

    Several refs are fine — the aggregate case (a row summarising many objects) is a list of boxes, not a lie
    about one of them. When the refs record a source layer that is not open, nothing is drawn (an unverified
    box in the wrong place is worse than no box)."""
    if viewer is None:
        return False
    refs = [r for r in (refs or []) if r is not None]
    if not refs:
        clear_selection(viewer)
        return False

    layer, missing = _resolve_source_layer(refs, viewer)
    if missing:
        debug_log('selection overlay: the object\'s source layer is not open — not drawing', None)
        clear_selection(viewer)
        return False

    ndim = _viewer_ndim(viewer)
    scale, translate = _overlay_transform(layer, ndim)

    rects = [r for r in (_rect_for(ref, ndim) for ref in refs) if r is not None]
    centres = [c for c in (_centre_for(ref, ndim) for ref in refs) if c is not None]
    if not rects:
        clear_selection(viewer)
        return False

    try:
        _replace_layer(viewer, BBOX_LAYER, viewer.add_shapes, rects, scale=scale, translate=translate)
        box = viewer.layers[BBOX_LAYER]
        box.shape_type = 'rectangle'
        box.edge_color = _EDGE
        box.face_color = 'transparent'
        box.edge_width = 2
    except Exception as exc:
        debug_log('selection overlay: could not draw the bbox', exc)
        return False

    try:
        if centres:
            _replace_layer(viewer, CENTRE_LAYER, viewer.add_points, np.vstack(centres),
                           scale=scale, translate=translate)
            centre = viewer.layers[CENTRE_LAYER]
            centre.face_color = _EDGE
            centre.border_color = _EDGE
            centre.size = 6
    except Exception as exc:
        debug_log('selection overlay: could not draw the centre', exc)

    # Under the highlight, don't render a mismatched-resolution image beside the target. Restore first so a
    # move to a different-dimension target re-evaluates from the user's real arrangement, not a stale one.
    _restore_hidden(viewer)
    _hide_mismatched_images(viewer, layer)

    return True


def clear_selection(viewer):
    """Hide the overlay and restore any image layers PyCAT hid. **Escape means nothing is selected, not
    "nothing happened".**"""
    for name in (BBOX_LAYER, CENTRE_LAYER):
        try:
            if name in viewer.layers:
                viewer.layers[name].visible = False
        except Exception as exc:
            debug_log(f'selection overlay: could not hide {name}', exc)
    _restore_hidden(viewer)
