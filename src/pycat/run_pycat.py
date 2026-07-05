"""
Application Execution Module for PyCAT

PyCAT : Python Condensate Analysis Toolbox 

PyCAT is an open source application for the analysis of biomolecular condensates in biological images. It is 
diverse and robust enough for use in a wide range of bio-image analyses. The application is built on top of
the napari viewer, which allows for interactive visualization of images and annotations. PyCAT provides a
variety of tools for image processing, data analysis, and visualization, and is designed to be user-friendly
and accessible to researchers with a wide range of technical backgrounds.   

It provides a python native, no-code interface for the analysis of biological images. It serves not only as a 
stand-alone application, but as a platform for the development of new image analysis tools and methods. It is my
hope that PyCAT will be a valuable resource for the scientific community, and that it will help to advance our
understanding of the complex biological processes that underlie the formation and function of biomolecular condensates.
I hope it is useful to the community and that others will contribute to its development.

This module defines the run_pycat_func function, which is used to run the PyCAT application by creating a napari viewer
instance and initializing the CentralManager. The CentralManager acts as the central coordinating class for PyCAT,
integrating various components such as file input/output, data management, and user interface elements.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024

License
-------
Copyright (c) 2024, Christian Neureuter, Banerjee Lab, State University of New York at Buffalo
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the 
following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following 
disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following 
disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the University at Buffalo, the author, nor the names of its contributors may be used to endorse 
or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, 
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE 
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, 
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR 
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, 
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE 
USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

# Standard library imports
import sys
import os
import warnings

# Set dask scheduler to synchronous BEFORE dask is imported anywhere.
# Prevents dask from importing 'distributed' which crashes on Windows
# environments with malformed SSL certificates in the Windows cert store.
# Must be an env var — dask.config.set() is too late since dask checks
# _distributed_available() before consulting config during .compute().
os.environ.setdefault('DASK_SCHEDULER', 'synchronous')

# Suppress CuPy CUDA path warning in ALL processes (main + worker subprocesses).
# PYTHONWARNINGS is read by every Python interpreter at startup before any
# imports run, so it suppresses the warning even in ProcessPoolExecutor workers
# that spawn fresh interpreters where warnings.filterwarnings() is too late.
# Also set CUDA_PATH to empty string to prevent CuPy from searching for
# the CUDA toolkit — this stops the warning at its source in worker processes.
os.environ.setdefault('CUDA_PATH', '')
_cupy_filter = 'ignore::UserWarning:cupy._environment'
_existing = os.environ.get('PYTHONWARNINGS', '')
if _cupy_filter not in _existing:
    os.environ['PYTHONWARNINGS'] = (
        (_existing + ',' if _existing else '') + _cupy_filter
    )
# Suppress CuPy's CUDA path warning before any imports trigger it.
# CuPy works via the GPU driver alone; the full CUDA toolkit is not required.
warnings.filterwarnings(
    "ignore",
    message="CUDA path could not be detected",
    category=UserWarning,
)
import importlib.resources as resources

# Third party imports
import napari
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QIcon 

# Local application imports
from pycat.central_manager import CentralManager
from pycat.utils.logging_utils import get_logger

log = get_logger(__name__)





def _prewarm_cellpose_model():
    """
    Pre-downloads and caches the Cellpose model on first launch so users don't experience
    a silent hang the first time they click 'Run Cellpose' inside the GUI.
    Progress output is printed to the terminal where run-pycat was invoked.
    """
    try:
        from pathlib import Path
        model_cache = Path.home() / '.cellpose' / 'models' / 'cyto2'
        if model_cache.exists():
            log.info("Cellpose model already cached, skipping download.")
            return
        log.info("Cellpose model not found in cache — downloading now "
                 "(one-time setup, may take a minute)...")
        from cellpose import models
        models.CellposeModel(gpu=False, pretrained_model='cyto2')  # GPU init done separately in background
        log.info("Cellpose model downloaded and cached successfully.")
    except Exception as e:
        log.warning("Could not pre-cache Cellpose model: %s", e)
        log.warning("Cellpose will attempt to download the model on first use instead.")


def run_pycat_func():
    """
    Function to run the PyCAT application by creating a napari viewer instance and initializing the CentralManager.
    """
    _prewarm_cellpose_model()  # Cache Cellpose model before GUI opens to avoid silent hang on first use

    app = QApplication(sys.argv)  # sys.argv is necessary for proper app initialization

    # Global UI font: a sans-serif family (Arial/Segoe/DejaVu depending on OS) at a
    # slightly larger base size, so default text reads at the larger scale the
    # step headers use rather than the small Qt default.
    try:
        from PyQt5.QtGui import QFont
        _ui_font = QFont("Arial")
        _ui_font.setStyleHint(QFont.SansSerif)   # fall back to any sans-serif if Arial absent
        _ui_font.setPointSize(10)                # larger base than the Qt default (~8-9pt)
        app.setFont(_ui_font)
    except Exception:
        pass

    # On Windows, the taskbar groups by AppUserModelID; without an explicit one a
    # Python-launched app shows the generic Python icon instead of our window
    # icon. Set an explicit ID so the PyCAT logo appears in the taskbar.
    try:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'BanerjeeLab.PyCAT.napari')
    except Exception:
        pass

    try:
        # Use importlib.resources to get the path to the PyCAT logo
        logo_path = resources.files('pycat') / 'icons' / 'pycat_logo_512.png'
        with resources.as_file(logo_path) as icon_path:
            icon_path_str = str(icon_path)
        app.setWindowIcon(QIcon(icon_path_str))  # Set PyCAT logo as window icon
    except FileNotFoundError:
        log.warning("The PyCAT logo file was not found.")
    except ModuleNotFoundError:
        log.warning("The specified module 'pycat' was not found.")
    except Exception as e:
        log.warning("An unexpected error occurred setting the window icon: %s", e)

    log.info("Running PyCAT")  # Print message to console

    # Pre-compile Numba JIT kernels and check GPU in background thread
    # so the GUI opens instantly without waiting for CUDA initialization
    import threading
    def _warmup():
        try:
            from pycat.toolbox.numba_utils import warmup_numba
            warmup_numba()
        except Exception as e:
            print(f"[PyCAT Numba] Warmup skipped: {e}")
        # PyTorch/CUDA initialization — done in background so GUI opens instantly
        try:
            import torch
            if torch.cuda.is_available():
                print(f"[PyCAT] PyTorch CUDA available — Cellpose will use GPU: {torch.cuda.get_device_name(0)}")
            else:
                print("[PyCAT] PyTorch CUDA not available — Cellpose will use CPU.")
                print("[PyCAT] To enable GPU: pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118")
        except Exception as e:
            print(f"[PyCAT] Could not check PyTorch CUDA status: {e}")
    threading.Thread(target=_warmup, daemon=True).start()

    viewer = napari.Viewer(title="PyCAT-Napari")
    cm = CentralManager(viewer)

    # Batch processing setup — stored on cm (plain object) not viewer (pydantic model)
    from pycat.batch_processor import BatchProcessor, add_batch_toolbar_button
    from pycat.batch_step_registry import register_all_steps
    bp = BatchProcessor(viewer)
    register_all_steps(bp)
    cm._pycat_batch_processor = bp   # readable via central_manager in widgets
    bp._central_manager = cm          # so bp.record() can notify the checklist
    add_batch_toolbar_button(viewer, bp)

    # Finalize the window after the event loop starts. The app-wide stylesheet
    # (QGroupBox title spacing) triggers an async relayout that would re-show the
    # window at default size, so it must run BEFORE the maximize — and the
    # maximize is staggered a bit later so it lands after that relayout settles.
    _GROUPBOX_QSS = """
QGroupBox { margin-top: 22px; padding-top: 10px; border: 1px solid rgba(255,255,255,0.15); border-radius: 4px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 8px; top: 2px; padding: 0 4px; }
"""
    def _apply_style():
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.setStyleSheet((_app.styleSheet() or "") + _GROUPBOX_QSS)
        except Exception:
            pass
    # ── Robust maximize (event-driven, not a timing race) ────────────────
    # History: maximize has repeatedly re-broken because it was done either
    # synchronously before the event loop (ignored — the startup relayout
    # re-shows the window at default size) or on a fixed timer delay (whichever
    # delay was chosen eventually lost the race as later UI changes lengthened
    # the startup relayout). The durable fix is to stop guessing a delay: assert
    # maximized after the loop starts, then WATCH the window state for a short
    # settling period and re-assert if any late relayout un-maximizes it. The
    # watcher disconnects itself once the state is stable, so there is no ongoing
    # cost and no fixed delay to out-grow.
    _maximize_state = {'deadline_ms': 2500, 'elapsed': 0, 'timer': None}

    def _maximize():
        try:
            viewer.window._qt_window.showMaximized()
        except Exception:
            pass

    def _is_maximized():
        try:
            return bool(viewer.window._qt_window.isMaximized())
        except Exception:
            return True  # can't tell → assume fine, stop watching

    def _ensure_maximized():
        """Re-assert maximize if a startup relayout dropped it; stop once the
        state has been stable through the settling window."""
        try:
            if not _is_maximized():
                _maximize()
            _maximize_state['elapsed'] += 100
            if _maximize_state['elapsed'] >= _maximize_state['deadline_ms']:
                t = _maximize_state.get('timer')
                if t is not None:
                    t.stop()
        except Exception:
            pass

    def _brand_welcome():
        """Replace napari's canvas welcome logo (a QLabel styled via a QSS
        `image:` url, objectName 'logo_silhouette') with the PyCAT logo, keep the
        hotkeys, and show 'PyCAT <ver> • napari <ver>'. Also set the window icon.
        Best-effort and fully guarded — napari internals vary by version."""
        from PyQt5.QtGui import QIcon
        from PyQt5.QtWidgets import QLabel
        try:
            _lp = resources.files('pycat') / 'icons' / 'pycat_logo_512.png'
            with resources.as_file(_lp) as _p:
                logo_str = str(_p).replace('\\', '/')   # forward slashes for QSS url
        except Exception:
            logo_str = None
        try:
            from importlib.metadata import version as _pkgver
            pycat_ver = _pkgver('pycat-napari')
        except Exception:
            pycat_ver = ''
        try:
            if logo_str:
                viewer.window._qt_window.setWindowIcon(QIcon(logo_str))
        except Exception:
            pass
        try:
            import warnings as _warnings
            with _warnings.catch_warnings():
                _warnings.simplefilter('ignore', FutureWarning)
                qtv = (getattr(viewer.window, '_qt_viewer', None)
                       or getattr(viewer.window, 'qt_viewer', None))
            welcome = getattr(qtv, '_welcome_widget', qtv) if qtv is not None else None
            if welcome is not None and logo_str:
                for lbl in welcome.findChildren(QLabel):
                    name = lbl.objectName()
                    txt = lbl.text() or ''
                    is_logo = ('logo' in name.lower() or 'silhouette' in name.lower()
                               or (not txt and lbl.minimumWidth() >= 200))
                    if is_logo:
                        # Override the QSS image at matching specificity so it
                        # wins over napari's themed logo rule.
                        if name:
                            lbl.setStyleSheet(f"#{name} {{ image: url('{logo_str}'); }}")
                        else:
                            lbl.setStyleSheet(f"image: url('{logo_str}');")
                    elif (txt and 'napari' in txt.lower()
                          and pycat_ver and 'PyCAT' not in txt):
                        # PyCAT first, then napari.
                        lbl.setText(f"PyCAT {pycat_ver}   \u2022   {txt}")
                    elif txt and ('image.sc' in txt.lower() or 'forum' in txt.lower()):
                        # Reword the help line to cover PyCAT as well as napari.
                        lbl.setText(
                            "PyCAT \u2022 napari  \u2014  For help with PyCAT visit "
                            "github.com/BanerjeeLab-repertoire/pycat-napari, and for "
                            "napari visit forum.image.sc/tag/napari.")
        except Exception:
            pass

    # Assert maximize AFTER the event loop starts (a pre-loop showMaximized() is
    # silently discarded by the startup relayout), then poll-and-reassert briefly
    # so any late relayout can't leave the window un-maximized. This replaces the
    # old fixed-delay single-shot that kept getting out-grown by UI changes.
    try:
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, _apply_style)     # stylesheet first (triggers relayout)
        QTimer.singleShot(0, _maximize)        # first maximize once loop is live
        QTimer.singleShot(80, _brand_welcome)  # brand once the welcome widget exists
        # Settling watcher: every 100ms, re-assert maximize if it dropped, until
        # the state has been stable for the deadline. Self-stops.
        _mx_timer = QTimer()
        _mx_timer.setInterval(100)
        _mx_timer.timeout.connect(_ensure_maximized)
        _maximize_state['timer'] = _mx_timer
        _mx_timer.start()
    except Exception:
        _apply_style(); _maximize(); _brand_welcome()

    # Re-apply branding whenever the canvas returns to empty (napari regenerates
    # the welcome screen when the last layer is removed), so the PyCAT logo/text
    # don't revert to napari's defaults after a Clear.
    def _rebrand_if_empty(*_a):
        try:
            if len(viewer.layers) == 0:
                from PyQt5.QtCore import QTimer as _QT
                _QT.singleShot(50, _brand_welcome)
        except Exception:
            pass
    try:
        viewer.layers.events.removed.connect(_rebrand_if_empty)
        viewer.layers.events.inserted.connect(_rebrand_if_empty)
    except Exception:
        pass

    napari.run()


def main():
    """
    Main function to run the PyCAT application. Serves as the entry point for the application.
    """
    run_pycat_func()

if __name__ == "__main__":
    main()




