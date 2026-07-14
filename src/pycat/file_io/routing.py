"""
**What IS this file? Ask before opening it as an image.**

Four questions the loaders ask *before* they commit to a route:

* **Does it carry real imaging metadata**, or is it a plain picture? (image-vs-mask routing)
* **Did PyCAT write it?** (the embedded signifier — a saved mask must come back as a mask)
* **Does it carry an embedded tag store?** (a saved user tag must outlive the file)
* **Is it an undeclared multipage TIFF?** (no axis metadata, several pages — *the user has to be
  asked whether those pages are T or Z, and **T and Z load identically**, so nothing downstream can
  discover the answer for itself*)

── Why these four, and why now ──────────────────────────────────────────────────────────

**They were methods that never touched `self`.** Four functions taking ``(self, file_path)``,
using the ``self`` for nothing at all — *static functions wearing method clothes*, wedged inside a
3,108-line class between the loaders, the dialogs and the lazy wrappers.

Nothing about them was ever specific to a ``FileIOClass`` instance, and nothing about them needs a
viewer, a data repository, or a reader. They answer a question about a **path**.

*The rule for this split: take what depends on nothing, first. Each move is then provably safe, and
what is left behind is smaller and no more tangled than it was.*
"""

from __future__ import annotations

import os


def _file_has_imaging_metadata_safe(file_path):
    """Best-effort check for whether a file carries real imaging-structure
    metadata (pixel size, channels, dimensional axes). Used ONLY to choose the
    wording of the image-vs-mask prompt, so it must never raise — any failure
    returns True (softer 'looks like X, confirm' wording) rather than crashing
    the load. (Replaces an earlier call to a method that was never defined,
    which crashed every menu-Add / drop of a non-signifier file.)"""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        # Formats that inherently carry structured imaging metadata.
        if ext in ('.ims', '.czi'):
            return True
        if ext in ('.tif', '.tiff'):
            try:
                import tifffile
                with tifffile.TiffFile(file_path) as tf:
                    # OME-XML, ImageJ metadata, or a resolution tag all count
                    # as real imaging metadata.
                    if getattr(tf, 'is_ome', False) or getattr(tf, 'is_imagej', False):
                        return True
                    p0 = tf.pages[0]
                    for tag in ('XResolution', 'YResolution', 'ImageDescription'):
                        try:
                            if tag in p0.tags:
                                return True
                        except Exception:
                            pass
                return False
            except Exception:
                return True  # can't tell -> assume metadata (softer prompt)
        # PNG/JPG typically carry no imaging metadata.
        if ext in ('.png', '.jpg', '.jpeg'):
            return False
        return True
    except Exception:
        return True

def _read_pycat_signifier(file_path):
    """Read PyCAT's saved-file signifier from a TIFF's ImageDescription, if
    present. Returns 'image' / 'mask' / None. Lets a file PyCAT itself saved
    be re-loaded with its type known exactly, without guessing."""
    try:
        import tifffile, json as _json
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ('.tif', '.tiff'):
            return None
        with tifffile.TiffFile(file_path) as tf:
            desc = None
            try:
                desc = tf.pages[0].tags['ImageDescription'].value
            except Exception:
                desc = getattr(tf, 'imagej_metadata', None)
            if not desc:
                return None
            if isinstance(desc, bytes):
                desc = desc.decode('utf-8', 'ignore')
            # The description may be OME-XML or our JSON; only parse JSON.
            desc = desc.strip()
            if not desc.startswith('{'):
                return None
            tag = _json.loads(desc)
            if isinstance(tag, dict) and tag.get('pycat'):
                k = tag.get('kind')
                if k in ('image', 'mask'):
                    return k
    except Exception:
        pass
    return None

def _read_pycat_tags(file_path):
    """Read PyCAT's embedded tag store ({'tags':[...],'edges':[...]}) from a
    saved TIFF's ImageDescription, if present. Returns the dict or None. This
    is how layer tags (role/modality/lineage/etc.) survive save→reload —
    they ride in the same JSON blob as the image/mask signifier."""
    try:
        import tifffile, json as _json
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in ('.tif', '.tiff'):
            return None
        with tifffile.TiffFile(file_path) as tf:
            try:
                desc = tf.pages[0].tags['ImageDescription'].value
            except Exception:
                desc = getattr(tf, 'imagej_metadata', None)
            if not desc:
                return None
            if isinstance(desc, bytes):
                desc = desc.decode('utf-8', 'ignore')
            desc = desc.strip()
            if not desc.startswith('{'):
                return None
            tag = _json.loads(desc)
            if isinstance(tag, dict) and tag.get('pycat'):
                ts = tag.get('pycat_tags')
                if isinstance(ts, dict):
                    return ts
    except Exception:
        pass
    return None

def _tiff_multipage_undeclared(file_path):
    """Return (n_pages, is_undeclared) for a TIFF: n_pages is the page count of
    the first series; is_undeclared is True when the file carries no ImageJ/OME
    axis metadata AND the series' leading axis is unlabelled ('Q'), i.e. a
    plain multipage TIFF whose stack axis type is unknown. Safe: any failure
    returns (1, False) so the caller falls back to the normal 2D path."""
    try:
        import tifffile
        with tifffile.TiffFile(file_path) as t:
            if t.is_imagej or t.is_ome:
                return (len(t.pages), False)  # metadata present → not our case
            series = t.series[0]
            axes = getattr(series, 'axes', '') or ''
            n_pages = len(t.pages)
            # Undeclared when the leading (non-YX) axis is 'Q' (unknown) or the
            # shape has >1 in a leading position with no T/Z label.
            lead = axes[:-2] if len(axes) >= 2 else axes
            undeclared = n_pages > 1 and (('T' not in lead) and ('Z' not in lead))
            return (n_pages, undeclared)
    except Exception:
        return (1, False)
