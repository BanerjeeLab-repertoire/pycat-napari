"""Per-file load helpers for the loader (file_io_decomposition).

`_add_image_or_mask_single` (is this an image or a mask? load it) and `_open_image_auto_single` (is this 2D or
a stack? route it) — the per-file workhorses the public `add_image_or_mask` / `open_image_auto` entry points
call once per selected file. Moved VERBATIM out of `file_io` as a mixin; `FileIOClass` inherits it, so every
`self._add_image_or_mask_single(...)` / `self._open_image_auto_single(...)` call site (in the public entries,
which stay in file_io as orchestration) is unchanged. No Qt — these route to `open_image` and delegate the
actual pixel/metadata work to the format openers and `open_2d_image`, whose fire order is untouched.
"""
from __future__ import annotations

import os

from pycat.file_io.image_reader import open_image, read_plane
from pycat.utils.general_utils import debug_log


class _LoadingMixin:
    def _add_image_or_mask_single(self, file_path, clear_first=False):
        """Route a SINGLE file to an Image or Labels layer, classifying
        image-vs-mask (signifier → pixel-stats → prompt). clear_first controls
        whether the session is cleared first: the menu "Add" path passes False
        (keep current); the drop path passes True for the first file so a drop
        starts a fresh session like Open does.
        (Extracted so add_image_or_mask can loop over a multi-file selection.)"""
        if not file_path:
            return

        # Probe storage once here (this router runs for menu-Add and for drops);
        # delegated open_image_auto calls below pass _skip_storage_probe so the
        # few-MB probe read happens only once. May redirect to a fast local copy.
        file_path = self._warn_if_slow_storage(file_path) or file_path

        # 1. PyCAT signifier — authoritative, no prompt.
        sig = self._read_pycat_signifier(file_path)
        if sig == 'mask':
            self.open_2d_mask(file_paths=[file_path], clear_first=clear_first)
            return
        if sig == 'image':
            self._open_image_auto_single(file_path, clear_first=clear_first,
                                         _skip_storage_probe=True)
            return

        # 2/3. No signifier — classify by pixel stats for a default, and decide
        # whether we MUST ask (no imaging metadata at all).
        looks_like_mask = False
        try:
            import numpy as _np
            img = open_image(file_path)
            plane = read_plane(img, path=file_path, c=0, t=0, z=0)
            is_int = _np.issubdtype(plane.dtype, _np.integer)
            uniq = _np.unique(plane)
            n_unique = int(uniq.size)
            # A label mask has integer values that are (a) few and (b) look like
            # label IDs: contiguous from 0 (0,1,2,...,N) or binary. A grayscale
            # image — even integer-typed — has values scattered across its range,
            # so uniq won't be a contiguous 0..N run. Requiring the contiguous
            # pattern (not just "few values") avoids mis-tagging low-contrast
            # images as masks.
            if is_int and n_unique <= 256 and n_unique >= 1:
                mn = int(uniq.min()); mx = int(uniq.max())
                contiguous_from_zero = (mn == 0 and mx == n_unique - 1)
                binary = (n_unique <= 2 and mn == 0)
                if contiguous_from_zero or binary:
                    looks_like_mask = True
        except Exception as _e:
            debug_log("file_io: add_image_or_mask classification failed", _e)

        has_meta = self._file_has_imaging_metadata_safe(file_path)

        # Ask the user. When there's no imaging metadata AND no signifier we have
        # nothing to go on, so the prompt is essential; otherwise it's a
        # confirmation with the detected type pre-selected.
        as_mask = looks_like_mask
        try:
            from qtpy.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setWindowTitle("Add as image or mask?")
            if not has_meta:
                lead = (f"'{os.path.basename(file_path)}' has no imaging-structure "
                        "metadata and no PyCAT signifier, so PyCAT can't tell what "
                        "it is. Please choose:")
            else:
                guess = ("looks like a LABEL MASK" if looks_like_mask
                         else "looks like an IMAGE")
                lead = f"'{os.path.basename(file_path)}' {guess}. Load as:"
            box.setText(lead + "\n\nMask → Labels layer (colocalization / analysis).\n"
                               "Image → Image layer.")
            mask_btn = box.addButton("Mask (Labels)", QMessageBox.AcceptRole)
            img_btn = box.addButton("Image", QMessageBox.RejectRole)
            box.setDefaultButton(mask_btn if looks_like_mask else img_btn)
            box.exec_()
            as_mask = (box.clickedButton() is mask_btn)
        except Exception:
            pass

        if as_mask:
            self.open_2d_mask(file_paths=[file_path], clear_first=clear_first)
        else:
            self._open_image_auto_single(file_path, clear_first=clear_first,
                                         _skip_storage_probe=True)

    def _open_image_auto_single(self, file_path, clear_first=True,
                                _skip_storage_probe=False):
        """Route a SINGLE file to the correct loader by inspecting its structure.
        (Extracted so open_image_auto can loop over a multi-file selection.)"""
        if not file_path:
            return

        # Warn if this file is on slow storage / a cloud placeholder before the
        # potentially long load begins, and optionally copy it local (with a
        # progress bar) — returning the path to actually load from. Skipped when a
        # caller (e.g. _add_image_or_mask_single) has already probed/redirected.
        if not _skip_storage_probe:
            file_path = self._warn_if_slow_storage(file_path) or file_path

        ext = os.path.splitext(file_path)[1].lower()
        # IMS is always a stack format (T/C/Z), route directly.
        if ext == '.ims':
            self.open_stack(file_path=file_path, clear_first=clear_first)
            return

        n_t = n_z = n_c = n_p = 1
        parsed = False
        try:
            # ── Inspect ONCE, and CARRY the answer ──────────────────────────────
            #
            # This function reads ``.dims`` and ``.scenes`` to decide 2-D versus stack — and then
            # **used to throw all of it away.** ``open_stack`` and ``open_2d_image`` then opened
            # the file and worked it out again, each with its own subtly different rule.
            #
            # The 1.6.6 reader cache made the *re-opening* free. **It did not make the
            # re-inspection free** — on a CZI, ``.dims`` walks the subblock directory. *The cache
            # hid the design flaw rather than fixing it.*
            #
            # The structure is now stored on ``self`` and read by the loader that runs next. **One
            # inspection, one answer** — and nothing downstream can disagree with it, which would
            # be its own kind of bug and a very hard one to see.
            from pycat.file_io.image_structure import inspect_image

            img = open_image(file_path)
            _structure = inspect_image(img, file_path)
            self._pending_structure = _structure

            n_t = _structure.n_t
            n_z = _structure.n_z
            n_c = _structure.n_c
            n_p = _structure.n_scenes
            parsed = _structure.parsed
            print(f"[PyCAT open-auto] {os.path.basename(file_path)}: "
                  f"P={n_p} T={n_t} C={n_c} Z={n_z} → "
                  f"{'stack' if _structure.is_stack else '2D'}")
        except Exception as _e:
            debug_log("file_io: open_image_auto structure parse failed; "
                      "falling back to 2D loader", _e)

        # Multi-position (P>1) or any real Z/T axis → stack loader.
        if parsed and (n_t > 1 or n_z > 1 or n_p > 1):
            self.open_stack(file_path=file_path, clear_first=clear_first)
            return

        # Undeclared multipage TIFF: a TIFF whose metadata declares no T/Z/P axis
        # but which nonetheless has multiple pages (a plain writer / non-ImageJ
        # "save as TIFF" leaves the stack axis unlabelled — tifffile calls it 'Q').
        # Most microscopy platforms (Andor, Zeiss, Leica, saved-from-.h5) can emit
        # such split/stacked TIFFs, so this is common, not a FRAP quirk. The old
        # behaviour mis-routed these to the 2D loader → "loaded as individual
        # images". Now: detect the case and ASK whether it's a time-series,
        # z-stack, or genuinely separate images (with a remember-choice option).
        if ext in ('.tif', '.tiff'):
            n_pages, undeclared = self._tiff_multipage_undeclared(file_path)
            if undeclared and n_pages > 1:
                choice = self._ask_multipage_axis(file_path, n_pages)
                if choice in ('T', 'Z'):
                    # Both T and Z are 3D and load the same way; the label is
                    # recorded so downstream steps can warn if an unknown/assumed
                    # axis is used in an axis-dependent operation.
                    self.central_manager.active_data_class.data_repository['stack_axis_label'] = choice
                    self.central_manager.active_data_class.data_repository['stack_axis_assumed'] = True
                    self.open_stack(file_path=file_path, clear_first=clear_first)
                    return
                elif choice == 'separate':
                    self.open_2d_image(file_paths=[file_path],
                                       clear_first=clear_first)
                    return
                # choice is None (dialog failed) → fall through to 2D as before.

        self.open_2d_image(file_paths=[file_path], clear_first=clear_first)
