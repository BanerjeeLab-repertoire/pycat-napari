"""
**TIFF pixels do not go through BioIO, because BioIO's TIFF path is broken on zarr 3.2.**

``bioio-tifffile`` builds its lazy dask array by calling ``tif.aszarr()``, and ``tifffile``'s zarr
store does::

    from zarr.core.chunk_grids import RegularChunkGrid
    ...
    except ImportError as exc:
        raise ValueError(f'zarr {zarr.__version__} < 3 is not supported') from exc

**zarr 3.2 renamed that class.** The import fails, and the user sees::

    ValueError: zarr 3.2.1 < 3 is not supported

***That message is a lie.*** 3.2.1 is not less than 3. ``tifffile`` blames the version for **any**
ImportError out of its zarr-3 module, and the real failure — ``cannot import name
'RegularChunkGrid'`` — is one frame up, **where nobody looks.**

And PyCAT's own lazy-read fix is what walked into it
-----------------------------------------------------
The eager ``get_image_data()`` decoded the page directly and **never touched tifffile's zarr
store.** ``get_image_dask_data()`` goes straight through it.

***The old path worked precisely because it was doing the wrong thing.***

Why not pin zarr
----------------
Because **BioIO does not need to be the pixel transport for TIFF at all.** ``tifffile`` seeks a
single page directly — no zarr, no dask graph, no OME plane-map walk. It is **faster than the BioIO
path even when BioIO works**, which is why ``_TiffPageStack`` was written in the first place.

*Pinning would re-pin the stack the 1.6.0 migration existed to free — and it would be a guess:
nobody knows which zarr 3.x ``tifffile 2026.6.1`` was built against.*

**BioIO still supplies dimensions, scenes, channel names and pixel size for TIFF.** It is good at
that, and none of it goes near the zarr store.
"""

import os
import tempfile

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")


@pytest.mark.core
def test_a_plane_is_BIT_IDENTICAL_to_a_full_read():
    """**The floor.** A faster reader that returns different pixels is not a reader."""
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    try:
        truth = np.random.default_rng(0).integers(0, 4096, (32, 48), dtype=np.uint16)
        tifffile.imwrite(path, truth)

        plane = planes.read_tiff_plane(path, t=0, c=0, z=0)

        assert plane is not None, "the reader declined on a plain single-page TIFF"
        assert np.array_equal(plane, truth), "the pixels differ from a full tifffile read"
    finally:
        os.unlink(path)


@pytest.mark.core
def test_the_INTERLEAVED_page_order_picks_the_RIGHT_plane():
    """***A wrong page is worse than a slow one.***

    It would show the **wrong channel** or the **wrong timepoint** — and *nothing about the image
    would look broken.* Every reported number after that is quietly wrong.

    Each page here carries a unique value, so a mis-mapped page is unmissable.
    """
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    n_t, n_z, n_c = 4, 2, 3

    pages = []
    expected = {}
    for t in range(n_t):
        for z in range(n_z):
            for c in range(n_c):
                value = t * 100 + z * 10 + c
                pages.append(np.full((8, 8), value, np.uint16))
                expected[(t, z, c)] = value

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    try:
        tifffile.imwrite(path, np.stack(pages))

        wrong = []
        for (t, z, c), value in expected.items():
            plane = planes.read_tiff_plane(path, t=t, c=c, z=z, n_channels=n_c, n_z=n_z)
            if plane is None or plane[0, 0] != value:
                got = None if plane is None else int(plane[0, 0])
                wrong.append(f"t={t} z={z} c={c}: expected {value}, got {got}")

        assert not wrong, (
            "the page mapping is wrong:\n  " + "\n  ".join(wrong)
            + "\n\nThis would show the wrong channel or timepoint, and nothing would look broken."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_the_PLAIN_time_series_case_is_exact():
    """Single channel, single Z — the overwhelmingly common case, where ``page == t``."""
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    try:
        tifffile.imwrite(path, np.stack([np.full((8, 8), i, np.uint16) for i in range(10)]))

        for frame in range(10):
            plane = planes.read_tiff_plane(path, t=frame, n_channels=1, n_z=1)
            assert plane is not None and plane[0, 0] == frame, (
                f"frame {frame} returned {None if plane is None else plane[0, 0]}"
            )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_an_OUT_OF_RANGE_page_DECLINES_rather_than_returning_page_zero():
    """**Declining is the whole safety property.**

    If the interleaving assumption is wrong for some file, the computed page will be out of range
    — and ``None`` sends the caller to BioIO. **Returning page 0 instead would put the wrong
    frame on screen and say nothing.**
    """
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    try:
        tifffile.imwrite(path, np.stack([np.full((8, 8), i, np.uint16) for i in range(3)]))

        assert planes.read_tiff_plane(path, t=99, n_channels=1, n_z=1) is None, (
            "an out-of-range frame returned an array. It must decline — returning page 0 would "
            "show the wrong frame with nothing to indicate it."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_a_NON_TIFF_is_declined():
    """The reader is for TIFF. Anything else goes to BioIO, which is what it is for."""
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    assert planes.is_tiff("a.tif") and planes.is_tiff("a.tiff")
    assert not planes.is_tiff("a.czi")
    assert not planes.is_tiff("a.ims")

    assert planes.read_tiff_plane("nonexistent.czi") is None


@pytest.mark.core
def test_read_plane_ROUTES_tiff_away_from_bioio():
    """The wiring. **If ``read_plane`` does not check for TIFF first, none of the above matters.**"""
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "file_io"
              / "image_reader.py").read_text(encoding='utf-8', errors='ignore')

    tree = ast.parse(source)
    reader = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == 'read_plane'), None)
    assert reader is not None

    calls = [getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
             for node in ast.walk(reader) if isinstance(node, ast.Call)]

    assert 'read_tiff_plane' in calls, (
        "`read_plane` does not route TIFF to the native page reader. BioIO's TIFF path goes "
        "through tifffile's zarr store, which is broken on zarr 3.2."
    )
