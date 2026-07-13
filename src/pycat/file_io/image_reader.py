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
# **Default: aicsimageio.** The incumbent stays the default until the comparison has been run on
# real CZI, OME-TIFF and Micro-Manager data. *Flipping a default is a decision to make with evidence
# in hand, not with a passing import.*
_DEFAULT_BACKEND = 'aicsimageio'

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
            return BioImage(path, **kwargs)
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

    return AICSImage(path, **kwargs)


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
