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

    # ── FRACTION OF THE SCENE, not a multiple of the plane ──────────────────────
    #
    # **A raw ratio is scale-dependent, and my first threshold was wrong because of it.**
    #
    # The harness flagged one of Gable's files at **3.7x** and called it a problem. *It was not.*
    # That file is 600 frames of 177x162 — **a 57 KB plane** — so 3.7x is ~212 KB against a **34 MB
    # scene: 0.6% of it.** The same fixed overhead (page tags, metadata, a numpy temporary) reads as
    # 3.7x on a small plane and 0.01x on a 4 MB one.
    #
    # **A loader that reads the whole scene allocates ~100% of it.** That is the thing to test.
    assert scene_fraction < 0.15, (
        f"reading ONE plane allocated {peak / 1e6:.2f} MB — **{scene_fraction:.0%} of the whole "
        f"{n_frames}-frame scene** ({peak / plane_bytes:.1f}x one plane).\n\n"
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
