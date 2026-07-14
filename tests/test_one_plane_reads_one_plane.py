"""
**Does reading one plane read one plane?**

── The check that was missing, and why the migration passed without it ──────────────────

The BioIO migration was validated by comparing **shape, dtype, dimension order, pixel size, scenes,
and a SHA-256 of the pixels** across 38 real files. **31 identical, 0 different.**

***That was true, and it was insufficient.***

It measured **correctness** and **nothing about cost** — so a loader that read the **entire scene**
to fetch **one plane** passed every check while **freezing the application.** The freeze was
**invisible to it by construction**, and it took a user saying *"it lags"* to find it.

The audit named exactly this:

    "…did not compare: bytes read during construction; peak resident memory; time to first plane;
     …whether a one-plane request materializes a scene.

     **That is exactly why the migration passed correctness testing while regressing user
     experience.**"

── The metric, and the one I tried first that DOESN'T WORK ──────────────────────────────

**Bytes read is unreliable, and I found that out by testing it rather than assuming it.**

The OS **page cache** serves a recently-touched file from RAM, and ``tifffile`` **memory-maps** — so
the pixels arrive by *page fault*, not by ``read()``. Measured on a 13 MB, 50-frame TIFF::

    lazy  read:  0 bytes
    EAGER read:  0 bytes        <- the WHOLE SCENE, and the counter saw NOTHING

***A metric that reports zero for the bug it exists to catch is worse than no metric.*** ``psutil``'s
I/O counters are blind to it too.

**Peak ALLOCATION is immune to all of it.** An eager read **must allocate the whole scene**, and
*the cache cannot hide an allocation*. Same file::

    lazy  peak:  0.28 MB   ( 1.1x one plane)
    EAGER peak: 13.12 MB   (50.1x — exactly the 50 frames)

── Why a RATIO ──────────────────────────────────────────────────────────────────────────

    amplification = peak_allocated / bytes_in_one_plane

**It needs no baseline and no "before" run.** ~1x means the loader read the plane. ~Nx means it read
the whole N-frame scene to hand you one frame. **One number, one file, and the answer is
unambiguous.**
"""

import gc
import os
import tempfile
import tracemalloc

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")


def _peak_allocation(function):
    """Bytes at the high-water mark of ``function``. **The cache cannot hide an allocation.**"""
    gc.collect()
    tracemalloc.start()
    try:
        function()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


@pytest.fixture
def a_fifty_frame_tiff():
    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    frames = np.random.default_rng(0).integers(0, 255, (50, 512, 512), dtype=np.uint8)
    tifffile.imwrite(path, frames)

    # **Warm the cache deliberately.** The hard case is the one where the OS has the whole file in
    # RAM — because that is where a bytes-read metric goes blind, and where this one must not.
    tifffile.imread(path)

    yield path, 512 * 512, 50

    os.unlink(path)


@pytest.mark.core
def test_reading_ONE_PLANE_does_not_allocate_the_WHOLE_SCENE(a_fifty_frame_tiff):
    """***The check that would have caught the freeze on day one.***"""
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    path, plane_bytes, n_frames = a_fifty_frame_tiff

    peak = _peak_allocation(
        lambda: planes.read_tiff_plane(path, t=10, n_channels=1, n_z=1))

    scene_bytes = plane_bytes * n_frames
    scene_fraction = peak / scene_bytes

    # ── WHERE DOES PEAK SIT BETWEEN THE FLOOR AND THE CEILING? ──────────────────
    #
    # **Three thresholds went wrong before this one, and each taught something:**
    #
    # 1. *"amplification < 3x"* — **vacuous**. For a single-plane file, one plane IS the whole
    #    scene, so the ratio is 1.0x by construction. **30 of 32 real files could not fail.**
    #
    # 2. *"fraction of scene < 15%"* — flagged a 57 KB plane in a 600-frame file at 3.7x, which is
    #    **0.6% of a 34 MB scene.** *Fixed overhead reads as 3.7x on a small plane and 0.01x on a
    #    big one.*
    #
    # 3. …and the same fix then flagged the **3-channel** files. **Reading one plane out of a
    #    3-plane scene necessarily allocates 33% of it.** ***A correct loader MUST hit the floor,
    #    and I was calling the floor a failure.***
    #
    # The file itself sets both bounds:
    #
    #     floor   = one plane        (what a correct loader allocates)
    #     ceiling = the whole scene  (what a broken one allocates)
    #
    # **Where peak sits between them** is scale-free, plane-count-free, and needs no invented
    # constant. *(And it only has power when there are enough planes for the two to be far apart —
    # which is why this fixture uses 50.)*
    position = (peak - plane_bytes) / max(scene_bytes - plane_bytes, 1)

    assert position < 0.1, (
        f"reading ONE plane allocated {peak / 1e6:.2f} MB — **{position:.0%} of the way from "
        f"'one plane' to 'the whole {n_frames}-frame scene'** ({scene_fraction:.0%} of it, "
        f"{peak / plane_bytes:.1f}x one plane).\n\n"
        f"On a large acquisition that is a freeze, and **no correctness test can see it**: the "
        f"pixels that come back are perfectly right."
    )


@pytest.mark.core
def test_the_METRIC_ACTUALLY_CATCHES_an_eager_read(a_fifty_frame_tiff):
    """**A guard that cannot fail is not a guard.**

    The eager pattern — ``imread(path)[t]`` — is exactly what the loader used to do. If the metric
    does not flag it, the metric is decorative.

    *(This is not hypothetical. My first attempt used bytes-read, and it reported **zero for both**
    — the page cache and memory-mapping hid the entire scene read. I only found that out because I
    tested the metric against the bug instead of trusting it.)*
    """
    path, plane_bytes, n_frames = a_fifty_frame_tiff

    eager_peak = _peak_allocation(lambda: tifffile.imread(path)[10])
    amplification = eager_peak / plane_bytes

    assert amplification > 10.0, (
        f"the eager pattern `imread(path)[t]` allocated only {amplification:.1f}x one plane. "
        f"**The metric cannot distinguish it from a lazy read**, so it would not catch the "
        f"regression it exists to catch."
    )


@pytest.mark.core
def test_the_LAZY_and_EAGER_paths_are_SEPARATED_by_an_order_of_magnitude(a_fifty_frame_tiff):
    """The two together. **The margin is what makes the threshold safe.**"""
    planes = pytest.importorskip("pycat.file_io.tiff_planes")

    path, plane_bytes, _ = a_fifty_frame_tiff

    lazy = _peak_allocation(lambda: planes.read_tiff_plane(path, t=10, n_channels=1, n_z=1))
    eager = _peak_allocation(lambda: tifffile.imread(path)[10])

    assert eager > lazy * 5, (
        f"lazy peaked at {lazy / 1e6:.2f} MB and eager at {eager / 1e6:.2f} MB — only "
        f"{eager / max(lazy, 1):.1f}x apart. The threshold in the test above has no margin."
    )


# ══════════════════════════════════════════════════════════════════════════════════════════
#  THE WRAPPER — the path the user's finger is actually on
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# ── The harness tested ONE of FOUR lazy paths, and NOT the one that bit ────────────────────
#
#     FORMAT   LOAD PATH                LAZY WRAPPER              tested?
#     ------------------------------------------------------------------------
#     .ims  -> _open_stack_ims       -> _ImsReaderTYX/ZYX/TZYX      NO   <- SKIPPED
#     .tif  -> _open_stack_generic   -> _TiffPageStack              NO
#     .czi  -> _open_stack_generic   -> _LazyArraySource            NO
#     any   -> read_plane            -> (no wrapper)                YES  <- only this
#
# ***And the bug Gable actually FELT — the IMS scrubbing lag — lived in
# ``_ImsReaderTYX.__array__``, which none of that ever touched.***
#
# Gable: *"since we have ims loading lazily why are we not trying to time them in the same way?
# the issues with lazy not being so lazy were there for everything."* **He was right.**
#
# ``read_plane`` is the **classification** path. What the user does is::
#
#     drag the slider    ->  wrapper[t]         ->  __getitem__  ->  ONE frame
#     napari thumbnail   ->  np.asarray(layer)  ->  __array__    ->  ALL frames   <- THE BUG


@pytest.mark.core
def test_SCRUBBING_one_frame_allocates_one_frame():
    """**The path the user feels.** ``read_plane`` is not it.

    Dragging the time slider calls ``wrapper[t]``. If that allocates the whole stack, the
    application freezes — *and every correctness test still passes, because the frame that comes
    back is perfectly right.*
    """
    file_io = pytest.importorskip("pycat.file_io.file_io")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    n_frames, size = 60, 512
    try:
        tifffile.imwrite(
            path,
            np.random.default_rng(0).integers(0, 255, (n_frames, size, size), dtype=np.uint8))
        tifffile.imread(path)                     # warm the cache — the hard case

        wrapper = file_io._TiffPageStack(path, n_frames, size, size, np.dtype('float32'),
                                         channel_idx=0, n_channels=1)

        # The wrapper returns float32, so one frame costs 4 bytes/px whatever the file holds.
        plane_bytes = size * size * 4
        scene_bytes = plane_bytes * n_frames

        peak = _peak_allocation(lambda: wrapper[n_frames // 2])

        position = (peak - plane_bytes) / max(scene_bytes - plane_bytes, 1)

        assert position < 0.1, (
            f"scrubbing to ONE frame allocated {peak / 1e6:.2f} MB — **{position:.0%} of the way "
            f"from 'one frame' to 'the whole {n_frames}-frame stack'**.\n\n"
            f"This is what the user does when they drag the slider. **It is the freeze**, and no "
            f"correctness test can see it."
        )
    finally:
        os.unlink(path)


@pytest.mark.core
def test_np_asarray_on_a_WRAPPER_still_REFUSES():
    """**The other half of the same bug.**

    ``wrapper[t]`` being cheap is not enough — *anything* that treats the layer as an array calls
    ``__array__``: a thumbnail, a contrast estimate, a plugin, a layer-list refresh.

    **That is what made the IMS stack lag**, and PyCAT's own source had said so for months.
    """
    file_io = pytest.importorskip("pycat.file_io.file_io")

    handle, path = tempfile.mkstemp(suffix='.tif')
    os.close(handle)

    try:
        tifffile.imwrite(path, np.random.default_rng(0).integers(
            0, 255, (20, 128, 128), dtype=np.uint8))

        wrapper = file_io._TiffPageStack(path, 20, 128, 128, np.dtype('float32'),
                                         channel_idx=0, n_channels=1)

        with pytest.raises(RuntimeError, match="implicit full-stack read"):
            np.asarray(wrapper)

    finally:
        os.unlink(path)
