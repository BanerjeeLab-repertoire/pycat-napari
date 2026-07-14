"""Dual pixel / micron coordinate readout for the napari status bar.

PyCAT sets each image layer's ``scale`` to the physical pixel size (µm/px), so
napari's built-in status bar reports the cursor position in **world units
(microns)** — the raw **pixel index** is hidden. Pixel indices are what most of
the analysis actually runs in (blob sigma in px, linking distances, template
windows, cross-referencing FIJI, debugging detection), so showing only microns
is a real source of confusion.

This module installs a mouse-move callback that writes **both** coordinates to
the status bar, e.g.::

    px (r=362, c=483)  |  µm (y=242.5, x=323.6)  |  Bead Detections = 171

It reads the pixel index via ``layer.world_to_data`` on the top visible layer
under the cursor, and the world (µm) position from ``viewer.cursor.position``.
When the active layer has no non-trivial scale (scale == 1), px and µm are the
same and only one is shown to avoid clutter.
"""

from __future__ import annotations

import numpy as np


def _fmt(v, nd=0):
    try:
        if nd == 0:
            return f"{int(round(float(v)))}"
        return f"{float(v):.{nd}f}"
    except Exception:
        return "?"


def _top_data_layer(viewer):
    """Return the top-most visible layer that has data with a shape (image /
    labels), preferring the selected layer, else the highest visible one."""
    # Prefer the active (selected) layer if it is visible and has data.
    active = getattr(viewer.layers, "selection", None)
    active = getattr(active, "active", None) if active is not None else None
    if active is not None and getattr(active, "visible", False) \
            and hasattr(active, "data") and getattr(active, "ndim", 0) >= 2:
        return active
    for layer in reversed(list(viewer.layers)):
        if getattr(layer, "visible", False) and hasattr(layer, "data") \
                and getattr(layer, "ndim", 0) >= 2:
            return layer
    return None


def _coordinate_status(viewer):
    """Build the 'px … | µm …' status string for the current cursor position.

    Returns None if a coordinate can't be resolved (so the caller can leave the
    default napari status in place)."""
    try:
        world = viewer.cursor.position  # world coords (µm here), full ndim
    except Exception:
        return None
    layer = _top_data_layer(viewer)
    if layer is None or world is None:
        return None

    # World (µm) — take the last two axes as (y, x) for display.
    world = np.asarray(world, dtype=float)

    # Pixel index via the layer's own world->data transform (handles scale +
    # translate + any affine correctly, per-layer).
    try:
        data_pt = np.asarray(layer.world_to_data(world), dtype=float)
    except Exception:
        data_pt = None

    # Determine whether a real (non-identity) scale is in play on the display
    # axes; if not, px == µm and we only show one form.
    scale = np.asarray(getattr(layer, "scale", np.ones(len(world))), dtype=float)
    has_scale = bool(np.any(np.abs(scale[-2:] - 1.0) > 1e-9)) if scale.size >= 2 else False

    # Bounds check on the two display axes so we only report when actually over
    # the image (napari reports a coord even off-image; px index would be OOB).
    if data_pt is not None and getattr(layer, "data", None) is not None:
        try:
            shp = layer.data.shape
            r, c = data_pt[-2], data_pt[-1]
            H, W = shp[-2], shp[-1]
            if not (0 <= r < H and 0 <= c < W):
                return None
        except Exception:
            pass

    parts = []
    if data_pt is not None:
        parts.append(f"px (r={_fmt(data_pt[-2])}, c={_fmt(data_pt[-1])})")
    if has_scale:
        parts.append(f"\u00b5m (y={_fmt(world[-2], 1)}, x={_fmt(world[-1], 1)})")
    if not parts:
        return None

    # Value under cursor, when resolvable (nice for QC / thresholding).
    try:
        if data_pt is not None:
            idx = tuple(int(round(v)) for v in data_pt)
            val = np.asarray(layer.data)[idx]
            parts.append(f"{layer.name} = {_fmt(val, 0) if np.isscalar(val) or val.ndim == 0 else '…'}")
    except Exception:
        pass

    return "  |  ".join(parts)


def install_coordinate_readout(viewer):
    """Attach the dual px/µm readout to *viewer*'s status bar.

    Safe to call once at startup. Never raises into the caller — a failure just
    leaves napari's default status behaviour untouched.
    """
    # ── TWO WRITERS, ONE STATUS BAR — and that is the flicker ───────────────────
    #
    # This appended a ``mouse_move_callbacks`` handler that wrote ``v.status``. **But napari writes
    # ``v.status`` on the same event**, from its own handler — so both fire, and **whichever runs
    # last wins.** The order is not guaranteed, so the bar alternates between the two strings as
    # the mouse moves.
    #
    # *That is the flicker Gable reported, and the overlap: two different strings rendered into the
    # same widget, neither of them cleanly.*
    #
    # **Racing napari's writer cannot be won.** The fix is to be the *only* writer: napari SOURCES
    # the status string from the active layer's ``get_status()``. Wrapping that means there is one
    # writer, one string, and no order to depend on.
    #
    # ``object.__setattr__`` because a napari Layer is a pydantic-adjacent object and a plain
    # ``setattr`` on a method is rejected — *the same trap that silently killed the layer-tag hook.*
    def _wrap_get_status(layer):
        original = getattr(layer, 'get_status', None)
        if original is None or getattr(layer, '_pycat_status_wrapped', False):
            return

        def _get_status(position=None, *, view_direction=None, dims_displayed=None, world=False):
            try:
                dual = _coordinate_status(viewer)
                if dual:
                    return dual
            except Exception:
                pass
            # Any failure falls back to napari's own string — the readout is a convenience, and it
            # must never be the reason the status bar stops working.
            return original(position, view_direction=view_direction,
                            dims_displayed=dims_displayed, world=world)

        object.__setattr__(layer, 'get_status', _get_status)
        object.__setattr__(layer, '_pycat_status_wrapped', True)

    def _on_layer_added(event):
        try:
            _wrap_get_status(event.value)
        except Exception:
            pass

    try:
        # Wrap every layer that is already here, and every one added later.
        for existing in list(getattr(viewer, 'layers', [])):
            _wrap_get_status(existing)

        viewer.layers.events.inserted.connect(_on_layer_added)
    except Exception:
        # Never let the readout break interaction; napari's default status stays in place.
        pass
