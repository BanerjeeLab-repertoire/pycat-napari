"""Session save/clear action for the file loader (file_io_decomposition).

`save_and_clear_all` — the Save-and-Clear dialog (pick outputs, confirm), the session write, and the
repository/viewer clear — moved VERBATIM out of `file_io` as a mixin. `FileIOClass` inherits it, so
`file_io.save_and_clear_all(viewer)` (called from the menu and ui_modules) is unchanged. It is Qt-bound (a save
dialog), so it lives here rather than in the Qt-free `session.py`.
"""
from __future__ import annotations

import os

from PyQt5.QtWidgets import QCheckBox, QDialog, QFileDialog, QMessageBox

from pycat.file_io.dialogs import LayerDataframeSelectionDialog


class _SessionActionsMixin:
    def save_and_clear_all(self, viewer):
        """
        Provides options for saving selected layers and dataframes based on user input from a dialog, with additional 
        options for naming files and deciding whether to clear saved data from both the viewer and the repository.

        Parameters
        ----------
        viewer : object
            The Napari viewer object containing the layers and data to be managed.

        Notes
        -----
        This method presents a dialog to the user for selecting which layers and dataframes to save and whether to clear 
        these items from the viewer and repository after saving. It supports flexible file naming and formats, ensuring 
        data is preserved in a user-specified manner.
        """
        self.viewer = viewer
        # Get layer names and dataframe names from the viewer and analysis data abd present them to the user
        dataframe_names = self.central_manager.active_data_class.get_dataframes().keys()
        dialog = LayerDataframeSelectionDialog(self.viewer.layers, dataframe_names)
        result = dialog.exec_()

        # If the user chose "Clear Without Saving", discard everything now.
        if result == QDialog.Accepted and getattr(dialog, 'clear_without_saving', False):
            self._clear_everything(viewer)
            print("[PyCAT] Cleared all layers and data without saving.")
            return

        # If user clicks OK, proceed with saving and clearing
        if result == QDialog.Accepted:
            selected_layers, selected_dataframes, clear_all = dialog.get_selections()
        # If user cancels the dialog, return without saving or clearing
        elif result == QDialog.Rejected:
            return

        # Present a file dialog for saving the selected layers and dataframes, get the save path and base name
        options = QFileDialog.Options()
        default_file_name = os.path.join(os.path.dirname(self.filePath), self.base_file_name + "_placeholder_name")
        save_file_path, _ = QFileDialog.getSaveFileName(None, "Save Files", default_file_name, "All Files (*)", options=options)

        # If the user cancels the save dialog, return without saving or clearing
        if not save_file_path:
            return
        
        # Check if the user has changed the base file name
        user_provided_base_name = os.path.splitext(os.path.basename(save_file_path))[0]
        default_base_name = os.path.splitext(os.path.basename(default_file_name))[0]

        if user_provided_base_name != default_base_name:
            #save_name = os.path.dirname(save_file_path) + os.sep + user_provided_base_name
            save_name = os.path.join(os.path.dirname(save_file_path), user_provided_base_name)
        else:
            #save_name = os.path.dirname(save_file_path) + os.sep + self.base_file_name
            save_name = os.path.join(os.path.dirname(save_file_path), self.base_file_name)

        # Record the save selections now that we have the full picture
        bp = getattr(self.central_manager, '_pycat_batch_processor', None)
        if bp:
            bp.record('save_and_clear', {
                'save_path': save_name,
                'saved_layers': list(selected_layers),
                'saved_dataframes': list(selected_dataframes),
                'clear_all': clear_all,
            })

        # ── Consolidate into ONE session folder (not scattered loose files) ──
        #
        # Files used to be written with a flat `save_name` prefix straight into
        # the chosen directory, so a session's artifacts scattered among the
        # user's data files. Instead, gather them into a dedicated session folder
        # and record a manifest, so the top-level "Load Session" can restore the
        # whole working state (source image referenced by path, derived layers +
        # dataframes reloaded). The user's chosen name/location is honoured as the
        # PARENT; the session folder is created inside it.
        from pycat.file_io import session_manifest as _sm
        _parent_dir = os.path.dirname(save_name)
        _stem = os.path.basename(save_name)
        try:
            _session_dir = _sm.default_session_dir(_parent_dir, self.base_file_name or _stem)
            _session_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            _session_dir = None
        # Inside the session folder, keep the stem-based naming the loader expects.
        _base_in_session = (str(_session_dir / (self.base_file_name or _stem))
                            if _session_dir is not None else save_name)
        save_name = _base_in_session

        # Get the names of all layers in the viewer (needed by the clear logic
        # below, whichever branch runs).
        layer_names = [layer.name for layer in self.viewer.layers]

        # Do the actual file writes in the pure, Qt-free writer. It takes the
        # already-decided inputs (which layers/dataframes, the final in-session
        # save_name, the created session dir) and writes the layer files, the
        # dataframe CSVs, the metadata JSON, and the session manifest.
        _dataframes = self.central_manager.active_data_class.get_dataframes()
        clear_dfs_list = list(_dataframes.keys())
        _file_metadata = self.central_manager.active_data_class.data_repository.get('file_metadata')
        from pycat.file_io.writers import write_session_outputs
        write_session_outputs(
            self.central_manager,
            {layer.name: layer for layer in self.viewer.layers},
            selected_layers,
            selected_dataframes,
            _dataframes,
            _file_metadata,
            save_name,
            _session_dir,
            getattr(self, 'filePath', None),
            self.base_file_name or _stem)

        # Clear all layers and dataframes from the viewer and data instance.
        # If "Remember measurements across clears" is on, preserve the measured
        # sizes so the user doesn't need to re-measure for a second image.
        if clear_all:
            _persist = getattr(self.central_manager, 'persist_measurements', False)
            _dr = self.central_manager.active_data_class.data_repository
            _saved = {}
            if _persist:
                _saved = {k: _dr.get(k) for k in
                          ('ball_radius', 'object_size', 'cell_diameter')
                          if _dr.get(k) is not None}
            self.viewer.layers.select_all()
            self.viewer.layers.remove_selected()
            self.central_manager.active_data_class.reset_values(
                clear_all=True, df_names_to_reset=clear_dfs_list)
            if _persist and _saved:
                _dr2 = self.central_manager.active_data_class.data_repository
                for k, v in _saved.items():
                    try:
                        _dr2[k] = v
                    except Exception:
                        pass
        # Clear only the saved layers and dataframes
        else:
            for layer_name in selected_layers:
                if layer_name in layer_names:
                    self.viewer.layers.remove(layer_name)
            self.central_manager.active_data_class.reset_values(df_names_to_reset=selected_dataframes)

        # Save/Clear is a hard boundary between datasets. Reset the workflow UI
        # and the in-memory batch recorder so subsequent operations start a new
        # process instead of being appended to the previous saved dataset.
        try:
            wc = getattr(self.central_manager, 'workflow_checklist', None)
            if wc is not None:
                wc.reset()
        except Exception:
            pass
        try:
            bp = getattr(self.central_manager, '_pycat_batch_processor', None)
            if bp is not None:
                # Save/Clear ends this dataset's recording. If there are unsaved
                # recorded steps, offer to export the batch config first (unless
                # the user silenced the prompt for this session), so the workflow
                # isn't lost when the recorder resets.
                if (bp.has_unsaved_steps()
                        and not getattr(bp, '_export_prompt_silenced', False)):
                    box = QMessageBox(self.viewer.window._qt_window
                                      if hasattr(self.viewer.window, '_qt_window') else None)
                    box.setIcon(QMessageBox.Question)
                    box.setWindowTitle("Export batch config?")
                    box.setText(
                        "This batch workflow recording hasn't been saved.\n\n"
                        "Save-and-Clear ends the current recording. Export the "
                        "batch config now so you can replay this workflow later?")
                    box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard)
                    box.setDefaultButton(QMessageBox.Save)
                    _dont_ask = QCheckBox("Don't ask again this session")
                    box.setCheckBox(_dont_ask)
                    choice = box.exec_()
                    if _dont_ask.isChecked():
                        bp._export_prompt_silenced = True
                    if choice == QMessageBox.Save:
                        from pathlib import Path as _Path
                        path, _ = QFileDialog.getSaveFileName(
                            None, "Save Batch Config", "", "JSON (*.json)")
                        if path:
                            bp.save_config(_Path(path))
                bp.terminate_recording()
        except Exception:
            pass
