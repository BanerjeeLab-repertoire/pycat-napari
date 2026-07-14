"""
`ImageSource` — explicit ownership of the readers a set of lazy layers depends on.

THE PROBLEM IT SOLVES
---------------------
When PyCAT lazy-loads an IMS file, each napari layer is backed by a wrapper (`_ImsReaderTYX`
et al.) that reads frames on demand from an `imaris_ims_file_reader` reader. That reader owns
an open HDF5 handle, and `imaris_ims_file_reader` closes the handle when the reader object is
garbage-collected. **A wrapper holding `self._reader = reader` is NOT enough** to keep the file
open — proven on 2026-07-14 (docs/audits/ims_zarr_refs_resolved_2026-07-14.md): dropping the
external reference and forcing GC made a frame read raise `OSError: Can't ... read data`.

Today `FileIOClass` keeps readers alive *by accident*: it stashes the primary reader on
`self._ims_reader` and every sibling-position reader on `self._ims_zarr_refs`, and `self` (the
FileIOClass) lives for the whole session. That works, but the ownership is implicit and smeared
across the instance dict — which is exactly why the four remaining big loaders can't be
extracted from file_io.py (external audit #9 / handoff §3.2).

WHAT THIS IS
------------
A small, explicit container that owns the readers a group of layers needs, with a lifetime that
**must be tied to the layers** — not to the load call. Anything that outlives the layers (e.g.
the session-scoped FileIOClass, or an attribute on the layer/central-manager that lives as long
as the layers do) can own it. Anything load-scoped MUST NOT.

WHAT THIS IS NOT (yet)
----------------------
This is deliberately minimal and **additive**. Nothing imports or uses it yet. It is the object
`_open_stack_ims` will hand its readers to once adoption is wired and the retention guard
(`tests/test_ims_reader_retention.py`) confirms the transfer kept every sibling reader alive.
It is intentionally NOT a general "loaded image" model — it owns *resources*, nothing more.
Metadata, channel info, pixel size, etc. stay where they already live; conflating them here
would recreate the god-object this is meant to dissolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImageSource:
    """Owns the reader objects a set of lazy layers reads from.

    The *only* invariant this guarantees: as long as this object is alive, every reader it holds
    is alive, and therefore every HDF5 (or other) file handle those readers own stays open. The
    caller is responsible for keeping this object alive **at least as long as the layers** built
    from these readers.

    Attributes
    ----------
    file_path : str
        The originally-opened file (the primary position for a multi-position IMS). Preserved
        because it is read externally — e.g. timeseries_condensate_tools looks it up to locate
        the on-disk source. This is the replacement home for the old ``_ims_file_path``.
    readers : list
        Every reader whose lifetime must be pinned: the primary reader AND each sibling-position
        reader. For a single-position file this is length 1; for the multi-position case it holds
        one entry per opened position. This replaces the retention role of ``_ims_reader`` +
        ``_ims_zarr_refs``. Order is load order (primary first).
    """

    file_path: str = ""
    readers: list[Any] = field(default_factory=list)

    def retain(self, reader: Any) -> Any:
        """Pin ``reader``'s lifetime to this ImageSource and return it unchanged.

        Deduplicates by identity so the primary reader (which is also a sibling entry when
        ``pos_path == file_path``) is not held twice. Returns the reader so call sites can write
        ``pos_reader = source.retain(ImsReader(pos_path))`` inline.
        """
        if not any(r is reader for r in self.readers):
            self.readers.append(reader)
        return reader

    def close(self) -> None:
        """Explicitly drop all retained readers.

        Call ONLY when the layers built from these readers are gone. After this, any lazy wrapper
        still pointing at one of these readers will raise on the next frame read once the reader
        is collected — which is correct: the source is closed. Idempotent.

        Note: we drop references rather than calling a reader ``.close()`` because
        ``imaris_ims_file_reader`` manages its handle on collection; releasing the reference is
        the supported way to let it close. If a future reader type needs an explicit close, add it
        here behind a ``hasattr(r, 'close')`` check.
        """
        self.readers.clear()

    def __len__(self) -> int:
        return len(self.readers)
