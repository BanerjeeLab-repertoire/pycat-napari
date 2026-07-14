"""
**A wrong page puts a real image on screen from the wrong timepoint, and nothing looks broken.**

``tiff_planes.read_tiff_plane`` used one hardcoded interleaving rule for every TIFF::

    index = ((t * n_z) + z) * n_channels + c

**It was wrong on the canonical order it was written for.** Asked for ``(t=0, c=1)`` of a 3×2
**TCYX** file it returned ``(t=1, c=0)`` — and asked for ``(t=1, c=1)`` it **declined**, falling
back to BioIO, *which for TIFF is the broken ``aszarr()`` path.*

── Why it survived ──────────────────────────────────────────────────────────────────────

``series.pages`` is a list of **pages**, not **planes**, and the two are not the same thing. A plain
3×2 TCYX file reports ``len(pages) == 3`` with ``pages[0].shape == (2, 4, 4)`` — ***one page holds
both channels.*** An OME-TIFF of the same data reports ``len(pages) == 10`` with
``pages[0].shape == (8, 8)`` — *one plane per page.* **Neither layout can be hardcoded.**

But for a single-channel time series — Micro-Manager bead stacks, the only TIFF PyCAT is routinely
pointed at — a page **is** a frame, the formula collapses to ``page = t``, and the old rule was
**correct by coincidence.**

***A rule that is right on the only data anyone tested is indistinguishable from a rule that is
right.***

The mapping is now derived from ``series.axes`` — which tifffile reports faithfully — split into
the axes that select the *page* and the axes that must be sliced *within* it.
"""

import os
import pathlib
import tempfile

import numpy as np
import pytest
import tifffile

from pycat.file_io.tiff_planes import read_tiff_plane


def _tmp():
    """A scratch directory.

    Deliberately NOT pytest's ``tmp_path`` fixture: the hand-rolled sandbox runner
    (``tools/run_core_tests.py``) has no fixture injection, so a test that needs one is
    **silently skipped locally and only ever runs in CI** — which is precisely how a guard stops
    guarding.
    """
    return pathlib.Path(tempfile.mkdtemp())


def _write(path, axes, sizes):
    """A TIFF whose every plane encodes its own (t, c, z) as ``t*100 + c*10 + z``.

    So a wrong page is **detectable**, not merely plausible.
    """
    order = [axis for axis in axes if axis not in 'YX']
    shape = tuple(sizes[axis] for axis in order) + (4, 4)

    data = np.zeros(shape, np.uint16)
    for index in np.ndindex(*[sizes[axis] for axis in order]):
        at = dict(zip(order, index))
        data[index] = at.get('T', 0) * 100 + at.get('C', 0) * 10 + at.get('Z', 0)

    tifffile.imwrite(str(path), data, metadata={'axes': axes})
    return sizes


# Every ordering of T, C and Z that a microscope or an export actually produces.
LAYOUTS = [
    ('TCYX',  dict(T=3, C=2)),
    ('CTYX',  dict(C=2, T=3)),
    ('TZYX',  dict(T=3, Z=2)),
    ('ZTYX',  dict(Z=2, T=3)),
    ('ZYX',   dict(Z=5)),
    ('CYX',   dict(C=3)),
    ('ZCYX',  dict(Z=2, C=3)),
    ('CZYX',  dict(C=3, Z=2)),
    ('TZCYX', dict(T=2, Z=2, C=2)),
    ('TCZYX', dict(T=2, C=2, Z=2)),
]


@pytest.mark.core
@pytest.mark.parametrize('axes,sizes', LAYOUTS)
def test_the_page_map_comes_from_the_FILE_not_from_an_assumption(axes, sizes):
    """**Ask for a plane; the plane's own value says which one you actually got.**"""
    path = _tmp() / f"{axes}.tif"
    _write(path, axes, sizes)

    n_channels = sizes.get('C', 1)
    n_z = sizes.get('Z', 1)

    wrong = []
    for t in range(sizes.get('T', 1)):
        for c in range(n_channels):
            for z in range(n_z):
                plane = read_tiff_plane(str(path), t=t, c=c, z=z,
                                        n_channels=n_channels, n_z=n_z)
                expected = t * 100 + c * 10 + z

                if plane is None:
                    wrong.append(f"(t={t},c={c},z={z}) DECLINED")
                elif plane.shape != (4, 4):
                    wrong.append(f"(t={t},c={c},z={z}) shape {plane.shape}, expected one plane")
                elif int(plane.flat[0]) != expected:
                    wrong.append(f"(t={t},c={c},z={z}) got plane {int(plane.flat[0]):03d}, "
                                 f"wanted {expected:03d}")

    assert not wrong, (
        f"**{axes}: the reader returned the wrong plane.**\n  " + "\n  ".join(wrong)
        + "\n\nA DECLINE falls back to BioIO, which for TIFF is the broken `aszarr()` path. "
          "A WRONG PLANE is worse: it puts a real image on screen from the wrong timepoint or "
          "channel, and nothing about it looks broken."
    )


@pytest.mark.core
def test_the_SINGLE_CHANNEL_time_series_is_untouched():
    """**The VPT baseline runs through this path. It must not move.**

    Micro-Manager bead stacks are plain single-channel TYX, where a page *is* a frame. This is the
    case the old hardcoded rule got right by coincidence — and the case the validated
    η ≈ 8.325 viscosity depends on. *Changing the page map must not change these pixels.*
    """
    path = _tmp() / "beads.tif"
    frames = np.stack([np.full((8, 8), i, np.uint16) for i in range(20)])
    tifffile.imwrite(str(path), frames, metadata={'axes': 'TYX'})

    for t in range(20):
        plane = read_tiff_plane(str(path), t=t, n_channels=1, n_z=1)
        assert plane is not None, f"frame {t} declined — the VPT read path is broken"
        assert np.array_equal(plane, frames[t]), f"frame {t} is not the frame that was written"


@pytest.mark.core
def test_an_RGB_stack_keeps_its_SAMPLES_axis():
    """``S`` is part of the image, not a plane selector. Indexing it would return one colour."""
    path = _tmp() / "rgb.tif"
    data = np.zeros((4, 6, 6, 3), np.uint8)
    for t in range(4):
        data[t, ..., 0] = t * 10
        data[t, ..., 1] = t * 10 + 1
        data[t, ..., 2] = t * 10 + 2
    tifffile.imwrite(str(path), data, photometric='rgb', metadata={'axes': 'TYXS'})

    for t in range(4):
        plane = read_tiff_plane(str(path), t=t)
        assert plane is not None and plane.shape == (6, 6, 3), (
            "the samples axis was indexed away — an RGB frame came back as a single channel"
        )
        assert np.array_equal(plane, data[t])


@pytest.mark.core
def test_an_UNDECLARED_multipage_tiff_still_reads():
    """No axes metadata at all — the caller's geometry is all there is, and ``page = t`` holds."""
    path = _tmp() / "plain.tif"
    frames = np.stack([np.full((5, 5), i, np.uint16) for i in range(7)])
    tifffile.imwrite(str(path), frames)

    for t in range(7):
        plane = read_tiff_plane(str(path), t=t, n_channels=1, n_z=1)
        assert plane is not None and np.array_equal(plane, frames[t])


@pytest.mark.core
def test_an_OME_TIFF_puts_ONE_PLANE_per_page_and_that_works_too():
    """**The same data, two page layouts.** This is why neither can be hardcoded.

    Plain TCYX: 3 pages, ``page.shape == (2, 4, 4)`` — both channels on one page.
    OME TCYX:   10 pages, ``page.shape == (8, 8)`` — one plane per page.
    """
    path = _tmp() / "acquisition.ome.tif"
    data = np.zeros((5, 2, 8, 8), np.uint16)
    for t in range(5):
        for c in range(2):
            data[t, c] = t * 100 + c * 10
    tifffile.imwrite(str(path), data, ome=True, metadata={'axes': 'TCYX'})

    for t in range(5):
        for c in range(2):
            plane = read_tiff_plane(str(path), t=t, c=c, n_channels=2, n_z=1)
            assert plane is not None, f"(t={t},c={c}) declined on an OME-TIFF"
            assert int(plane.flat[0]) == t * 100 + c * 10
