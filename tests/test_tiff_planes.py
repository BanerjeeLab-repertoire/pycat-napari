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


@pytest.mark.core
def test_every_TiffPageStack_CONSTRUCTION_has_enough_arguments():
    """**The 1.6.4 "fix" for item 8 was broken, and compiled fine.**

    It called ``_TiffPageStack(file_path)`` — **one argument, where five are required.** That
    raised ``TypeError``, was caught by the surrounding ``except Exception``, and **fell straight
    through to the eager ``tifffile.imread`` anyway.**

    ***The fix was reported as done, the tests were green, and the eager read still happened every
    time.***

    A malformed call inside a ``try/except`` is **invisible** — it does not crash, it does not fail
    a test, it just silently takes the path it was supposed to avoid. So the arity is checked
    statically.
    """
    import ast
    import pathlib

    # The class lives in `lazy_sources.py` (extracted from `file_io.py` so the wrappers import
    # without Qt), but it is CONSTRUCTED elsewhere — so the definition and the call sites are
    # read from different files, and every file in the package is scanned for calls.
    file_io_dir = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "file_io")

    def _tree(path):
        return ast.parse(path.read_text(encoding='utf-8', errors='ignore'))

    definition = next((n for n in ast.walk(_tree(file_io_dir / "lazy_sources.py"))
                       if isinstance(n, ast.ClassDef) and n.name == '_TiffPageStack'), None)
    assert definition is not None, (
        "`_TiffPageStack` is not defined in `lazy_sources.py`. If it moved again, repoint this "
        "guard — a re-export is invisible to `ast.ClassDef`, so a stale path makes this test "
        "silently vacuous rather than red."
    )

    init = next(n for n in definition.body
                if isinstance(n, ast.FunctionDef) and n.name == '__init__')

    parameters = [a.arg for a in init.args.args[1:]]          # drop `self`
    n_required = len(parameters) - len(init.args.defaults)

    # The loader injects the class as `tiff_page_stack_cls` rather than importing it (the
    # decomposition's import-cycle dodge), so the real construction sites call it under THAT
    # name. Checking only the literal `_TiffPageStack(` would leave the guard with no calls to
    # check at all — the arity bug it exists to catch would sail straight through.
    _NAMES = {'_TiffPageStack', 'tiff_page_stack_cls'}

    malformed = []
    for path in sorted(file_io_dir.rglob("*.py")):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, 'id', None) not in _NAMES:
                continue

            supplied = len(node.args) + len(node.keywords)
            if supplied < n_required:
                malformed.append(
                    f"{path.name} line {node.lineno}: {supplied} argument(s), but {n_required} "
                    f"are required ({', '.join(parameters[:n_required])})")

    assert not malformed, (
        "these `_TiffPageStack(...)` calls will raise TypeError:\n  " + "\n  ".join(malformed)
        + "\n\n**Inside a try/except this is INVISIBLE** — it does not crash, it silently falls "
          "through to the eager read it was written to avoid. That is exactly what shipped in "
          "1.6.4."
    )


@pytest.mark.core
def test_a_MULTIFILE_OME_set_is_READ_and_not_DECLINED():
    """***A fallback that does not exist is not a fallback.***

    The first version of ``read_tiff_plane`` **declined** on a multi-file OME set, on the reasoning
    that *"the caller falls back to BioIO."*

    **But for TIFF, BioIO is exactly what is broken.** ``bioio-tifffile`` reads pixels through
    ``tif.aszarr()``, which is incompatible with zarr 3.2. So the decline **handed the file to a
    path that cannot work** — and Gable's 1200-frame MMStack came back as::

        read_plane failed: ValueError: zarr 3.2.1 < 3 is not supported

    *It was a dead end with a comment explaining why it was safe.*

    **tifffile resolves the multi-file set itself.** ``series`` walks the OME-XML, finds the
    companion files, and exposes **one page list spanning them** — and it handles *absent*
    companions too. Using the series' pages means multi-file comes for free.
    """
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "file_io"
              / "tiff_planes.py").read_text(encoding='utf-8', errors='ignore')

    assert 'handle.series' in source, (
        "`read_tiff_plane` does not use tifffile's `series`, which is what resolves a multi-file "
        "OME set. Without it the reader declines — and the BioIO 'fallback' cannot read TIFF."
    )

    # And it must not bail out on the multi-file case any more.
    tree = ast.parse(source)
    reader = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == 'read_tiff_plane'), None)
    assert reader is not None

    body = ast.get_source_segment(source, reader) or ''
    assert '_is_multifile_ome(handle)' not in body, (
        "`read_tiff_plane` still declines on a multi-file OME set. That hands the file to BioIO — "
        "**which cannot read TIFF pixels at all on zarr 3.2.** It is a dead end."
    )
