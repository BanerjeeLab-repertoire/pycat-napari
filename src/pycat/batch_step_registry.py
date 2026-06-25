"""
pycat/batch_step_registry.py
============================
Example step registry for PyCAT batch processing.

This file shows HOW to wire each pipeline widget's "Run" button to the
batch recorder, and how to write the replay functions that execute during
a batch run.

Drop this file into  src/pycat/  alongside batch_processor.py, then
call  register_all_steps(bp)  from  run_pycat_func()  after creating the
BatchProcessor.

Each step registration has two parts:
  1. A RECORDER — wired to the widget's "Run" button via connect() or
     by monkey-patching the click callback with the @record_step decorator.
  2. A REPLAYER — a function that receives
         (viewer, image_path: Path, params: dict, output_dir: Path)
     and reproduces the step programmatically for a single file.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pycat.batch_processor import BatchProcessor


# ---------------------------------------------------------------------------
# Replayer helpers
# ---------------------------------------------------------------------------

def _load_image_into_viewer(viewer, image_path: Path):
    """
    Use PyCAT's own open-image routine so the internal data store is
    populated correctly.  Falls back to a direct napari layer add if the
    PyCAT routine is not importable (useful for unit tests).
    """
    try:
        from pycat.toolbox.file_io_tools import open_2d_image  # type: ignore
        open_2d_image(viewer, str(image_path), channel_name=image_path.stem)
    except ImportError:
        import numpy as np
        from skimage import io
        img = io.imread(str(image_path))
        viewer.add_image(img, name=image_path.stem)


def _save_outputs(viewer, output_dir: Path):
    """
    Save all current napari layers and PyCAT data frames to output_dir.
    Mirrors what 'Save and Clear' does in the GUI.
    """
    try:
        from pycat.toolbox.file_io_tools import save_layer, get_pycat_dataframes  # type: ignore
        for layer in viewer.layers:
            save_layer(layer, output_dir)
        for name, df in get_pycat_dataframes(viewer).items():
            df.to_csv(output_dir / f"{name}.csv", index=False)
    except ImportError:
        # Minimal fallback: save images only
        from skimage import io
        import numpy as np
        for layer in viewer.layers:
            try:
                data = np.asarray(layer.data)
                io.imsave(str(output_dir / f"{layer.name}.tiff"), data)
            except Exception:  # noqa: BLE001
                pass


def _clear_viewer(viewer):
    """Remove all layers to prepare for the next file."""
    viewer.layers.select_all()
    viewer.layers.remove_selected()


# ---------------------------------------------------------------------------
# Step replayers  (one per recorded step)
# ---------------------------------------------------------------------------
# Signature: fn(viewer, image_path: Path, params: dict, output_dir: Path)

def replay_open_image(viewer, image_path: Path, params: dict, output_dir: Path):
    """Load a single image file into the viewer."""
    _load_image_into_viewer(viewer, image_path)


def replay_preprocessing(viewer, image_path: Path, params: dict, output_dir: Path):
    """Re-run preprocessing on the active image layer."""
    try:
        from pycat.toolbox.image_processing_tools import run_preprocessing_pipeline  # type: ignore
        run_preprocessing_pipeline(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] preprocessing tools not importable – step skipped.")


def replay_background_removal(viewer, image_path: Path, params: dict, output_dir: Path):
    try:
        from pycat.toolbox.image_processing_tools import run_background_removal  # type: ignore
        run_background_removal(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] background removal not importable – step skipped.")


def replay_cellpose_segmentation(viewer, image_path: Path, params: dict, output_dir: Path):
    try:
        from pycat.toolbox.segmentation_tools import run_cellpose  # type: ignore
        run_cellpose(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] Cellpose segmentation not importable – step skipped.")


def replay_condensate_segmentation(viewer, image_path: Path, params: dict, output_dir: Path):
    try:
        from pycat.toolbox.segmentation_tools import run_condensate_segmentation  # type: ignore
        run_condensate_segmentation(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] condensate segmentation not importable – step skipped.")


def replay_cell_analysis(viewer, image_path: Path, params: dict, output_dir: Path):
    try:
        from pycat.toolbox.analysis_tools import run_cell_analyzer  # type: ignore
        run_cell_analyzer(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] cell analyzer not importable – step skipped.")


def replay_condensate_analysis(viewer, image_path: Path, params: dict, output_dir: Path):
    try:
        from pycat.toolbox.analysis_tools import run_condensate_analyzer  # type: ignore
        run_condensate_analyzer(viewer, **params)
    except ImportError:
        print("[PyCAT Batch] condensate analyzer not importable – step skipped.")


def replay_save_and_clear(viewer, image_path: Path, params: dict, output_dir: Path):
    """Save outputs and wipe the viewer for the next file."""
    _save_outputs(viewer, output_dir)
    _clear_viewer(viewer)


# ---------------------------------------------------------------------------
# Public registration function
# ---------------------------------------------------------------------------

#: Maps step name (as recorded) → replay function
_STEP_MAP = {
    "open_image":               replay_open_image,
    "preprocessing":            replay_preprocessing,
    "background_removal":       replay_background_removal,
    "cellpose_segmentation":    replay_cellpose_segmentation,
    "condensate_segmentation":  replay_condensate_segmentation,
    "cell_analysis":            replay_cell_analysis,
    "condensate_analysis":      replay_condensate_analysis,
    "save_and_clear":           replay_save_and_clear,
}


def register_all_steps(bp: "BatchProcessor"):
    """Register every known step with the BatchProcessor."""
    for name, fn in _STEP_MAP.items():
        bp.register_step(name, fn)
    print(f"[PyCAT Batch] Registered {len(_STEP_MAP)} replay steps.")
