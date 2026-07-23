"""
**Asking the user. Not reading the file.**

Three dialogs that interrupt a load to ask a question only a human can answer:

* **Copy this file locally?** — the file is on slow storage, and scrubbing it will crawl.
* **Are these pages T or Z?** — an undeclared multipage TIFF says nothing about its own axis, and
  ***T and Z load identically***, so **nothing downstream can discover the answer for itself.**

── Session state, not instance state ────────────────────────────────────────────────────

Two of these kept their memory on ``self`` — ``self._multipage_axis_choice`` (*"remember my answer
for the rest of this session"*) and ``self._local_cache_files``. **Neither was ever read by another
method.** They were scratch variables that happened to be spelled as attributes of a 3,108-line
class, and they are now module-level, which is what they always were.

── A leak, now cleaned up at startup ────────────────────────────────────────────────────

``_LOCAL_CACHE_FILES`` used to carry the comment *"Track for optional cleanup at session
end"* — and there was no cleanup, so a copied 1.5 GB acquisition stayed in TEMP forever.

Cleanup now lives in ``local_cache.py`` and runs **at startup**, not session end (session
end never runs on a crash or a kill, and racing GC during teardown is the worst possible
time to delete a file a lazy reader might still hold). The copy step below records each
copy's *origin* into a manifest so that later sweep can show the user which acquisition,
from which folder, it is about to delete — and let them **Keep** any of it for a week.
*Deleting a scientist's data silently is worse than a cache that grows; this deletes it
visibly, and only what they didn't keep.*
"""

from __future__ import annotations

import os as _os
import shutil
import tempfile

# **No module-level Qt import.** Every function here imports the widgets it needs INSIDE its body —
# and `QProgressDialog` inside a `try/except` that sets it to `None` on failure, a deliberate
# graceful-degradation path for a headless or minimal Qt install.
#
# *Hoisting them to module scope would turn a soft dependency into a hard one, and the
# copy-to-local path would stop working entirely rather than working without a progress bar.*


# Session memory for the "these pages are T / Z — remember this" answer. A one-element list because
# it is rebound, not mutated.
_MULTIPAGE_AXIS_CHOICE = [None]

# Files copied to the local cache this session. **Nothing reads this** — see the module docstring.
_LOCAL_CACHE_FILES = []


def _ask_copy_to_local(file_path, verdict):
    """Ask whether to copy a slow-storage file to fast local temp storage
    before loading. Returns 'yes'|'no'|'always'|'never' (or 'no' if the dialog
    can't be shown)."""
    try:
        from PyQt5.QtWidgets import QMessageBox, QCheckBox
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        return 'no'
    try:
        size_mb = (verdict.size_bytes or 0) / (1024 * 1024)
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        size_mb = 0
    where = {'network': 'a network location', 'removable': 'a removable drive',
             'cloud_placeholder': 'cloud storage (will download)'}.get(
                 getattr(verdict, 'location', ''), 'slow storage')
    box = QMessageBox()
    box.setWindowTitle("Copy to local storage first?")
    box.setIcon(QMessageBox.Question)
    box.setText(
        f"'{_os.path.basename(file_path)}' ({size_mb:.0f} MB) is on {where}, "
        "which loads slowly. Copy it to fast local temp storage first (with a "
        "progress bar), then load from the copy?")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    always = QCheckBox("Always do this for slow files this session")
    box.setCheckBox(always)
    res = box.exec_()
    if res == QMessageBox.Yes:
        return 'always' if always.isChecked() else 'yes'
    return 'never' if always.isChecked() else 'no'

def _copy_to_local_with_progress(file_path, verdict):
    """Copy a (slow-storage) file to a local temp dir in chunks, showing a Qt
    progress bar (the copy IS the slow I/O, so this doubles as the slow-load
    progress indicator). Returns the local path, or None on failure/cancel."""
    try:
        from PyQt5.QtWidgets import QProgressDialog
        from PyQt5.QtCore import Qt
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        QProgressDialog = None
    try:
        total = _os.path.getsize(file_path)
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        total = getattr(verdict, 'size_bytes', 0) or 0
    dst_dir = _os.path.join(tempfile.gettempdir(), 'pycat_local_cache')
    try:
        _os.makedirs(dst_dir, exist_ok=True)
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        return None
    # Opportunistic cleanup: remove cached copies older than ~24h so the
    # cache doesn't grow unbounded across sessions (the OS clears the temp
    # dir eventually, but this keeps it tidy between reboots).
    try:
        import time as _time
        now = _time.time()
        for _f in _os.listdir(dst_dir):
            _p = _os.path.join(dst_dir, _f)
            try:
                if now - _os.path.getmtime(_p) > 86400:
                    _os.remove(_p)
            except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                pass
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        pass
    dst = _os.path.join(dst_dir, _os.path.basename(file_path))
    # If a fresh local copy already exists (same size), reuse it.
    try:
        if _os.path.exists(dst) and total and _os.path.getsize(dst) == total:
            print(f"[PyCAT storage] reusing local copy: {dst}")
            return dst
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        pass

    dlg = None
    if QProgressDialog is not None:
        try:
            dlg = QProgressDialog(
                f"Copying {_os.path.basename(file_path)} to local storage…",
                "Cancel", 0, 100)
            dlg.setWindowTitle("Copying to local storage")
            dlg.setWindowModality(Qt.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
        except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
            dlg = None

    CHUNK = 8 * 1024 * 1024  # 8 MB chunks
    copied = 0
    try:
        with open(file_path, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                buf = fsrc.read(CHUNK)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)
                if dlg is not None and total:
                    pct = int(copied * 100 / total)
                    dlg.setValue(min(pct, 100))
                    from PyQt5.QtWidgets import QApplication
                    QApplication.processEvents()
                    if dlg.wasCanceled():
                        fdst.close()
                        try:
                            _os.remove(dst)
                        except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                            pass
                        print("[PyCAT storage] copy cancelled by user")
                        return None
        if dlg is not None:
            dlg.setValue(100)
        print(f"[PyCAT storage] copied to local cache: {dst} "
              f"({copied/(1024*1024):.0f} MB)")
        # Track for optional cleanup at session end.
        _LOCAL_CACHE_FILES.append(dst)
        # Record the origin so a LATER session's startup sweep can show the user which
        # acquisition (and from where) it is about to delete — the cache dir itself is flat
        # (basenames only), so the source path has to be written down at copy time or it is lost.
        try:
            from pycat.file_io.local_cache import record_copy
            record_copy(dst, file_path)
        except Exception as _rc_exc:  # broad-ok: best-effort storage/cache op — logged, the load continues
            from pycat.utils.general_utils import debug_log
            debug_log('dialogs: could not record cache copy origin', _rc_exc)
        return dst
    except Exception as e:  # broad-ok: best-effort storage/cache op — logged, the load continues
        print(f"[PyCAT storage] copy-to-local failed: {e}")
        try:
            if _os.path.exists(dst):
                _os.remove(dst)
        except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
            pass
        return None

def _ask_multipage_axis(file_path, n_pages):
    """Prompt for how to interpret an undeclared multipage TIFF: time-series
    (T), z-stack (Z), or genuinely separate 2D images. Returns 'T', 'Z',
    'separate', or None (dialog unavailable). A 'remember this choice'
    checkbox skips the prompt for later undeclared TIFFs this session."""
    # Honour a remembered choice from earlier this session.
    remembered = _MULTIPAGE_AXIS_CHOICE[0]
    if remembered is not None:
        return remembered
    try:
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                     QRadioButton, QCheckBox, QPushButton,
                                     QButtonGroup)
    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
        return None
    dlg = QDialog()
    dlg.setWindowTitle("Unlabelled multipage TIFF")
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel(
        f"'{_os.path.basename(file_path)}' has {n_pages} pages but no axis "
        "metadata (the stack axis type is unknown).\n\nHow should PyCAT load "
        "it? (Time-series and z-stack load the same way — the label only "
        "affects axis-dependent analysis steps, which will warn if the axis "
        "was assumed.)"))
    grp = QButtonGroup(dlg)
    rb_t = QRadioButton("Time-series (T) — a movie / recovery / tracking stack")
    rb_z = QRadioButton("Z-stack (Z) — an axial slice series")
    rb_s = QRadioButton("Separate 2D images — unrelated planes, load individually")
    rb_t.setChecked(True)
    for rb in (rb_t, rb_z, rb_s):
        grp.addButton(rb); v.addWidget(rb)
    remember = QCheckBox("Remember my choice for other unlabelled TIFFs this session")
    v.addWidget(remember)
    ok = QPushButton("Load"); ok.clicked.connect(dlg.accept)
    v.addWidget(ok)
    if dlg.exec_() != QDialog.Accepted:
        return None
    choice = 'T' if rb_t.isChecked() else ('Z' if rb_z.isChecked() else 'separate')
    if remember.isChecked():
        _MULTIPAGE_AXIS_CHOICE[0] = choice
    return choice


# ── Two Qt dialog CLASSES, moved from file_io.py (file-io decomposition, 1.6.146) ─────────────
# Unlike the ask-functions above, these are QDialog SUBCLASSES — they need Qt at class-definition
# time, so this module now imports the widgets at module scope for them (the functions still import
# lazily). Nothing external imported these from file_io; FileIOClass now imports them from here.
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QWidget, QLabel, QCheckBox, QRadioButton,
                             QPushButton, QLineEdit, QMessageBox, QComboBox)
import os
import numpy as np

class LayerDataframeSelectionDialog(QDialog):
    """
    A dialog that allows users to select from a list of layers and dataframe names for operations
    such as saving or processing. Users can also choose a clearing option to specify whether all
    data should be cleared or only the data that has been saved.

    Parameters
    ----------
    layers : list
        A list of layer objects. Each layer object is expected to have a 'name' attribute.
    dataframe_names : list of str
        A list of names representing the dataframes available for selection.

    Attributes
    ----------
    selected_layers : list
        A list of names of the layers that the user has selected.
    selected_dataframes : list of str
        A list of names of the dataframes that the user has selected.
    
    Methods
    -------
    get_selections(self):
        Returns the selections of layers and dataframes, along with the clearing option.
    """
    def __init__(self, layers, dataframe_names):
        """
        Initializes the dialog with the provided layers and dataframe names, setting up
        the UI components including checkboxes for each layer and dataframe, and radio buttons
        for clearing options.
        """
        super().__init__() # Initialize the parent class
        
        self.layers = layers
        self.dataframe_names = dataframe_names  # Expecting list of dataframe names
        self.selected_layers = []
        self.selected_dataframes = []
        
        layout = QVBoxLayout()

        # List all available layers with checkboxes, annotated with their
        # estimated on-disk size and whether they are RECONSTRUCTABLE.
        #
        # Two kinds of waste motivated this:
        #  * An UPSCALED IMAGE carries no information its source didn't — it is a
        #    pure interpolation, so saving a 4x upscale costs 16x the pixels for
        #    nothing (measured: 484 kB -> 7.7 MB). It is reconstructable from the
        #    source + the factor, so it is flagged and unticked by default.
        #  * A MASK SEGMENTED AT HIGH RESOLUTION is NOT redundant — its sub-pixel
        #    boundaries are real information that downscaling destroys (it can
        #    merge touching objects). Those are never flagged as disposable.
        layout.addWidget(QLabel("Select Layers to Save:"))
        _hint = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>Sizes are the compressed "
            "estimate. Layers marked <span style='color:#e8a33d;'>reconstructable"
            "</span> are pure interpolations of another layer (an upscale carries "
            "no new information) — they're unticked by default. Masks segmented at "
            "high resolution are <i>not</i> reconstructable and are never flagged."
            "</span>")
        _hint.setWordWrap(True)
        layout.addWidget(_hint)

        self.layer_checkboxes = {}

        def _est_size_mb(layer):
            """Rough compressed-size estimate: masks/labels compress ~100x, images
            ~1.5x. Cheap heuristic — no need to actually compress to inform a
            checkbox."""
            try:
                d = layer.data
                n = int(np.prod(d.shape))
                # right-sized bytes-per-pixel (matches what the saver now writes)
                is_label = (type(layer).__name__ == 'Labels')
                if is_label:
                    try:
                        mx = int(np.asarray(d[0] if d.ndim == 3 else d).max())
                    except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                        mx = 65535
                    bpp = 1 if mx <= 255 else 2
                    ratio = 100.0           # masks compress enormously
                else:
                    bpp = 2
                    ratio = 1.5             # real image data barely compresses
                return (n * bpp / ratio) / (1024 * 1024)
            except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                return 0.0

        def _is_reconstructable(layer):
            """True only for layers that are pure interpolations of another layer
            (upscaled IMAGES). Deliberately conservative: never flags a mask, since
            a mask segmented at high res holds real sub-pixel information."""
            try:
                if type(layer).__name__ == 'Labels':
                    return False            # never call a segmentation disposable
                name = str(layer.name).lower()
                if 'upscal' in name:
                    return True
                # Tag-based: provenance says it was derived by an upscale step.
                # Was `(get_tags(layer) or {}).get('operation', ...)`: `get_tags` returns a LIST, so
                # this raised into the `except` and never fired, and `operation` is not a tag key —
                # the vocabulary's key is `op`. (Same two mistakes as the copy in
                # `session_manifest._is_reconstructable`, which is this function duplicated.) Inert
                # until an upscale op is registered; the name check above is the live path.
                try:
                    from pycat.utils.layer_tags import get_tag
                    if 'upscal' in str(get_tag(layer, 'op', '') or '').lower():
                        return True
                except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                    pass
                return False
            except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                return False

        default_checked_layers = [
            "Labeled Cell Mask",
            "Cell Labeled Puncta Mask",
            "Overlay Image",
            "Pre-Processed Fluorescence Image",
        ]

        # Smart session default: tick every DERIVED layer (masks, tracks,
        # processed images), and never the SOURCE IMAGE (it is on disk — a session
        # references it, and copying it just wastes space) or a reconstructable
        # upscale. This removes the ticking burden — the user only unticks/adds if
        # they want to override.
        try:
            from pycat.file_io.session_manifest import (
                _is_source_image_layer as _sess_is_source)
            _src_stem = getattr(
                getattr(self, '_central_manager', None), 'active_data_class', None)
        except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
            _sess_is_source = None
        # Best-effort source stem for identifying the original image layer.
        _src_stem_name = ''
        try:
            _src_stem_name = (self.layers and
                              max((str(l.name) for l in self.layers), key=len)) or ''
        except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
            _src_stem_name = ''

        _total_all = 0.0
        for layer in self.layers:
            mb = _est_size_mb(layer)
            _total_all += mb
            recon = _is_reconstructable(layer)
            _is_source = False
            try:
                if _sess_is_source is not None:
                    # Identify the source by the loaded-image heuristic/tags.
                    _is_source = _sess_is_source(layer, '')
            except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                _is_source = False
            label = f"{layer.name}   ({mb:.1f} MB)"
            if _is_source:
                label += "   — source image (already on disk; referenced, not copied)"
            elif recon:
                label += "   — reconstructable (upscale of another layer)"
            checkbox = QCheckBox(label)
            if _is_source:
                checkbox.setStyleSheet("color: #7fa7d4;")
                checkbox.setToolTip(
                    "This is the originally-loaded image. A session references it "
                    "by path rather than copying it (it is already on disk and is "
                    "the largest file), so it is unticked by default.")
            elif recon:
                checkbox.setStyleSheet("color: #e8a33d;")
                checkbox.setToolTip(
                    "This layer is an upscaled copy of another layer. Upscaling "
                    "adds no information — it can be recreated from the source "
                    "layer and the scale factor, so saving it wastes space "
                    "(a 4x upscale is 16x the pixels). Unticked by default.")
            self.layer_checkboxes[layer.name] = checkbox
            layout.addWidget(checkbox)

            # Smart default: tick every derived layer; never the source or an
            # upscale.
            if not recon and not _is_source:
                checkbox.setChecked(True)


        # List all available Python dataframe names with checkboxes
        layout.addWidget(QLabel("Select Dataframes to Save:"))
        self.df_checkboxes = {}
        # Create checkboxes for each dataframe name
        for df_name in self.dataframe_names:
            checkbox = QCheckBox(df_name)
            self.df_checkboxes[df_name] = checkbox
            layout.addWidget(checkbox)
            # Smart default: every analysis dataframe is part of the session.
            checkbox.setChecked(True)

        # Radio buttons for Clearing option
        self.clear_all_radio = QRadioButton("Clear All")
        self.clear_saved_radio = QRadioButton("Clear Only Saved")
        self.clear_all_radio.setChecked(True)  # Default to clear all 
        layout.addWidget(self.clear_all_radio)
        layout.addWidget(self.clear_saved_radio)
        
        # Ok and Cancel buttons
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.ok_btn)
        layout.addWidget(self.cancel_btn)

        # Clear WITHOUT saving — discards everything after an explicit confirm.
        self.clear_without_saving = False
        self.discard_btn = QPushButton("☠  Clear Without Saving")
        self.discard_btn.setToolTip(
            "Discard all layers and data without saving anything.")
        self.discard_btn.setStyleSheet(
            "QPushButton { color: #b00020; font-weight: bold; }")
        def _on_discard():
            confirm = QMessageBox.warning(
                self, "Clear without saving?",
                "This will permanently clear ALL layers and data and save "
                "NOTHING.\n\nAll unsaved data will be lost. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm == QMessageBox.Yes:
                self.clear_without_saving = True
                self.accept()
        self.discard_btn.clicked.connect(_on_discard)
        layout.addWidget(self.discard_btn)

        self.setLayout(layout)

    def get_selections(self):
        """
        Gathers and returns the user's selections, including the selected layers, selected
        dataframes, and the selected clearing option.

        Returns
        -------
        tuple
            A tuple containing two lists (selected layers and selected dataframes) and a boolean
            indicating the clearing option (True for clearing all, False for clearing only saved).
        """
        # Update which layers/dataframes are selected
        self.selected_layers = [layer for layer, checkbox in self.layer_checkboxes.items() if checkbox.isChecked()]
        self.selected_dataframes = [df for df, checkbox in self.df_checkboxes.items() if checkbox.isChecked()]
        
        clear_all = self.clear_all_radio.isChecked()
        
        return self.selected_layers, self.selected_dataframes, clear_all


class ChannelAssignmentDialog(QDialog):
    """
    A dialog for assigning names to image channels, providing a user-friendly interface for 
    specifying custom names for each channel based on the file path or default naming conventions. 
    It supports differentiating between mask channels and other image types.

    Parameters
    ----------
    channels : list of tuples
        A list where each tuple contains channel data, the file path of the channel, and potentially
        other metadata. The channel data and file path are used in the UI.
    is_mask : bool, optional
        A flag indicating whether the channels being named are mask channels. This affects the
        default naming convention. Default is False.
    parent : QWidget, optional
        The parent widget of the dialog. Default is None.

    Attributes
    ----------
    channel_name_inputs : list of QLineEdit
        A list of QLineEdit widgets that allow the user to enter custom names for each channel.

    Methods
    -------
    initUI(self):
        Initializes the user interface components of the dialog, including labels and text input
        fields for channel names, and the OK button to accept the naming.
    """
    def __init__(self, channels, is_mask=False, parent=None, channel_info=None):
        """
        Initializes the dialog with the provided channels, setting up the UI for channel naming.

        Parameters
        ----------
        channel_info : list of dict, optional
            Per-channel metadata-derived info from identify_channel(), used
            to pre-populate default names (e.g. "DAPI", "EGFP") instead of
            generic "Segmentation Image"/"Fluorescence Image" placeholders
            when channel identity could be determined from file metadata.
        """
        super().__init__(parent)
        self.channels = channels
        self.is_mask = is_mask
        self.channel_info = channel_info or []
        self.initUI()

    def initUI(self):
        # `derive_layer_name` stays in file_io.py (two tests AST-parse it there); lazy import avoids a cycle.
        from pycat.file_io.file_io import derive_layer_name
        """
        Sets up the layout and UI elements of the dialog, including labels indicating the channel
        number and file name, and text input fields pre-populated with default names that the user
        can customize. An OK button is provided for confirming the naming.
        """
        layout = QVBoxLayout()
        self.channel_name_inputs = [] # Create a list to store the textbox name inputs

        # Are these entries separate FILES (a multi-select) or channels of one
        # multichannel image? Distinct file paths mean the user opened several
        # files at once — each should be named from its own filename, NOT from
        # the positional "Segmentation Image"/"Fluorescence Image" convention
        # (which belongs to the single-file two-channel cell-analysis workflow).
        _distinct_files = len({fp for (_d, fp, _k) in self.channels}) > 1

        # Add labels and input fields for each channel
        for channel_num, (channel_data, file_path, _) in enumerate(self.channels):
            label = QLabel(f"Channel {channel_num + 1} ({os.path.basename(file_path)}):")
            input_field = QLineEdit()

            info = self.channel_info[channel_num] if channel_num < len(self.channel_info) else None
            if self.is_mask and _distinct_files:
                default_name = derive_layer_name(
                    os.path.splitext(os.path.basename(file_path))[0], file_path,
                    [info] if info else None, is_mask=True)
            elif _distinct_files:
                # Separate files → filename-derived name (e.g. '..._DAPI.tif' →
                # 'cells_DAPI'), so two DAPI/GFP files are distinguishable and
                # neither is mislabelled "Segmentation Image".
                default_name = derive_layer_name(
                    os.path.splitext(os.path.basename(file_path))[0], file_path,
                    [info] if info else None)
            elif not self.is_mask:
                # Channels of ONE multichannel image: keep the positional
                # convention (the two-channel cell workflow relies on it), but
                # enrich with metadata identity when the file provides it.
                if info is not None and info.get('source') != 'position':
                    if channel_num == 0:
                        default_name = f"Segmentation Image ({info['label']})"
                    elif channel_num == 1:
                        default_name = f"Fluorescence Image ({info['label']})"
                    else:
                        default_name = f"{info['layer_name']} {os.path.basename(file_path)}"
                elif channel_num == 0:
                    default_name = "Segmentation Image"
                elif channel_num == 1:
                    default_name = "Fluorescence Image"
                else:
                    default_name = f"{os.path.basename(file_path)} Ch {channel_num+1}"
            else:
                default_name = f"{os.path.basename(file_path)} Ch {channel_num+1} Mask"

            # Set the default name in the input field
            input_field.setText(default_name)
            self.channel_name_inputs.append(input_field)

            # Add the label and input field to the layout
            layout.addWidget(label)
            layout.addWidget(input_field)

        # ── Opt-in: which channel contains the condensates? ────────────────────────
        # Metadata can't recover this per-experiment fact, and when two fluorescence channels
        # get the same generic name the only thing telling them apart is load order — which
        # silently drove the wrong channel (e.g. DAPI) into condensate segmentation. So let the
        # user state it ONCE, opt-in, and remember it per acquisition layout. "Don't set" leaves
        # it undecided (the honest default — we never guess).
        self.condensate_choice = None
        if not self.is_mask and len(self.channels) > 1:
            from PyQt5.QtWidgets import QComboBox
            layout.addWidget(QLabel("Which channel contains the condensates? "
                                    "(optional — remembered for files acquired this way)"))
            self._condensate_dd = QComboBox()
            self._condensate_dd.addItem("Don't set (I'll choose per run)", -1)
            for cn, (_, fp, _) in enumerate(self.channels):
                info = self.channel_info[cn] if cn < len(self.channel_info) else None
                lbl = (info or {}).get('label')
                bucket = (info or {}).get('bucket')
                desc = f"Channel {cn + 1}"
                if lbl:
                    desc += f" — {lbl}" + (f" ({bucket})" if bucket else "")
                self._condensate_dd.addItem(desc, cn)
            # Pre-select a remembered designation for this layout, if any.
            try:
                from pycat.utils.channel_designations import recall_designation
                remembered = recall_designation(self.channel_info)
                if remembered is not None:
                    ix = self._condensate_dd.findData(remembered)
                    if ix >= 0:
                        self._condensate_dd.setCurrentIndex(ix)
            except Exception:  # broad-ok: Qt/layer-inspection best-effort — a UI probe that fails degrades gracefully
                pass
            layout.addWidget(self._condensate_dd)

        # Add the OK button to confirm the channel names
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button)

        # Set the layout for the dialog
        self.setLayout(layout)
        self.setWindowTitle("Channel Name Assignment")


# Main FileIOClass for handling file input/output operations
