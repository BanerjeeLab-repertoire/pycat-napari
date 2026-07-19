"""**Switch microscope position in place — no reopen, no materialise, no stale frame.**

A multi-position acquisition (CZI/IMS/OME-TIFF) now loads **one** scene at a time (see
`file_io._open_stack_generic` + `file_io.scenes`). This dock is how you change which one: a dropdown of
the positions, and picking one rebinds the loaded layer(s) to that scene *in place*.

── The three things a scene switch must get right ──────────────────────────────────────────────
1. **Never a stale frame.** The new layer data is a fresh `_SceneStack` pinned to the new scene, which
   re-pins the reader on every read — so a frame from the previous position can never be served. This
   is the headline hazard, handled by construction rather than by clearing a cache.
2. **Re-read calibration.** A position can legitimately differ in pixel size, so `update_metadata`
   re-reads the *now-current* scene (not a fixed scene 0). Assuming the old calibration carries over
   would silently mis-scale every length and area.
3. **Don't leave stale derived layers looking current.** Analyses computed on the previous position are
   not recomputed by a switch; they are tagged with the scene they were computed on and the user is
   warned, so a mask from position 1 cannot masquerade as belonging to position 2.

The first frame of a new scene can be a slow random-access read, so it is warmed **off the Qt thread**
(`qt_worker.run_with_progress`) — the switch shows a progress dialog, never "Not Responding".
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from pycat.utils.general_utils import debug_log
from pycat.file_io.scenes import (build_scene_stack, list_scenes, scene_of, tag_scene_layer,
                                   SCENE_TAG_KEY)

DOCK_NAME = 'Scene / Position'
_PRIOR_SCENE_TAG = 'computed_on_scene'      # stamped on a derived layer when the scene switches under it


class SceneSwitcherWidget(QWidget):
    """The dock contents: a position dropdown that rebinds the loaded scene layers in place."""

    def __init__(self, viewer=None, central_manager=None, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.central_manager = central_manager
        self._reader = None
        self._scenes = []
        self._switching = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        row = QHBoxLayout()
        row.addWidget(QLabel('Position:'))
        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.currentIndexChanged.connect(self._on_selected)
        row.addWidget(self.combo, 1)
        layout.addLayout(row)

        self.refresh_button = QPushButton('Refresh positions')
        self.refresh_button.clicked.connect(self.refresh)
        layout.addWidget(self.refresh_button)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color: gray;')
        layout.addWidget(self.status)
        layout.addStretch(1)

        self.refresh()

    # ── discovery ───────────────────────────────────────────────────────────────────────────
    def _scene_layers(self):
        """The image layers that hold a scene — the ones a switch rebinds. Keyed by the scene tag the
        loader stamps, so only multi-position layers are touched."""
        layers = getattr(self.viewer, 'layers', []) if self.viewer is not None else []
        return [layer for layer in layers if scene_of(layer)]

    def _reader_for(self, layer):
        """The scenes-capable reader behind ``layer``. The layer pins its readers via
        ``metadata['pycat_image_source']`` (the generic loader retains ``(reader, dask)`` tuples), so
        unwrap those and find the one exposing ``set_scene``/``scenes``; failing that, re-open the file
        (the reader cache hands back the same handle)."""
        meta = getattr(layer, 'metadata', {}) or {}
        src = meta.get('pycat_image_source')
        path = getattr(src, 'file_path', None) if src is not None else None
        if src is not None:
            for retained in getattr(src, 'readers', []):
                for obj in (retained if isinstance(retained, tuple) else (retained,)):
                    if hasattr(obj, 'set_scene') and hasattr(obj, 'scenes'):
                        return obj, path
        if path:
            try:
                from pycat.file_io.image_reader import open_image
                return open_image(path), path
            except Exception as exc:
                debug_log('scene switcher: could not re-open the reader', exc)
        return None, path

    def refresh(self):
        """Re-scan for a multi-position file and repopulate the dropdown."""
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            scene_layers = self._scene_layers()
            if not scene_layers:
                self._reader, self._scenes = None, []
                self.combo.setEnabled(False)
                self.status.setText('Open a multi-position file to switch positions.')
                return
            reader, _path = self._reader_for(scene_layers[0])
            self._reader = reader
            self._scenes = list_scenes(reader) if reader is not None else []
            if not self._scenes:
                self.combo.setEnabled(False)
                self.status.setText('This file exposes no switchable positions.')
                return
            for name in self._scenes:
                self.combo.addItem(str(name))
            current = scene_of(scene_layers[0])
            if current in self._scenes:
                self.combo.setCurrentIndex(self._scenes.index(current))
            self.combo.setEnabled(True)
            self.status.setText(f'{len(self._scenes)} positions. Switching rebinds the loaded '
                                f'layer(s) in place.')
        finally:
            self.combo.blockSignals(False)

    # ── the switch ──────────────────────────────────────────────────────────────────────────
    def _on_selected(self, idx):
        if self._switching or idx < 0 or idx >= len(self._scenes):
            return
        self.switch_to(self._scenes[idx])

    def switch_to(self, target):
        """Rebind every loaded scene layer to ``target`` — the testable core of the switch.

        Builds a fresh ``_SceneStack`` per layer and warms its first frame OFF the Qt thread, then, on
        the caller's thread, swaps each layer's data, re-reads the scene's calibration, re-tags, and
        marks any derived layers as belonging to the position they were computed on.
        """
        reader = self._reader
        scene_layers = self._scene_layers()
        if reader is None or not scene_layers:
            return
        priors = {scene_of(layer) for layer in scene_layers}
        prior = next(iter(priors)) if len(priors) == 1 else None

        self._switching = True
        try:
            plans = [(layer, int(getattr(getattr(layer, 'data', None), '_ci', 0) or 0))
                     for layer in scene_layers]

            def _work(progress):
                # Worker thread: build the new-scene wrappers and warm the first (slow) frame. It must
                # NOT touch napari — it computes and returns; the caller swaps the data.
                built = []
                for i, (layer, channel_idx) in enumerate(plans):
                    stack = build_scene_stack(reader, target, channel_idx=channel_idx)
                    try:
                        _ = stack[0]                        # warm the first frame off-thread
                    except Exception as exc:                # a bad read is a finding, not a crash
                        debug_log('scene switcher: first-frame warm failed', exc)
                    built.append((layer, stack))
                    progress(i + 1, len(plans))
                return built

            from pycat.utils.qt_worker import run_with_progress
            built = run_with_progress(
                _work, title='Switching position', text=f"Loading position '{target}'…",
                parent=getattr(getattr(self.viewer, 'window', None), '_qt_window', None))

            for layer, stack in built:
                try:
                    layer.data = stack                      # in-place rebind; identity/tags preserved
                    tag_scene_layer(layer, target)
                except Exception as exc:
                    debug_log('scene switcher: could not rebind a layer to the new scene', exc)

            self._reread_metadata(reader)
            self._mark_prior_scene_derived(prior, scene_layers)
        finally:
            self._switching = False

    def _reread_metadata(self, reader):
        """Re-read the NOW-current scene's calibration into the data repository — a position can
        legitimately differ, so the old pixel size must not carry over."""
        try:
            adc = getattr(self.central_manager, 'active_data_class', None)
            if adc is not None and hasattr(adc, 'update_metadata'):
                adc.update_metadata(reader)
        except Exception as exc:
            debug_log('scene switcher: could not re-read per-scene metadata', exc)

    def _mark_prior_scene_derived(self, prior, scene_layers):
        """Stamp every DERIVED layer with the scene it was computed on, so a switch cannot leave a
        mask/labels from the previous position looking like it belongs to the new one."""
        if not prior or self.viewer is None:
            return
        sources = set(id(layer) for layer in scene_layers)
        stale = []
        for layer in list(getattr(self.viewer, 'layers', [])):
            if id(layer) in sources or scene_of(layer):
                continue                                    # a live scene layer, not a derived one
            try:
                from pycat.utils.layer_tags import tag_layer, get_tag
                if get_tag(layer, _PRIOR_SCENE_TAG):
                    continue
                if tag_layer(layer, _PRIOR_SCENE_TAG, str(prior), source='inferred'):
                    stale.append(getattr(layer, 'name', '?'))
            except Exception as exc:
                debug_log('scene switcher: could not mark a derived layer', exc)
        if stale:
            try:
                from pycat.utils.notify import show_warning
                show_warning(
                    f"{len(stale)} layer(s) were computed on position '{prior}' and were NOT "
                    f"recomputed by this switch: {', '.join(stale)}. They are tagged as belonging to "
                    f"that position so they are not mistaken for the new one.")
            except Exception as exc:
                debug_log('scene switcher: could not warn about stale derived layers', exc)


class SceneSwitcherDock:
    """Owns the one switcher dock. Re-showing replaces it rather than stacking another."""

    def __init__(self, viewer, central_manager=None):
        self.viewer = viewer
        self.central_manager = central_manager
        self.widget = None
        self._dock = None

    def show(self):
        if self._dock is not None:
            self.widget.refresh()
            return self.widget
        self.widget = SceneSwitcherWidget(self.viewer, self.central_manager)
        self._dock = self.viewer.window.add_dock_widget(self.widget, name=DOCK_NAME, area='right')
        return self.widget

    def close(self):
        try:
            if self._dock is not None:
                self.viewer.window.remove_dock_widget(self._dock)
        except Exception as exc:
            debug_log('scene switcher: could not remove the dock', exc)
        finally:
            self._dock = None
            self.widget = None
