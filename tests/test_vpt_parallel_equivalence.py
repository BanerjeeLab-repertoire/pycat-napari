"""Regression test: VPT CPU-parallel bead detection must produce results
IDENTICAL to serial detection.

The parallelisation only changes *where* the per-frame blob detection runs (a
process pool vs. the calling thread), never *what* it computes. This test is the
gate that guarantees that: it detects beads on a small synthetic TIFF stack both
serially and through the ProcessPool worker path and asserts the returned
coordinates match frame-for-frame. If a future change to the detection path or
the worker makes the two diverge, this test fails loudly.

GPU equivalence (blob_log_gpu vs. skimage) is validated separately on hardware
with a CUDA device, since it cannot run in CI.
"""

import numpy as np
import pytest
import tifffile

from concurrent.futures import ProcessPoolExecutor

from pycat.toolbox.vpt_tools import (
    detect_beads_frame,
    dedup_detections,
    _detect_frame_worker,
)


def _make_synthetic_stack(path, n_frames=4, shape=(128, 128), n_beads=25, seed=0):
    """Deterministic synthetic bead movie: bright Gaussian spots on a dim
    background, saved as a multipage TIFF the worker can re-open by path."""
    rng = np.random.default_rng(seed)
    ys = rng.integers(8, shape[0] - 8, n_beads)
    xs = rng.integers(8, shape[1] - 8, n_beads)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    frames = []
    for t in range(n_frames):
        img = rng.normal(50, 3, shape).astype(np.float32)
        for by, bx in zip(ys, xs):
            # small per-frame jitter so frames are not identical
            jy = by + rng.normal(0, 0.3)
            jx = bx + rng.normal(0, 0.3)
            img += 200.0 * np.exp(-((yy - jy) ** 2 + (xx - jx) ** 2) / (2 * 2.0 ** 2))
        frames.append(img.astype(np.uint16))
    stack = np.stack(frames, 0)
    tifffile.imwrite(path, stack)
    return stack


def test_parallel_detection_matches_serial(tmp_path):
    tif_path = tmp_path / "synthetic_beads.tif"
    stack = _make_synthetic_stack(str(tif_path))

    det_kwargs = dict(min_sigma=1.0, max_sigma=4.0, num_sigma=4,
                      threshold=0.02, host_mask=None)
    merge_radius = 3

    # Serial: detect + dedup per frame, in-process.
    serial = {}
    for t in range(len(stack)):
        frame = stack[t].astype(np.float32)
        coords = detect_beads_frame(frame, **det_kwargs)
        coords = dedup_detections(coords, frame, merge_radius)
        serial[t] = sorted((round(float(y), 4), round(float(x), 4))
                           for y, x in coords)

    # Parallel: same work via the picklable worker + a process pool, reading
    # each frame back from the TIFF by descriptor (as the real code does).
    src_desc = {'kind': 'tiff', 'path': str(tif_path), 'nc': 1, 'ci': 0}
    tasks = [(t, src_desc, True, det_kwargs, merge_radius)
             for t in range(len(stack))]
    parallel = {}
    with ProcessPoolExecutor(max_workers=2) as ex:
        for t, coords in ex.map(_detect_frame_worker, tasks):
            parallel[t] = sorted((round(float(y), 4), round(float(x), 4))
                                 for y, x in coords)

    # Every frame must match exactly.
    for t in range(len(stack)):
        assert serial[t] == parallel[t], (
            f"frame {t}: parallel detection differs from serial "
            f"({len(serial[t])} vs {len(parallel[t])} beads)")


def test_worker_reads_frame_by_descriptor(tmp_path):
    """The worker's TIFF descriptor reader returns the correct frame."""
    from pycat.toolbox.vpt_tools import _read_frame_from_descriptor
    tif_path = tmp_path / "s.tif"
    stack = _make_synthetic_stack(str(tif_path), n_frames=3)
    src_desc = {'kind': 'tiff', 'path': str(tif_path), 'nc': 1, 'ci': 0}
    for t in range(len(stack)):
        got = _read_frame_from_descriptor(t, src_desc)
        assert np.allclose(got, stack[t].astype(np.float32))
