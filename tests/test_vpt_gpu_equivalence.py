"""VPT GPU / CPU-parallel blob-detection equivalence tests.

These assert that the three detection tiers PyCAT can pick between —

    serial CPU  ==  CPU-parallel (ProcessPool)  ==  GPU (CuPy LoG)

produce *identical* blob sets on real data, so the tier selector in
``detect_beads_stack`` is free to choose the fastest available path without ever
changing results. The GPU tier in particular was previously verified on a single
frame in a sandbox WITHOUT CUDA; this exercises the whole fixture stack on real
hardware.

Requires a functional CUDA GPU (``gpu_available()``). Skipped otherwise — CI
runners and GPU-less lab machines still collect green. Marked ``integration``
(the GPU tier is opt-in and not part of the headless ``core`` suite).

The fixture ``mmstack_20frames.tif`` is a 20-frame 171x201 Micro-Manager crop —
the sparse-detection regime. It is intentionally self-contained (committed to
the repo) so the test needs no external data; the dense-frame *speedup* numbers
that justify the GPU tier are measured separately, not asserted here.
"""

# Standard library imports
import os
from concurrent.futures import ProcessPoolExecutor

# Third party imports
import numpy as np
import pytest

# Local application imports
from pycat.toolbox.gpu_utils import gpu_available

pytestmark = pytest.mark.integration

_FIXTURE = os.path.join(os.path.dirname(__file__), "Fixtures", "mmstack_20frames.tif")

# LoG scale-space parameters — cover the fixture's bead size range.
_PARAMS = dict(min_sigma=1.0, max_sigma=5.0, num_sigma=5, threshold=0.02)

# Skip the whole module unless BOTH a GPU and the fixture are available.
_skip_no_gpu = pytest.mark.skipif(
    not gpu_available(), reason="requires a functional CUDA GPU (CuPy)"
)
_skip_no_fixture = pytest.mark.skipif(
    not os.path.exists(_FIXTURE), reason=f"fixture not found: {_FIXTURE}"
)


def _coord_key(coords):
    """Sorted, rounded (y, x) set — the equivalence identity used by the
    ``detect_beads_stack`` guard (rounded to 3 dp to absorb float noise)."""
    return sorted((round(float(y), 3), round(float(x), 3)) for y, x in coords)


def _blob_key(blobs):
    """Same identity for (y, x, sigma) blob_log output (sigma ignored)."""
    return sorted((round(float(y), 3), round(float(x), 3)) for y, x, *_ in blobs)


@pytest.fixture(scope="module")
def frames():
    """The fixture stack as a list of [0, 1] float32 frames, read exactly the
    way the production reader yields them (via ``_TiffPageStack``)."""
    import tifffile
    from pycat.file_io.file_io import _TiffPageStack

    raw = tifffile.imread(_FIXTURE)
    if raw.ndim == 2:
        raw = raw[None]
    T, H, W = raw.shape
    stack = _TiffPageStack(_FIXTURE, T, H, W, raw.dtype, channel_idx=0, n_channels=1)
    return [stack[t] for t in range(T)]


@pytest.fixture(scope="module")
def serial_detections(frames):
    """Ground-truth serial-CPU detections, one (y, x) set per frame."""
    from pycat.toolbox import vpt_tools as vpt

    return [
        _coord_key(vpt.detect_beads_frame(f, **_PARAMS, use_gpu=False))
        for f in frames
    ]


@_skip_no_fixture
@_skip_no_gpu
def test_blob_log_gpu_matches_skimage(frames):
    """The GPU LoG blob detector reproduces ``skimage.feature.blob_log`` exactly,
    frame by frame, on real CUDA."""
    from skimage import feature as skf
    from pycat.toolbox import vpt_tools as vpt

    for t, f in enumerate(frames):
        cpu = skf.blob_log(f, **_PARAMS)
        gpu = vpt.blob_log_gpu(f, **_PARAMS)
        assert _blob_key(cpu) == _blob_key(gpu), f"blob_log mismatch on frame {t}"


@_skip_no_fixture
@_skip_no_gpu
def test_gpu_matches_serial(frames, serial_detections):
    """``detect_beads_frame(use_gpu=True)`` == serial CPU across all frames — the
    condition the stack-level equivalence guard checks on frame 0, here on every
    frame."""
    from pycat.toolbox import vpt_tools as vpt

    for t, f in enumerate(frames):
        gpu = _coord_key(vpt.detect_beads_frame(f, **_PARAMS, use_gpu=True))
        assert gpu == serial_detections[t], f"GPU!=serial on frame {t}"


@_skip_no_fixture
@_skip_no_gpu
def test_cpu_parallel_matches_serial(serial_detections):
    """The ProcessPool worker path (the CPU-parallel tier) reads frames from a
    source descriptor in a subprocess and must return the same blobs as serial.

    Uses the real production worker ``_detect_frame_worker`` + descriptor, not a
    re-implementation, so this covers the actual parallel read/detect path."""
    import tifffile
    from pycat.file_io.file_io import _TiffPageStack
    from pycat.toolbox import vpt_tools as vpt

    raw = tifffile.imread(_FIXTURE)
    if raw.ndim == 2:
        raw = raw[None]
    T, H, W = raw.shape
    stack = _TiffPageStack(_FIXTURE, T, H, W, raw.dtype, channel_idx=0, n_channels=1)
    desc = vpt._bead_source_descriptor(stack)
    assert desc is not None, "fixture must be a file-backed stack for the pool path"

    tasks = [(t, desc, True, _PARAMS, None) for t in range(T)]
    with ProcessPoolExecutor(max_workers=4) as ex:
        parallel = {t: _coord_key(coords)
                    for t, coords in ex.map(vpt._detect_frame_worker, tasks)}

    for t in range(T):
        assert parallel[t] == serial_detections[t], f"parallel!=serial on frame {t}"
