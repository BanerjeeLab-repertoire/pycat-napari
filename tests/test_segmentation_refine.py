"""
Regression tests for the segmentation refinement path.

The key correctness property established this development cycle: the windowed
"fast" refinement filter is bit-for-bit identical to the original per-object
implementation. This test locks that in so a future change to either can't
silently diverge — automating the manual `np.array_equal` check that was run by
hand when the optimization was introduced.

It deliberately tests the PURE refinement functions (no napari / cellpose), so it
runs in CI. The full segment-a-cell golden master (object count on a fixed
synthetic image) is stubbed with a maintainer TODO because it needs the viewer
pipeline; the equivalence + invariant tests below are the high-value core.

Run: pytest tests/test_segmentation_refine.py -v
"""

import numpy as np
import pytest
import scipy.ndimage as ndi

from tests.fixtures_synthetic import synthetic_puncta_image

# The refinement functions are pure and importable without napari.
seg = pytest.importorskip("pycat.toolbox.segmentation_tools",
                          reason="segmentation_tools import (may pull cellpose) unavailable")


def _make_refine_inputs(seed=0):
    """Build (original, processed, puncta_mask, cell_mask, labeled) for the
    refinement filter from a synthetic puncta image."""
    img, labels = synthetic_puncta_image(shape=(300, 300), n_puncta=60,
                                          radius=4, seed=seed)
    puncta = labels > 0
    cell = np.ones_like(puncta, dtype=bool)
    cell[0:2, 0:2] = False
    import skimage as sk
    labeled = sk.measure.label(puncta)
    return img.astype(np.float32), img.astype(np.float32), puncta, cell, labeled


def test_fast_refinement_matches_original_bitforbit():
    """The windowed fast filter must produce an identical mask to the original."""
    orig, proc, puncta, cell, labeled = _make_refine_inputs(seed=1)
    slow = seg.puncta_refinement_filtering_func(
        orig, proc, puncta, cell, labeled, min_spot_radius=2)
    fast = seg.puncta_refinement_filtering_func_fast(
        orig, proc, puncta, cell, labeled, min_spot_radius=2)
    assert np.array_equal(slow, fast), "fast refinement diverged from original"


def test_refinement_output_within_cell():
    """Invariant: refined puncta never fall outside the cell mask."""
    orig, proc, puncta, cell, labeled = _make_refine_inputs(seed=2)
    refined = seg.puncta_refinement_filtering_func_fast(
        orig, proc, puncta, cell, labeled, min_spot_radius=2)
    assert not (refined & ~cell).any()


def test_refinement_is_subset_of_input():
    """Invariant: refinement only removes objects, never adds pixels."""
    orig, proc, puncta, cell, labeled = _make_refine_inputs(seed=3)
    refined = seg.puncta_refinement_filtering_func_fast(
        orig, proc, puncta, cell, labeled, min_spot_radius=2)
    assert not (refined & ~puncta).any()


# ---------------------------------------------------------------------------
# GOLDEN-MASTER (full segmentation object count) — reference TBD by maintainer.
# ---------------------------------------------------------------------------

# TODO(maintainer): once you settle on a trusted synthetic (or real) image and
# have run the full condensate segmentation on it, record the expected object
# count here to lock it in as a golden master. This guards the end-to-end
# pipeline against silent drift (e.g. from a skimage/scipy update). Left None
# because the full pipeline needs the napari viewer + cellpose, which are not
# available in CI — run it once interactively, paste the number, and enable.
GOLDEN_SEGMENTATION_OBJECT_COUNT = None


@pytest.mark.skipif(GOLDEN_SEGMENTATION_OBJECT_COUNT is None,
                    reason="Fill GOLDEN_SEGMENTATION_OBJECT_COUNT from a trusted run")
def test_full_segmentation_object_count_golden():
    """Characterization: full condensate segmentation on the fixed synthetic
    image yields the locked-in object count. Fill the constant to enable."""
    # NOTE: wire this to _segment_core (viewer-free) once you decide the
    # reference image and count. Left as a stub so the intent is recorded.
    pytest.skip("enable by setting GOLDEN_SEGMENTATION_OBJECT_COUNT and wiring _segment_core")
