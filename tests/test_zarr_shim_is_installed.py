"""**The shim was written, tested, and never installed.**

`tifffile_zarr_shim` had **zero production call sites** — only its own definition and its unit
test. The 2026-07-16 audit recorded it as *"dead code that looks like a live workaround"* and
suggested deleting it. The reverse is true: the workaround was never wired, and the bug it fixes
is live on this tree.

Reproduced with the shim uninstalled (zarr 3.2.1, tifffile 2026.4.11):

    from zarr.core.chunk_grids import RegularChunkGrid   -> ImportError
    tifffile ... .aszarr()                               -> ValueError: zarr 3.2.1 < 3 is not supported

The error is tifffile blaming the version for *any* ImportError out of its zarr-3 module — 3.2.1 is
not < 3. One symbol moved in the zarr 3.2 restructure. The effect, per the shim's own docstring:
every read that falls to the BioIO dask path — **multi-channel TIFFs and all CZI** — fails to load
lazily.

`tests/test_tifffile_zarr_shim.py` already proves the shim *works* when called. This proves it is
*called*. A fix nobody installs is not a fix, and the distance between those two tests is where
this lived.
"""

# Standard library imports
import importlib

# Third party imports
import numpy as np
import pytest

pytestmark = pytest.mark.core


def test_importing_file_io_INSTALLS_the_shim():
    """The wiring itself. `pycat.file_io` owns the reads that need it, and it must land before
    `tifffile.zarr` is first imported — so the package import is the install point."""
    import pycat.file_io as fio

    assert hasattr(fio, 'TIFFFILE_ZARR_READY'), (
        'pycat.file_io no longer reports whether the tifffile/zarr reconciliation succeeded'
    )


def test_the_symbol_tifffile_LOOKS_FOR_is_present_after_that_import():
    """The specific thing tifffile does at `tifffile/zarr.py` import time:
    `from zarr.core.chunk_grids import RegularChunkGrid`."""
    pytest.importorskip('zarr')
    import pycat.file_io  # noqa: F401  — the import IS the action under test

    import zarr.core.chunk_grids as cg
    assert hasattr(cg, 'RegularChunkGrid'), (
        'RegularChunkGrid is still missing after importing pycat.file_io — every BioIO dask read '
        '(multi-channel TIFF, all CZI) will fail with the misleading "zarr < 3 is not supported"'
    )


def test_a_lazy_read_ACTUALLY_WORKS_after_importing_file_io(tmp_path):
    """End-to-end, on a real file: aszarr -> open -> read a chunk.

    This is the assertion that would have caught the miss. The unit test proved the shim repairs
    things *when called*; nothing proved anything called it.
    """
    tifffile = pytest.importorskip('tifffile')
    zarr = pytest.importorskip('zarr')
    import pycat.file_io  # noqa: F401

    path = tmp_path / 'lazy.tif'
    data = np.arange(4 * 32 * 32, dtype=np.uint16).reshape(4, 32, 32)
    tifffile.imwrite(str(path), data)

    with tifffile.TiffFile(str(path)) as handle:
        store = handle.series[0].aszarr()
        arr = zarr.open(store, mode='r')
        chunk = np.asarray(arr[1])

    assert np.array_equal(chunk, data[1]), 'the lazy read returned the wrong plane'


def test_the_shim_is_IDEMPOTENT_so_the_package_import_cannot_break_on_reload():
    """`pycat.file_io` runs it at import; anything else may call it again. Installing twice must be
    a no-op, or a reload would be a landmine."""
    from pycat.file_io.tifffile_zarr_shim import install_tifffile_zarr_shim

    assert install_tifffile_zarr_shim() == install_tifffile_zarr_shim()


def test_the_shim_module_still_has_a_HOME_and_is_not_orphaned_again():
    """The failure mode this file exists to prevent: the shim drifting back out of the import path
    and nobody noticing, because everything still imports fine and only *lazy reads* break."""
    import inspect
    import pycat.file_io as fio

    src = inspect.getsource(fio)
    assert 'install_tifffile_zarr_shim' in src, (
        'pycat.file_io no longer installs the shim — multi-channel TIFF and CZI lazy reads are '
        'broken again, silently, because the import still succeeds'
    )
