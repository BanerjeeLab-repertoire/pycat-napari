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
SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".czi", ".png", ".jpg", ".jpeg"}

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
        viewer,
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.config = config
        self.output_dir = output_dir
        self.step_registry = step_registry
        self.viewer = viewer
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        output_dir = self.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        total = len(self.files)

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
                results.append(f"✓ {image_path.name}")
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                results.append(f"✗ {image_path.name}: {exc}")
                print(f"[PyCAT Batch] ERROR on {image_path.name}:\n{tb}")

        summary = "\n".join(results)
        self.finished.emit(
            f"Batch complete — {total} file(s) processed.\n"
            f"Output: {output_dir}\n\n{summary}"
        )

    def _process_file(self, image_path: Path, output_dir: Path):
        """
        Replay each recorded step for a single image file.

        The design here is intentionally open-ended: PyCAT's pipeline widgets
        call functions through the step registry.  Each entry in config['steps']
        has:
            { "step": "<name>", "params": { ... } }

        The registry maps step names → callables that accept
            (viewer, image_path, params, output_dir)
        and return nothing (they operate via napari layers / pycat data store).

        Steps that are not registered are logged and skipped so that configs
        created in one PyCAT version remain forward-compatible.
        """
        for step_entry in self.config.get("steps", []):
            step_name = step_entry.get("step", "")
            params = step_entry.get("params", {})
            fn = self.step_registry.get(step_name)
            if fn is None:
                print(f"[PyCAT Batch] Step '{step_name}' not registered – skipping.")
                continue
            fn(self.viewer, image_path, params, output_dir)


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
            viewer=self.processor.viewer,
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

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, step_name: str, params: Dict[str, Any]):
        """Append a step to the in-memory config log."""
        self.config["steps"].append(
            {
                "step": step_name,
                "params": params,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        print(f"[PyCAT Batch] Recorded step: {step_name}  params={params}")

    def clear_recording(self):
        """Reset the session recording."""
        self.config = _empty_config()

    # ------------------------------------------------------------------
    # Config I/O
    # ------------------------------------------------------------------

    def save_config(self, path: Path):
        """Save the current session config to a JSON file."""
        self.config["saved"] = datetime.now().isoformat(timespec="seconds")
        with open(path, "w") as f:
            json.dump(self.config, f, indent=2)
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
        """
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

    action = QAction("⚡  Batch Run", main_window)
    action.setToolTip(
        "Save the current analysis config and/or run the recorded\n"
        "pipeline on all images in a folder."
    )
    action.triggered.connect(processor.open_batch_dialog)
    toolbar.addAction(action)

    # Also add a quick "Save Config" action for convenience
    save_action = QAction("💾  Save Config", main_window)
    save_action.setToolTip("Save the current session's recorded steps to a JSON config file.")
    save_action.triggered.connect(_make_save_handler(processor))
    toolbar.addAction(save_action)

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
