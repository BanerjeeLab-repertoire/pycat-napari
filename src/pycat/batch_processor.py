"""
pycat/batch_processor.py
========================
PyCAT Batch Processing Module
------------------------------
Provides two complementary features:

1. **Session Config Recording** – Every time a PyCAT widget calls a
   processing function the call is automatically appended to an in-memory
   log.  At any point the user can export that log as a JSON config file
   (or TOML, if preferred) and reload it later to replay the exact same
   sequence of steps on any folder of images.

2. **Batch Runner** – A toolbar button ("Batch Run") opens a small dialog
   that lets the user:
   - Load a saved config file  (or use the one already recorded this session)
   - Pick a folder to process  (auto-detects the folder of the currently
     open image, with a manual-override button)
   - Run the pipeline on every compatible image file in that folder,
     saving outputs to a subfolder called  `pycat_batch_results/`

Usage
-----
Integrate into PyCAT's `__init__.py` / `run_pycat_func()`:

    from pycat.batch_processor import BatchProcessor, add_batch_toolbar_button

    def run_pycat_func():
        viewer = napari.Viewer()
        ...                          # existing PyCAT setup
        bp = BatchProcessor(viewer)
        add_batch_toolbar_button(viewer, bp)
        napari.run()

Then wrap every analysis function you want recorded with the decorator:

    from pycat.batch_processor import record_step

    @record_step("preprocess")
    def run_preprocessing(params):
        ...

Or call `bp.record(step_name, params)` directly inside any widget callback.

Dependencies
------------
All dependencies are already part of PyCAT's environment:
    json, pathlib, datetime  – stdlib
    PyQt5                    – already required by napari
    napari                   – already required
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Qt imports – PyCAT already depends on PyQt5 via napari
# ---------------------------------------------------------------------------
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# Supported image extensions (same as PyCAT's Open 2D Images dialog)
SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".czi", ".png", ".jpg", ".jpeg", ".ims"}


def _first_step(config: Dict, *names: str) -> Optional[Dict]:
    """Return the first recorded step matching one of *names*."""
    for step in config.get("steps", []):
        if step.get("step") in names:
            return step
    return None


def _config_uses_split_source_files(config: Dict) -> bool:
    """True when the recorded open step came from multiple source files."""
    open_step = _first_step(config, "open_image", "open_stack")
    if not open_step:
        return False
    params = open_step.get("params", {}) or {}
    src = params.get("source_files") or []
    return len(src) > 1


def _primary_source_suffix(config: Dict) -> Optional[str]:
    """Infer the filename suffix that identifies the primary file in split-file batches.

    If the user recorded a workflow by opening two separate files as channels,
    the batch folder contains pairs/groups. Processing every file independently
    double-counts the dataset. We process only files that look like the first
    recorded source and let replay_open_image locate its companions.
    """
    open_step = _first_step(config, "open_image", "open_stack")
    if not open_step:
        return None
    params = open_step.get("params", {}) or {}
    src = params.get("source_files") or []
    if len(src) < 2:
        return None
    from pathlib import Path as _Path
    stems = [_Path(x).stem for x in src]
    primary = stems[0]
    # Longest common prefix among the recorded split files.
    prefix = primary
    for st in stems[1:]:
        i = 0
        while i < min(len(prefix), len(st)) and prefix[i] == st[i]:
            i += 1
        prefix = prefix[:i]
    suffix = primary[len(prefix):]
    return suffix or None


def _filter_split_source_primaries(files: List[Path], config: Dict) -> List[Path]:
    """Drop companion split-channel files from the top-level batch file list."""
    if not _config_uses_split_source_files(config):
        return files
    suffix = _primary_source_suffix(config)
    if not suffix:
        print("[PyCAT Batch] Split-file workflow detected, but primary suffix "
              "could not be inferred — processing all files.")
        return files
    primaries = [p for p in files if p.stem.endswith(suffix)]
    if primaries:
        print(f"[PyCAT Batch] Split-file workflow detected — processing "
              f"{len(primaries)} primary file(s) matching '*{suffix}' and "
              "loading companion files during replay.")
        return primaries
    print(f"[PyCAT Batch] Split-file workflow detected, but no files matched "
          f"the inferred primary suffix '*{suffix}' — processing all files.")
    return files

# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------
CONFIG_VERSION = "1.0"


def _empty_config() -> Dict:
    return {
        "pycat_config_version": CONFIG_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "steps": [],
    }


def _batch_pycat_version() -> str:
    """The installed ``pycat-napari`` version, for per-row provenance in the consolidated table.
    Returns ``''`` (blank, not a guess) if it cannot be resolved."""
    try:
        from importlib.metadata import version
        return version('pycat-napari')
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Batch worker (runs in a background QThread so the GUI stays responsive)
# ---------------------------------------------------------------------------

class BatchWorker(QThread):
    """Executes the recorded pipeline on a list of files."""

    progress = pyqtSignal(int, int, str)   # current, total, message
    finished = pyqtSignal(str)             # summary message
    error = pyqtSignal(str)               # error message

    def __init__(
        self,
        files: List[Path],
        config: Dict,
        output_dir: Path,
        step_registry: Dict[str, Callable],
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.config = config
        self.output_dir = output_dir
        self.step_registry = step_registry
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _auto_ball_radius_active(self) -> bool:
        """Whether to auto-estimate ball_radius for this batch.

        True only when (a) the recorded workflow is a fluorescence one where
        intensity-threshold object-size estimation is valid — inferred from the
        recorded step names — AND (b) no explicit ball_radius was recorded on the
        open_image step (an explicitly-set value always takes precedence).
        Brightfield / time-series / z-stack workflows are excluded (top-hat +
        Otsu size estimation is not valid there).
        """
        steps = self.config.get("steps", [])
        step_names = {s.get("step", "") for s in steps}
        # Cellular-fluorescence tell: subcellular/condensate segmentation.
        cellular = bool(step_names & {
            'segment_subcellular_objects', 'condensate_segmentation',
            'condensate_analysis'})
        # In-vitro-fluorescence tell: the ivf_* segmentation step.
        invitro = 'ivf_segmentation' in step_names
        if not (cellular or invitro):
            return False
        # An explicit ball_radius recorded on open_image wins — don't override.
        for s in steps:
            if s.get("step") in ("open_image", "open_stack", "open_image_stack"):
                if 'ball_radius' in (s.get("params") or {}):
                    return False
        return True

    def run(self):
        output_dir = self.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # Decide whether automatic object-size → ball_radius estimation applies
        # to this batch, and tell the user up front (it's not a hidden step). It
        # applies only for fluorescence workflows where intensity thresholding is
        # valid, and only when the recorded workflow did NOT pin an explicit
        # ball_radius (an explicit value always wins).
        self._auto_ball_radius = self._auto_ball_radius_active()
        if self._auto_ball_radius:
            print("[PyCAT Batch] Automatic object-size estimation is ON: "
                  "ball_radius will be estimated per image from the fluorescence "
                  "signal (top-hat + Otsu → median object size). This applies "
                  "because this is a fluorescence workflow with no explicit "
                  "ball_radius recorded. "
                  "# TODO: optimize the estimator on a real dataset.")

        results = []
        total = len(self.files)

        # ── Comparative-phenotyping condition/metadata (increment 1, Part B) ────────────────
        # One resolver for the whole run, from the sheet/pattern the config carries. `None` when no
        # source is configured — and then nothing below writes a metadata file, so a batch with no
        # sample sheet or filename pattern produces exactly the outputs it did before (additive).
        from pycat.utils.sample_metadata import (resolver_from_config,
                                                 write_image_sample_metadata)
        _sample_resolver = resolver_from_config(self.config)

        # ── One tidy top-level table for the whole batch (comparative phenotyping inc 2) ───────
        # The keystone comparative output: instead of N per-image folders a scientist joins by hand,
        # batch streams one long-format `consolidated_long.csv` — every measurement tagged with its
        # image, condition (WT/dose/replicate…), and provenance. Additive: it sits ALONGSIDE the
        # per-image folders and removes nothing. The condition-field vocabulary is fixed up front
        # (from the sheet/pattern) so streaming append can never drift a column.
        from pycat.utils.consolidated_table import ConsolidatedLongWriter, records_from_output_dir
        _condition_fields = (_sample_resolver.condition_field_names()
                             if _sample_resolver is not None else [])
        _consolidated = ConsolidatedLongWriter(
            output_dir / 'consolidated_long.csv', _condition_fields)
        _pycat_version = _batch_pycat_version()

        for idx, image_path in enumerate(self.files):
            if self._cancelled:
                self.finished.emit(
                    f"Cancelled after {idx}/{total} files.\n"
                    f"Partial results saved to: {output_dir}"
                )
                return

            self.progress.emit(idx + 1, total, f"Processing: {image_path.name}")

            file_output = output_dir / image_path.stem
            file_output.mkdir(exist_ok=True)

            try:
                self._process_file(image_path, file_output)
                # Attach this image's condition (WT/rep2/10µM…) beside its results. No-op when no
                # metadata source is configured (resolver is None).
                write_image_sample_metadata(_sample_resolver, image_path, file_output)
                # Append this image's measurements to the top-level consolidated table, read from the
                # per-image CSVs it just wrote. An image with no object tables contributes no rows.
                _consolidated_ok = True
                try:
                    _meta = (_sample_resolver.for_image(image_path)
                             if _sample_resolver is not None else None)
                    _consolidated.add_image(
                        image_path.stem,
                        records_from_output_dir(file_output, image_path.stem),
                        sample_metadata=_meta,
                        provenance={'pycat_version': _pycat_version})
                except Exception as _cexc:      # broad-ok: batch_step — per-image processing SUCCEEDED and
                    # its own folder is written; only the consolidated-table append failed. This must be
                    # VISIBLE, not a clean success: a silently dropped image makes a 93-of-100 cohort look
                    # complete (the exception_context_classification spec's batch_step rule).
                    _consolidated_ok = False
                    print(f"[PyCAT Batch] consolidated-table append failed for "
                          f"{image_path.name}: {_cexc}")
                if _consolidated_ok:
                    results.append(f"✓ {image_path.name}")
                else:
                    results.append(
                        f"⚠ {image_path.name}: processed, but NOT added to the consolidated table — its "
                        f"rows are MISSING from consolidated_long.csv (see terminal). The per-image output "
                        f"folder is complete.")
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                results.append(f"✗ {image_path.name}: {exc}")
                print(f"[PyCAT Batch] ERROR on {image_path.name}:\n{tb}")

        print(f"[PyCAT Batch] {_consolidated.summary()}")
        # A provenance sidecar (feature units/definitions + software versions) next to the CSV, so the
        # consolidated table's reproducibility record travels with it (wire_orphans B2).
        try:
            _sidecar = _consolidated.write_provenance_sidecar()
            if _sidecar:
                print(f"[PyCAT Batch] Provenance sidecar -> {_sidecar}")
        except Exception as _pexc:  # broad-ok: the sidecar is an additive extra; never fail the batch over it
            print(f"[PyCAT Batch] provenance sidecar skipped: {_pexc}")

        # A sheet row that matched no image is a likely filename typo — warn once, don't crash.
        if _sample_resolver is not None:
            try:
                _sample_resolver.warn_unmatched_sheet_rows()
            except Exception as _wexc:
                print(f"[PyCAT Batch] sample-metadata unmatched-row check failed: {_wexc}")

        summary = "\n".join(results)
        self.finished.emit(
            f"Batch complete — {total} file(s) processed.\n"
            f"Output: {output_dir}\n\n{summary}"
        )

    def _process_file(self, image_path: Path, output_dir: Path):
        """
        Replay each recorded step for a single image file in headless mode.

        Steps communicate via a shared `state` dict rather than the napari viewer.
        Each step function signature is:
            fn(state: dict, image_path: Path, params: dict, output_dir: Path) -> None

        The state dict is initialised empty and built up as steps run:
            state['image']         – raw image array (set by open_image)
            state['preprocessed']  – processed image (set by preprocessing steps)
            state['data_instance'] – BaseDataClass for this file
            state['labeled_cells'] – labeled cell mask array
            state['puncta_mask']   – refined puncta mask array

        Steps that are not registered are logged and skipped so that configs
        created in one PyCAT version remain forward-compatible.
        """
        state: Dict = {}  # shared across all steps for this file
        # Propagate the batch-level auto-ball_radius decision so the open_image
        # replay can estimate it per image when appropriate.
        state['_auto_ball_radius'] = getattr(self, '_auto_ball_radius', False)
        for step_entry in self.config.get("steps", []):
            step_name = step_entry.get("step", "")
            params = step_entry.get("params", {})
            fn = self.step_registry.get(step_name)
            if fn is None:
                print(f"[PyCAT Batch] Step '{step_name}' not registered – skipping.")
                continue
            try:
                fn(state, image_path, params, output_dir)
            except Exception as _step_exc:
                import traceback
                print(f"[PyCAT Batch] ERROR in step '{step_name}' for "
                      f"{image_path.name}:\n{traceback.format_exc()}")
                print(f"[PyCAT Batch] Skipping remaining steps for this file.")
                break


# ---------------------------------------------------------------------------
# Batch dialog
# ---------------------------------------------------------------------------

class BatchDialog(QDialog):
    """Dialog for configuring and launching a batch run."""

    def __init__(self, processor: "BatchProcessor", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.processor = processor
        self.setWindowTitle("PyCAT — Batch Run")
        self.setMinimumWidth(560)
        self._worker: Optional[BatchWorker] = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── Config section ──────────────────────────────────────────────
        cfg_group = QGroupBox("Analysis Config")
        cfg_layout = QFormLayout(cfg_group)

        self._config_path_edit = QLineEdit()
        self._config_path_edit.setPlaceholderText(
            "Using current session recording  (or load a saved config)"
        )
        self._config_path_edit.setReadOnly(True)

        cfg_btn_row = QHBoxLayout()
        load_cfg_btn = QPushButton("Load Config…")
        load_cfg_btn.clicked.connect(self._load_config)
        save_cfg_btn = QPushButton("Save Current Session Config…")
        save_cfg_btn.clicked.connect(self._save_config)
        cfg_btn_row.addWidget(load_cfg_btn)
        cfg_btn_row.addWidget(save_cfg_btn)

        cfg_layout.addRow("Config file:", self._config_path_edit)
        cfg_layout.addRow("", cfg_btn_row)

        step_count = len(self.processor.config.get("steps", []))
        self._step_info = QLabel(f"Session steps recorded: {step_count}")
        cfg_layout.addRow("", self._step_info)

        root.addWidget(cfg_group)

        # ── Folder section ───────────────────────────────────────────────
        folder_group = QGroupBox("Input Folder")
        folder_layout = QFormLayout(folder_group)

        self._folder_edit = QLineEdit()
        self._folder_edit.setText(self._detect_folder())
        self._folder_edit.setPlaceholderText("Folder containing images to process…")

        folder_btn_row = QHBoxLayout()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_folder)
        detect_btn = QPushButton("Re-detect")
        detect_btn.setToolTip("Detect folder from currently open image")
        detect_btn.clicked.connect(lambda: self._folder_edit.setText(self._detect_folder()))
        folder_btn_row.addWidget(browse_btn)
        folder_btn_row.addWidget(detect_btn)

        folder_layout.addRow("Folder:", self._folder_edit)
        folder_layout.addRow("", folder_btn_row)
        root.addWidget(folder_group)

        # ── Output / progress ────────────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(100)
        self._log.setPlaceholderText("Batch log will appear here…")
        root.addWidget(self._log)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self._run_btn = btn_box.addButton("▶  Run Batch", QDialogButtonBox.AcceptRole)
        self._cancel_btn = btn_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        self._run_btn.clicked.connect(self._run_batch)
        self._cancel_btn.clicked.connect(self._cancel_or_close)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_folder(self) -> str:
        """Return the folder of the first loaded image layer, or ''."""
        try:
            for layer in self.processor.viewer.layers:
                src = getattr(layer, "source", None)
                if src and getattr(src, "path", None):
                    p = Path(src.path)
                    return str(p.parent) if p.is_file() else str(p)
        except Exception:  # noqa: BLE001
            pass
        # Fallback: look inside PyCAT's internal data store if available
        try:
            ds = self.processor.viewer.pycat_data_store  # type: ignore[attr-defined]
            if hasattr(ds, "last_image_path") and ds.last_image_path:
                return str(Path(ds.last_image_path).parent)
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load PyCAT Config", "", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path) as f:
                cfg = json.load(f)
            self.processor.config = cfg
            self._config_path_edit.setText(path)
            steps = len(cfg.get("steps", []))
            self._step_info.setText(f"Steps loaded: {steps}")
            self._log.append(f"Config loaded from: {path}  ({steps} steps)")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Load Error", str(exc))

    def _save_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session Config",
            f"pycat_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            "JSON Files (*.json)",
        )
        if not path:
            return
        self.processor.save_config(Path(path))
        self._config_path_edit.setText(path)
        self._log.append(f"Config saved to: {path}")

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder")
        if folder:
            self._folder_edit.setText(folder)

    def _run_batch(self):
        folder = self._folder_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, "No Folder", "Please select a valid input folder.")
            return

        config = self.processor.config
        if not config.get("steps"):
            reply = QMessageBox.question(
                self,
                "No Steps Recorded",
                "No analysis steps have been recorded yet.\n"
                "Do you want to load a config file instead?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._load_config()
            return

        files = [
            p
            for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        files = _filter_split_source_primaries(files, config)

        if not files:
            QMessageBox.information(
                self,
                "No Images Found",
                f"No supported image files found in:\n{folder}\n\n"
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
            return

        output_dir = Path(folder) / "pycat_batch_results"
        self._log.append(
            f"Starting batch: {len(files)} file(s) → {output_dir}"
        )

        self._progress_bar.setMaximum(len(files))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._run_btn.setEnabled(False)

        self._worker = BatchWorker(
            files=files,
            config=config,
            output_dir=output_dir,
            step_registry=self.processor.step_registry,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _cancel_or_close(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._log.append("Cancellation requested…")
        else:
            self.reject()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int, message: str):
        self._progress_bar.setValue(current)
        self._log.append(f"[{current}/{total}] {message}")

    def _on_finished(self, summary: str):
        self._progress_bar.setVisible(False)
        self._run_btn.setEnabled(True)
        self._log.append(summary)
        QMessageBox.information(self, "Batch Complete", summary)

    def _on_error(self, msg: str):
        self._progress_bar.setVisible(False)
        self._run_btn.setEnabled(True)
        self._log.append(f"ERROR: {msg}")


# ---------------------------------------------------------------------------
# BatchProcessor – the main integration class
# ---------------------------------------------------------------------------

class BatchProcessor:
    """
    Central object that ties config recording to the batch runner.

    Parameters
    ----------
    viewer : napari.Viewer
        The active napari viewer instance.
    """

    def __init__(self, viewer):
        self.viewer = viewer
        self.config: Dict = _empty_config()
        self.step_registry: Dict[str, Callable] = {}
        # Recording is OFF by default at startup — the user opts in with the
        # Batch dialog's start/stop toggle. This prevents exploratory clicking
        # from being captured into a workflow before the user decides to record.
        self.recording_enabled: bool = False
        # Dirty = there are recorded steps not yet written to a config file.
        # Used to prompt for export before a save-and-clear wipes the recording.
        self._dirty: bool = False
        # When True, don't prompt to export on save-and-clear for the rest of
        # the session (user ticked "don't ask again").
        self._export_prompt_silenced: bool = False

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, step_name: str, params: Dict[str, Any]):
        """Append a step to the in-memory config log.

        The workflow checklist's progress bar is a separate, always-on
        feature from batch recording (an opt-in toggle for building a
        replayable headless config) -- it must update regardless of whether
        the user has recording enabled. Previously the checklist
        notification lived after the `recording_enabled` early-return, so
        with recording off (the default) EVERY step in EVERY pipeline was
        silently dropped and the checklist never advanced past whatever
        `WorkflowChecklistManager.activate()` marks at pipeline-switch time.
        """
        if not self.recording_enabled:
            print(f"[PyCAT Batch] Recording disabled — step not saved to batch config: {step_name}")
            try:
                cm = getattr(self, '_central_manager', None)
                if cm is not None:
                    cm.workflow_checklist.on_step_recorded(step_name)
            except Exception:  # broad-ok: ui_cleanup — checklist progress notify must not break batch recording
                pass
            return
        params = dict(params or {})
        # Snapshot the active/all layer names at record time, to help diagnose
        # cases where a step captured the wrong dropdown layer name.
        try:
            params.setdefault(
                '_active_layer_at_record',
                getattr(getattr(self.viewer.layers.selection, 'active', None), 'name', None))
            params.setdefault('_all_layers_at_record',
                              [l.name for l in self.viewer.layers])
        except Exception:
            pass
        self.config["steps"].append(
            {
                "step": step_name,
                "params": params,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._dirty = True
        # Minimal terminal breadcrumb only. The full step name, parameters, and
        # layer snapshots are shown in the "☰ Recorded Steps" viewer, so dumping
        # the whole params dict here (including layer snapshots) is redundant
        # noise. Keep a short one-liner so the recorder isn't silent.
        print(f"[PyCAT Batch] Recorded step {len(self.config['steps'])}: {step_name}")

        # Notify the workflow checklist so it can auto-check this step
        try:
            cm = getattr(self, '_central_manager', None)
            if cm is not None:
                cm.workflow_checklist.on_step_recorded(step_name)
        except Exception:  # broad-ok: ui_cleanup — checklist progress notify must not break batch recording
            pass

    def clear_recording(self):
        """Reset the session recording. Recording returns to OFF (opt-in) — the
        user re-enables it with the toggle when they want to record the next
        dataset's workflow."""
        self.config = _empty_config()
        self.recording_enabled = False
        self._dirty = False
        # Keep the toolbar toggle in sync (recording just went OFF).
        try:
            sync = getattr(self, '_toolbar_record_sync', None)
            if sync is not None:
                sync()
        except Exception:
            pass
        print("[PyCAT Batch] Session recording reset (recording OFF — use the "
              "toggle to start recording again).")

    def has_unsaved_steps(self) -> bool:
        """True if there are recorded steps not yet written to a config file."""
        return self._dirty and len(self.config.get("steps", [])) > 0

    def terminate_recording(self):
        """End the current recorded process at a dataset boundary.

        Save/Clear is a hard boundary between independent datasets. The just-
        completed pipeline's steps should not keep accumulating onto the next
        dataset's recording. Callers are responsible for prompting the user to
        export the config first if it has unsaved steps.
        """
        self.clear_recording()

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def save_config(self, path: Path):
        """Save the current session config to a JSON file."""
        self.config["saved"] = datetime.now().isoformat(timespec="seconds")
        with open(path, "w") as f:
            json.dump(self.config, f, indent=2)
        self._dirty = False
        print(f"[PyCAT Batch] Config saved → {path}")

    def load_config(self, path: Path):
        """Load a previously saved config JSON file."""
        with open(path) as f:
            self.config = json.load(f)
        print(f"[PyCAT Batch] Config loaded ← {path}  "
              f"({len(self.config.get('steps', []))} steps)")

    # ------------------------------------------------------------------
    # Step registry
    # ------------------------------------------------------------------

    def register_step(self, name: str, fn: Callable):
        """
        Register a callable that can be replayed during batch processing.

        The callable signature must be:
            fn(viewer, image_path: Path, params: dict, output_dir: Path) -> None

        Registering the same NAME twice is REJECTED, not silently overwritten.
        A plain dict assignment keeps the last writer and discards the first, which
        makes the registry a latent trap: implement a real replay handler, let a stale
        skip-stub further down the map override it, and you debug a handler that never
        runs. `morphological_complexity` was registered twice for exactly this reason.
        Both were no-ops so nothing broke -- but the next one might not be.
        """
        if name in self.step_registry and self.step_registry[name] is not fn:
            raise ValueError(
                f"Batch step {name!r} is already registered. Registering it twice "
                f"would silently discard the earlier handler. Remove the duplicate "
                f"rather than letting the last one win.")
        self.step_registry[name] = fn

    # ------------------------------------------------------------------
    # Open dialog
    # ------------------------------------------------------------------

    def open_batch_dialog(self):
        """Open the Batch Run dialog (call from toolbar button)."""
        dlg = BatchDialog(self, parent=None)
        dlg.exec_()


# ---------------------------------------------------------------------------
# record_step decorator
# ---------------------------------------------------------------------------

def record_step(step_name: str, processor_attr: str = "_pycat_batch_processor"):
    """
    Decorator that automatically records a step whenever the wrapped function
    is called.

    Usage
    -----
    # In a widget class that holds a reference to BatchProcessor as self._bp:
    @record_step("run_preprocessing")
    def _on_preprocess_clicked(self, params):
        ...

    The decorator inspects the first argument (self) for the processor
    attribute, so it works naturally with widget methods.

    Alternatively, pass the processor explicitly:
        batch_processor.record("run_preprocessing", params)
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            # Try to find the BatchProcessor on `self`
            bp: Optional[BatchProcessor] = None
            if args:
                bp = getattr(args[0], processor_attr, None)

            # Build params: everything except self
            params: Dict[str, Any] = {}
            if len(args) > 1:
                params["args"] = [
                    a if isinstance(a, (str, int, float, bool, type(None))) else str(a)
                    for a in args[1:]
                ]
            params.update(
                {
                    k: v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
                    for k, v in kwargs.items()
                }
            )

            result = fn(*args, **kwargs)

            if bp is not None:
                bp.record(step_name, params)

            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Toolbar integration
# ---------------------------------------------------------------------------

def add_batch_toolbar_button(viewer, processor: BatchProcessor):
    """
    Add a "⚡ Batch Run" button to napari's existing toolbar area.

    napari exposes `viewer.window._qt_window` which is a QMainWindow.
    We add a small dedicated QToolBar with a single action so it sits
    cleanly next to the existing napari controls.
    """
    try:
        main_window = viewer.window._qt_window  # type: ignore[attr-defined]
    except AttributeError:
        print("[PyCAT Batch] Could not find napari Qt main window – toolbar not added.")
        return

    toolbar = QToolBar("PyCAT Batch", main_window)
    toolbar.setObjectName("pycat_batch_toolbar")

    # ── Batch section label ────────────────────────────────────────────────
    _batch_lbl = QAction("Batch:", main_window)
    _batch_lbl.setEnabled(False)   # non-clickable section label
    toolbar.addAction(_batch_lbl)

    action = QAction("⚡  Batch Run", main_window)
    action.setToolTip(
        "Save the current analysis config and/or run the recorded\n"
        "pipeline on all images in a folder."
    )
    action.triggered.connect(processor.open_batch_dialog)
    toolbar.addAction(action)

    # ── Start/stop recording toggle (Batch section) ────────────────────────
    # Recording is OFF at startup and opt-in: the user turns it on before
    # clicking through the workflow they want to capture. Placed left of Save
    # Config, in the Batch section.
    record_action = QAction(main_window)
    record_action.setCheckable(True)

    def _sync_record_action():
        on = bool(getattr(processor, 'recording_enabled', False))
        record_action.setChecked(on)
        if on:
            # Green circle = actively recording.
            record_action.setText("\U0001F7E2  Recording")
            record_action.setToolTip(
                "Batch recording is ON — steps are being captured. Click to "
                "pause (already-recorded steps are kept).")
        else:
            # Red circle = idle / ready to record.
            record_action.setText("\U0001F534  Record")
            record_action.setToolTip(
                "Batch recording is OFF. Click to start recording your workflow "
                "steps into a batch config.")

    def _toggle_record():
        processor.recording_enabled = not bool(
            getattr(processor, 'recording_enabled', False))
        _sync_record_action()
        state = "ON" if processor.recording_enabled else "OFF"
        print(f"[PyCAT Batch] Recording {state}.")

    record_action.triggered.connect(_toggle_record)
    _sync_record_action()
    # Expose so other code (e.g. after Save & Clear resets recording) can resync.
    processor._toolbar_record_sync = _sync_record_action
    toolbar.addAction(record_action)

    # Also add a quick "Save Config" action for convenience
    save_action = QAction("💾  Save Config", main_window)
    save_action.setToolTip("Save the current session's recorded steps to a JSON config file.")
    save_action.triggered.connect(_make_save_handler(processor))
    toolbar.addAction(save_action)

    # Recorded Steps viewer — belongs with the Batch controls (it's the batch
    # workflow you're recording). Uses a distinct clipboard/checklist glyph, NOT
    # the ☰ hamburger (which reads as a napari-style menu). Wired to the
    # MenuManager's dialog via the central manager.
    _cm = getattr(processor, '_central_manager', None)
    _mm = getattr(_cm, 'menu_manager', None) if _cm is not None else None
    if _mm is not None and hasattr(_mm, '_show_recorded_steps_dialog'):
        recorded_action = QAction("\U0001F4CB  Recorded Steps", main_window)  # 📋 clipboard
        recorded_action.setToolTip(
            "View the batch workflow recorded so far — each step and the "
            "layers/parameters it used.")
        recorded_action.triggered.connect(_mm._show_recorded_steps_dialog)
        toolbar.addAction(recorded_action)

    # ── Separator: Batch section (above) | Layer Actions section (below) ────
    toolbar.addSeparator()
    _layers_lbl = QAction("Layer Actions:", main_window)
    _layers_lbl.setEnabled(False)   # non-clickable section label
    toolbar.addAction(_layers_lbl)

    # Clear + Home moved here from the top menu bar — they act on the
    # layers/view, so they belong in Layer Actions. Wired to the MenuManager /
    # file_io via the central manager.
    if _cm is not None:
        clear_action = QAction("\u2620  Clear", main_window)   # ☠ hazard
        clear_action.setToolTip(
            "Clear all layers and data WITHOUT saving — resets to the start of a "
            "workflow.")
        try:
            _fio = _cm.file_io
            clear_action.triggered.connect(
                lambda: _fio.clear_all_without_saving(viewer, confirm=True))
            toolbar.addAction(clear_action)
        except Exception:
            pass
    if _mm is not None and hasattr(_mm, '_home_fit_view'):
        home_action = QAction("\u2302  Home", main_window)     # ⌂ house
        home_action.setToolTip("Fit the view to the selected image or ROI layer.")
        home_action.triggered.connect(_mm._home_fit_view)
        toolbar.addAction(home_action)

    # Show/hide-all-layers eye toggle. As layers accumulate, toggling each layer's
    # eye in the layer list is tedious; this flips every layer's visibility in one
    # click. The icon/tooltip reflect the action that the next click performs.
    # Show/hide-all-layers eye toggle. As layers accumulate, toggling each layer's
    # eye in the layer list is tedious; this flips every layer's visibility in one
    # click. Like the Gray/Viridis colormap button, the label shows the ACTION the
    # NEXT click will perform and cycles on each click: "👁 Show" → click shows all
    # → becomes "🚫 Hide" → click hides all → back to "👁 Show".
    eye_action = QAction("👁  Show", main_window)
    eye_action._pycat_next = 'show'   # the action the NEXT click will perform
    eye_action.setToolTip("Show all layers (click again to hide all).")

    def _toggle_all_layers():
        try:
            layers = list(viewer.layers)
            if not layers:
                return
            target_show = getattr(eye_action, '_pycat_next', 'show') == 'show'
            for l in layers:
                try:
                    l.visible = target_show
                except Exception:
                    pass
            # Flip the label/tooltip to the action the NEXT click will perform.
            if target_show:
                eye_action._pycat_next = 'hide'
                eye_action.setText("🚫  Hide")
                eye_action.setToolTip("Hide all layers (click again to show all).")
            else:
                eye_action._pycat_next = 'show'
                eye_action.setText("👁  Show")
                eye_action.setToolTip("Show all layers (click again to hide all).")
        except Exception as e:
            print(f"[PyCAT] Show/hide all layers failed: {e}")

    eye_action.triggered.connect(_toggle_all_layers)
    toolbar.addAction(eye_action)

    # Colormap reset: flip every IMAGE layer between gray and viridis in one
    # click. IMS/multichannel loads assign per-channel colors (blue/green/red/
    # magenta) which some users find harder to read than a neutral map. Labels
    # and mask layers are left untouched (their colormaps are categorical).
    cmap_action = QAction("\U0001F3A8  Gray", main_window)
    cmap_action.setToolTip(
        "Set all image layers to grayscale (click again for viridis).")
    cmap_action._pycat_next = 'gray'   # the colormap the NEXT click will apply

    def _reset_colormaps():
        try:
            import napari
            target = getattr(cmap_action, '_pycat_next', 'gray')
            n = 0
            for lyr in viewer.layers:
                if isinstance(lyr, napari.layers.Image):
                    try:
                        lyr.colormap = target
                        n += 1
                    except Exception:
                        pass
            # Flip for next click and update the button label.
            if target == 'gray':
                cmap_action._pycat_next = 'viridis'
                cmap_action.setText("\U0001F3A8  Viridis")
                cmap_action.setToolTip(
                    "Set all image layers to viridis (click again for gray).")
            else:
                cmap_action._pycat_next = 'gray'
                cmap_action.setText("\U0001F3A8  Gray")
                cmap_action.setToolTip(
                    "Set all image layers to grayscale (click again for viridis).")
        except Exception as e:
            print(f"[PyCAT] Colormap reset failed: {e}")

    cmap_action.triggered.connect(_reset_colormaps)
    toolbar.addAction(cmap_action)

    # Side-by-side grid toggle: tile visible image (+ mask) layers for
    # comparison. Lives here in the Layers section next to the show/hide-all and
    # colormap controls, since it's a viewer-layout action. Delegates to the
    # MenuManager's managed-grid implementation.
    grid_action = QAction("\U0001F5C3  Grid", main_window)   # 🗃 card-box glyph
    grid_action.setToolTip(
        "Toggle side-by-side grid view: tiles the visible image (and mask) "
        "layers for comparison, reflowing to only the ones shown. Annotation / "
        "drawing layers are set aside while grid is on and return when it's off.")

    def _toggle_grid():
        try:
            cm = getattr(processor, '_central_manager', None)
            mm = getattr(cm, 'menu_manager', None) if cm is not None else None
            if mm is not None and hasattr(mm, '_toggle_grid_view'):
                mm._toggle_grid_view()
            else:
                # Fallback: plain napari grid toggle if the manager isn't reachable.
                viewer.grid.enabled = not bool(viewer.grid.enabled)
        except Exception as e:
            print(f"[PyCAT] Grid toggle failed: {e}")

    grid_action.triggered.connect(_toggle_grid)
    toolbar.addAction(grid_action)

    # ── Information section: Metadata + Tags ───────────────────────────────
    # Moved here from the top menu bar. Both are "inspect information about the
    # current data/layers" actions, so they're grouped in their own section.
    if _mm is not None and (hasattr(_mm, '_show_metadata_dialog')
                            or hasattr(_mm, 'open_tag_inspector')):
        toolbar.addSeparator()
        _info_lbl = QAction("Information:", main_window)
        _info_lbl.setEnabled(False)   # non-clickable section label
        toolbar.addAction(_info_lbl)

        if hasattr(_mm, '_show_metadata_dialog'):
            meta_action = QAction("\u24d8  Metadata", main_window)   # ⓘ info
            meta_action.setToolTip(
                "View acquisition metadata (pixel size, objective, wavelengths, "
                "dimensions, date) for the loaded file.")
            meta_action.triggered.connect(_mm._show_metadata_dialog)
            toolbar.addAction(meta_action)

        if hasattr(_mm, 'open_tag_inspector'):
            tags_action = QAction("\u25a3  Tags", main_window)       # ▣ tag
            tags_action.setToolTip(
                "Layer Tag Inspector — view and override the structured tags and "
                "lineage assigned to each layer.")
            tags_action.triggered.connect(_mm.open_tag_inspector)
            toolbar.addAction(tags_action)

    main_window.addToolBar(Qt.TopToolBarArea, toolbar)
    print("[PyCAT Batch] Batch toolbar added.")


def _make_save_handler(processor: BatchProcessor):
    def _save():
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Save PyCAT Session Config",
            f"pycat_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            "JSON Files (*.json)",
        )
        if path:
            processor.save_config(Path(path))
            QMessageBox.information(None, "Saved", f"Config saved to:\n{path}")
    return _save
