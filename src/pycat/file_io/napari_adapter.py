"""
**Napari display. Not file I/O.**

The camera, the scale bar, and the per-layer scale alignment. **None of it touches a file** — these
four functions read the viewer and write the viewer, and they were sitting in the middle of a
3,108-line ``FileIOClass`` whose other 31 methods open, route, tag and save images.

── Why this is the first thing to come out ──────────────────────────────────────────────

The audit's complaint about ``FileIOClass`` is that it does **eighteen things**: dialogs, storage
diagnostics, reader selection, metadata parsing, image-vs-mask classification, TIFF internals, lazy
wrappers, layer naming, channel colours, **napari camera fitting**, **scale bars**, data-repository
updates, batch recording, tag restoration, stack materialisation, save/export, session clearing.

*"This makes unit testing difficult and increases the chance that UI edits affect scientific reading
behaviour."*

These four are the cleanest cut: they depend on **``viewer`` and ``central_manager`` and nothing
else** — no file handle, no reader, no path, no data repository. They come out as plain functions
with no loss and no risk, and what is left behind is 237 lines smaller and one responsibility
lighter.

*A 3,108-line class is not split in one move. It is split in a sequence of moves each of which is
provably safe, and this is the first.*
"""

from __future__ import annotations

import os

# ── The toggle lives HERE, with the layers it toggles ────────────────────────────────────
#
# It was a module-level constant in `file_io.py`, and importing it back from there would make this
# module import its former host — **a cycle.** *(And an unverifiable one: the sandbox has no Qt, so
# `import napari_adapter` dies on PyQt5 before it ever reaches the circular import. A cycle I cannot
# test is a cycle I will not ship.)*
#
# The diameter-annotation layers are created **on demand** by the measure widget the first time the
# user measures, so a session that never measures diameters is not cluttered with them. Flip this to
# True to revert to the eager behaviour if the on-demand path ever misbehaves.
EAGER_DIAMETER_LAYERS = False

def _is_calibrated(central_manager, px):
    """**Is this pixel size a real calibration, or the 1.0 fallback?**

    ``abs(px - 1.0) > 1e-9`` was the test, in **both** scale-bar functions — on the reasoning that
    a real microscope pixel size is essentially never exactly 1.0 µm/px.

    ***"Essentially never" is not never.*** A downsampled, low-magnification, derived or synthetic
    image can have a **genuine** 1.0 µm/px — and it would be shown a **"px" scale bar**, silently
    lying about what the bar measures.

    *Same sentinel fixed in ``_finalise_stack_load`` (1.6.15) and ``_tag_loaded_layer`` (1.6.23).
    These were the third and fourth copies.*

    The repository records **where the number came from** — ask it. Fall back to the old guess only
    when no provenance was recorded, because a wrong `True` shows a µm bar on an uncalibrated image,
    while a wrong `False` shows a px bar on a calibrated one: *both wrong, neither catastrophic, and
    the guess is all the old code ever had.*
    """
    try:
        dr = central_manager.active_data_class.data_repository
        if 'pixel_size_from_metadata' in dr:
            return bool(dr['pixel_size_from_metadata'])
    except Exception:
        pass
    return abs(float(px) - 1.0) > 1e-9


def _align_layer_scales(viewer, central_manager):
    """Give every unit-scale layer the same physical extent as the primary
    scaled image layer, so all layers overlay and the µm scale bar stays
    consistent. Layers that already carry a meaningful (non-unit) scale — the
    reference itself, or an explicitly-scaled upscaled layer — are left alone.
    Image/Labels layers are aligned by field of view (handles upscaled masks);
    Shapes/Points overlays inherit the reference's per-pixel scale."""
    import numpy as np
    try:
        import napari.layers as _nl
    except Exception:
        return
    try:
        ref = None
        for l in viewer.layers:
            if isinstance(l, _nl.Image):
                rs = np.asarray(l.scale, float)
                if (rs.size >= 2 and np.all(np.isfinite(rs))
                        and np.any(np.abs(rs[-2:] - 1.0) > 1e-9)):
                    ref = l
                    break
        if ref is None:
            return
        ref_scale = np.asarray(ref.scale, float)
        ref_shape = np.asarray(getattr(ref, 'data').shape, float)
        if ref_shape.size < 2:
            return
        ref_fov = ref_shape[-2:] * ref_scale[-2:]
        for l in viewer.layers:
            if l is ref:
                continue
            try:
                sc = np.asarray(l.scale, float)
                if sc.size >= 2 and np.any(np.abs(sc[-2:] - 1.0) > 1e-9):
                    continue   # already scaled — don't override
                if isinstance(l, (_nl.Shapes, _nl.Points)):
                    new_yx = ref_scale[-2:]     # pixel-coordinate overlay
                elif (isinstance(l, _nl.Image) and getattr(l, 'rgb', False)):
                    # RGB overlays (e.g. the side-by-side "Overlay Image",
                    # which is (H, 2W, 3)) are built at the SAME per-pixel
                    # resolution as the reference — they just have more pixels
                    # (two panels wide). Fit them to the reference field of
                    # view would compress the extra width into one image's
                    # worth of world units (the "overlay looks squished in X"
                    # symptom). Instead give them the reference's per-pixel
                    # scale so each overlay pixel matches a reference pixel.
                    new_yx = ref_scale[-2:]
                else:
                    shp = np.asarray(getattr(l, 'data').shape, float)
                    if shp.size < 2:
                        continue
                    spatial_shape = shp[-2:]
                    new_yx = ref_fov / spatial_shape
                if not (np.all(np.isfinite(new_yx)) and np.all(new_yx > 0)):
                    continue
                new_scale = list(np.asarray(l.scale, float))
                new_scale[-2] = float(new_yx[0]); new_scale[-1] = float(new_yx[1])
                l.scale = new_scale
            except Exception:
                continue
    except Exception:
        pass

def _enable_auto_scale_bar(viewer, central_manager, image_layer=None):
    """
    Enable napari's scale bar for a freshly-loaded image.

    - Real metadata pixel size  → µm bar: sets ``layer.scale`` (a display-only
      transform that never touches ``layer.data`` or any calculation).
    - No metadata               → pixel bar (scale left at 1).

    NEVER sets ``layer.units`` — that is the confirmed cause of the black
    canvas on lazy 3D stacks. The unit label comes from ``scale_bar.unit``.
    """
    try:
        import napari.layers as _nl
        dr = central_manager.active_data_class.data_repository
        from_meta = bool(dr.get('pixel_size_from_metadata', False))
        mpx_sq = dr.get('microns_per_pixel_sq', 1)
        if image_layer is None:
            imgs = [l for l in viewer.layers if isinstance(l, _nl.Image)]
            if not imgs:
                return
            image_layer = imgs[-1]
        sb = viewer.scale_bar
        sb.visible = True
        # Show a µm bar whenever a real pixel size is known — from metadata
        # OR entered by the user (e.g. via the pixel-size gate). Only fall
        # back to pixels when no valid scale exists. A non-finite or non-
        # positive scale would make the world extent degenerate and, on
        # reset_view (the Home button), drive the camera zoom to NaN, which
        # crashes napari's scale-bar overlay — so we validate strictly.
        import numpy as _np
        try:
            mpx_sq = float(mpx_sq)
        except (TypeError, ValueError):
            mpx_sq = 1.0
        px = _np.sqrt(mpx_sq) if (_np.isfinite(mpx_sq) and mpx_sq > 0) else 0.0
        if _np.isfinite(px) and px > 0 and _is_calibrated(central_manager, px):
            sc = [float(s) for s in image_layer.scale]
            if all(_np.isfinite(s) and s > 0 for s in sc[:-2]) or len(sc) <= 2:
                sc[-1] = px; sc[-2] = px
                image_layer.scale = sc
            label = 'um'
        else:
            label = 'px'
        try:
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter('ignore', FutureWarning)
                sb.unit = label
        except Exception:
            pass
        # Now that the reference image carries a µm scale, bring any layers
        # that were added earlier (e.g. the diameter overlays) into alignment.
        if label == 'um':
            _align_layer_scales(viewer, central_manager)
    except Exception as e:
        print(f"[PyCAT] auto scale bar skipped: {e}")

def _update_scale_bar_for_active_layer(viewer, central_manager):
    """Update the napari scale bar to reflect the physical pixel size of
    whichever Image layer is currently active (top of the selection).

    This fires on viewer.layers.selection.events.changed so switching to
    an upscaled layer (scale = source_scale / 2) shows the correct bar.

    Scale bar logic:
      • layer.scale[-1] is the physical size of one pixel in µm.
      • The bar length in world units is unchanged — what changes is the
        label. If the upscaled layer has scale 0.085 µm/px and the bar
        spans 588 pixels, it correctly shows ~50 µm, the same FOV as the
        original 294-px image at 0.17 µm/px. So the bar length is right;
        we just need to make sure the unit is 'um' when any valid µm scale
        is set on the active layer.
    """
    try:
        import napari.layers as _nl
        import numpy as _np
        import warnings as _w
        # Find the topmost selected Image layer
        sel = [l for l in viewer.layers.selection
               if isinstance(l, _nl.Image)]
        if not sel:
            return
        # napari puts the most-recently-selected layer last in the set
        active = sel[-1]
        sc = [float(v) for v in active.scale]
        if not sc:
            return
        px = sc[-1]   # µm per pixel on the active layer
        sb = viewer.scale_bar
        if _np.isfinite(px) and px > 0 and _is_calibrated(central_manager, px):
            # Valid µm scale — show µm bar
            with _w.catch_warnings():
                _w.simplefilter('ignore', FutureWarning)
                sb.unit = 'um'
        else:
            # Unit or pixel scale — show px bar
            with _w.catch_warnings():
                _w.simplefilter('ignore', FutureWarning)
                sb.unit = 'px'
    except Exception:
        pass

def _fit_view_to_layer(viewer, central_manager, layer=None, margin=0.9, attempt=0):
    """Fit the napari camera to an image layer, mirroring the (working)
    Home button exactly.

    The Home button reads ``layer.extent.world`` — the transform-aware extent
    napari actually renders with — and it fits correctly. An earlier version
    of this auto-fit recomputed ``shape × scale`` by hand, which can disagree
    with the real extent right after load: the µm/px scale was just assigned
    and napari's transform/extent cache may not have caught up at the moment
    the deferred fit fires, so the image opened tiny even though pressing Home
    afterwards fit it fine. Using ``extent.world`` here makes auto-fit behave
    identically to Home. Retries with growing delays until the canvas has a
    real size (it can be 0 while the dock is still laying out after load).
    """
    try:
        import numpy as np
        import napari.layers as _nl

        if layer is None:
            imgs = [l for l in viewer.layers if isinstance(l, _nl.Image)]
            if not imgs:
                return
            layer = imgs[-1]

        cw = ch = None
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.simplefilter('ignore', FutureWarning)
            for accessor in ('_qt_viewer', 'qt_viewer'):
                try:
                    sz = getattr(viewer.window, accessor).canvas.size
                    cw, ch = float(sz[0]), float(sz[1])
                    break
                except Exception:
                    continue

        # Canvas not laid out yet → retry shortly (up to ~6 attempts).
        if (not cw or not ch or cw <= 1 or ch <= 1) and attempt < 6:
            from PyQt5.QtCore import QTimer as _QT
            _QT.singleShot(120 * (attempt + 1),
                           lambda: _fit_view_to_layer(viewer, central_manager, layer, margin, attempt + 1))
            return

        # Transform-aware world extent — same source of truth as Home.
        ext = np.asarray(layer.extent.world)     # (2, ndim): [mins, maxs]
        mins, maxs = ext[0], ext[1]
        nd = viewer.dims.ndisplay
        dims = list(viewer.dims.displayed)[-nd:]
        sizes = [float(maxs[d] - mins[d]) for d in dims]

        center = (mins + maxs) / 2.0
        viewer.camera.center = tuple(float(center[d]) for d in dims)

        if nd == 2 and cw and ch and all(s > 0 for s in sizes):
            # displayed dims are [y, x]; canvas is (width=x, height=y).
            _z = min(ch / sizes[0], cw / sizes[1]) * margin
            _z_before = float(viewer.camera.zoom)
            viewer.camera.zoom = _z
            if os.environ.get('PYCAT_DEBUG'):
                print(f"[PyCAT fit] layer='{layer.name}' extent_world_size(yx)={sizes} "
                      f"canvas(w,h)=({cw},{ch}) zoom {_z_before:.4f} -> {_z:.4f} "
                      f"(attempt {attempt})")
                from PyQt5.QtCore import QTimer as _QTd
                _QTd.singleShot(600, lambda: print(
                    f"[PyCAT fit] zoom 600ms later = {float(viewer.camera.zoom):.4f} "
                    f"(if changed, something reset it)"))
        else:
            viewer.reset_view()
    except Exception as e:
        try:
            viewer.reset_view()
        except Exception:
            pass
        if os.environ.get('PYCAT_DEBUG'):
            print(f"[PyCAT] fit view skipped: {e}")


def _add_diameter_annotation_layers(viewer):
    """Add the 'Object Diameter'/'Cell Diameter' line-annotation layers,
    seeded with one invisible near-zero-length line so the (otherwise empty)
    Shapes layers report a FINITE extent. An empty Shapes layer reports a NaN
    extent in this napari build, which makes reset_view (the Home button)
    compute a NaN camera zoom and crash the scale-bar overlay. The seed is
    ignored by calculate_length, which measures the last non-degenerate line.

    As of the drawing-layer rework these layers are created ON DEMAND by the
    measure widget (via pycat.toolbox.drawing_layers.add_drawing_layer) instead
    of eagerly at every file load, so a session that never measures diameters
    isn't cluttered with two annotation layers. The module flag
    EAGER_DIAMETER_LAYERS restores the old eager behaviour if needed (a one-line
    revert): when False (default) this method is a no-op at load time; the
    measure widget creates the seeded, tagged layers when first used.

    NOTE on the Home-button crash: the NaN-extent crash only occurs when an
    EMPTY Shapes layer is present. With eager creation off, no diameter layer
    exists until the user makes one (and the factory seeds it), so the interim
    is safe — the absence of a layer cannot NaN the extent.
    """
    if not EAGER_DIAMETER_LAYERS:
        return
    import numpy as _np
    for _nm, _ec, _ew in (('Object Diameter', 'red', 2),
                          ('Cell Diameter', 'white', 5)):
        if _nm in [l.name for l in viewer.layers]:
            continue
        lyr = viewer.add_shapes(name=_nm, shape_type='line',
                                     edge_color=_ec, edge_width=_ew)
        try:
            lyr.add(_np.array([[0.0, 0.0], [0.0, 1e-4]]),
                    shape_type='line', edge_width=0.0)
            lyr.current_edge_width = _ew
        except Exception:
            pass
