"""
**Ending a session. Not starting one.**

``_clear_everything`` resets the napari space to the workflow start state — removes the layers,
empties the data repository, resets the batch recorder, drops the cached readers and their open file
handles.

── Why it is here and not in `file_io.py` ───────────────────────────────────────────────

It depends on **`viewer` and `central_manager` and nothing else.** No path, no reader, no metadata —
it is not *doing* I/O, it is *undoing* it. It sat in a 3,108-line class next to the loaders because
that is where the loaders were, not because it belonged with them.

*The rule for this split: take what depends on nothing, first.*
"""

from __future__ import annotations


def _clear_everything(viewer, central_manager):
    """
    Reset the napari space to the workflow start state: remove all layers,
    reset the data repository/dataframes, and reset the workflow checklist
    progress bar. Saves nothing. Shared by Save & Clear's discard option and
    the top-bar Clear button.
    """
    # Drop the cached readers. They hold open file handles, and after a Clear the user is
    # done with those files. *(The cache exists because one drag-and-drop used to construct
    # the reader three to four times — see `image_reader._READER_CACHE`.)*
    try:
        from pycat.file_io.image_reader import clear_reader_cache
        clear_reader_cache()
    except Exception:
        pass

    viewer = viewer
    try:
        df_names = list(central_manager.active_data_class.get_dataframes().keys())
    except Exception:
        df_names = []
    _persist = getattr(central_manager, 'persist_measurements', False)
    _dr = central_manager.active_data_class.data_repository
    _saved = {}
    if _persist:
        _saved = {k: _dr.get(k) for k in
                  ('ball_radius', 'object_size', 'cell_diameter')
                  if _dr.get(k) is not None}
    viewer.layers.select_all()
    viewer.layers.remove_selected()
    central_manager.active_data_class.reset_values(
        clear_all=True, df_names_to_reset=df_names)
    # Dismiss any lingering napari notifications from the previous session.
    try:
        from napari.utils.notifications import notification_manager
        notification_manager.records.clear()
    except Exception:
        pass
    if _persist and _saved:
        _dr2 = central_manager.active_data_class.data_repository
        for k, v in _saved.items():
            try:
                _dr2[k] = v
            except Exception:
                pass
    # Reset the workflow checklist so the next dataset starts from step 1.
    try:
        wc = getattr(central_manager, 'workflow_checklist', None)
        if wc is not None:
            wc.reset()
    except Exception:
        pass

    # Reset the batch recording so the recorded-steps list starts empty for
    # the next dataset. The plain Clear button previously left the recording
    # intact (only Save & Clear reset it via terminate_recording); both paths
    # now reset it here. clear_recording() also flips the record toggle back
    # to OFF and resyncs the toolbar button.
    try:
        bp = getattr(central_manager, '_pycat_batch_processor', None)
        if bp is not None:
            bp.clear_recording()
    except Exception:
        pass

    # Reset the "Measure Line(s)" status circle back to red on clear, UNLESS
    # the user asked to remember measurements across clears (then the
    # measurement — and its done state — carries over).
    try:
        if not _persist:
            tb = getattr(central_manager, 'toolbox_functions_ui', None)
            mls = getattr(tb, '_measure_line_status', None)
            if mls is not None and hasattr(mls, 'reset'):
                mls.reset()
    except Exception:
        pass

    # Reset the optional "Run Upscaling" status circle on clear (its upscaled
    # output layers are removed, so the step is no longer "done").
    try:
        tb = getattr(central_manager, 'toolbox_functions_ui', None)
        ups = getattr(tb, '_upscaling_status', None)
        if ups is not None and hasattr(ups, 'reset'):
            ups.reset()
    except Exception:
        pass

    # Re-show the pixel-size gate for the next dataset. Clearing wipes the
    # scale from the data repository, but the gate only re-evaluates on its
    # own triggers — call its reset so it reappears (honoring the persist /
    # "keep for session" checkbox, which retains the remembered value).
    try:
        pxr = getattr(central_manager, '_pixel_gate_refresh', None)
        if pxr is not None and hasattr(pxr, '_reset_gate'):
            pxr._reset_gate()
    except Exception as _reset_exc:
        # Without this reset the gate **never reappears for the next dataset** — so the second
        # file of a session is loaded with no calibration check at all, and nothing says so.
        from pycat.utils.general_utils import report_guarantee_failure
        report_guarantee_failure("file_io: pixel-size gate reset after clear", _reset_exc)


def _auto_clear_before_load(viewer, central_manager):
    """Reset to the workflow start state before loading a new dataset.

    Returns True if it is safe to proceed with the load, False if the user
    declined to discard existing work.

    If no image layers are present, there is nothing to clear and we proceed
    immediately. If layers exist, we treat that as potentially-unsaved work
    and ask for confirmation (mirroring the Clear button's safety prompt)
    before wiping — so a new load never silently discards analysis. On
    confirmation we reuse _clear_everything, the same full reset the Clear
    button uses (layers, data repository, dataframes, workflow checklist,
    and batch recording), so the new dataset starts from a clean state.
    """
    from qtpy.QtWidgets import QMessageBox
    try:
        has_layers = len(viewer.layers) > 0
    except Exception:
        has_layers = False
    if not has_layers:
        return True  # nothing to clear

    # There is existing work — confirm before discarding it.
    try:
        from qtpy.QtWidgets import QMessageBox
        resp = QMessageBox.question(
            None, "Load new image?",
            "Loading a new image will clear the current layers and reset the "
            "workflow.\n\nAny unsaved analysis will be lost. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return False
    except Exception:
        # If the dialog can't be shown, err on the side of NOT destroying
        # work silently — proceed only if there were no layers (handled
        # above). Here layers exist, so bail out safely.
        return False

    try:
        _clear_everything(viewer, central_manager)
    except Exception:
        # If the reset fails, still allow the load to proceed (napari will
        # add the new layers alongside; not ideal but not destructive).
        pass
    return True


def clear_all_without_saving(viewer, central_manager, confirm=True):
    """
    Clear all layers and data without saving, resetting the workspace to the
    beginning-of-workflow (startup) state. If `confirm` is True, asks for
    explicit confirmation first and warns that all unsaved data will be lost.
    """
    from qtpy.QtWidgets import QMessageBox

    if confirm:
        reply = QMessageBox.warning(
            None, "Clear everything without saving?",
            "This resets the workspace to the start of a workflow.\n\n"
            "All layers and analysis data will be permanently cleared and "
            "NOTHING will be saved. All unsaved data will be lost.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
    _clear_everything(viewer, central_manager)
    print("[PyCAT] Workspace cleared without saving.")
