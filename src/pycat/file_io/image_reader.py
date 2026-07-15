"""
**One seam between PyCAT and whichever imaging library reads the file.**

``aicsimageio`` is in **maintenance mode**. Its maintainers name ``bioio`` as the *"compatible
successor"* — and the compatibility is not a claim, it is the design: ``BioImage`` exposes the same
names, the same semantics, and the same **TCZYX** ordering as ``AICSImage``.

What the audit found
--------------------
The migration is **far smaller than the 3,874-line ``file_io.py`` suggests**, and the reasons matter
more than the number:

**1. The API surface PyCAT actually uses is FIFTEEN attributes** — and BioIO matches every one::

    .data  .dims  .shape  .dtype  .metadata  .scenes  .set_scene  .current_scene
    .physical_pixel_sizes  .get_image_data  .get_image_dask_data  .xarray_dask_data

*(``.max``, ``.astype`` and ``.ndim`` are on the returned numpy array, not on the reader.)*

**2. The lazy layer is ALREADY decoupled — and this is the single most important finding.**
``multidim_io``'s ``_ZarrTZYX_generic`` wraps a **plain zarr array**, not an ``AICSImage``. The
reader is used only to *write* that zarr. **So the lazy path — the one carrying the ``__array__``
frame-zero landmine that has already cost this project two bugs — is untouched by the swap.**

**3. Only ONE format genuinely needs it.** ``.ims`` has its own HDF5 reader;
``.tif`` is read by ``tifffile`` on the fast path and only falls back here for scenes and metadata;
video goes through ``cv2``. **``.czi`` is the one format that truly requires the library.**

**4. The reader never escapes into the analysis code.** It is a local variable in five functions,
and it reaches exactly **three** consumers — all of them metadata extractors.

Why a seam rather than a find-and-replace
------------------------------------------
A find-and-replace would work, and it would be **irreversible in one step.** This project has
already been bitten twice by a change that looked safe and could not be A/B-ed:

* the **rolling-ball normalisation** that made batch disagree with the recording
* the **frame-zero collapse** that told users their movie was a still image

**Both were invisible until someone compared two runs.** So the swap ships as a **switch**, and both
libraries stay installable side-by-side until the BioIO path has read every format on real data::

    PYCAT_IMAGE_READER=bioio      run-pycat        # the new path
    PYCAT_IMAGE_READER=aicsimageio run-pycat       # the old one, unchanged

``compare_readers()`` opens the **same file with both** and reports every difference in shape,
dtype, dimension order, pixel size, and **pixel content.** *That is the acceptance test, and it runs
on Gable's own data rather than on a synthetic file that proves nothing.*
"""

from __future__ import annotations

import os


# ── Which library? ───────────────────────────────────────────────────────────────────────
#
# **Default: bioio.** The switch was made in **1.6.0**, and it was made **with evidence in hand.**
#
# BioIO and aicsimageio were run against **38 real files** in separate environments — they cannot
# coexist, because aicsimageio is frozen in 2023 and pins ``zarr<2.16``, ``tifffile<2023.3``,
# ``fsspec<2023.9``, ``lxml<5`` — and the results were compared offline:
#
#     **identical  31**   including the Zeiss CZI, ``3.30 hr_1_MMStack_Pos0``, every OME-TIFF,
#                         every in-vitro TIFF, and every batch output
#     **different   0**   shape, dtype, **dimension order**, **pixel size**, scenes, **and the
#                         SHA-256 of the pixels**
#     not comparable 6    all ``.ims`` — and **neither library reads them**
#
# The ``.ims`` result is **not a gap**: PyCAT intercepts ``.ims`` and routes it to
# ``imaris_ims_file_reader``, its own HDF5 reader. *The comparison tested a path PyCAT does not
# take.*
#
# ``PYCAT_IMAGE_READER=aicsimageio`` still selects the old path **if it is installed** — but it is
# no longer a dependency, and installing it would break the modern stack it forbids.
_DEFAULT_BACKEND = 'bioio'

_BACKEND = os.environ.get('PYCAT_IMAGE_READER', _DEFAULT_BACKEND).strip().lower()


class ImageReaderUnavailable(ImportError):
    """Neither library is installed, or the requested one is not."""


def backend_name() -> str:
    """Which library will ``open_image`` actually use."""
    return _BACKEND


def available_backends() -> dict:
    """**What is actually importable — not what the requirements file claims.**

    A backend that is declared and missing is worse than one that is absent: the code takes the
    path, and fails at the point of use rather than at startup.
    """
    found = {}

    try:
        import aicsimageio  # noqa: F401
        found['aicsimageio'] = getattr(aicsimageio, '__version__', 'unknown')
    except ImportError:
        pass

    try:
        import bioio  # noqa: F401
        found['bioio'] = getattr(bioio, '__version__', 'unknown')
    except ImportError:
        pass

    return found


# ── One reader per file, not four ────────────────────────────────────────────────────────
#
# A single drag-and-drop used to construct the reader **three to four times** before one pixel
# reached the screen::
#
#     _add_image_or_mask_single   -> open_image()    "is this an image or a mask?"
#     _open_image_auto_single     -> open_image()    "is this 2D or a stack?"
#       -> _open_stack_generic    -> open_image()
#          OR open_2d_image       -> open_image() x3  (probe, fallback check, reload)
#
# **Reader construction is not free.** Depending on the plugin it parses OME-XML, walks the TIFF
# series, reads the **CZI subblock directory**, and enumerates scenes — *every time*. For a large
# CZI that is the same expensive directory walk, four times over, before anything is displayed.
#
# So the seam caches. **Keyed on the resolved path AND the file's mtime+size**, because a reader
# holds an open handle and a stale one after the file changes would be worse than slow — it would
# be wrong.
#
# **Deliberately small (4).** This is a "the same file, several times, within one load" cache, not
# a session cache: holding readers for every file a user has ever opened would pin file handles and
# memory for no benefit.
_READER_CACHE = {}
_READER_CACHE_LIMIT = 4


def _cache_key(path):
    """Path, size and mtime. **A reader for a file that has changed is worse than no cache.**"""
    import os
    try:
        resolved = os.path.realpath(str(path))
        stat = os.stat(resolved)
        return (resolved, stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


def clear_reader_cache():
    """Drop every cached reader. Called when the viewer is cleared, and available for tests."""
    _READER_CACHE.clear()


def _reader_kwargs_for(path, kwargs):
    """**Tell BioIO which TIFF reader to use. Do not let it guess.**

    ── The bug a user reported (1.6.17) ──────────────────────────────────────────────────

    Opening an ordinary microscope TIFF printed, twice, in red::

        Attempted file (In Cell 8-DAPI.tif) load with reader:
        <class 'bioio_ome_tiff.reader.Reader'> failed with error:
        bioio-ome-tiff does not support the image ... Failed to parse XML for the
        provided file. Error: syntax error: line 1, column 0

    **And then the file opened fine.** ``P=1 T=1 C=1 Z=1 → 2D``.

    ``BioImage(path)`` with no reader runs BioIO's **plugin auto-selection**: it tries
    ``bioio-ome-tiff`` first, that plugin looks for OME-XML, a plain TIFF has none, and it raises.
    BioIO catches it, prints the attempt, and falls through to ``bioio-tifffile``, which works.

    *The error is BioIO's, it is not fatal, and the load succeeds — but the user has no way to know
    any of that.* **It reads exactly like a corrupt file, and it names their own image.** A
    scientist seeing that goes looking at their microscope, or at their data. That is the same cost
    as the ``'_TIFF' object has no attribute 'RESUNIT'`` message this codebase already has a
    startup check for.

    ── Why bioio-tifffile is the right reader for BOTH ───────────────────────────────────

    It wraps ``tifffile``, which reads **plain and OME TIFF alike**. And PyCAT does not take TIFF
    pixels from BioIO at all — ``tiff_planes.read_tiff_plane`` seeks the page directly, precisely
    because ``bioio-ome-tiff`` reads through ``tif.aszarr()``, which is broken on zarr 3.2. **BioIO
    is only supplying dimensions, scenes, channel names and pixel size for TIFF**, and
    ``bioio-tifffile`` supplies all of them.

    *So the OME plugin was never on the pixel path. It was only ever a noisy first guess.*

    **A caller passing its own ``reader=`` wins** — an explicit request is not overridden.
    """
    if kwargs.get('reader') is not None:
        return kwargs

    from pathlib import Path
    if Path(str(path)).suffix.lower() not in ('.tif', '.tiff'):
        return kwargs

    # BioIO's own error names the OME plugin's class as ``bioio_ome_tiff.reader.Reader``, so the
    # sibling is ``bioio_tifffile.reader.Reader``. Try the package's top-level export too — a
    # plugin is free to re-export, and a hard-coded submodule path that moves in a point release
    # would silently put us back on the noisy auto-probe.
    reader_class = None
    for module_name, attribute in (('bioio_tifffile.reader', 'Reader'),
                                   ('bioio_tifffile', 'Reader')):
        try:
            import importlib
            reader_class = getattr(importlib.import_module(module_name), attribute, None)
        except ImportError:
            continue
        if reader_class is not None:
            break

    if reader_class is None:
        # The plugin is a declared dependency, but if it is genuinely absent or has moved, let
        # BioIO probe as before rather than fail. **A noisy load beats no load.**
        return kwargs

    return {**kwargs, 'reader': reader_class}


def open_image(path, **kwargs):
    """Open ``path`` and return a reader.

    **The returned object has the same interface either way** — that is the entire point of the
    seam. Every caller in PyCAT touches only the fifteen attributes both libraries share, so
    nothing downstream needs to know which one it got.

    BioIO's reader plugins are installed **separately** (``bioio-czi``, ``bioio-ome-tiff``,
    ``bioio-tifffile``, …). That is a deliberate improvement — it is why a user who only opens TIFFs
    no longer has to carry a CZI dependency — but it also means **a missing plugin is a missing
    format**, and the error must say so plainly rather than surfacing as "cannot read file".
    """
    # ── Cache: the same file is opened three to four times per drag-and-drop ──
    #
    # Keyed on path + size + mtime, so a file that CHANGED gets a fresh reader. **A stale reader
    # would be worse than a slow one** — it would be wrong.
    #
    # `kwargs` bypasses the cache: a caller passing options wants a reader built THEIR way, and
    # silently handing them a differently-configured one is exactly the kind of quiet wrongness
    # this project keeps finding.
    _key = _cache_key(path) if not kwargs else None
    if _key is not None and _key in _READER_CACHE:
        _cached = _READER_CACHE[_key]

        # ── A SHARED reader is STATEFUL, and that is a correctness bug ────────────
        #
        # ``set_scene()`` **mutates the reader.** With a cache, two call sites hold the *same
        # object* — so a site that moves to scene 2 leaves the next caller's reader **parked on
        # scene 2.**
        #
        # That caller reads **the wrong field of view**, and ***nothing about the image looks
        # broken.*** For a multi-position CZI that is a silently wrong analysis.
        #
        # *This was introduced by the cache in 1.6.6, and it is exactly the class of quiet
        # wrongness this project keeps finding.*
        #
        # So a cached reader is **returned to its first scene** before it is handed out. Callers
        # that want a different scene say so — ``read_plane(scene=...)``, ``set_scene(...)`` — and
        # the next caller still starts from a known state.
        try:
            _scenes = getattr(_cached, 'scenes', None)
            if _scenes:
                _first = _scenes[0]
                if getattr(_cached, 'current_scene', _first) != _first:
                    _cached.set_scene(_first)
        except Exception:
            # A reader that cannot be rewound is a reader that cannot be shared. **Drop it and
            # build a fresh one** rather than hand out an object in an unknown state.
            _READER_CACHE.pop(_key, None)
            _cached = None

        if _cached is not None:
            return _cached

    if _BACKEND == 'bioio':
        try:
            from bioio import BioImage
        except ImportError as exc:
            raise ImageReaderUnavailable(
                "PYCAT_IMAGE_READER=bioio, but bioio is not installed.\n"
                "  pip install bioio bioio-ome-tiff bioio-tifffile bioio-czi\n"
                "Unset PYCAT_IMAGE_READER to use aicsimageio."
            ) from exc

        try:
            _reader = BioImage(path, **_reader_kwargs_for(path, kwargs))
            if _key is not None:
                if len(_READER_CACHE) >= _READER_CACHE_LIMIT:
                    _READER_CACHE.pop(next(iter(_READER_CACHE)))
                _READER_CACHE[_key] = _reader
            return _reader
        except Exception as exc:
            # BioIO's readers are separate packages. "No reader found" means a MISSING PLUGIN,
            # not a broken file — and a user staring at "cannot read" would go looking in the
            # wrong place entirely.
            message = str(exc).lower()
            if 'reader' in message or 'plugin' in message or 'no support' in message:
                from pathlib import Path
                suffix = Path(str(path)).suffix.lower()
                plugin = {
                    '.czi': 'bioio-czi',
                    '.tif': 'bioio-tifffile',
                    '.tiff': 'bioio-ome-tiff',
                    '.nd2': 'bioio-nd2',
                    '.lif': 'bioio-lif',
                    '.png': 'bioio-imageio',
                    '.jpg': 'bioio-imageio',
                    '.jpeg': 'bioio-imageio',
                    '.bmp': 'bioio-imageio',
                }.get(suffix)
                raise ImageReaderUnavailable(
                    f"bioio has no reader installed for {suffix} files."
                    + (f"\n  pip install {plugin}" if plugin else "")
                    + "\nThis is a MISSING PLUGIN, not a corrupt file."
                ) from exc
            raise

    try:
        from aicsimageio import AICSImage
    except ImportError as exc:
        raise ImageReaderUnavailable(
            "aicsimageio is not installed.\n"
            "  pip install aicsimageio\n"
            "Or set PYCAT_IMAGE_READER=bioio to use the successor library."
        ) from exc

    _reader = AICSImage(path, **kwargs)
    if _key is not None:
        if len(_READER_CACHE) >= _READER_CACHE_LIMIT:
            _READER_CACHE.pop(next(iter(_READER_CACHE)))
        _READER_CACHE[_key] = _reader
    return _reader


def compare_readers(path, verbose=True) -> dict:
    """**Open the same file with BOTH libraries and report every difference.**

    This is the acceptance test, and it is deliberately not a unit test on a synthetic file.

    *A synthetic TIFF proves that both libraries can read a synthetic TIFF.* It says nothing about a
    Zeiss CZI with three scenes, or a Micro-Manager OME-TIFF whose frame interval lives in a
    non-standard tag, or the astigmatic bead movie that has already exposed two loader bugs in this
    project. **The comparison has to run on the real files.**

    Returns a dict of differences. An empty ``'differences'`` list is the thing to look for.
    """
    have = available_backends()
    if len(have) < 2:
        return {'error': f"need BOTH libraries installed; found {list(have)}",
                'available': have}

    from aicsimageio import AICSImage
    from bioio import BioImage

    old = AICSImage(path)
    new = BioImage(path)

    differences = []
    report = {'path': str(path), 'versions': have}

    # ── Shape, dtype, dimension order ────────────────────────────────────────
    #
    # A dimension-order difference is the one that would corrupt everything downstream in silence:
    # PyCAT indexes TCZYX by position in several places, so a reader that returned CTZYX would not
    # crash — it would return the wrong channel.
    for attribute in ('shape', 'dtype'):
        old_value = getattr(old, attribute, None)
        new_value = getattr(new, attribute, None)
        report[attribute] = {'aicsimageio': str(old_value), 'bioio': str(new_value)}
        if str(old_value) != str(new_value):
            differences.append(f"{attribute}: {old_value} vs {new_value}")

    old_order = getattr(getattr(old, 'dims', None), 'order', None)
    new_order = getattr(getattr(new, 'dims', None), 'order', None)
    report['dims.order'] = {'aicsimageio': old_order, 'bioio': new_order}
    if old_order != new_order:
        differences.append(
            f"*** DIMENSION ORDER: {old_order} vs {new_order} — this would silently return the "
            f"wrong channel, not raise ***")

    # ── Physical pixel size ──────────────────────────────────────────────────
    #
    # PyCAT already knows how much this one costs. `microns_per_pixel_sq` defaulting to 1 is a
    # SENTINEL, and a reader that reports a pixel size where the other does not (or vice versa)
    # changes every length, area and diffusion coefficient the program computes.
    old_pixel = getattr(old, 'physical_pixel_sizes', None)
    new_pixel = getattr(new, 'physical_pixel_sizes', None)
    report['physical_pixel_sizes'] = {'aicsimageio': str(old_pixel), 'bioio': str(new_pixel)}
    if str(old_pixel) != str(new_pixel):
        differences.append(
            f"*** PIXEL SIZE: {old_pixel} vs {new_pixel} — every length, area and diffusion "
            f"coefficient depends on this ***")

    # ── Scenes ───────────────────────────────────────────────────────────────
    old_scenes = list(getattr(old, 'scenes', []) or [])
    new_scenes = list(getattr(new, 'scenes', []) or [])
    report['scenes'] = {'aicsimageio': old_scenes, 'bioio': new_scenes}
    if old_scenes != new_scenes:
        differences.append(f"scenes: {old_scenes} vs {new_scenes}")

    # ── THE PIXELS ───────────────────────────────────────────────────────────
    #
    # Everything above could match while the actual data differs — a byte-order bug, an off-by-one
    # in a chunk boundary, a scene selected differently. **The only claim worth making is that the
    # pixels are identical.**
    try:
        import numpy as np

        old_data = np.asarray(old.get_image_data("ZYX", C=0, T=0))
        new_data = np.asarray(new.get_image_data("ZYX", C=0, T=0))

        if old_data.shape != new_data.shape:
            differences.append(f"*** PIXEL SHAPE: {old_data.shape} vs {new_data.shape} ***")
        elif not np.array_equal(old_data, new_data):
            worst = float(np.abs(old_data.astype(float) - new_data.astype(float)).max())
            differences.append(f"*** PIXELS DIFFER — max absolute difference {worst} ***")
            report['pixel_max_difference'] = worst
        else:
            report['pixels'] = 'IDENTICAL'

    except Exception as exc:
        differences.append(f"could not compare pixels: {type(exc).__name__}: {exc}")

    report['differences'] = differences

    if verbose:
        print(f"\n  {path}")
        print(f"  aicsimageio {have.get('aicsimageio')}  vs  bioio {have.get('bioio')}\n")
        for key in ('shape', 'dtype', 'dims.order', 'physical_pixel_sizes', 'scenes'):
            if key in report:
                print(f"    {key:22} {report[key]['aicsimageio']}")
                print(f"    {'':22} {report[key]['bioio']}")
        print(f"    {'pixels':22} {report.get('pixels', 'see differences')}\n")

        if differences:
            print(f"  *** {len(differences)} DIFFERENCE(S) ***\n")
            for line in differences:
                print(f"    {line}")
        else:
            print("  IDENTICAL on every axis checked, including the pixels.")

    return report

def read_plane(image, *, path=None, scene=None, t=0, c=0, z=0, dtype=None):
    """**Read exactly ONE YX plane. Never the whole scene.**

    ── ``get_image_data()`` LOADS THE ENTIRE SCENE ────────────────────────────

    This is not a subtlety — **both libraries document it in the same words**:

        *"The ``.get_image_data`` function will **load the whole scene into memory** and then
        retrieve the specified chunk."*
        — BioIO docs, and aicsimageio's before them

    ``get_image_dask_data()`` is the lazy one: *"will not load any piece of the imaging data into
    memory until you specifically call ``.compute()``."*

    **PyCAT was calling the eager one in eight places in the loading path** — including to read a
    *single plane* in order to *classify* a file. On a large 4-D acquisition that reads the entire
    scene **to look at one frame**, and it can happen **more than once per file** because the
    reader is constructed several times before anything is displayed.

    *That is the freeze.*

    ── This is NOT a BioIO regression, and the distinction matters ────────────

    **aicsimageio documented the same eager semantics.** The calls were wrong in 1.5.x too. What
    the migration did was **expose** them — a different CZI backend (``pylibczirw`` rather than
    ``aicspylibczi``) and a different TIFF reader can make the same mistake cost very differently.

    *Chasing "what did BioIO break?" would have been chasing a phantom. The loader was always
    eager here.*

    ── Why one function rather than eight fixes ───────────────────────────────

    Eight call sites, each free to reach for the eager API again. **A single reader means the ban
    can be enforced**: ``test_the_loader_NEVER_uses_the_eager_API`` fails the build if
    ``get_image_data(`` appears anywhere in ``file_io``.
    """
    import numpy as np

    if scene is not None:
        image.set_scene(scene)

    # ── TIFF pixels do NOT go through BioIO ──────────────────────────────────────
    #
    # ``bioio-tifffile`` builds its dask array via ``tif.aszarr()`` — and **tifffile's zarr store
    # is broken on zarr 3.2**::
    #
    #     ImportError: cannot import name 'RegularChunkGrid' from 'zarr.core.chunk_grids'
    #     -> caught, and re-raised as: ValueError: zarr 3.2.1 < 3 is not supported
    #
    # ***That message is a lie.*** 3.2.1 is not less than 3. tifffile blames the version for **any**
    # ImportError out of its zarr-3 module, and the real cause is one frame up, where nobody looks.
    #
    # **And the lazy-read fix is what walked into it.** The eager ``get_image_data()`` decoded the
    # page directly and **never touched tifffile's zarr store**. *The old path worked precisely
    # because it was doing the wrong thing.*
    #
    # ``tifffile`` can seek a single page **directly** — no zarr, no dask graph, no OME plane-map
    # walk. It is **faster than the BioIO path even when BioIO works**, which is why
    # ``_TiffPageStack`` exists.
    #
    # *(BioIO still supplies dimensions, scenes, channel names and pixel size for TIFF. It is good
    # at that, and none of it goes near the zarr store.)*
    #
    # ``read_tiff_plane`` returns ``None`` when it cannot establish the page mapping with
    # confidence — a multi-file OME set, an unexpected page count. ***A wrong page is worse than a
    # slow one:*** it would show the wrong channel, and nothing would look broken.
    # ``path`` is passed in by the caller, which always has it. **Digging into BioIO's internals
    # for it would be a guess** — the public API does not expose the source path, and a private
    # attribute that moves in a point release would break this silently.
    if path is not None:
        from pycat.file_io.tiff_planes import read_tiff_plane, is_tiff

        if is_tiff(path):
            _dims = getattr(image, 'dims', None)
            _plane = read_tiff_plane(
                path, t=t, c=c, z=z,
                n_channels=int(getattr(_dims, 'C', 1) or 1),
                n_z=int(getattr(_dims, 'Z', 1) or 1),
                dtype=dtype)
            if _plane is not None:
                return _plane

    lazy = image.get_image_dask_data("YX", T=int(t), C=int(c), Z=int(z))

    # A dask array computes on demand; a numpy one is already here. Both libraries return dask
    # from `get_image_dask_data`, but a reader plugin is free not to — so ask, do not assume.
    if hasattr(lazy, 'compute'):
        lazy = lazy.compute()

    plane = np.asarray(lazy)

    if dtype is not None:
        plane = plane.astype(dtype, copy=False)

    return plane
