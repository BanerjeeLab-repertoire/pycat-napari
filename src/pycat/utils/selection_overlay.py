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

The overlay is display-only: it owns no analysis state, nothing reads it back, and deleting it costs
nothing.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log

BBOX_LAYER = 'Selection'
CENTRE_LAYER = 'Selection centre'

_EDGE = '#ff8c00'          # the same orange the plot's selection overlay uses


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


def _replace_layer(viewer, name, add, data, scale=None):
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
    return add(data, **kwargs)


def show_selection(viewer, refs):
    """Draw ``refs`` as the selection overlay. Returns True if anything was drawn.

    Several refs are fine — the aggregate case (a row summarising many objects) is a list of boxes,
    not a lie about one of them.
    """
    if viewer is None:
        return False
    refs = [r for r in (refs or []) if r is not None]
    ndim = _viewer_ndim(viewer)

    rects = [r for r in (_rect_for(ref, ndim) for ref in refs) if r is not None]
    centres = [c for c in (_centre_for(ref, ndim) for ref in refs) if c is not None]
    if not rects:
        clear_selection(viewer)
        return False

    try:
        _replace_layer(viewer, BBOX_LAYER, viewer.add_shapes, rects)
        layer = viewer.layers[BBOX_LAYER]
        layer.shape_type = 'rectangle'
        layer.edge_color = _EDGE
        layer.face_color = 'transparent'
        layer.edge_width = 2
    except Exception as exc:
        debug_log('selection overlay: could not draw the bbox', exc)
        return False

    try:
        if centres:
            _replace_layer(viewer, CENTRE_LAYER, viewer.add_points, np.vstack(centres))
            centre = viewer.layers[CENTRE_LAYER]
            centre.face_color = _EDGE
            centre.border_color = _EDGE
            centre.size = 6
    except Exception as exc:
        debug_log('selection overlay: could not draw the centre', exc)

    return True


def clear_selection(viewer):
    """Hide the overlay. **Escape means nothing is selected, not "nothing happened".**"""
    for name in (BBOX_LAYER, CENTRE_LAYER):
        try:
            if name in viewer.layers:
                viewer.layers[name].visible = False
        except Exception as exc:
            debug_log(f'selection overlay: could not hide {name}', exc)
