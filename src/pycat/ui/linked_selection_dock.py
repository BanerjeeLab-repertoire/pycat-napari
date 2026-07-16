"""**One place that answers "what did I just click?" — and stays put.**

── What it replaces ────────────────────────────────────────────────────────────────────────

Clicking a point in a plot added an image layer named ``object <N>`` holding that object's crop. The
layer is reused for the *same* object, so it is one layer per **distinct object clicked** — explore a
scatter for a minute and the layer list fills with crops you have to clean up by hand. Worse, the
name is keyed on ``object_id`` alone: ``object 7`` from two different masks **collide onto one
layer**, so a click on one segmentation silently overwrites the other's crop.

This is one dock. It updates in place, it owns no analysis state, and closing it costs nothing.

── What it shows ───────────────────────────────────────────────────────────────────────────

The crop, and the facts needed to know *which* object it is: where it came from, which frame, its
parent, and the measurements on its row. The crop goes through increment 1's ``crop_for_ref`` —
slice-before-materialize — so previewing an object in a 40-frame acquisition reads **one plane**, not
the movie.

It subscribes to the **deferred** half of `SelectionService` (increment 4): reading pixels is the
expensive part, so dragging across a scatter updates this once, on the trailing edge, for the point
the user actually stopped on.
"""

from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

from pycat.utils.general_utils import debug_log

DOCK_NAME = 'Linked Selection'
_VIEW_ID = 'linked_selection_dock'

_PREVIEW_PX = 220


def _to_pixmap(crop):
    """A grayscale crop as a QPixmap, scaled to the preview box.

    Contrast-stretched per crop on purpose: an 8-pixel object in a dim field is invisible at the
    acquisition's global range, and this is a *preview* — it answers "which object is this?", not
    "what is its intensity?". The measurements below it are the numbers.
    """
    arr = np.asarray(crop, dtype=float)
    if arr.ndim > 2:
        arr = arr.reshape(arr.shape[-2], -1) if arr.ndim == 3 else arr.squeeze()
    if arr.size == 0:
        return None

    lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
    span = (hi - lo) or 1.0
    eight = np.clip((arr - lo) / span * 255.0, 0, 255).astype(np.uint8)
    eight = np.ascontiguousarray(eight)

    height, width = eight.shape
    image = QImage(eight.data, width, height, width, QImage.Format_Grayscale8)
    return QPixmap.fromImage(image.copy()).scaled(
        _PREVIEW_PX, _PREVIEW_PX, Qt.KeepAspectRatio, Qt.FastTransformation)


class LinkedSelectionWidget(QWidget):
    """The dock's contents. Subscribes to the shared dispatcher; owns nothing else."""

    def __init__(self, viewer=None, central_manager=None, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.central_manager = central_manager
        self._ref = None
        self._pinned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.preview = QLabel('Click a point in a plot.')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(_PREVIEW_PX)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.preview)

        self.title = QLabel('')
        self.title.setStyleSheet('font-weight: bold;')
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        facts = QGroupBox('Where it came from')
        self._facts = QGridLayout(facts)
        self._fact_labels = {}
        for row, key in enumerate(('Dataset', 'Frame', 'Parent', 'Layer')):
            name = QLabel(f'{key}:')
            value = QLabel('—')
            value.setWordWrap(True)
            self._facts.addWidget(name, row, 0)
            self._facts.addWidget(value, row, 1)
            self._fact_labels[key] = value
        layout.addWidget(facts)

        self.linkability = QLabel('')
        self.linkability.setWordWrap(True)
        self.linkability.setStyleSheet('color: gray;')
        layout.addWidget(self.linkability)

        buttons = QHBoxLayout()
        self.reveal_button = QPushButton('Reveal in image')
        self.reveal_button.setToolTip(
            'Move the camera and frame to this object.\n\n'
            'Clicking a point does NOT do this on its own — exploratory clicking should not keep '
            'taking your view somewhere. Double-clicking a point does the same as this button.')
        self.reveal_button.clicked.connect(self.reveal)
        buttons.addWidget(self.reveal_button)

        self.pin_button = QPushButton('Pin')
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip(
            'Keep showing this object while you click around. Unpin to follow the selection again.')
        self.pin_button.toggled.connect(self._on_pin)
        buttons.addWidget(self.pin_button)
        layout.addLayout(buttons)

        layout.addStretch(1)
        self._set_enabled(False)

    # ── the dispatcher ────────────────────────────────────────────────────────────────────
    def subscribe(self, service):
        """Hear about selections. The **deferred** half: this reads pixels, so a drag across a
        scatter updates it once, on the point the user stopped on (increment 4's debounce)."""
        if service is None:
            return self
        try:
            service.subscribe_deferred(_VIEW_ID, self._on_selection)
        except Exception as exc:
            debug_log('linked selection: could not subscribe to the dispatcher', exc)
        return self

    def _on_selection(self, selection):
        if self._pinned:
            return          # the user asked to keep looking at this one
        ref = self._ref_for(selection)
        if ref is not None:
            self.show_ref(ref)

    def _ref_for(self, selection):
        """The `ObjectRef` behind a `Selection`.

        The service speaks entity ids — plain strings — because that is what survives a sort. The
        ref that a plot handed to `make_pickable` is what carries the bbox, so the hub keeps the
        mapping. A selection this dock cannot resolve is one it says nothing about.
        """
        try:
            hub = getattr(self, 'hub', None)
            if hub is not None and selection.primary_id:
                return hub._refs.get(selection.primary_id)
        except Exception as exc:
            debug_log('linked selection: could not resolve the selection to a ref', exc)
        return None

    # ── contents ──────────────────────────────────────────────────────────────────────────
    def show_ref(self, ref):
        """Show ``ref``: its crop, and the facts that say which object it is."""
        self._ref = ref
        if ref is None:
            self._set_enabled(False)
            self.preview.setText('Click a point in a plot.')
            return

        self.title.setText(self._describe(ref))
        self._fact_labels['Dataset'].setText(str(ref.source_path or '—'))
        self._fact_labels['Frame'].setText('—' if ref.frame is None else str(ref.frame))
        self._fact_labels['Parent'].setText('—' if ref.parent_id is None else str(ref.parent_id))
        self._fact_labels['Layer'].setText(
            (ref.source_layer_id or '—')[:8] if ref.source_layer_id else '—')
        self.linkability.setText(
            '' if getattr(ref, 'entity_id', None) else
            'This table has no stable identity — its points are matched by row position, so '
            'sorting or filtering it will link the wrong object.')
        self._set_enabled(True)
        self._show_crop(ref)

    @staticmethod
    def _describe(ref):
        kind = (ref.tags or {}).get('target') or 'object'
        return f'{kind} {ref.object_id}' if ref.object_id is not None else str(kind)

    def _show_crop(self, ref):
        try:
            from pycat.utils.brushing import crop_for_ref
            crop, message = crop_for_ref(ref, viewer=self.viewer)
        except Exception as exc:
            debug_log('linked selection: could not crop the object', exc)
            crop, message = None, ''

        if crop is None:
            # *"Nothing happened" is the worst possible answer to a click* — say why.
            self.preview.setPixmap(QPixmap())
            self.preview.setText(message or 'This object cannot be shown as an image.')
            return

        pixmap = _to_pixmap(crop)
        if pixmap is None:
            self.preview.setText('This object cannot be shown as an image.')
            return
        self.preview.setPixmap(pixmap)

    # ── actions ───────────────────────────────────────────────────────────────────────────
    def reveal(self):
        """**Explicitly** go to the object — the one gesture that is allowed to move the view."""
        if self._ref is None or self.viewer is None:
            return
        try:
            from pycat.utils.object_ref import resolve_in_viewer
            resolve_in_viewer(self._ref, self.viewer, centre=True)
        except Exception as exc:
            debug_log('linked selection: could not reveal the object', exc)

    def _on_pin(self, checked):
        self._pinned = bool(checked)

    def _set_enabled(self, on):
        self.reveal_button.setEnabled(bool(on) and self.viewer is not None)
        self.pin_button.setEnabled(bool(on))
        if not on:
            self.title.setText('')
            for label in self._fact_labels.values():
                label.setText('—')
            self.linkability.setText('')


class LinkedSelectionDock:
    """Owns the one dock. Re-showing replaces it rather than stacking another."""

    def __init__(self, viewer, central_manager=None):
        self.viewer = viewer
        self.central_manager = central_manager
        self.widget = None
        self._dock = None

    def show(self):
        if self._dock is not None:
            return self.widget
        self.widget = LinkedSelectionWidget(self.viewer, self.central_manager)
        self.widget.subscribe(getattr(self.central_manager, 'selection', None))
        self._dock = self.viewer.window.add_dock_widget(
            self.widget, name=DOCK_NAME, area='right')
        return self.widget

    def close(self):
        try:
            if self._dock is not None:
                self.viewer.window.remove_dock_widget(self._dock)
        except Exception as exc:
            debug_log('linked selection: could not remove the dock', exc)
        finally:
            self._dock = None
            self.widget = None
