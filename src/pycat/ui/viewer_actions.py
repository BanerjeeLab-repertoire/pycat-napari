"""Viewer/layer action helpers — extracted from MenuManager (ui_decomposition Part 2).

Two viewer-level actions live here: ``_home_fit_view`` (reset the camera to fit all layers) and
``_process_foreign_layers`` (tag/normalize layers that appeared from outside PyCAT). ``MenuManager`` keeps
thin wrappers that call these; the menu/toolbar actions (``_home_fit_view`` is wired in batch_processor,
``_process_foreign_layers`` is called from ``_setup_menu_bar``) are unchanged. Moved VERBATIM.
"""
from __future__ import annotations

import napari



def _home_fit_view(self):
    """
    Fit the camera to the selected Image / Labels / Shapes (ROI) layer.
    For an arbitrary Points/line selection (or nothing selected), show a
    brief notice and do nothing.
    """
    import numpy as np
    from napari.utils.notifications import show_info as _info
    layer = self.viewer.layers.selection.active
    if layer is None:
        _info("Select an image or ROI layer, then press Home.")
        return
    fittable = isinstance(
        layer, (napari.layers.Image, napari.layers.Labels, napari.layers.Shapes))
    if not fittable:
        _info(f"'{layer.name}' isn't an image or ROI — nothing to fit to.")
        return
    try:
        ext = np.asarray(layer.extent.world)      # (2, ndim): [mins, maxs]
        mins, maxs = ext[0], ext[1]
        nd = self.viewer.dims.ndisplay
        dims = list(self.viewer.dims.displayed)[-nd:]
        center = (mins + maxs) / 2.0
        self.viewer.camera.center = tuple(float(center[d]) for d in dims)

        # Zoom to fit: need the canvas size in pixels. Prefer the private
        # `_qt_viewer` attribute — the public `window.qt_viewer` property is
        # deprecated (napari ≤0.8) and emits a FutureWarning on access, so we
        # try the private one first and only fall back with the warning muted.
        cw = ch = None
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.simplefilter('ignore', FutureWarning)
            for accessor in ('_qt_viewer', 'qt_viewer'):
                try:
                    sz = getattr(self.viewer.window, accessor).canvas.size
                    cw, ch = float(sz[0]), float(sz[1])
                    break
                except Exception:
                    continue
        sizes = [float(maxs[d] - mins[d]) for d in dims]
        if nd == 2 and cw and ch and all(s > 0 for s in sizes):
            # displayed dims are [y, x]; canvas is (width=x, height=y)
            zoom = min(ch / sizes[0], cw / sizes[1]) * 0.9   # 10% margin
            self.viewer.camera.zoom = zoom
        else:
            # Couldn't compute a fit zoom — at least re-center via reset.
            self.viewer.reset_view()
    except Exception:
        try:
            self.viewer.reset_view()
        except Exception:
            pass


def _process_foreign_layers(self):
    """Remove napari-reader-loaded (foreign) layers and re-open their source
    files through PyCAT's opener. Runs deferred (QTimer) so it doesn't mutate
    the layer list from inside the inserted-event callback. Handles the
    multi-layer case (one dropped multi-channel file → several foreign
    layers sharing a path)."""
    paths = getattr(self, '_pending_foreign_paths', [])
    self._pending_foreign_paths = []
    if not paths:
        return
    # Collect and remove every foreign layer whose source path is in our set.
    try:
        to_remove = []
        for layer in list(self.viewer.layers):
            try:
                src = getattr(layer, 'source', None)
                sp = getattr(src, 'path', None) if src is not None else None
            except Exception:
                sp = None
            if sp and sp in paths:
                to_remove.append(layer)
        for layer in to_remove:
            try:
                self.viewer.layers.remove(layer)
            except Exception:
                pass
    except Exception:
        pass
    # Re-open each unique path through PyCAT's context-aware opener, guarding
    # against the backstop re-triggering on PyCAT's own inserts.
    import os as _os
    self._pycat_reroute_guard = True
    try:
        from napari.utils.notifications import show_info as _info
        for i, p in enumerate(paths):
            try:
                # First dropped file replaces the session (normal open);
                # additional files add without clearing (comparison).
                self.central_manager.file_io.open_image_auto(
                    file_path=p, clear_first=(i == 0))
            except Exception as _e:
                print(f"[PyCAT] Could not re-open dropped file '{p}': {_e}")
        try:
            _info("Loaded dropped file(s) through PyCAT.")
        except Exception:
            pass
    finally:
        self._pycat_reroute_guard = False
