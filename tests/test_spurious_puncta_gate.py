"""
**Spurious puncta came back, because the tree had REGRESSED.**

Meet reported them returning and **sent the file that worked.** Diffing it against the tree was
decisive: **the tree was the older file.** It had lost an entire subsystem — and Meet's copy already
contained the module-level ``from cellpose import models`` that 1.5.523 "discovered".

*A newer file was overwritten with an older one at some point during this session's validation
work.*

The mechanism — verified, not assumed
--------------------------------------
``sk.exposure.equalize_adapthist`` **normalises every cell to unit maximum.**

So a cell containing **only noise** is amplified by ``1 / cell_max`` — measured at **500×** on a
cell holding nothing but background — and **both cells come out of CLAHE with the same [0, 1]
range.** The empty cell's noise now has structure, and **it segments as puncta.**

Why the existing contrast check could never catch it
-----------------------------------------------------
The restored code says it plainly:

    *"``check_contrast_func`` **cannot catch this**: it inspects the image AFTER those
    contrast-maximising steps, so it essentially **never fires**. This gate runs BEFORE them, on
    the raw intensity image, and is **the only place in the chain where absolute brightness is
    still available**."*

What was restored
-----------------
* ``compute_image_intensity_stats`` — measures the image's absolute background and noise floor
  **once, before any per-cell renormalisation**
* ``cell_has_punctate_signal`` — **a hypothesis test, not a contrast heuristic.** A pixel counts as
  evidence only if it clears **both** a local floor **and** an absolute one
* ``min_relative_max`` in ``cell_mask_stretching`` — the dim-cell gain ceiling (a 50× cap)
* the four parameters threaded through both segmentation entry points
"""

import io
import contextlib

import numpy as np
import pytest


def _two_cells(size=128, seed=0):
    """One cell with **real puncta**, one containing **only noise**."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]

    image = np.full((size, size), 100.0)
    real = ((yy - 40) ** 2 + (xx - 40) ** 2) < 25 ** 2
    empty = ((yy - 90) ** 2 + (xx - 90) ** 2) < 25 ** 2

    image[real] = 300 + rng.normal(0, 15, real.sum())
    for cy, cx in ((32, 32), (48, 45), (38, 50)):
        image += 2000 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 2.5 ** 2))

    image[empty] = 105 + rng.normal(0, 8, empty.sum())     # noise only — 5 counts of it

    image = np.clip(image, 0, 65535).astype(np.uint16)

    labels = np.zeros((size, size), np.int32)
    labels[real] = 1
    labels[empty] = 2

    return image, labels


@pytest.mark.core
def test_an_EMPTY_cell_is_not_reported_as_having_puncta():
    """**The bug Meet reported.** A cell holding 5 counts of noise above background.

    Without the gate, per-cell CLAHE amplifies it **500×** and its noise segments as puncta.
    """
    segmentation = pytest.importorskip("pycat.toolbox.segmentation_tools")

    image, labels = _two_cells()

    with contextlib.redirect_stderr(io.StringIO()):
        stats = segmentation.compute_image_intensity_stats(image, labels, smooth_sigma=1.0)
        has_puncta, info = segmentation.cell_has_punctate_signal(
            image, labels == 2, image_stats=stats, min_spot_radius=2)

    assert not has_puncta, (
        f"a cell containing ONLY NOISE was reported as having puncta "
        f"(largest blob {info['largest_blob_px']}px, peak z={info['z_local']:.1f}). "
        f"**equalize_adapthist normalises every cell to unit maximum**, so this cell's noise is "
        f"amplified 500x and becomes speckle."
    )


@pytest.mark.core
def test_a_REAL_cell_still_passes_the_gate():
    """**A gate with no power is a gate that never says anything.**

    Measured: the real cell has a **278 px** blob at **122σ**. It must not be rejected.
    """
    segmentation = pytest.importorskip("pycat.toolbox.segmentation_tools")

    image, labels = _two_cells()

    with contextlib.redirect_stderr(io.StringIO()):
        stats = segmentation.compute_image_intensity_stats(image, labels, smooth_sigma=1.0)
        has_puncta, info = segmentation.cell_has_punctate_signal(
            image, labels == 1, image_stats=stats, min_spot_radius=2)

    assert has_puncta, (
        f"a cell with three real puncta was REJECTED (largest blob {info['largest_blob_px']}px, "
        f"peak z={info['z_local']:.1f}). The gate is throwing away good data."
    )


@pytest.mark.core
def test_the_gate_is_WIRED_IN_and_not_merely_present():
    """**A restored function that nobody calls is a restored function that does nothing.**

    The whole regression was that these existed in one file and not in the one that shipped.
    """
    import ast
    import pathlib

    # segmentation_tools.py is being decomposed into a toolbox/segmentation/ package (1.6.240+); the
    # gate functions and cell_mask_stretching now live in family modules. Inspect the WHOLE segmentation
    # surface so "the function exists and is wired in" is checked wherever the code actually lives —
    # which is a stronger guarantee than pinning it to one file.
    _tb = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"
    _files = [_tb / "segmentation_tools.py"] + sorted((_tb / "segmentation").glob("*.py"))
    _texts = [p.read_text(encoding='utf-8', errors='ignore') for p in _files]
    source = "\n".join(_texts)  # for the "is CALLED" substring checks below

    signatures = {}
    for _t in _texts:  # parse each file on its own — from __future__ must be at a file's top
        for n in ast.walk(ast.parse(_t)):
            if isinstance(n, ast.FunctionDef):
                signatures[n.name] = [a.arg for a in n.args.args]

    for name in ('compute_image_intensity_stats', 'cell_has_punctate_signal'):
        assert name in signatures, f"`{name}` is missing — the regression has returned"

    for name in ('segment_subcellular_objects', 'run_segment_subcellular_objects'):
        assert 'punctate_gate' in signatures[name], (
            f"`{name}` has lost the `punctate_gate` parameter. The gate exists but nothing "
            f"reaches it."
        )

    assert 'image_stats' in signatures['segment_subcellular_objects']

    # And the gate must actually be CALLED, and the stats actually COMPUTED.
    assert 'cell_has_punctate_signal(' in source, "the gate is never called"
    assert 'compute_image_intensity_stats(' in source, "the absolute stats are never measured"

    # `min_relative_max` — the dim-cell gain ceiling.
    assert 'min_relative_max' in signatures['cell_mask_stretching'], (
        "cell_mask_stretching has lost `min_relative_max` — the 50x gain ceiling that stops a "
        "dim cell being amplified into speckle"
    )


# ---------------------------------------------------------------------------
# The gate's OTHER failure mode: a cell that is mostly CONDENSATE.
# ---------------------------------------------------------------------------
# The gate's floor is `background + 5 * sigma`, and both were taken as the median and
# MAD of the WHOLE cell, on the stated assumption that "puncta are a small area
# fraction and barely move the MAD". True for puncta; false for condensates. Once the
# objects are more than about half the cell's pixels, the median crosses INTO the
# bright population and the MAD starts measuring the GAP between nucleoplasm and
# condensate rather than the noise; the floor then lands ABOVE the objects and the cell
# is declared empty and skipped before any per-object check runs. Measured on the
# constructed-truth large-object scenes: 6 of 18 cells, every one of them at an object
# FOOTPRINT of 0.40 or more (which after the PSF is more than half the pixels).
#
# These tests pin the fix (`robust_cell_background`) from both sides: it must recover
# the background of a condensate-dominated cell, AND leave a clean one alone — because
# the second is what the noise-only rejection above depends on.

def _condensate_dominated_cell(size=200, radius=88, n_objects=7, object_radius=30,
                               base=800.0, amplitude=2400.0, noise=6.0, seed=0):
    """A cell where the condensates cover MORE THAN HALF of it.

    More than half is where the whole-cell median/MAD breaks, and it breaks hard: the
    median crosses into the bright population, and the MAD stops describing the noise
    and starts describing the distance between the two populations. Below half it
    degrades gracefully (the MAD's 50th percentile of |deviation| still lands inside
    the background), which is why this went unnoticed for puncta.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    centre = size // 2
    cell = ((yy - centre) ** 2 + (xx - centre) ** 2) <= radius ** 2
    image = np.zeros((size, size), np.float64)
    image[cell] = base
    for k in range(n_objects):
        angle = 2 * np.pi * k / n_objects
        cy = centre + int(0.45 * radius * np.sin(angle))
        cx = centre + int(0.45 * radius * np.cos(angle))
        blob = ((yy - cy) ** 2 + (xx - cx) ** 2) <= object_radius ** 2
        image[blob & cell] = base + amplitude
    image = image + rng.normal(0, noise, image.shape)
    return np.clip(image, 0, 65535).astype(np.uint16), cell


@pytest.mark.core
def test_a_condensate_dominated_cell_is_not_mistaken_for_an_empty_one():
    """The large-object blind spot, from the failing side.

    Verified to discriminate: on this fixture the old whole-cell median/MAD puts the
    background at 0.0486 (inside the condensates, whose own level is there) and the
    floor at 0.0509, giving a largest qualifying blob of ZERO — the cell is skipped.
    The clipped estimate puts it at 0.0122 with a floor of 0.0124 and finds a
    14 640 px blob.
    """
    segmentation = pytest.importorskip("pycat.toolbox.segmentation_tools")
    image, cell = _condensate_dominated_cell()
    with contextlib.redirect_stderr(io.StringIO()):
        stats = segmentation.compute_image_intensity_stats(image, cell.astype(np.int32),
                                                           smooth_sigma=1.0)
        has_puncta, info = segmentation.cell_has_punctate_signal(
            image, cell, image_stats=stats, min_spot_radius=2)
    assert has_puncta, (
        f"a cell that is ~40% condensate by area was reported as having no punctate "
        f"signal (largest blob {info['largest_blob_px']}px, floor {info['threshold']:.5f}, "
        f"sigma {info['sigma_cell']:.6f}). The gate's background estimate is being set "
        f"by the objects it is supposed to find.")


@pytest.mark.core
def test_the_background_estimate_is_unbiased_on_clean_noise():
    """...and the fix must not move the clean case, because that is what the
    noise-only rejection above stands on."""
    from pycat.toolbox.segmentation.intensity import robust_cell_background
    rng = np.random.default_rng(0)
    values = rng.normal(0.0080, 0.00020, 20000)
    base, sigma = robust_cell_background(values)
    assert abs(base - 0.0080) < 0.00002, base
    assert abs(sigma - 0.00020) < 0.00002, sigma


@pytest.mark.core
def test_the_background_estimate_ignores_a_bright_population():
    """Directly: adding a bright population must not move the background estimate."""
    from pycat.toolbox.segmentation.intensity import robust_cell_background
    rng = np.random.default_rng(1)
    background = rng.normal(0.0080, 0.00020, 12000)
    bright = rng.normal(0.0350, 0.00050, 9000)          # 43% of the pixels
    base, sigma = robust_cell_background(np.concatenate([background, bright]))
    assert abs(base - 0.0080) < 0.00005, base
    assert sigma < 0.0004, sigma
