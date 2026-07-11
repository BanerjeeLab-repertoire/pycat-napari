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

# ── OpenMP runtime safety (must run BEFORE numpy/torch/numba/napari import) ──
# On Apple Silicon (arm64) several bundled libraries (PyTorch, Numba, MKL,
# Cellpose) can each pull in their OWN copy of the OpenMP runtime. When two
# copies load into the same process the program can abort at the C level with a
# segmentation fault at launch — which multiple native-arm64 macOS users hit.
# The "OMP: Info #276 ... omp_set_nested ... deprecated" banner is a tell-tale
# sign of a duplicate OpenMP load. Setting KMP_DUPLICATE_LIB_OK=TRUE tells the
# Intel/LLVM OpenMP runtime to tolerate the duplicate instead of aborting, and
# capping the thread counts avoids oversubscription races during init. These are
# no-ops on machines that don't have the conflict, so they are safe everywhere.
# They MUST be set before the first native import, hence right after `import os`.
for _var, _val in (
    ('KMP_DUPLICATE_LIB_OK', 'TRUE'),
    ('OMP_NUM_THREADS', os.environ.get('OMP_NUM_THREADS', '4')),
    ('KMP_INIT_AT_FORK', 'FALSE'),
):
    os.environ.setdefault(_var, _val)

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





def _is_macos_arch_mismatch():
    """
    Return True when PyCAT is running in a suspicious macOS architecture state —
    specifically x86_64 Python under Rosetta translation on an Apple Silicon
    machine. Importing/initializing PyTorch/Cellpose in that state can segfault in
    native code before Python can catch it, so we skip the prewarm and tell the
    user to use a native arm64 environment.
    """
    if sys.platform != 'darwin':
        return False
    import platform as _platform
    import subprocess as _subprocess
    python_arch = _platform.machine().lower()
    # Direct Rosetta-translation flag: proc_translated == 1 means this process is
    # an x86 binary being translated on Apple Silicon.
    try:
        r = _subprocess.run(['sysctl', '-n', 'sysctl.proc_translated'],
                            capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip() == '1':
            return True
    except Exception:
        pass
    # Belt-and-suspenders: Apple Silicon hardware while Python reports x86_64.
    try:
        r = _subprocess.run(['sysctl', '-n', 'hw.optional.arm64'],
                            capture_output=True, text=True, timeout=2)
        if (r.returncode == 0 and r.stdout.strip() == '1'
                and python_arch in {'x86_64', 'amd64'}):
            return True
    except Exception:
        pass
    return False


def _cellpose_model_cached(model_name):
    """Return True if a Cellpose weight file for `model_name` is already cached.

    Cellpose stores weights under version-dependent directories and with suffixed
    filenames (e.g. `cyto2torch_0`, `cyto2_cp3`, `cpsam`), NOT as a bare file named
    exactly `model_name`. So we scan the known cache locations for any file whose
    name starts with the model name. Also honours the CELLPOSE_LOCAL_MODELS_PATH
    override some installs use. Safe: any error returns False (prewarm proceeds).
    """
    try:
        from pathlib import Path
        import os
        candidates = []
        env_dir = os.environ.get('CELLPOSE_LOCAL_MODELS_PATH')
        if env_dir:
            candidates.append(Path(env_dir))
        home = Path.home()
        candidates += [
            home / '.cellpose' / 'models',   # Cellpose <4 default
            home / '.cellpose',              # some builds drop weights one level up
        ]
        for d in candidates:
            try:
                if not d.is_dir():
                    continue
                for f in d.iterdir():
                    if f.is_file() and f.name.startswith(model_name):
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def _prewarm_cellpose_model():
    """
    Pre-downloads and caches the Cellpose model on first launch so users don't experience
    a silent hang the first time they click 'Run Cellpose' inside the GUI.
    Progress output is printed to the terminal where run-pycat was invoked.

    Two safety layers protect against native crashes during PyTorch/Cellpose init:

    1. **Architecture guard.** On macOS, if PyCAT is running as x86_64 under Rosetta
       on Apple Silicon, the prewarm is skipped entirely with a clear message —
       that environment is known to segfault during PyTorch init, and the real fix
       is a native arm64 environment, not caching a model.

    2. **Subprocess isolation.** The model load still runs in a SEPARATE SUBPROCESS,
       so any *other* native crash (e.g. an older CPU without AVX) only kills the
       subprocess — PyCAT still launches and the non-Cellpose segmentation methods
       (Multi-Otsu, StarDist, RF) remain available.

    The model is selected via PyCAT's version-aware builder, so the SAME model the
    real segmentation will use is cached: on Cellpose <4 the fast ``cyto2`` CNN
    (selected via ``model_type``), on Cellpose >=4 ``cpsam`` (via
    ``pretrained_model``). This avoids caching the wrong model or using the wrong
    Cellpose API for the installed version.
    """
    try:
        from pathlib import Path

        # (1) Architecture guard — skip on Rosetta/x86-on-Apple-Silicon.
        if _is_macos_arch_mismatch():
            log.warning(
                "Skipping Cellpose prewarm: PyCAT appears to be running as x86_64 "
                "under Rosetta emulation on an Apple Silicon Mac. PyTorch/Cellpose "
                "can crash during initialization in this state. Install PyCAT in a "
                "native arm64 Miniforge/conda environment (check with "
                "`python -c \"import platform; print(platform.machine())\"` — it "
                "should say 'arm64'). The GUI will still start; Cellpose may be "
                "unavailable until you switch to a native environment.")
            return

        # Version-aware model + cache path (cyto2 on Cellpose<4, cpsam on >=4).
        try:
            from pycat.toolbox.segmentation_tools import default_cellpose_model
            model_name = default_cellpose_model()
        except Exception:
            model_name = 'cyto2'
        # Cellpose does NOT save weights as a bare file named exactly `cyto2` — the
        # real cached filenames are variants like `cyto2torch_0`, `cyto2_cp3`, or
        # `cpsam` (and the dir differs across Cellpose versions). Checking for an
        # exact `.cellpose/models/<model_name>` path therefore always missed, so
        # the prewarm subprocess ran on EVERY launch and printed the misleading
        # "one-time download" message even though Cellpose already had it cached.
        # Instead, look for ANY cached weight file whose name starts with the model
        # name, across the known cache locations.
        if _cellpose_model_cached(model_name):
            log.info("Cellpose model '%s' already cached — no download needed; "
                     "it will load from disk when you run segmentation.",
                     model_name)
            return
        log.info("Cellpose model '%s' not in the local cache yet — downloading it "
                 "once now. This is a ONE-TIME setup (it's saved to your "
                 "~/.cellpose cache and reused on every future launch), and may "
                 "take a minute...", model_name)

        # (2) Subprocess isolation — load via PyCAT's version-aware builder so the
        # child uses the correct Cellpose API (model_type vs pretrained_model) for
        # the installed version, matching what real segmentation will do.
        import subprocess
        code = ("from pycat.toolbox.segmentation_tools import "
                "_build_cellpose_model, default_cellpose_model; "
                "_build_cellpose_model(default_cellpose_model())")
        proc = subprocess.run([sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode == 0:
            log.info("Cellpose model '%s' downloaded and cached — this won't "
                     "happen again; future launches load it straight from disk.",
                     model_name)
        elif proc.returncode < 0:
            # Negative return code == killed by a signal (e.g. -11 = SIGSEGV).
            log.warning(
                "Cellpose model pre-cache crashed at the native level "
                "(signal %d) — this usually means the installed PyTorch is not "
                "compatible with this CPU/architecture. PyCAT will still start; "
                "Cellpose segmentation may be unavailable, but the other "
                "segmentation methods (Multi-Otsu, StarDist, Random Forest) will "
                "work. To enable Cellpose, install a compatible PyTorch (e.g. "
                "`conda install -c conda-forge pytorch nomkl`) in a native "
                "environment.", -proc.returncode)
        else:
            log.warning("Could not pre-cache Cellpose model (exit %d): %s",
                        proc.returncode, (proc.stderr or '').strip()[-500:])
            log.warning("Cellpose will attempt to download on first use instead.")
    except Exception as e:
        log.warning("Could not pre-cache Cellpose model: %s", e)
        log.warning("Cellpose will attempt to download the model on first use instead.")


def run_pycat_func():
    """
    Function to run the PyCAT application by creating a napari viewer instance and initializing the CentralManager.
    """
    _prewarm_cellpose_model()  # Cache Cellpose model before GUI opens to avoid silent hang on first use

    app = QApplication(sys.argv)  # sys.argv is necessary for proper app initialization

    # OS-level branding: without these the taskbar / dock / window-manager label
    # the process as "napari" (or "python"). Present it as PyCAT everywhere.
    try:
        app.setApplicationName("PyCAT")
        app.setApplicationDisplayName("PyCAT")
        app.setOrganizationName("Banerjee Lab")
        try:
            # Linux: associate with a desktop entry name if present.
            app.setDesktopFileName("PyCAT")
        except Exception:
            pass
    except Exception:
        pass

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
        # Use importlib.resources to get the path to the PyCAT logo.
        # IMPORTANT: build the QIcon INSIDE the as_file() block. as_file() may
        # extract the resource to a temporary file that is deleted when the
        # `with` exits (zipped/bundled installs), so using the path afterwards
        # would silently load nothing — leaving napari's default icon. Loading
        # into a QPixmap here reads the bytes while the file is guaranteed to
        # exist, so the icon no longer depends on the path persisting.
        from PyQt5.QtGui import QPixmap as _QPixmap
        # Prefer the reduced mark (roundel only, no wordmark): at taskbar/titlebar
        # sizes the full logo's text is illegible, while the mark stays crisp.
        # Fall back to the full logo if the mark isn't present.
        _pm = None
        for _name in ('pycat_mark.png', 'pycat_logo_512.png'):
            try:
                logo_path = resources.files('pycat') / 'icons' / _name
                with resources.as_file(logo_path) as icon_path:
                    _candidate = _QPixmap(str(icon_path))
                if not _candidate.isNull():
                    _pm = _candidate
                    break
            except Exception:
                continue
        if _pm is not None and not _pm.isNull():
            app.setWindowIcon(QIcon(_pm))
        else:
            log.warning("The PyCAT logo could not be loaded as a pixmap.")
    except FileNotFoundError:
        log.warning("The PyCAT logo file was not found.")
    except ModuleNotFoundError:
        log.warning("The specified module 'pycat' was not found.")
    except Exception as e:
        log.warning("An unexpected error occurred setting the window icon: %s", e)

    log.info("Running PyCAT")  # Print message to console

    # Create the napari viewer (Qt/OpenGL) FIRST, on the main thread, before any
    # background warmup touches torch/Numba. On macOS, importing/initialising
    # torch (and running Numba JIT) on a worker thread *while* Qt initialises on
    # the main thread can segfault at the C level — the native libraries are not
    # safe to initialise concurrently. Creating the viewer first lets Qt finish
    # its main-thread setup before the warmup thread starts. The warmup can also
    # be skipped entirely by setting PYCAT_SKIP_WARMUP=1.
    viewer = napari.Viewer(title="PyCAT")

    # Dual pixel / micron coordinate readout in the status bar. PyCAT scales
    # layers by pixel size (µm/px), so napari's default status shows microns
    # only; this surfaces the raw pixel index alongside it (px is what the
    # analysis runs in). Best-effort — never blocks launch.
    try:
        from pycat.ui.coordinate_readout import install_coordinate_readout
        install_coordinate_readout(viewer)
    except Exception as _e:
        print(f"[PyCAT] Coordinate readout not installed: {_e}")

    if os.environ.get('PYCAT_SKIP_WARMUP', '') not in ('1', 'true', 'True'):
        import threading
        def _warmup():
            try:
                from pycat.toolbox.numba_utils import warmup_numba
                warmup_numba()
            except Exception as e:
                print(f"[PyCAT Numba] Warmup skipped: {e}")
            # PyTorch/CUDA status check — background so the GUI stays responsive.
            # SKIPPED on macOS: (a) there is no CUDA on Apple Silicon, so the
            # check has no useful result there, and (b) importing torch on this
            # worker thread *while* Qt/CentralManager initialise on the main
            # thread is a known cause of a C-level segfault at launch on arm64
            # Macs. Deferring the torch import to first actual use avoids the
            # concurrent native-init race entirely.
            if sys.platform == 'darwin':
                pass
            else:
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
        # The welcome logo is applied via a QSS `image: url(...)` that Qt reads
        # LAZILY when it paints — long after this function returns. So the file
        # must persist for the window's lifetime, not just a `with` block. If we
        # used resources.as_file()'s temp path, it could be deleted before Qt
        # ever paints (zipped installs), silently leaving napari's default bean.
        # Copy the logo to a stable per-session temp file so the QSS url stays
        # valid, and keep a reference so it isn't garbage-collected/cleaned early.
        logo_str = None
        try:
            import tempfile as _tempfile, atexit as _atexit, os as _os, shutil as _shutil
            _lp = resources.files('pycat') / 'icons' / 'pycat_logo_512.png'
            _persist = _os.path.join(_tempfile.gettempdir(),
                                     'pycat_welcome_logo_512.png')
            with resources.as_file(_lp) as _p:
                _shutil.copyfile(str(_p), _persist)
            _atexit.register(lambda: _os.path.exists(_persist)
                             and _os.remove(_persist))
            logo_str = _persist.replace('\\', '/')   # forward slashes for QSS url
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




