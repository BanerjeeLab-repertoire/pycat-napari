"""PyCAT file I/O.

── Why this file is not empty ───────────────────────────────────────────────────────────

``tifffile_zarr_shim`` existed with **zero production call sites**. It was recorded as *"dead code
that looks like a live workaround"* — the reverse turned out to be true: the workaround was never
installed, and the bug it fixes is live.

Reproduced on this tree (zarr 3.2.1, tifffile 2026.4.11), with no shim::

    from zarr.core.chunk_grids import RegularChunkGrid   -> ImportError
    tifffile ... .aszarr()                               -> ValueError: zarr 3.2.1 < 3 is not supported

That error is tifffile blaming the version for *any* ImportError out of its zarr-3 module; 3.2.1 is
obviously not < 3. The real cause is one symbol the zarr 3.2 restructure moved. The effect in PyCAT
is what the shim's own docstring says: **every read that falls to the BioIO dask path** —
multi-channel TIFFs (where ``_TiffPageStack`` declines on the page-count check) and **all CZI** (no
tifffile fast path at all) — **fails to load lazily.**

So it is installed here, at the package that owns those reads, because it must land **before**
``tifffile.zarr`` is first imported and every one of those paths goes through ``pycat.file_io``.

The call is safe to make at import: it is idempotent, it no-ops when the symbol is already present
(older zarr, or a future tifffile that does not need it), it swallows a missing zarr, and it
declines to install a broken stand-in — in which case tifffile's own error surfaces rather than
being masked. See ``tifffile_zarr_shim`` for the full reasoning, including why bumping tifffile is
not the fix (2026.5.2 drops numpy 2.0 and would drag ``numpy>=2.1`` through torch, cellpose,
scikit-image and cupy).
"""

from pycat.file_io.tifffile_zarr_shim import install_tifffile_zarr_shim as _install_tifffile_zarr_shim

#: True when ``zarr.core.chunk_grids.RegularChunkGrid`` is available after the attempt — either it
#: was already there, or the shim supplied it. False means tifffile's zarr store will fail on its
#: own terms, which is the honest outcome when the reconciliation is not possible.
TIFFFILE_ZARR_READY = _install_tifffile_zarr_shim()
