"""
The puncta refinement filter, tested for the first time.

Why this had never been tested
------------------------------
``segmentation_tools`` imported napari at module scope, so **none of its 16 pure analysis
functions could be imported without a GUI** — and CI therefore could not see any of them. The
puncta refinement filter is the most consequential of them: it decides which detections survive
into every downstream count.

Its SNR gate was found **completely dead** in 1.5.416 — ``object_mean / bg_std <= 1.0`` never
fires on any camera with a positive background, because even a "punctum" of pure noise has an
object mean vastly exceeding the background's standard deviation. Two of the five quality
conditions had never rejected anything, on any image, for the life of the pipeline.

That fix was measured against synthetic data, but **nothing exercised it in the codebase**. This
does.

The scene
---------
Five detections inside one cell, on a 500-count camera pedestal:

* three **REAL** puncta (amplitudes 80, 80, 40 above the local background), and
* two **SPURIOUS** ones — labelled regions with **no signal added at all**, i.e. pure noise.

The filter must keep all three and reject both.
"""

import numpy as np
import pytest
from scipy import ndimage as ndi


@pytest.mark.core
def test_puncta_filter_keeps_real_rejects_spurious():
    seg = pytest.importorskip(
        "pycat.toolbox.segmentation_tools",
        reason="segmentation_tools import (may pull cellpose) unavailable")

    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    pedestal = 500.0
    img = np.full((h, w), pedestal + 20.0)
    cell = ((yy - 100) ** 2 + (xx - 100) ** 2) < 75 ** 2
    img[cell] += 40.0                      # diffuse cytoplasmic signal

    labels = np.zeros((h, w), np.int32)
    truth = {}
    spec = [(70, 70, 80), (70, 130, 80), (130, 70, 40), (130, 130, 0), (100, 55, 0)]
    for i, (cy, cx, amp) in enumerate(spec, start=1):
        if amp:
            img += amp * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 2.0 ** 2))
        labels[((yy - cy) ** 2 + (xx - cx) ** 2) < 9] = i
        truth[i] = "real" if amp else "spurious"

    img = ndi.gaussian_filter(img, 1.0) + rng.normal(0, 5.0, (h, w))

    out = seg.puncta_refinement_filtering_func(
        img, img, labels > 0, cell.astype(np.int32), labels, 2)
    mask = out[0] if isinstance(out, tuple) else out
    kept = set(np.unique(labels[mask > 0])) - {0}

    real = {k for k, v in truth.items() if v == "real"}
    spurious = {k for k, v in truth.items() if v == "spurious"}

    missing = sorted(real - kept)
    assert not missing, (
        f"The filter REJECTED real puncta {missing}. These have amplitudes of 40-80 counts "
        f"above the local background and are plainly detectable. Rejecting them means the "
        f"puncta count is silently too low — set PYCAT_REFINE_DEBUG=1 to see which of the "
        f"eight conditions fired."
    )

    surviving = sorted(spurious & kept)
    assert not surviving, (
        f"The filter KEPT spurious detections {surviving} — labelled regions with NO signal "
        f"added, i.e. pure noise. This is what the SNR gate exists to remove, and it is "
        f"exactly the failure found in 1.5.416: `object_mean / bg_std <= 1.0` never fires on "
        f"a camera with a positive background, so the gate rejected nothing at all. The gate "
        f"must be a CONTRAST (`(object_mean - background) / background_noise`), which is "
        f"pedestal-invariant."
    )


@pytest.mark.core
def test_puncta_filter_reports_what_it_rejected():
    """A filter that removes objects and says nothing is indistinguishable from a
    segmentation that never found them.

    The eight rejection conditions decide which detections survive into every downstream
    count. Their reasons were computed for each dropped object and then **discarded** unless
    ``PYCAT_REFINE_DEBUG=1`` was set — and even then they were ``print``ed to a console a
    napari user never sees. A user whose puncta silently vanished had no way to find out why.

    The summary is now always produced, and the *"almost everything was rejected"* case
    escalates to a warning — because that almost always means a threshold is wrong for the
    data, not that every detection was spurious. Without it, a user simply concludes *"there
    are no puncta in my cells"*.
    """
    seg = pytest.importorskip("pycat.toolbox.segmentation_tools")

    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]
    rng = np.random.default_rng(0)

    img = np.full((h, w), 520.0)
    cell = ((yy - 100) ** 2 + (xx - 100) ** 2) < 75 ** 2
    img[cell] += 40.0
    labels = np.zeros((h, w), np.int32)

    # Every detection is pure noise: the filter should reject all five AND say so loudly.
    for i, (cy, cx) in enumerate([(70, 70), (70, 130), (130, 70), (130, 130), (100, 55)],
                                 start=1):
        labels[((yy - cy) ** 2 + (xx - cx) ** 2) < 9] = i
    img = ndi.gaussian_filter(img, 1.0) + rng.normal(0, 5.0, (h, w))

    # Patch the name AS BOUND IN the module that DEFINES the filter — puncta_refinement.py (the
    # refinement family moved out of segmentation_tools in 1.6.242). `from pycat.utils.notify import
    # show_warning as napari_show_warning` copies the reference at import time, so the filter resolves
    # it in puncta_refinement's namespace; patching it anywhere else has no effect.
    pr = pytest.importorskip("pycat.toolbox.segmentation.puncta_refinement")
    messages = []
    real_warn = pr.napari_show_warning
    real_info = pr.napari_show_info
    pr.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    pr.napari_show_info = lambda msg, *a, **k: messages.append(msg)
    try:
        seg.puncta_refinement_filtering_func(
            img, img, labels > 0, cell.astype(np.int32), labels, 2)
    finally:
        pr.napari_show_warning = real_warn
        pr.napari_show_info = real_info

    joined = " ".join(messages)
    assert messages, (
        "Every detection was rejected and the filter said NOTHING. The user is left with an "
        "empty mask and no way to tell whether the segmentation found nothing or the filter "
        "threw everything away."
    )
    assert "rejected" in joined, f"the summary does not say what happened: {joined!r}"
    assert any(tok in joined for tok in ("local_snr", "local_intensity", "gradient")), (
        f"the summary must NAME the conditions that fired, so a wrong threshold can be "
        f"traced to the exact check: {joined!r}"
    )
