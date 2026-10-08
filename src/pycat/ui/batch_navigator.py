"""**Batch results → the image.** A selected cell or condensate from ANY image of a batch opens that image in
napari, centres on the object and outlines it; a click on an object in that image selects it back in the
plots and tables.

It is one more ``SelectionView`` on the application ``SelectionService`` (the VPT results dock's pattern):
plots and tables select by ``_pycat_entity_id``; this view maps an entity id to (image, object kind, label,
bbox) and does the rest. Each image's batch outputs — the image the condensates were measured on, the cell
labels and the per-condensate labels, all in the pixel space of the tables' bboxes — are loaded once, the
first time one of its objects is selected, as layers named ``"... [<image stem>]"``; selecting an object
from another image shows that image's layers and hides the previous one's.
"""
from __future__ import annotations

import pathlib

import numpy as np

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.general_utils import debug_log
from pycat.utils.selection_service import Selection

SOURCE_PATH_COLUMN = '_pycat_source_path'
SELECTION_LAYER = 'Batch selection'
# (output-file suffix, layer title, kind) — kind 'image' or the object kind its labels index
_OUTPUTS = (('measured_image', 'Image', 'image'),
            ('labeled_cells', 'Cells', 'cell'),
            ('condensate_labels', 'Condensates', 'condensate'))


def _bbox(row):
    try:
        return tuple(float(row[c]) for c in ('bbox_y0', 'bbox_x0', 'bbox_y1', 'bbox_x1'))
    except (KeyError, TypeError, ValueError):
        return None


class BatchImageNavigator:
    """``tables`` maps 'cell' / 'condensate' to the batch's concatenated tables (rows carry the entity id,
    ``_pycat_source_path``, bbox, and ``label`` / ``global_punctum_label``)."""

    def __init__(self, viewer, output_dir, tables, service, view_id='batch.image', *, read=None):
        self.viewer = viewer
        self.output_dir = pathlib.Path(output_dir)
        self.service = service
        self.view_id = str(view_id)
        self._read = read or _read_tiff
        self._objects = {}            # eid -> (stem, kind, label, bbox)
        self._by_label = {}           # (stem, kind, label) -> eid
        self._layers = {}             # stem -> {kind: layer}
        self.current_stem = None
        self._cb = None
        for kind, label_col in (('cell', 'label'), ('condensate', 'global_punctum_label')):
            df = tables.get(kind)
            if df is None or ENTITY_ID_COLUMN not in df.columns:
                continue
            for _, row in df.iterrows():
                eid, src = row.get(ENTITY_ID_COLUMN), row.get(SOURCE_PATH_COLUMN)
                if eid is None or not isinstance(src, str):
                    continue
                stem = pathlib.Path(src).stem
                try:
                    label = int(row[label_col])
                except (KeyError, TypeError, ValueError):
                    label = 0
                self._objects[str(eid)] = (stem, kind, label, _bbox(row))
                if label > 0:
                    self._by_label[(stem, kind, label)] = str(eid)
        try:
            # The EXPENSIVE half of a selection (it may read an image's files): on the service's trailing
            # debounce, so dragging across a plot loads only the image the user stops on.
            service.subscribe_deferred(self.view_id, self.apply_selection)
        except Exception as exc:                        # broad-ok: optional_probe — a navigator that can't subscribe still loads images
            debug_log('batch_navigator: could not register the view', exc)
        self._install_pick()

    # ── loading an image's outputs ─────────────────────────────────────────────────────────────
    def show_image(self, stem):
        """Make ``stem``'s layers present and visible, and every other batch image's hidden."""
        if stem not in self._layers:
            folder = self.output_dir / stem
            layers = {}
            for suffix, title, kind in _OUTPUTS:
                data = self._read(folder / f'{stem}_{suffix}.tiff')
                if data is None:
                    continue
                name = f'{title} [{stem}]'
                if kind == 'image':
                    layers[kind] = self.viewer.add_image(data, name=name, colormap='gray')
                else:
                    layers[kind] = self.viewer.add_labels(np.asarray(data).astype(np.int32), name=name,
                                                          opacity=0.35)
            self._layers[stem] = layers
        for other, layers in self._layers.items():
            for layer in layers.values():
                layer.visible = (other == stem)
        self.current_stem = stem
        return self._layers[stem]

    # ── inbound: a selection elsewhere opens and outlines the object ───────────────────────────
    def apply_selection(self, state):
        eids = [str(e) for e in (getattr(state, 'entity_ids', ()) or ())]
        primary = getattr(state, 'primary_id', None) or (eids[0] if eids else None)
        info = self._objects.get(str(primary)) if primary is not None else None
        if info is None:
            return
        stem, _kind, _label, bbox = info
        try:
            self.show_image(stem)
            if bbox is not None:
                self._outline(bbox)
                self._centre(bbox)
        except Exception as exc:                        # broad-ok: ui_cleanup — revealing is best-effort; never fail a selection over it
            debug_log('batch_navigator: could not reveal the selection', exc)

    def _outline(self, bbox):
        y0, x0, y1, x1 = bbox
        pad = 2.0
        rect = np.array([[y0 - pad, x0 - pad], [y0 - pad, x1 + pad], [y1 + pad, x1 + pad], [y1 + pad, x0 - pad]])
        if SELECTION_LAYER in self.viewer.layers:
            layer = self.viewer.layers[SELECTION_LAYER]
            layer.data = [rect]
            layer.visible = True
        else:
            self.viewer.add_shapes([rect], shape_type='rectangle', name=SELECTION_LAYER,
                                   edge_color='#ff8c00', face_color='transparent', edge_width=2)

    def _centre(self, bbox):
        y0, x0, y1, x1 = bbox
        camera = self.viewer.camera
        camera.center = ((y0 + y1) / 2.0, (x0 + x1) / 2.0)
        span = max(y1 - y0, x1 - x0, 8.0) * 6.0       # the object and its surroundings fill the view
        canvas = getattr(self.viewer, '_canvas_size', None) or (600, 600)
        camera.zoom = float(min(canvas)) / span

    # ── outbound: a click on an object in the shown image selects it ───────────────────────────
    def _install_pick(self):
        def _pick(_viewer, event):
            self.pick_at(event.position)
        try:
            self.viewer.mouse_drag_callbacks.append(_pick)
            self._cb = _pick
        except Exception as exc:                        # broad-ok: optional_probe — no viewer callbacks headless — reveal still works
            debug_log('batch_navigator: could not install the image click', exc)

    def pick_at(self, position):
        """Select the condensate (finest first) or cell under ``position`` in the shown image. Returns the
        entity id selected, or None."""
        if self.current_stem is None or self.service.is_busy:
            return None
        layers = self._layers.get(self.current_stem, {})
        for kind in ('condensate', 'cell'):
            layer = layers.get(kind)
            if layer is None:
                continue
            try:
                value = layer.get_value(position, world=True)
            except Exception as exc:                    # broad-ok: ui_cleanup — a click outside the data / mid-transition
                debug_log('batch_navigator: could not read the clicked label', exc)
                continue
            eid = self._by_label.get((self.current_stem, kind, int(value))) if value else None
            if eid is not None:
                bbox = self._objects[eid][3]
                if bbox is not None:
                    self._outline(bbox)                 # self-highlight: our own selection is not echoed back
                self.service.select(Selection(entity_ids=(eid,), primary_id=eid, mode='selected',
                                              source_view=self.view_id,
                                              generation=self.service.next_generation()))
                return eid
        return None

    def close(self):
        try:
            self.service.unsubscribe(self.view_id)
        except Exception as exc:                        # broad-ok: ui_cleanup — teardown is best-effort; never raise on close
            debug_log('batch_navigator: unsubscribe failed', exc)
        if self._cb is not None:
            try:
                self.viewer.mouse_drag_callbacks.remove(self._cb)
            except ValueError:
                pass
            self._cb = None


def _read_tiff(path):
    """The array in ``path``, or None when the batch did not write it (an older batch / a step not run)."""
    path = pathlib.Path(path)
    if not path.is_file():
        return None
    import tifffile
    return tifffile.imread(str(path))
