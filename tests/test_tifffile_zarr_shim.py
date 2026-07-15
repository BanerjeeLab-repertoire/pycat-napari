r"""
Guard for the tifffile ↔ zarr 3.2 shim.

The shim (src/pycat/file_io/tifffile_zarr_shim.py) supplies ``RegularChunkGrid`` to
``zarr.core.chunk_grids`` so tifffile's zarr store imports on zarr 3.2, which is the path
``bioio-tifffile`` / ``bioio-ome-tiff`` / ``bioio-czi`` use for lazy/dask reads. Without it,
multi-channel TIFFs and all CZI files fail to load lazily with the misleading
``zarr 3.2.1 < 3 is not supported``.

This test proves the shim fixes the ACTUAL broken operation — ``tifffile.aszarr()`` end-to-end
(build store → open → read a chunk) — not merely that a symbol got set. It is CI-safe: it writes a
tiny multi-page TIFF to a tmp dir and needs no external data.

Skips cleanly if zarr/tifffile aren't the versions that need the shim (e.g. a future tifffile that
fixed the coupling, or an older zarr where the symbol never went missing).
"""

import os
import tempfile

import numpy as np
import pytest


def _zarr_needs_shim():
    """True iff this env is the one the shim targets: tifffile present, and
    zarr.core.chunk_grids missing RegularChunkGrid until the shim runs."""
    try:
        import tifffile  # noqa: F401
        import zarr.core.chunk_grids as cg
    except Exception:
        return False
    return not hasattr(cg, "RegularChunkGrid")


def test_shim_makes_aszarr_work_on_zarr_32():
    """With the shim installed, tifffile.aszarr() must build a store, open it, and read a chunk.
    This is the exact operation bioio uses for lazy TIFF/CZI reads."""
    try:
        import tifffile
        import zarr
    except Exception:
        pytest.skip("tifffile/zarr not installed")

    from pycat.file_io.tifffile_zarr_shim import install_tifffile_zarr_shim

    # Install (idempotent). If it can't supply the symbol AND the symbol isn't already present,
    # there's nothing to test on this env.
    installed = install_tifffile_zarr_shim()
    import zarr.core.chunk_grids as cg
    if not hasattr(cg, "RegularChunkGrid"):
        pytest.skip("shim could not supply RegularChunkGrid on this zarr; nothing to guard")
    assert installed is True

    # Exercise the real path that was broken.
    tmp = os.path.join(tempfile.gettempdir(), "_shim_guard.tif")
    try:
        tifffile.imwrite(tmp, np.arange(4 * 16 * 16, dtype=np.uint16).reshape(4, 16, 16))
        with tifffile.TiffFile(tmp) as t:
            store = t.aszarr()
            try:
                z = zarr.open(store, mode="r")
                arr = z[0] if hasattr(z, "__getitem__") else z
                sample = np.asarray(arr[0, :2, :2]) if arr.ndim == 3 else np.asarray(arr[:2, :2])
                assert sample.size > 0
                # First plane starts at 0,1,2,... — sanity that we read real data.
                assert int(np.asarray(arr[0]).ravel()[0]) == 0
            finally:
                store.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_shim_is_idempotent_and_safe_to_reimport():
    """Calling the installer repeatedly is a no-op after the first success, and never raises."""
    try:
        import zarr.core.chunk_grids  # noqa: F401
    except Exception:
        pytest.skip("zarr not installed")
    from pycat.file_io.tifffile_zarr_shim import install_tifffile_zarr_shim
    a = install_tifffile_zarr_shim()
    b = install_tifffile_zarr_shim()
    # Second call must agree with the first (both True if the symbol is/became available).
    assert a == b
