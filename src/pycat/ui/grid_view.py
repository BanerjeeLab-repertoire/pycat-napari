"""Managed grid-view feature — extracted from MenuManager (ui_decomposition Part 2).

The napari grid-view manager (arrange visible layers in a grid, keeping an anchor layer fixed, and keep
the grid in sync as layer visibility/membership changes) lives here. ``MenuManager._toggle_grid_view`` /
``_apply_managed_grid`` are thin wrappers that call these; the menu action and its label are unchanged.
Moved VERBATIM.
"""
from __future__ import annotations

import math
import napari



def _toggle_grid_view(self, *args, **kwargs):
    """Toggle a PyCAT-managed side-by-side grid for comparing images.

    napari's raw grid mode tiles EVERY layer — including PyCAT's annotation
    Shapes layers (Cell/Object Diameter) and any drawing layers, which then
    get their own empty tiles instead of overlaying the images. It also grids
    by layer count regardless of the visibility eyeball. This managed version:
      - tiles only IMAGE layers (annotations/shapes/points stay overlaid,
        hidden behind the scenes while comparing — they can't be tiled
        meaningfully since an annotation belongs to one image),
      - respects the visibility eyeball: hidden image layers are dropped from
        the grid and it reflows,
      - recomputes automatically when layer visibility changes while grid is
        on, and restores the normal overlaid view when toggled off.
    """
    try:
        self._pycat_grid_on = not getattr(self, '_pycat_grid_on', False)
    except Exception:
        self._pycat_grid_on = True

    from napari.utils.notifications import show_info as _info
    if self._pycat_grid_on:
        # Snapshot the CANONICAL order of tileable layers at the moment grid
        # is turned on. Every reflow arranges visible layers against THIS
        # fixed anchor (not the transient list order), so toggling visibility
        # — including "show/hide all" — can never shuffle the grid: a layer
        # always returns to the same relative slot. Layers added later append
        # to the anchor in arrival order.
        self._grid_canonical_order = [
            l for l in self.viewer.layers
            if isinstance(l, (napari.layers.Image, napari.layers.Labels))]
        self._apply_managed_grid()
        # Recompute the grid whenever any layer's visibility toggles.
        if not getattr(self, '_grid_vis_wired', False):
            try:
                for lyr in self.viewer.layers:
                    try:
                        lyr.events.visible.connect(self._on_grid_layer_vis_changed)
                    except Exception:
                        pass
                # New layers added while grid is on should also be watched.
                self.viewer.layers.events.inserted.connect(
                    self._on_grid_layers_changed)
                self.viewer.layers.events.removed.connect(
                    self._on_grid_layers_changed)
                self._grid_vis_wired = True
            except Exception:
                pass
        # If any non-image (annotation / drawing) layers were pulled out to
        # keep them from claiming empty grid tiles, tell the user they're
        # temporarily set aside and will come back when grid is turned off —
        # so a drawing layer vanishing from the list isn't alarming.
        n_removed = len(getattr(self, '_grid_removed_nonimage', []))
        if n_removed:
            _info(f"Side-by-side grid view ON. {n_removed} annotation/"
                  f"drawing layer(s) temporarily set aside (with their "
                  f"contents) and will return when you toggle grid off.")
        else:
            _info("Side-by-side grid view ON (image layers only).")
        # Surface an acquisition-metadata comparison so the user knows
        # whether the images being compared were acquired under the same
        # settings (different exposure / laser / objective / filters make a
        # quantitative comparison untrustworthy — independent of the grid).
        try:
            self._maybe_warn_metadata_diff()
        except Exception:
            pass
    else:
        try:
            self.viewer.grid.enabled = False
        except Exception:
            pass
        # Re-insert the non-image layers removed for grid mode.
        n_restored = len(getattr(self, '_grid_removed_nonimage', []))
        self._restore_grid_removed_layers()
        # Clear the canonical order anchor so a fresh snapshot is taken next
        # time grid is enabled.
        self._grid_canonical_order = []
        if n_restored:
            _info(f"Side-by-side grid view OFF. {n_restored} annotation/"
                  f"drawing layer(s) restored.")
        else:
            _info("Side-by-side grid view OFF.")


def _apply_managed_grid(self):
    """Enable napari grid, reflowed to only the VISIBLE tileable layers.

    The diagnostic on napari 0.7.1 established two facts that drive this:
      (1) napari's grid tiles by TOTAL layer count and ignores visibility, so
          hidden layers otherwise leave empty black tiles (grid does NOT
          reflow on its own, and shape=(-1,-1) auto-recomputes to the full
          count) — but
      (2) setting grid.shape EXPLICITLY to fit the visible count DOES reflow
          the canvas, and napari fills cells by LAYER INDEX.
    So: remove pure annotation/drawing layers; arrange the visible tileable
    layers (images + visible masks) into the front cells ORDERED BY A
    CANONICAL ANCHOR snapshotted when grid was enabled — so visibility
    toggles (including show/hide-all) reflow the grid without ever shuffling
    which layer sits where — and set grid.shape to fit the visible count.
    Hidden tileable layers sort after the visible ones; masks overlay their
    image via z-order and are governed by their own eyeball.

    Idempotent and re-entrancy-safe.
    """
    if getattr(self, '_grid_applying', False):
        return
    self._grid_applying = True
    try:
        g = self.viewer.grid
        # 1. Remove pure annotation/drawing layers (recorded for restore).
        if not hasattr(self, '_grid_removed_nonimage'):
            self._grid_removed_nonimage = []
        for idx in range(len(self.viewer.layers) - 1, -1, -1):
            lyr = self.viewer.layers[idx]
            if isinstance(lyr, (napari.layers.Shapes, napari.layers.Points)):
                if not any(l is lyr for _, l in self._grid_removed_nonimage):
                    self._grid_removed_nonimage.append((idx, lyr))
                try:
                    self.viewer.layers.remove(lyr)
                except Exception:
                    pass
        # 2. Count visible tileable layers and set an explicit grid shape.
        vis = self._grid_tileable_visible()
        n = len(vis)
        if n <= 1:
            g.enabled = False
            return
        # 3. Arrange visible tileable layers into the front cells, ordered by
        #    the CANONICAL anchor captured at grid-on (not by transient list
        #    order) so visibility toggles never shuffle the grid. Any layer
        #    not in the anchor (added after grid-on) is appended in arrival
        #    order. Hidden tileable layers go after the visible ones.
        anchor = getattr(self, '_grid_canonical_order', None) or []

        def _anchor_key(layer):
            try:
                return anchor.index(layer)
            except ValueError:
                return len(anchor) + list(self.viewer.layers).index(layer)

        vis_sorted = sorted(vis, key=_anchor_key)
        hidden_tileable = [
            l for l in self.viewer.layers
            if isinstance(l, (napari.layers.Image, napari.layers.Labels))
            and l not in vis]
        hidden_sorted = sorted(hidden_tileable, key=_anchor_key)
        target = vis_sorted + hidden_sorted + [
            l for l in self.viewer.layers
            if l not in vis_sorted and l not in hidden_sorted]
        try:
            for dst, lyr in enumerate(target):
                src = list(self.viewer.layers).index(lyr)
                if src != dst:
                    self.viewer.layers.move(src, dst)
        except Exception:
            pass
        cols = int(math.ceil(math.sqrt(n)))
        rows = int(math.ceil(n / cols))
        g.enabled = True
        try:
            g.stride = 1
            g.shape = (rows, cols)   # EXPLICIT shape → reflows (proven)
        except Exception:
            pass
    except Exception as _e:
        print(f"[PyCAT] managed grid failed: {_e}")
    finally:
        self._grid_applying = False
