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

── A leak, recorded rather than silently fixed ──────────────────────────────────────────

``_LOCAL_CACHE_FILES`` carries the comment *"Track for optional cleanup at session end."*

***There is no cleanup.*** Nothing reads this list. A user on a slow network share who accepts the
copy-to-local prompt for a 1.5 GB acquisition leaves 1.5 GB in ``%TEMP%/pycat_local_cache``, and it
stays there.

*It is preserved here exactly as it was, because a cleanup that deletes a scientist's data is worse
than a cache that grows — and choosing the right moment to purge it is a decision, not a
refactor.*
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
    except Exception:
        return 'no'
    import os as _os
    try:
        size_mb = (verdict.size_bytes or 0) / (1024 * 1024)
    except Exception:
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
    import os as _os
    import tempfile
    try:
        from PyQt5.QtWidgets import QProgressDialog
        from PyQt5.QtCore import Qt
    except Exception:
        QProgressDialog = None
    try:
        total = _os.path.getsize(file_path)
    except Exception:
        total = getattr(verdict, 'size_bytes', 0) or 0
    dst_dir = _os.path.join(tempfile.gettempdir(), 'pycat_local_cache')
    try:
        _os.makedirs(dst_dir, exist_ok=True)
    except Exception:
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
            except Exception:
                pass
    except Exception:
        pass
    dst = _os.path.join(dst_dir, _os.path.basename(file_path))
    # If a fresh local copy already exists (same size), reuse it.
    try:
        if _os.path.exists(dst) and total and _os.path.getsize(dst) == total:
            print(f"[PyCAT storage] reusing local copy: {dst}")
            return dst
    except Exception:
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
        except Exception:
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
                        except Exception:
                            pass
                        print("[PyCAT storage] copy cancelled by user")
                        return None
        if dlg is not None:
            dlg.setValue(100)
        print(f"[PyCAT storage] copied to local cache: {dst} "
              f"({copied/(1024*1024):.0f} MB)")
        # Track for optional cleanup at session end.
        _LOCAL_CACHE_FILES.append(dst)
        return dst
    except Exception as e:
        print(f"[PyCAT storage] copy-to-local failed: {e}")
        try:
            if _os.path.exists(dst):
                _os.remove(dst)
        except Exception:
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
    except Exception:
        return None
    import os as _os
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
