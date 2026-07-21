"""VPT UI — napari-facing layer/overlay/reveal-camera methods (Tracks/Points layers, the picked-bead ring, reveal + navigate + camera, session-view restore), extracted from vpt_ui.py (behaviour-preserving move).

A mixin so ``vpt_ui.py`` composes it instead of implementing it. Bodies are UNCHANGED; they use
``self`` (resolved by the composed class) and the imports below (copied verbatim from vpt_ui).
"""
from __future__ import annotations
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t
import numpy as np

from pycat.utils.pixel_size import pixel_size_um_or_default
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton, QComboBox, QLineEdit,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt


class _VptNapariMixin:
    """VPT napari-facing layer/overlay/reveal-camera methods. Mixed into ``VideoParticleTrackingUI``."""

    def _on_selection_image(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._reveal_track_in_viewer(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→image failed: {e}")

    def _add_pickable_bead_points(self, tracks, img_layer, mpp):
        """Add/refresh a Points layer with one point per bead per frame, each
        carrying its track_id, so clicking a bead selects that track everywhere
        (image->plot/table brushing). Matches the image layer's scale so points
        overlay the beads. The click handler resolves the nearest point to its
        track_id and drives the linked-selection dispatcher."""
        import numpy as _np
        if tracks is None or 'track_id' not in tracks or tracks.empty:
            return
        tr = tracks[tracks['track_id'] >= 0]
        if tr.empty:
            return
        ycol = 'y_um_raw' if 'y_um_raw' in tr else 'y_um'
        xcol = 'x_um_raw' if 'x_um_raw' in tr else 'x_um'
        frames = tr['frame'].values.astype(float)
        ys = (tr[ycol].values / mpp).astype(float)
        xs = (tr[xcol].values / mpp).astype(float)
        tids = tr['track_id'].values.astype(int)
        pts = _np.column_stack([frames, ys, xs])   # (T, Y, X)

        name = "Bead Picker"
        if name in self.viewer.layers:
            try:
                self.viewer.layers.remove(name)
            except Exception:
                pass

        add_kwargs = {'name': name, 'size': 8, 'opacity': 0.35,
                      'face_color': 'transparent', 'border_color': 'yellow',
                      'properties': {'track_id': tids}}
        # Match the image layer's spatial (y, x) scale, like the Tracks layer.
        if img_layer is not None:
            try:
                isc = _np.asarray(img_layer.scale, float)
                if isc.size >= 2:
                    yx = isc[-2:]
                    add_kwargs['scale'] = [1.0, float(yx[0]), float(yx[1])]
            except Exception:
                pass
        try:
            layer = self.viewer.add_points(pts, **add_kwargs)
        except Exception:
            # Older napari uses edge_color rather than border_color.
            add_kwargs.pop('border_color', None)
            add_kwargs['edge_color'] = 'yellow'
            layer = self.viewer.add_points(pts, **add_kwargs)

        self._bead_picker_tids = tids

        # Click -> select that track everywhere. Uses the layer's own
        # mouse_drag_callbacks so it only fires when the picker layer is active;
        # get_value returns the index of the point under the cursor.
        def _on_click(layer, event):
            if self._selection().is_busy:
                return
            try:
                idx = layer.get_value(
                    event.position, view_direction=event.view_direction,
                    dims_displayed=event.dims_displayed)
            except Exception:
                try:
                    idx = layer.get_value(event.position)
                except Exception:
                    idx = None
            if idx is None:
                return
            try:
                tid = int(self._bead_picker_tids[int(idx)])
            except Exception:
                return
            self._select_track(tid, source='image')

        try:
            layer.mouse_drag_callbacks.append(_on_click)
        except Exception:
            pass
        self._bead_picker_layer = layer
        # A layer-level callback only fires when the picker layer is ACTIVE — but the
        # user is usually on the image or Tracks layer, so a bead click never reached
        # the hub and image→plot/table brushing looked dead. Also install a
        # viewer-level pick that works whatever layer is selected: it hit-tests the
        # picker layer's points directly and only acts when a bead is under the cursor.
        self._install_viewer_bead_pick()

    def _install_viewer_bead_pick(self):
        """Select a track by clicking a bead REGARDLESS of the active layer.

        napari dispatches layer ``mouse_drag_callbacks`` only to the active layer, so
        the per-layer picker above is silent unless the user first selects "Bead
        Picker". This viewer-level callback fires on every click, queries the picker
        layer's ``get_value`` (a data lookup, independent of which layer is active),
        and selects the track only when a bead is actually under the cursor — so an
        empty-space click or a pan is untouched. Installed once per viewer."""
        if getattr(self, '_viewer_pick_installed', False):
            return

        def _on_viewer_click(viewer, event):
            if self._selection().is_busy:
                return
            layer = getattr(self, '_bead_picker_layer', None)
            if layer is None or not getattr(layer, 'visible', True):
                return
            tid = self._nearest_bead_tid(layer, event)
            if tid is None:
                return                               # click not near any bead — leave it alone
            self._select_track(tid, source='image')

        try:
            self.viewer.mouse_drag_callbacks.append(_on_viewer_click)
            self._viewer_pick_installed = True
        except Exception:                            # broad-ok: viewer teardown / missing callback list
            pass

    def _nearest_bead_tid(self, layer, event, radius_px=25.0):
        """Track id of the bead NEAREST the click (within ``radius_px`` image pixels)
        on the current frame — a tolerant "click near a bead" hit test.

        napari's Points ``get_value`` only returns a point the cursor sits exactly on;
        with hundreds of thousands of tiny, dense beads that essentially never hits, so
        image→everywhere looked dead (``idx=None``). This searches the picker layer's
        own point data instead. Bead coords are ``y_um/mpp`` → the data frame is pixels,
        so the radius is in pixels."""
        import numpy as _np
        data = getattr(layer, 'data', None)
        if data is None:
            return None
        data = _np.asarray(data, float)
        tids = getattr(self, '_bead_picker_tids', None)
        if data.size == 0 or tids is None or len(tids) != len(data):
            return None
        try:
            pos = _np.asarray(layer.world_to_data(event.position), float)
        except Exception:                            # broad-ok: fall back to the raw world position
            pos = _np.asarray(event.position, float)
        if data.shape[1] >= 3:                       # (T, Y, X) — restrict to the click's frame
            mask = data[:, 0].astype(int) == int(round(pos[0]))
            if not mask.any():
                return None
            pts = data[mask][:, -2:]; cand = _np.asarray(tids)[mask]
        else:
            pts = data[:, -2:]; cand = _np.asarray(tids)
        click = pos[-2:]
        d = _np.hypot(pts[:, 0] - click[0], pts[:, 1] - click[1])
        j = int(_np.argmin(d))
        if d[j] > radius_px:
            return None                              # nearest bead is too far — not a bead click
        try:
            return int(cand[j])
        except Exception:                            # broad-ok: non-int tid → no selection
            return None

    def _rebuild_track_layers(self, tracks, name="Bead Trajectories"):
        """(Re)build the napari Tracks layer + the pickable Points layer from a
        tracks DataFrame. Shared by the linker and by session load, so a loaded
        session gets exactly the same brushable layers a fresh link produces.

        tracks needs at least: track_id, frame, and y_um/x_um (or y_um_raw/
        x_um_raw). Scale is matched to the bead image layer so the tracks overlay
        the image 1:1."""
        import numpy as _np
        if tracks is None or 'track_id' not in tracks or tracks.empty:
            return
        mpp = self._mpx()
        _bead_name = self._bead_dd.currentText() if hasattr(self, '_bead_dd') else ''
        _img_layer = None
        try:
            import napari.layers as _nl
            for _l in self.viewer.layers:
                if isinstance(_l, _nl.Image):
                    if _bead_name and _l.name == _bead_name:
                        _img_layer = _l; break
                    if _img_layer is None:
                        _img_layer = _l
        except Exception:
            _img_layer = None

        tl = tracks[['track_id', 'frame']].copy()
        tl['y'] = tracks['y_um_raw'] / mpp if 'y_um_raw' in tracks else tracks['y_um'] / mpp
        tl['x'] = tracks['x_um_raw'] / mpp if 'x_um_raw' in tracks else tracks['x_um'] / mpp
        if name in self.viewer.layers:
            self.viewer.layers.remove(name)
        add_kwargs = {}
        if _img_layer is not None:
            try:
                isc = _np.asarray(_img_layer.scale, float)
                if isc.size >= 2:
                    yx = isc[-2:]
                    add_kwargs['scale'] = [1.0, float(yx[0]), float(yx[1])]
            except Exception:
                pass
        self.viewer.add_tracks(
            tl[['track_id', 'frame', 'y', 'x']].values, name=name,
            tail_width=self._BASE_TRACK_TAIL_WIDTH, **add_kwargs)
        try:
            self._add_pickable_bead_points(tracks, _img_layer, mpp)
        except Exception as _e:
            print(f"[PyCAT VPT] pickable bead layer failed: {_e}")

    def _world_scale_yx(self, mpp):
        """The (y, x) scale the image layer is ACTUALLY drawn at.

        Read once and used for both the highlight and the camera, so they stay
        consistent even when the layer scale differs from `_mpx()` — e.g. the
        pixel-size gate never fired and the image sits at scale 1.0. Placing the
        overlay in the same world frame the image uses is what guarantees it lands
        on the bead. This is the exact µm-vs-px confusion that bit before: pin it
        to one source of truth, the image layer's own scale.
        """
        import numpy as _np
        for layer in self.viewer.layers:
            if layer.__class__.__name__ == 'Image':
                scale = _np.asarray(layer.scale, float)
                if scale.size >= 2:
                    return float(scale[-2]), float(scale[-1])
                break
        return mpp, mpp

    def _navigate_to_bead(self, f0, y0, x0, sc_y, sc_x):
        """Take the user to the bead: step to its frame, centre on it, and zoom in.

        A plot click IS "take me to this bead" — that is what the user asked for. It was gated off
        while the plot-click loop existed (a camera move fired a ``draw_event`` that re-entered the
        pick); with one ``button_press`` per click (1.6.100) and the ``_revealing`` re-entrancy guard,
        the move is safe, so navigation is on by default now.

        The bead can sit on any frame, so going to it means the frame too; and centring alone is not
        enough on a 1000-frame movie where the bead is a few pixels — so it also zooms to frame a small
        window (``_BEAD_ZOOM_WINDOW_PX``) around it.
        """
        # Step to the track's first frame (axis 0 for a movie/stack).
        try:
            if self.viewer.dims.ndim > 2:
                step = list(self.viewer.dims.current_step)
                step[0] = f0
                self.viewer.dims.current_step = tuple(step)
        except Exception:
            pass
        # Centre on the bead, in the image's world frame.
        try:
            self.viewer.camera.center = (y0 * sc_y, x0 * sc_x)
        except Exception:
            pass
        # Zoom so a small window around the bead fills the view. napari zoom is canvas-px per
        # world-unit, so zoom = canvas_px / (window_px · scale). Fall back to a sane canvas size.
        try:
            canvas = getattr(getattr(self.viewer, 'window', None), 'qt_viewer', None)
            canvas_px = min(canvas.canvas.size) if canvas is not None else 600
            world_extent = self._BEAD_ZOOM_WINDOW_PX * max(sc_y, sc_x)
            if world_extent > 0:
                self.viewer.camera.zoom = float(canvas_px) / world_extent
        except Exception:
            pass

    def _draw_picked_track(self, path, frames, mpp, sc_y, sc_x):
        """Ring the picked bead ON the frame you are looking at, and trace its path.

        ── The ring used to sit where the bead STARTED ───────────────────────

        The marker was `add_points(path[:1])` — one point, `(1, 2)`, y and x with
        **no frame coordinate**. In a (T, Y, X) viewer that is a 2-D layer, so
        napari drew it on EVERY frame at the bead's frame-0 position. Scrub
        forward and the bead moves off while the ring stays behind: the "circle is
        offset from the bead" complaint. It was never padding — nothing pads this
        marker — it was a missing axis. `selection_overlay._centre_for` already
        guards the same trap ("a 3-D+ viewer needs the leading coordinate or the
        rectangle floats across every slice"); this path never got the memo.

        So the ring is now one point PER FRAME at `(frame, y, x)`. napari shows
        the one on the current slice, which means it sits ON the bead wherever you
        are in the movie — the true centre, by construction, at every timepoint.

        The trajectory line stays 2-D on purpose: a path is meant to be visible
        across the whole movie. Only the ring is per-frame.
        """
        hl_line = "Picked track"
        hl_start = "Picked bead"
        try:
            for name in (hl_line, hl_start, "Picked track start"):
                if name in self.viewer.layers:
                    self.viewer.layers.remove(name)      # incl. the old layer name
            if path.shape[0] >= 2:
                # ── The picked track is a Tracks layer, 2× the base width ──────
                #
                # A second Tracks copy of the picked path, drawn over "Bead Trajectories" and NOT
                # offset from it: both take their scale from the image layer (`[1.0, sc_y, sc_x]`), the
                # one source of truth, so they land on each other exactly. Using the SAME layer type as
                # the base is the point — its `tail_width` is in screen pixels, so at the deep
                # zoom-to-bead the line stays a fixed weight instead of ballooning the way the old
                # Shapes `edge_width` (data units) did and burying the trajectory detail. "2× bolder
                # than the base" is then literal: `_PICKED_TRACK_TAIL_WIDTH == 2 · base`.
                #
                # It stays a separate overlay rather than recolouring the base Tracks layer: borrowing
                # a layer's display state to mean "selected" is the `selected_label` mistake
                # `pycat.utils.selection_overlay` exists to undo — a user who coloured their tracks by
                # velocity would silently lose it on a plot click.
                #
                # `tail_length`/`head_length` span the whole track so it is fully drawn at ANY frame
                # (a napari Tracks layer otherwise shows only a short tail near the current frame — at
                # the bead's first frame that would be almost nothing).
                self._draw_picked_track_layer(hl_line, path, frames, sc_y, sc_x)
            self._add_bead_ring(hl_start, path, frames, mpp, sc_y, sc_x)
        except Exception:
            pass

    def _ensure_picked_colormap(self):
        try:
            from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
            if self._PICKED_COLORMAP not in AVAILABLE_COLORMAPS:
                AVAILABLE_COLORMAPS[self._PICKED_COLORMAP] = Colormap(
                    [[1.0, 0.549, 0.0, 1.0], [1.0, 0.549, 0.0, 1.0]],
                    name=self._PICKED_COLORMAP)
            return self._PICKED_COLORMAP
        except Exception:
            return None

    def _draw_picked_track_layer(self, name, path, frames, sc_y, sc_x):
        """The picked track as a Tracks layer at 2× the base width — see `_draw_picked_track`.

        Falls back to a thin Shapes path only if `add_tracks` is unavailable (older napari), so the
        highlight is never lost even when the preferred layer type is not there.
        """
        import numpy as _np
        f = _np.asarray(frames, float)
        data = _np.column_stack([_np.zeros(len(f)), f, path[:, 0], path[:, 1]])
        span = max(1, int(f.max() - f.min()) + 1)
        kw = dict(name=name, tail_width=self._PICKED_TRACK_TAIL_WIDTH,
                  tail_length=span, head_length=span, scale=[1.0, sc_y, sc_x])
        cmap = self._ensure_picked_colormap()
        if cmap is not None:
            kw['colormap'] = cmap
        try:
            self.viewer.add_tracks(data, **kw)
        except Exception:
            self.viewer.add_shapes(
                [path], name=name, shape_type='path', edge_color='#ff8c00',
                face_color='transparent', edge_width=0.2, opacity=0.7,
                scale=[sc_y, sc_x])

    def _add_bead_ring(self, name, path, frames, mpp, sc_y, sc_x):
        """The ring itself: per-frame when the viewer has a time axis, else flat.

        A hollow ring, never a filled disc — the point is to draw the eye to the
        bead, not to cover it up.
        """
        import numpy as _np
        ndim = int(getattr(self.viewer.dims, 'ndim', 2) or 2)
        if ndim >= 3 and frames is not None and len(frames) == path.shape[0]:
            data = _np.column_stack([_np.asarray(frames, float), path])
            scale = [1.0, sc_y, sc_x]        # the time axis is not microns
        else:
            data = path[:1]
            scale = [sc_y, sc_x]
        # Fixed pixel-unit size so a fine pixel size does not balloon the ring (0.5/mpp did). Static —
        # the zoom-to-bead navigation is what draws the eye, not a pulse.
        kw = dict(name=name, size=12, face_color='transparent',
                  border_color='#ff8c00', opacity=0.9, scale=scale)
        try:
            layer = self.viewer.add_points(data, **kw)
        except Exception:
            kw.pop('border_color', None); kw['edge_color'] = '#ff8c00'
            layer = self.viewer.add_points(data, **kw)
        return layer

    def _announce_picked_track(self, track_id, g, f0):
        """One line about the picked track — what it is, without going to it."""
        import numpy as _np
        try:
            n = int(len(g))
            step_nm = float(_np.median(_np.sqrt(
                _np.sum(_np.diff(g[['y_um', 'x_um']].values, axis=0) ** 2,
                        axis=1)))) * 1000 if n > 1 else 0.0
            napari_show_info(
                f"Track {int(track_id)}: {n} frames, starts frame {f0}, "
                f"median step {step_nm:.0f} nm — highlighted in viewer.")
        except Exception:
            pass

    def restore_session_view(self):
        """Rebuild the clickable VPT view from restored dataframes, after a session load.

        The method is reopened with the data repository already populated (``vpt_tracks`` etc. restored
        by the loader), so this redraws what a fresh **Compute MSD & Viscosity** would: the trajectory
        + pickable layers, and the MSD/moduli plots. It reuses ``_on_rheology`` — the exact handler the
        Compute button runs, which reads ``vpt_tracks`` from the repository — so there is no second,
        divergent render path. Returns True if it rebuilt anything.

        The slow part of VPT (detection + linking) produced ``vpt_tracks`` and is NOT redone; recomputing
        the MSD from the restored tracks is seconds. Parameters come back at their defaults (the frame
        interval auto-fills from the source metadata); a user who needs the session's exact bead radius/
        temperature can set them and re-Compute.
        """
        try:
            tracks = self._dr().get('vpt_tracks')
            if tracks is None or getattr(tracks, 'empty', True):
                return False
            self._rebuild_track_layers(tracks)
            self._on_rheology()               # reads vpt_tracks, computes MSD, draws the plots
            return True
        except Exception as exc:
            print(f"[PyCAT VPT] session view restore failed: {exc}")
            return False

    def _reveal_track_in_viewer(self, track_id):
        """Plot -> data brushing: clicking a track in the MSD plot marks that exact
        bead in the napari viewer — traces its trajectory, and surfaces its row.

        napari's Tracks layer has no per-track selection API, so instead of
        recolouring one track inside the layer we add a short-lived Shapes path
        highlight at the bead's position, which is the clear visual cue.

        ── Going to the bead, without looping ────────────────────────────────

        A pick navigates to the bead (`_navigate_to_bead`: step to its frame,
        centre, zoom) — that is what the user asked a plot click to do. It used to
        LOOP, though: moving the camera/frame fires napari's `draw_event`, which
        re-runs the MSD plot's blit-capture, which can re-enter the pick and reveal
        again — a continuous jump until force-close. That is why navigation was
        gated off for a while.

        Two things made it safe to turn back on (1.6.104): one `button_press` per
        click instead of dozens of `pick_event`s (1.6.100), and this reveal being
        re-entrant-guarded (below) so the camera move it fires cannot come back
        round as a second reveal.
        """
        # ── Re-entrancy: a camera move must not become another reveal ─────────
        #
        # Set before the work, released on the next event-loop tick AFTER it — the
        # same delayed pattern `SelectionService` uses, and for the same reason.
        # The release has to outlive both this call and the `draw_event` our own
        # camera move fires, which is why it is deferred rather than cleared here.
        if getattr(self, '_revealing', False):
            return
        self._revealing = True
        try:
            import numpy as _np
            tracks = self._dr().get('vpt_tracks')
            if tracks is None or 'track_id' not in tracks:
                return
            g = tracks[tracks['track_id'] == track_id]
            if g.empty:
                return
            g = g.sort_values('frame')
            mpp = self._mpx()
            # ── Match the BASE trajectory layer's coordinates, or the picked track offsets ──
            #
            # The reveal used `y_um`/`x_um` — the DRIFT-CORRECTED positions. But the "Bead
            # Trajectories" layer (`_rebuild_track_layers`) draws `y_um_raw`/`x_um_raw` when present:
            # the RAW positions, which sit on the actual beads in the image. Drift correction
            # subtracts the centre-of-mass motion, so the corrected path is shifted away from the
            # beads — and the picked track drew that shift as a visible offset from both the bead and
            # the base trajectory. Prefer the raw coords, exactly as the base layer does, so the
            # highlight lands on the bead it is highlighting.
            _yc = 'y_um_raw' if 'y_um_raw' in g else 'y_um'
            _xc = 'x_um_raw' if 'x_um_raw' in g else 'x_um'
            ys = (g[_yc].values / mpp)
            xs = (g[_xc].values / mpp)
            f0 = int(g['frame'].iloc[0])
            y0 = float(ys[0]); x0 = float(xs[0])

            sc_y, sc_x = self._world_scale_yx(mpp)

            # Select the Tracks layer if present (so the picked track is in focus).
            if "Bead Trajectories" in self.viewer.layers:
                try:
                    self.viewer.layers.selection = {
                        self.viewer.layers["Bead Trajectories"]}
                except Exception:
                    pass

            self._navigate_to_bead(f0, y0, x0, sc_y, sc_x)
            self._draw_picked_track(_np.column_stack([ys, xs]),
                                    g['frame'].values.astype(int), mpp, sc_y, sc_x)
            self._announce_picked_track(track_id, g, f0)
        except Exception as _e:
            print(f"[PyCAT VPT] reveal track failed: {_e}")
        finally:
            # In a `finally` so a broken reveal cannot wedge the plot: a guard that
            # never releases is not a fix, it is a dead plot — every later pick
            # would be silently swallowed.
            from pycat.utils.selection_service import _qt_defer
            _qt_defer(lambda: setattr(self, '_revealing', False))
