"""**One brushable results workspace: plots on the left, tables on the right, all cross-linked.**

Meet's request, generalized: a reusable panel that stacks scatter plots down the left and results tables
down the right, with every plot point, every table row, and (later) every labeled object in the napari
image referring to the SAME object — click any one and the others light up. It is the VPT results dock made
general: VPT wires four bespoke panels + a track table + a bead picker by hand; this expresses that shape as
a small config so the cellular-fluorescence, in-vitro, and batch panels reuse one implementation.

**It owns no dispatcher.** Every view here is a :class:`~pycat.utils.selection_service.SelectionView` on the
one application ``SelectionService`` (``central_manager.selection``), keyed on the stable
``_pycat_entity_id``. Because a cell and a condensate are different entity *types*, they are different keys —
so "two interleaved brushing tiers over one image" needs no special case: two views, two ids, one service.

**The VPT-refactor seam.** ``BrushablePlot`` is a scatter by default, but its object points and its drawing
are two overridable methods (``_object_points`` / ``_draw``), so a custom painter — VPT's MSD/moduli panels —
plugs into the same click→select / select→ring machinery without reimplementing it. That is the path by
which ``vpt/results_dock`` can later be refactored onto this core.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from pycat.utils.entity_ref import ENTITY_ID_COLUMN, without_identity
from pycat.utils.general_utils import debug_log
from pycat.utils.selection_service import Selection, register_view

_RING_KW = dict(marker='o', mfc='none', mec='#ff8c00', mew=2.0, ms=12, zorder=5, linestyle='None')


class BrushablePlot:
    """A scatter axes wired to the shared ``SelectionService`` as a ``SelectionView``.

    This is :func:`comparative_figures._attach_object_brushing` promoted to the ``SelectionView`` contract:
    a click selects the nearest object's entity everywhere; a selection arriving from another view rings the
    matching point here (self-highlight on emit, because the service suppresses a view's own receive).

    **VPT-refactor seam:** override ``_object_points`` (the ``[(entity_id, x, y), …]`` the plot brushes) and
    ``_draw`` (how the axes is painted) to back the same brushing with a custom painter instead of a scatter.
    """

    def __init__(self, ax, df, x_col, y_col, service, view_id, *,
                 entity_col=ENTITY_ID_COLUMN, marker_kwargs=None):
        self.ax = ax
        self.df = df
        self.x_col = x_col
        self.y_col = y_col
        self.service = service
        self.view_id = str(view_id)
        self.entity_col = entity_col
        self._marker_kwargs = dict(marker_kwargs or {})
        self._ring = None
        self._cid = None
        self._points = self._object_points()
        self._draw()
        self._connect()

    # ── the seam a custom painter (VPT) overrides ─────────────────────────────────────────────
    def _object_points(self):
        """``[(entity_id, x, y), …]`` — one per row that carries an entity id and finite coordinates."""
        pts = []
        df = self.df
        if df is None or self.entity_col not in getattr(df, 'columns', ()) \
                or self.x_col not in df.columns or self.y_col not in df.columns:
            return pts
        for eid, x, y in zip(df[self.entity_col], df[self.x_col], df[self.y_col]):
            if eid is None:
                continue
            try:
                xf, yf = float(x), float(y)
            except (TypeError, ValueError):
                continue
            if xf != xf or yf != yf:                    # NaN — no point to place
                continue
            pts.append((str(eid), xf, yf))
        return pts

    def _draw(self):
        xs = [p[1] for p in self._points]
        ys = [p[2] for p in self._points]
        kw = dict(s=28, alpha=0.8, picker=5)
        kw.update(self._marker_kwargs)
        self.artist = self.ax.scatter(xs, ys, **kw) if xs else None
        try:
            self.ax.set_xlabel(self.x_col)
            self.ax.set_ylabel(self.y_col)
        except Exception as exc:                        # broad-ok: ui_cleanup — labelling is cosmetic, never fatal
            debug_log('brushable_workspace: could not label plot axes', exc)

    @property
    def figure(self):
        return self.ax.figure

    def _connect(self):
        try:
            register_view(self.service, self)           # subscribes apply_selection + pushes current state
        except Exception as exc:                         # broad-ok: optional_probe — a plot that can't subscribe still draws
            debug_log('brushable_workspace: could not register the plot view', exc)
        try:
            self._cid = self.ax.figure.canvas.mpl_connect(
                'button_press_event',
                lambda ev: (getattr(ev, 'inaxes', None) is self.ax and getattr(ev, 'x', None) is not None
                            and self.emit_nearest(ev.x, ev.y)))
        except Exception as exc:                         # broad-ok: optional_probe — no live canvas headless — clicks come via emit_nearest
            debug_log('brushable_workspace: no canvas to connect the plot click', exc)

    # ── outbound: a click selects the nearest object ──────────────────────────────────────────
    def emit_nearest(self, x_disp, y_disp, radius_px=14.0):
        """Select the object whose point is nearest the click (display coords), if within ``radius_px``.
        Returns the entity id selected, or None. Early-outs while the service is mid-propagation."""
        if not self._points or self.service.is_busy:
            return None
        trans = self.ax.transData
        best = None
        for eid, x, y in self._points:
            px, py = trans.transform((x, y))
            d = ((px - x_disp) ** 2 + (py - y_disp) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, eid)
        if best is None or best[0] > radius_px:
            return None
        eid = best[1]
        self._ring_points({eid})                        # self-highlight — our own receive is suppressed
        self.service.select(Selection(
            entity_ids=(eid,), primary_id=eid, mode='selected',
            source_view=self.view_id, generation=self.service.next_generation()))
        return eid

    # ── inbound: ring the selected point(s) — a PROGRAMMATIC update, emits nothing ────────────
    def apply_selection(self, state):
        self._ring_points({str(e) for e in (getattr(state, 'entity_ids', ()) or ())})

    def _ring_points(self, eids):
        if self._ring is not None:
            try:
                self._ring.remove()
            except Exception:                            # broad-ok: ui_cleanup — a removed artist is already gone
                pass
            self._ring = None
        sel = [(x, y) for e, x, y in self._points if e in eids]
        if sel:
            xs, ys = zip(*sel)
            try:
                (self._ring,) = self.ax.plot(xs, ys, **_RING_KW)
            except Exception as exc:                     # broad-ok: ui_cleanup — the ring is cosmetic; never fail a selection over it
                debug_log('brushable_workspace: could not draw the selection ring', exc)
        try:
            self.ax.figure.canvas.draw_idle()
        except Exception:                                # broad-ok: optional_probe — no live canvas headless — nothing to redraw
            pass

    def close(self):
        try:
            self.service.unsubscribe(self.view_id)
        except Exception as exc:                         # broad-ok: ui_cleanup — teardown is best-effort; never raise on close
            debug_log('brushable_workspace: plot unsubscribe failed', exc)
        if self._cid is not None:
            try:
                self.ax.figure.canvas.mpl_disconnect(self._cid)
            except Exception:                            # broad-ok: ui_cleanup — a stale/twice-disconnected cid is harmless
                pass
            self._cid = None


class BrushableImageTier:
    """**A napari labels layer as one brushing tier over the image.**

    Click a labeled object → select its entity everywhere; a selection arriving from another view reveals
    that object in the image (the selection overlay). This is the "brush FROM the image" half, and the piece
    that makes two *interleaved* tiers possible: a cell-labels tier and a condensate-labels tier are two of
    these on one viewer, each with its own ``view_id`` and entity type, so a click on either brushes only its
    own tier.

    Requires the table rows to carry ``_pycat_entity_id`` and a ``label_col`` whose values are the layer's
    label values (so the clicked label maps to a row), plus a bbox (for the reveal / offline resolve).
    """

    def __init__(self, viewer, labels_layer, df, service, view_id, *,
                 label_col, source_path=None, entity_col=ENTITY_ID_COLUMN, reveal='resolve',
                 install_callback=True):
        from pycat.utils.object_ref import ObjectRef

        self.viewer = viewer
        self.labels_layer = labels_layer
        self.service = service
        self.view_id = str(view_id)
        # When a workspace owns several tiers over one viewer it installs ONE viewer-level pick handler and
        # sets this False — napari only delivers a LAYER's mouse callbacks to the ACTIVE layer, so per-layer
        # picking would make "which tier picks" depend on which layer happens to be selected.
        self._install_callback = install_callback
        # 'resolve' highlights via the layer's selected_label (its label values ARE the object ids —
        # cells, droplets). 'overlay' draws the bbox rectangle instead — for a layer whose label values
        # are NOT the row's object_id (the global per-punctum layer, where the row's label is per-cell).
        self.reveal = reveal
        self._label_to_eid = {}
        self._eid_to_ref = {}
        self._cb = None
        for _, row in df.iterrows():
            eid = row.get(entity_col)
            if eid is None:
                continue
            try:
                self._label_to_eid[int(row[label_col])] = str(eid)
            except (TypeError, ValueError):
                continue
            try:
                # A batch row carries its OWN source image in _pycat_source_path (each row a different
                # image); a single-image tier passes one source_path for all.
                _src = row.get('_pycat_source_path') or source_path
                self._eid_to_ref[str(eid)] = ObjectRef.from_row(row, source_path=_src)
            except Exception as exc:                     # broad-ok: ui_cleanup — a row without a resolvable bbox just can't reveal
                debug_log('brushable_workspace: could not build an ObjectRef for a row', exc)
        self._connect()

    def _connect(self):
        try:
            register_view(self.service, self)
        except Exception as exc:                         # broad-ok: optional_probe — an image tier that can't subscribe still shows the layer
            debug_log('brushable_workspace: could not register the image tier', exc)
        if not self._install_callback:
            return                                       # the workspace dispatches clicks at the viewer level
        try:
            self._cb = self._on_click
            self.labels_layer.mouse_drag_callbacks.append(self._cb)
        except Exception as exc:                         # broad-ok: optional_probe — no live layer callbacks headless — reveal still works
            debug_log('brushable_workspace: could not wire the image click', exc)

    def pick_at(self, position) -> bool:
        """Read the label of THIS tier's layer at ``position`` and, if it maps to an object, select that
        object's entity. Returns True if it handled the click (an object was there) — so a viewer-level
        dispatcher can try finer tiers first and stop at the first hit."""
        if self.service.is_busy:
            return False
        try:
            value = self.labels_layer.get_value(position, world=True)
        except Exception as exc:                         # broad-ok: ui_cleanup — a click outside the data / mid-transition
            debug_log('brushable_workspace: could not read the clicked label', exc)
            return False
        eid = self._label_to_eid.get(int(value)) if value else None
        if eid is None:
            return False
        self.service.select(Selection(
            entity_ids=(eid,), primary_id=eid, mode='selected',
            source_view=self.view_id, generation=self.service.next_generation()))
        return True

    def _on_click(self, layer, event):
        """A click on this tier's layer (per-layer path, single-tier / tests)."""
        self.pick_at(event.position)

    def apply_selection(self, state):
        """Reveal the selected object(s) in the image — a programmatic reveal, it emits no command."""
        from pycat.utils.object_ref import resolve_in_viewer
        from pycat.utils.selection_overlay import show_selection

        refs = [self._eid_to_ref[e] for e in (getattr(state, 'entity_ids', ()) or ()) if e in self._eid_to_ref]
        if not refs:
            return
        try:
            if self.reveal == 'overlay' or len(refs) > 1:
                show_selection(self.viewer, refs)       # bbox rectangle — robust when label != object_id
            else:
                resolve_in_viewer(refs[0], self.viewer, centre=False)
        except Exception as exc:                         # broad-ok: ui_cleanup — the reveal is best-effort; never fail a selection over it
            debug_log('brushable_workspace: could not reveal the selection in the image', exc)

    def close(self):
        try:
            self.service.unsubscribe(self.view_id)
        except Exception as exc:                         # broad-ok: ui_cleanup — teardown is best-effort; never raise on close
            debug_log('brushable_workspace: image tier unsubscribe failed', exc)
        if self._cb is not None:
            try:
                self.labels_layer.mouse_drag_callbacks.remove(self._cb)
            except Exception:                            # broad-ok: ui_cleanup — a callback already removed / layer gone
                pass
            self._cb = None


def _crop_to_pixmap(crop):
    """A 2-D numpy crop → a grayscale ``QPixmap`` (contrast-stretched). None if it cannot render."""
    import numpy as np
    from PyQt5.QtGui import QImage, QPixmap

    a = np.asarray(crop)
    if a.ndim != 2 or a.size == 0:
        return None
    a = a.astype(float)
    lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    g = (((a - lo) / (hi - lo)) * 255).astype('uint8') if hi > lo else np.zeros(a.shape, dtype='uint8')
    g = np.ascontiguousarray(g)
    h, w = g.shape
    return QPixmap.fromImage(QImage(g.tobytes(), w, h, w, QImage.Format_Grayscale8))


class BatchCropView:
    """**The batch 'image' — the crop of the selected object, read OFFLINE from its source file.**

    At batch end there is no live viewer, so an object cannot be revealed in a napari layer. Instead, a
    selection resolves the object's :class:`ObjectRef` (source path + bbox, both carried on the row) and
    ``crop_for_ref(viewer=None)`` → ``resolve_offline`` opens that image and slices the crop — no session,
    no re-segmentation. This closes the loop the spec calls out: a batch plot point / table row pulls up its
    originating image.
    """

    def __init__(self, df, service, view_id, *, entity_col=ENTITY_ID_COLUMN):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel
        from pycat.utils.object_ref import ObjectRef

        self.service = service
        self.view_id = str(view_id)
        self.label = QLabel("Select an object to see its image")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setMinimumSize(180, 180)
        self.last_crop = None
        self._eid_to_ref = {}
        for _, row in df.iterrows():
            eid = row.get(entity_col)
            if eid is None:
                continue
            try:
                self._eid_to_ref[str(eid)] = ObjectRef.from_row(row, source_path=row.get('_pycat_source_path'))
            except Exception as exc:                     # broad-ok: ui_cleanup — a row without a resolvable bbox just can't crop
                debug_log('brushable_workspace: batch ref build failed', exc)
        try:
            register_view(self.service, self)
        except Exception as exc:                         # broad-ok: optional_probe — a crop view that can't subscribe still shows
            debug_log('brushable_workspace: could not register the batch crop view', exc)

    def apply_selection(self, state):
        from pycat.utils.brushing import crop_for_ref
        from PyQt5.QtCore import Qt

        refs = [self._eid_to_ref[e] for e in (getattr(state, 'entity_ids', ()) or ()) if e in self._eid_to_ref]
        if not refs:
            return
        try:
            crop, message = crop_for_ref(refs[0], viewer=None)
        except Exception as exc:                         # broad-ok: optional_probe — an unreadable source file just shows nothing
            debug_log('brushable_workspace: offline crop failed', exc)
            return
        self.last_crop = crop
        pixmap = _crop_to_pixmap(crop) if crop is not None else None
        if pixmap is not None:
            self.label.setPixmap(pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif message:
            self.label.setText(str(message))

    def close(self):
        try:
            self.service.unsubscribe(self.view_id)
        except Exception as exc:                         # broad-ok: ui_cleanup — teardown is best-effort; never raise on close
            debug_log('brushable_workspace: batch crop unsubscribe failed', exc)


def _vertical_stack():
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(4)
    return holder, layout


class BrushableWorkspace(QWidget):
    """The panel: plots stacked on the left, tables stacked on the right, all on one ``SelectionService``.

    Build it, ``add_plot`` (top→bottom on the left) and ``add_table`` (top→bottom on the right), then mount
    ``.widget`` in a single persistent dock (the VPT lifecycle rule — one dock, kept alive, so highlight
    targets survive). ``detach()`` when the dock closes, so the dispatcher stops driving dead widgets.
    """

    def __init__(self, service, *, parent=None):
        super().__init__(parent)
        self.service = service
        self._views = []                                # every SelectionView, for teardown
        self._image_tiers = []                          # image tiers, in add order (finest last)
        self._viewer = None
        self._viewer_cb = None                          # the ONE viewer-level pick handler
        self._plots_holder, self._plots_layout = _vertical_stack()
        self._tables_holder, self._tables_layout = _vertical_stack()
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.addWidget(self._plots_holder)
        self.splitter.addWidget(self._tables_holder)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.splitter)

    @property
    def widget(self):
        return self

    def add_plot(self, df, x_col, y_col, view_id, *, title=None, marker_kwargs=None, plot_cls=BrushablePlot):
        """Add a brushable scatter (or a ``plot_cls`` variant — e.g. a VPT custom-painter subclass) to the
        left stack. Returns the ``BrushablePlot`` view."""
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

        fig = Figure(figsize=(4, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        ax = fig.add_subplot(111)
        if title:
            ax.set_title(title, fontsize=9)
        plot = plot_cls(ax, df, x_col, y_col, self.service, view_id, marker_kwargs=marker_kwargs)
        self._plots_layout.addWidget(canvas)
        self._views.append(plot)
        return plot

    def add_table(self, df, view_id, *, title=None):
        """Add a brushable results table to the right stack. The identity columns are hidden from display
        but kept for brushing (the row map keys on ``_pycat_entity_id``). Returns the ``BrushableTable``."""
        from pycat.ui.ui_utils import create_table_view
        from pycat.ui.brushable_table import make_brushable

        table_view = create_table_view(without_identity(df))     # display: no _pycat_* columns
        brushable = make_brushable(table_view, df, self.service, view_id)   # full df carries the entity ids
        if title:
            self._tables_layout.addWidget(QLabel(f"<b>{title}</b>"))
        self._tables_layout.addWidget(table_view)
        if brushable is not None:
            self._views.append(brushable)
        return brushable

    def add_image_tier(self, viewer, labels_layer, df, view_id, *, label_col, source_path=None, reveal='resolve'):
        """Add a napari labels layer as a brushing tier (click an object ↔ the plots/tables). Two tiers
        (cell labels + condensate labels) over one viewer are just two of these; the workspace dispatches
        clicks at the VIEWER level so picking never depends on the active layer. Returns the tier view."""
        tier = BrushableImageTier(viewer, labels_layer, df, self.service, view_id,
                                  label_col=label_col, source_path=source_path, reveal=reveal,
                                  install_callback=False)
        self._views.append(tier)
        self._image_tiers.append(tier)
        self._install_viewer_pick(viewer)
        return tier

    def _install_viewer_pick(self, viewer):
        """One viewer-level mouse handler that tries the image tiers **finest-first** (the last-added tier —
        the condensate layer — before the cell layer), so a click resolves to the most specific object under
        the cursor regardless of which napari layer is active."""
        if viewer is None or self._viewer_cb is not None:
            return
        self._viewer = viewer

        def _pick(_viewer, event):
            for tier in reversed(self._image_tiers):    # finest tier (added last) wins
                try:
                    if tier.pick_at(event.position):
                        return
                except Exception as exc:                # broad-ok: optional_probe — a tier that can't read a click is skipped
                    debug_log('brushable_workspace: a tier failed to pick', exc)

        try:
            viewer.mouse_drag_callbacks.append(_pick)
            self._viewer_cb = _pick
        except Exception as exc:                        # broad-ok: optional_probe — no viewer callbacks headless — tiers still reveal
            debug_log('brushable_workspace: could not install the viewer pick handler', exc)

    def add_offline_crop_view(self, df, view_id, *, title=None):
        """Add the batch 'image' — a `BatchCropView` that shows the selected object's crop read offline from
        its source file (source path + bbox carried on the row). Placed on the left (the image side)."""
        crop_view = BatchCropView(df, self.service, view_id)
        if title:
            self._plots_layout.addWidget(QLabel(f"<b>{title}</b>"))
        self._plots_layout.addWidget(crop_view.label)
        self._views.append(crop_view)
        return crop_view

    def detach(self):
        """Unsubscribe every view from the dispatcher and remove the viewer pick handler. Idempotent."""
        if self._viewer is not None and self._viewer_cb is not None:
            try:
                self._viewer.mouse_drag_callbacks.remove(self._viewer_cb)
            except Exception:                            # broad-ok: ui_cleanup — a callback already removed / viewer gone
                pass
            self._viewer_cb = None
        for view in self._views:
            try:
                view.close()
            except Exception as exc:                     # broad-ok: ui_cleanup — teardown is best-effort; never raise on close
                debug_log('brushable_workspace: a view failed to close', exc)
        self._views = []
        self._image_tiers = []
