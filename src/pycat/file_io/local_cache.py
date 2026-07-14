"""
**The local cache, and the promise to clean it up.**

``dialogs._copy_to_local_with_progress`` copies a slow-storage acquisition into
``%TEMP%/pycat_local_cache`` so scrubbing it doesn't crawl. For a long time the list of
what it copied (``_LOCAL_CACHE_FILES``) was **written and never read** — the comment said
*"Track for optional cleanup at session end,"* and there was no cleanup. A user on a slow
share who accepted the copy for a 1.5 GB acquisition left 1.5 GB in TEMP, permanently.

This module is that cleanup — but it is a *scientist's data*, so it does not delete quietly.

── The model (decided with Gable, 2026-07-14) ──────────────────────────────────────────

* **Clear at startup, not at exit.** Session-end never runs on a crash, a kill, or an OS
  shutdown, and deleting during teardown races garbage collection against the lazy readers
  that may still hold the file. **Startup is the one moment the *previous* session's cached
  copies are provably idle** — nothing has opened them yet — so that is when it is safe to
  sweep them.

* **Nothing is deleted without the user having seen it at least once.** The startup dialog
  lists every cached copy, **grouped by the source folder it came from**, with sizes and
  the date it was cached. The user sees *which acquisitions, from where* — never an opaque
  "N files, M GB."

* **"Keep" protects data; it does not silence the message.** Checking *Keep* on a file (or
  a whole source folder) excludes it from *this* clear and records it as protected. It is
  **not** a global "stop warning me" switch — deletion is always reported.

* **Protection expires.** A kept item is protected for ``KEEP_DAYS`` (default 7), then it
  reappears as a clear-candidate in a later startup. A one-time *Keep* never pins gigabytes
  in TEMP forever; the expiry *is* the periodic reminder.

── What persists ────────────────────────────────────────────────────────────────────────

Two tiny JSON files under a per-user config dir (there is no other PyCAT settings store, so
this adds a minimal one, scoped to just this feature):

* ``protected.json`` — ``{original_source_path: keep_until_epoch}``. Keyed by the *original*
  path, so protection survives even though the cache directory itself is flat (basenames only).

The manifest that maps a flat cached basename back to its origin lives **in the cache dir**
(``_manifest.json``), written by the copy step — because the cache is the only thing that
knows a file was cached, and a manifest beside it survives a config-dir reset.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

try:
    from pycat.utils.general_utils import debug_log
except Exception:  # pragma: no cover - during partial installs
    def debug_log(*_a, **_k):
        pass


CACHE_DIRNAME = 'pycat_local_cache'
MANIFEST_NAME = '_manifest.json'
PROTECTED_NAME = 'protected.json'
KEEP_DAYS = 7  # how long a "Keep" lasts before the item is re-proposed for clearing


# ── Paths ────────────────────────────────────────────────────────────────────────────────

def cache_dir():
    """The flat directory cached copies live in (``%TEMP%/pycat_local_cache``)."""
    return os.path.join(tempfile.gettempdir(), CACHE_DIRNAME)


def _config_dir():
    """A per-user config dir for the protected-set. Falls back to the cache dir if the
    platform config location can't be determined — the point is persistence, not location."""
    try:
        import sys as _sys
        if os.name == 'nt':
            base = os.environ.get('APPDATA') or os.path.expanduser('~')
        elif _sys.platform == 'darwin':
            base = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support')
        else:
            base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
                os.path.expanduser('~'), '.config')
        d = os.path.join(base, 'PyCAT')
        os.makedirs(d, exist_ok=True)
        return d
    except Exception as exc:
        debug_log('local_cache: config dir unavailable, using cache dir', exc)
        return cache_dir()


def _manifest_path():
    return os.path.join(cache_dir(), MANIFEST_NAME)


def _protected_path():
    return os.path.join(_config_dir(), PROTECTED_NAME)


# ── Manifest (basename → origin), written by the copy step ─────────────────────────────────

def _load_manifest():
    try:
        with open(_manifest_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_manifest(m):
    try:
        with open(_manifest_path(), 'w', encoding='utf-8') as f:
            json.dump(m, f, indent=2)
    except Exception as exc:
        debug_log('local_cache: could not write manifest', exc)


def record_copy(dst_path, source_path):
    """Called by ``dialogs`` right after a successful copy. Records where the cached copy
    came from, so a later session can show the user its origin. Keyed by basename because
    that is what survives in the flat cache dir."""
    try:
        m = _load_manifest()
        m[os.path.basename(dst_path)] = {
            'source': source_path,
            'source_dir': os.path.dirname(source_path),
            'cached_at': time.time(),
            'size_bytes': _safe_size(dst_path),
        }
        _save_manifest(m)
    except Exception as exc:
        debug_log('local_cache: could not record copy', exc)


# ── Protected set (origin path → keep-until), persisted across sessions ────────────────────

def _load_protected():
    try:
        with open(_protected_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_protected(p):
    try:
        with open(_protected_path(), 'w', encoding='utf-8') as f:
            json.dump(p, f, indent=2)
    except Exception as exc:
        debug_log('local_cache: could not write protected set', exc)


def _protect(source_paths, days=KEEP_DAYS):
    """Mark origin paths as kept until now + ``days``. Prunes already-expired entries so the
    file doesn't accumulate dead keys."""
    p = _load_protected()
    now = time.time()
    # prune expired
    p = {k: v for k, v in p.items() if isinstance(v, (int, float)) and v > now}
    until = now + days * 86400
    for sp in source_paths:
        p[sp] = until
    _save_protected(p)


def _is_protected(source_path, now=None):
    now = now if now is not None else time.time()
    p = _load_protected()
    until = p.get(source_path)
    return isinstance(until, (int, float)) and until > now


# ── Helpers ────────────────────────────────────────────────────────────────────────────────

def _safe_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _human(nbytes):
    n = float(nbytes or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f"{n:.0f} {unit}" if unit in ('B', 'KB') else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _scan_cache():
    """Return a list of candidate dicts for every real file in the cache dir (excluding the
    manifest), joined to its manifest entry where available. Files with no manifest entry
    (older sessions, manual copies) still appear — with their origin unknown."""
    d = cache_dir()
    manifest = _load_manifest()
    items = []
    try:
        names = os.listdir(d)
    except Exception:
        return items
    for name in names:
        if name == MANIFEST_NAME:
            continue
        full = os.path.join(d, name)
        if not os.path.isfile(full):
            continue
        meta = manifest.get(name, {})
        source = meta.get('source') or ''
        source_dir = meta.get('source_dir') or (
            os.path.dirname(source) if source else '(origin unknown)')
        items.append({
            'basename': name,
            'path': full,
            'source': source,
            'source_dir': source_dir,
            'cached_at': meta.get('cached_at'),
            'size_bytes': _safe_size(full),
        })
    return items


def _group_by_source(items):
    """Group candidates by source folder → list of items, for display."""
    groups = {}
    for it in items:
        groups.setdefault(it['source_dir'], []).append(it)
    # stable order: biggest groups first, then alphabetical
    return dict(sorted(groups.items(),
                       key=lambda kv: (-sum(i['size_bytes'] for i in kv[1]), kv[0])))


# ── The startup sweep ──────────────────────────────────────────────────────────────────────

def clear_cache_on_startup():
    """Called once at launch, BEFORE any file is opened this session.

    Lists cached copies grouped by source folder, lets the user *Keep* files or whole
    folders (protecting them for ``KEEP_DAYS``), then deletes everything not kept and reports
    exactly what was freed. Currently-protected items are shown but pre-checked and excluded
    from the default clear; when their protection has expired they reappear unchecked.

    Best-effort and non-fatal: a cleanup that crashes the app it is cleaning up for has done
    more harm than the disk it was reclaiming."""
    try:
        items = _scan_cache()
    except Exception as exc:
        debug_log('local_cache: scan failed', exc)
        return

    if not items:
        return

    now = time.time()
    protected_now = [it for it in items if it['source'] and _is_protected(it['source'], now)]
    clearable = [it for it in items if it not in protected_now]

    total = sum(it['size_bytes'] for it in items)

    # Try the dialog. If Qt isn't available (headless/tests), fall back to a conservative
    # console notice and DO NOT auto-delete — silent deletion without a user ever seeing the
    # list is exactly what this feature exists to avoid.
    try:
        chosen_to_clear = _show_dialog(items, protected_now, now, total)
    except Exception as exc:
        debug_log('local_cache: dialog unavailable; not clearing', exc)
        print(f"[PyCAT storage] {len(items)} cached file(s) in {cache_dir()} "
              f"({_human(total)}). Open PyCAT with a display to review and clear them.")
        return

    if chosen_to_clear is None:
        # User dismissed/cancelled — leave everything, protect nothing new.
        print("[PyCAT storage] Cache review cancelled — nothing cleared.")
        return

    _apply(items, chosen_to_clear)


def _apply(all_items, chosen_to_clear):
    """Delete the chosen items; protect the rest for KEEP_DAYS; report what was freed."""
    to_clear = {it['path'] for it in chosen_to_clear}
    kept = [it for it in all_items if it['path'] not in to_clear]

    # Protect what was kept (by origin path, where known).
    keep_sources = [it['source'] for it in kept if it['source']]
    if keep_sources:
        try:
            _protect(keep_sources)
        except Exception as exc:
            debug_log('local_cache: could not persist kept set', exc)

    freed = 0
    removed = 0
    manifest = _load_manifest()
    for it in chosen_to_clear:
        try:
            os.remove(it['path'])
            freed += it['size_bytes']
            removed += 1
            manifest.pop(it['basename'], None)
        except Exception as exc:
            debug_log(f"local_cache: could not remove {it['path']}", exc)
    _save_manifest(manifest)

    if removed:
        print(f"[PyCAT storage] Cleared {removed} cached file(s), freed {_human(freed)} "
              f"from {cache_dir()}.")
    if kept:
        print(f"[PyCAT storage] Kept {len(kept)} cached file(s) for {KEEP_DAYS} days "
              f"(will re-propose after that).")


# ── The dialog ─────────────────────────────────────────────────────────────────────────────

def _show_dialog(items, protected_now, now, total):
    """Grouped, two-level (folder + per-file) keep/clear dialog.

    Returns the list of item-dicts the user chose to CLEAR, or ``None`` if cancelled.
    Raises if Qt is unavailable — the caller treats that as "don't delete."
    """
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton,
        QScrollArea, QWidget, QFrame)
    from PyQt5.QtCore import Qt

    groups = _group_by_source(items)

    dlg = QDialog()
    dlg.setWindowTitle("PyCAT — local cache cleanup")
    outer = QVBoxLayout(dlg)

    outer.addWidget(QLabel(
        f"PyCAT copied {len(items)} acquisition(s) to fast local storage "
        f"({_human(total)} in your temp folder) to speed up loading.\n\n"
        "These are copies — your originals are untouched. Check <b>Keep</b> to hold a copy "
        f"for {KEEP_DAYS} more days; everything left unchecked will be deleted now.\n"
        "Deletion is always reported in the terminal."))

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    iv = QVBoxLayout(inner)

    # State: per-item checkbox widgets, and per-folder header checkbox.
    item_boxes = {}   # id(item) -> (QCheckBox, item)
    for source_dir, its in groups.items():
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        bl = QVBoxLayout(box)

        grp_size = sum(i['size_bytes'] for i in its)
        folder_cb = QCheckBox(
            f"Keep all from  {source_dir}   —   {len(its)} file(s), {_human(grp_size)}")
        folder_cb.setStyleSheet("font-weight: bold;")
        bl.addWidget(folder_cb)

        child_cbs = []
        for it in its:
            row = QHBoxLayout()
            cb = QCheckBox()
            # Pre-check items that are currently protected (not yet expired).
            if it in protected_now:
                cb.setChecked(True)
            when = ''
            if it.get('cached_at'):
                try:
                    when = time.strftime('%Y-%m-%d', time.localtime(it['cached_at']))
                except Exception:
                    when = ''
            label = QLabel(
                f"{it['basename']}   ({_human(it['size_bytes'])}"
                + (f", cached {when}" if when else "") + ")")
            row.addSpacing(20)
            row.addWidget(cb)
            row.addWidget(label, 1)
            iv_row = QWidget()
            iv_row.setLayout(row)
            bl.addWidget(iv_row)
            item_boxes[id(it)] = (cb, it)
            child_cbs.append(cb)

        # Folder header cascades to its children.
        def _make_cascade(children):
            def _cascade(state):
                for c in children:
                    c.setChecked(state == Qt.Checked)
            return _cascade
        folder_cb.stateChanged.connect(_make_cascade(child_cbs))

        iv.addWidget(box)

    iv.addStretch(1)
    scroll.setWidget(inner)
    outer.addWidget(scroll, 1)

    # Buttons
    btn_row = QHBoxLayout()
    keep_all = QPushButton("Keep everything")
    clear_unkept = QPushButton("Clear unchecked")
    cancel = QPushButton("Cancel")
    btn_row.addWidget(keep_all)
    btn_row.addStretch(1)
    btn_row.addWidget(cancel)
    btn_row.addWidget(clear_unkept)
    outer.addLayout(btn_row)

    result = {'action': None}

    def _do_clear():
        result['action'] = 'clear'
        dlg.accept()

    def _do_keep_all():
        for cb, _it in item_boxes.values():
            cb.setChecked(True)
        result['action'] = 'clear'  # clear the (now empty) unchecked set == keep all
        dlg.accept()

    def _do_cancel():
        result['action'] = 'cancel'
        dlg.reject()

    clear_unkept.clicked.connect(_do_clear)
    keep_all.clicked.connect(_do_keep_all)
    cancel.clicked.connect(_do_cancel)

    dlg.resize(640, 460)
    dlg.exec_()

    if result['action'] != 'clear':
        return None

    # Everything left UNCHECKED gets cleared.
    to_clear = [it for (cb, it) in item_boxes.values() if not cb.isChecked()]
    return to_clear
