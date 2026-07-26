"""Session-loader feature — extracted from MenuManager (ui_decomposition Part 2).

The "Load Session" discovery dialog (scans a folder for saved PyCAT sessions and reopens the chosen one)
lives here. ``MenuManager._open_session_loader`` / ``_load_discovered_session`` are thin wrappers that call
these; the menu action and its label are unchanged. Moved VERBATIM.
"""
from __future__ import annotations

from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit, QWidget, QComboBox, QSlider, QScrollArea, QSizePolicy, QAction, QTabWidget, QToolButton, QFrame


_SESSION_METHOD_SWITCH = {
    'VideoParticleTrackingUI': '_switch_to_vpt_analysis',
    'CondensateAnalysisUI': '_switch_to_condensate_analysis',
    'InVitroFluorUI': '_switch_to_invitro_fluor_analysis',
    'TimeSeriesInVitroFluorUI': '_switch_to_ts_invitro_fluor_analysis',
    'FRAPUI': '_switch_to_frap_analysis',
    'DropletFusionUI': '_switch_to_fusion_analysis',
    'TemperatureDependentUI': '_switch_to_temperature_analysis',
    'FDCurveUI': '_switch_to_fd_curve_analysis',
    'InVitroBFUI': '_switch_to_invitro_bf_analysis',
    'ZStackSegmentationUI': '_switch_to_zstack_analysis',
}


_SESSION_METHOD_BY_DATA = {
    'vpt_tracks': 'VideoParticleTrackingUI',
}


def _open_session_loader(self):
    """Open a folder browser to select a PyCAT output directory and reload."""
    from PyQt5.QtWidgets import (QFileDialog, QDialog, QListWidget, QAbstractItemView)
    from pathlib import Path
    from napari.utils.notifications import (
        show_info as napari_show_info,
    )
    from pycat.file_io.session_loader import (
        scan_output_folder, load_session, session_load_messages)
    from pycat.file_io.session_manifest import discover_sessions

    folder = QFileDialog.getExistingDirectory(
        None, "Select PyCAT Output Folder", "",
        QFileDialog.ShowDirsOnly
    )
    if not folder:
        return
    folder = Path(folder)

    groups = scan_output_folder(folder)
    if not groups:
        # ── The sessions are in SUBFOLDERS, and nothing looked there ──────────────────
        #
        # Saving always creates its own `session_<stem>_<timestamp>/`. The scan above is
        # `folder.iterdir()` — one level, files only — so pointing at the parent directory the
        # sessions were saved into (the obvious thing to do) reported "no outputs found" with
        # every session sitting in plain view underneath it.
        sessions = discover_sessions(folder)
        if sessions:
            self._load_discovered_session(folder, sessions)
            return
        napari_show_warning(
            f"No recognised PyCAT outputs found in {folder.name}.\n"
            "Expected files like *_preprocessed.tiff, *_cell_df.csv, etc."
        )
        return

    dlg = QDialog()
    dlg.setWindowTitle(f"Load Session — {folder.name}")
    dlg.setMinimumWidth(520)
    dlg.setMinimumHeight(480)
    vl = QVBoxLayout(dlg)

    n_files = sum(len(v) for v in groups.values())
    vl.addWidget(QLabel(
        f"Found {n_files} PyCAT output file(s) from "
        f"{len(groups)} image stem(s) in:\n{folder}"
    ))

    group_list = QListWidget()
    group_list.setSelectionMode(QAbstractItemView.MultiSelection)
    for stem, files in sorted(groups.items()):
        n_img = sum(1 for f in files if f["layer_type"] == "image")
        n_lbl = sum(1 for f in files if f["layer_type"] == "labels")
        n_df  = sum(1 for f in files if f["layer_type"] == "dataframe")
        group_list.addItem(
            f"{stem}  —  {n_img} image(s), {n_lbl} label(s), {n_df} table(s)"
        )
    group_list.selectAll()
    vl.addWidget(group_list)

    status_lbl  = QLabel("")
    status_lbl.setWordWrap(True)

    status_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    vl.addWidget(status_lbl)

    btn_row    = QHBoxLayout()
    load_btn   = QPushButton("Load Selected")
    cancel_btn = QPushButton("Cancel")
    btn_row.addWidget(load_btn); btn_row.addWidget(cancel_btn)
    vl.addLayout(btn_row)

    cancel_btn.clicked.connect(dlg.reject)

    def _on_load():
        selected_stems = {
            item.text().split("  —  ")[0].strip()
            for item in group_list.selectedItems()
        }
        if not selected_stems:
            napari_show_warning("No images selected.")
            return

        # A session REPLACES the workspace (clears first, guarded — see clear_before_session_load).
        from pycat.file_io.session import clear_before_session_load
        if not clear_before_session_load(self.central_manager.viewer, self.central_manager):
            status_lbl.setText("Load cancelled — current workspace kept.")
            return

        load_btn.setEnabled(False)
        status_lbl.setText("Loading session…")

        data_instance = self.central_manager.active_data_class

        # ── Off the Qt thread, behind a modal progress dialog ──────────────────────────────
        #
        # `use_worker=True` runs the read/decode on a QThread while a modal QProgressDialog keeps
        # the window painting — the "Python is not responding" freeze otherwise (the 1.6.81/82
        # bars made the wait visible, not shorter). The worker owns that dialog, so the inline
        # `prog_bar` is retired here: two bars for one operation is the UX trap the roadmap
        # flagged. `stems=selected_stems` loads exactly the user's selection (the folder re-scan
        # used to ignore it and load all eight of eight).
        result = load_session(
            folder, self.central_manager.viewer,
            data_instance, stems=selected_stems,
            central_manager=self.central_manager, use_worker=True,
        )

        load_btn.setEnabled(True)
        _status_text, _info_text = session_load_messages(result)
        status_lbl.setText(_status_text)
        napari_show_info(_info_text)
        for p, reason in result["skipped"]:
            print(f"[PyCAT Session] Skipped {p.name}: {reason}")

        # ── Reopen the analysis method and rebuild its VIEW, not just the data ──────────
        #
        # Restoring the dataframes into the repository is not "restoring the session": the user
        # expects the method they were in to reopen with its plots/tables/layers, not an empty
        # panel. So reopen the recorded method (or, for a session saved before the method was
        # recorded, infer it from the restored data) and ask it to rebuild its view. Switching
        # methods PRESERVES the data repository, so the reopened method sees the restored data.
        try:
            _active = result.get('active_method')
            if not _active:
                for _dkey, _cls in _SESSION_METHOD_BY_DATA.items():
                    if _dkey in result["loaded_dfs"]:
                        _active = _cls
                        break
            _switch = _SESSION_METHOD_SWITCH.get(_active)
            if _switch is not None:
                getattr(self.central_manager.analysis_methods_ui, _switch)()
                _cur = getattr(self.central_manager.analysis_methods_ui,
                               'current_analysis_ui', None)
                if _cur is not None and hasattr(_cur, 'restore_session_view') \
                        and _cur.restore_session_view():
                    napari_show_info("Session restored — the analysis view was rebuilt.")
                else:
                    napari_show_info("Session data restored; reopen the analysis "
                                     "method to rebuild its view.")
            elif _active:
                napari_show_info(f"Session data restored. Reopen '{_active}' to rebuild its view.")
        except Exception as _ve:
            print(f"[PyCAT Session] method reopen/restore failed: {_ve}")

        # Clicking Load LOADS and then CLOSES — the completion is reported by the toast above, so
        # the dialog has done its job. Cancel is the only way to dismiss WITHOUT loading. (Before,
        # Load left the dialog open and the user had to click Cancel to get rid of it, which reads
        # as "did it even work?".)
        dlg.accept()

    load_btn.clicked.connect(_on_load)
    dlg.exec_()


def _load_discovered_session(self, folder, sessions):
    """Pick ONE session and load it. **A session picker, not a file multi-select.**

    PyCAT knows what a session needs — its manifest records exactly that — so there is nothing
    for the user to curate here. The old dialog asked which *files* to load, `selectAll()`d them,
    and then ignored the answer anyway. The only question worth asking is *which session*, and
    only when there is more than one.
    """
    from PyQt5.QtWidgets import QInputDialog
    from napari.utils.notifications import show_info as napari_show_info
    from pycat.file_io.session_loader import session_picker_labels, load_session

    chosen = sessions[0]
    if len(sessions) > 1:
        labels = session_picker_labels(sessions)
        label, ok = QInputDialog.getItem(
            None, "Load Session",
            f"{len(sessions)} sessions found in {folder.name}. Which one?",
            labels, 0, False)
        if not ok:
            return
        chosen = sessions[labels.index(label)]

    # A session REPLACES the workspace (clears first, guarded — see clear_before_session_load); abort
    # the load if the user declines to discard existing work.
    from pycat.file_io.session import clear_before_session_load
    if not clear_before_session_load(self.central_manager.viewer, self.central_manager):
        return
    result = load_session(
        chosen['dir'], self.central_manager.viewer,
        self.central_manager.active_data_class,
        central_manager=self.central_manager, use_worker=True,
    )
    napari_show_info(
        f"Restored session '{chosen['name']}': "
        f"{len(result['loaded_layers'])} layer(s), {len(result['loaded_dfs'])} table(s)."
    )
