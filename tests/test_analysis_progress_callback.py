"""**Progress part 2, Part C — the per-cell analysis loops actually drive a determinate callback.**

The AST ratchet in `test_progress_analysis_half.py` pins that `cell_analysis_func` /
`puncta_analysis_func` accept `progress_callback` and that the runners route through the modal off-thread
runner. This is the FUNCTIONAL proof that the callback is real:

- it is invoked with `(done, total)` over the countable per-cell loop, ending at `(total, total)`;
- `progress_callback=None` is a complete no-op — the result is byte-identical to a callback run, so a
  headless/batch caller is unaffected.

Both are the contract the modal progress dialog depends on: a bar with nothing driving it is the freeze
this work removes.
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.base


def _data_instance():
    from pycat.data.data_modules import BaseDataClass
    dc = BaseDataClass()
    dc.data_repository['microns_per_pixel_sq'] = 1
    # A small cell_diameter so the tiny synthetic cells survive cell_analysis_func's min-area filter
    # (min_area scales with (diameter/2)²; the default 100 would drop them).
    dc.data_repository['cell_diameter'] = 10
    dc.data_repository['cell_df'] = pd.DataFrame({'label': [1, 2]})
    return dc


def _synthetic():
    """Two labelled cells, each holding one punctum, on a dim intensity image. Cells are large enough to
    survive the contour/opening morphology in cell_analysis_func."""
    labeled_cells = np.zeros((60, 60), dtype=int)
    labeled_cells[5:27, 5:27] = 1        # 22×22 — survives 7-iter opening
    labeled_cells[33:55, 33:55] = 2
    puncta = np.zeros((60, 60), dtype=bool)
    puncta[13:18, 13:18] = True          # inside cell 1
    puncta[41:46, 41:46] = True          # inside cell 2
    rng = np.random.default_rng(0)
    image = rng.uniform(10, 20, (60, 60)).astype(float)
    image[puncta] += 200.0
    return labeled_cells, puncta, image


def test_puncta_analysis_reports_progress_over_the_per_cell_loop():
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func
    labeled_cells, puncta, image = _synthetic()

    calls = []
    puncta_analysis_func(puncta, image, labeled_cells, _data_instance(),
                         progress_callback=lambda done, total: calls.append((done, total)))

    assert calls, "the per-cell loop never reported progress"
    assert all(t == 2 for _, t in calls), "total must be the cell count (2)"
    assert calls[-1] == (2, 2), "the last update must reach 100% (done == total)"


def test_progress_callback_None_is_a_NO_OP_with_an_identical_result():
    """Headless/batch callers pass no callback — the output must be byte-identical to a callback run."""
    from pycat.toolbox.feature_analysis_tools import puncta_analysis_func
    labeled_cells, puncta, image = _synthetic()

    with_cb = puncta_analysis_func(puncta, image, labeled_cells, _data_instance(),
                                   progress_callback=lambda d, t: None)
    without = puncta_analysis_func(puncta, image, labeled_cells, _data_instance(),
                                   progress_callback=None)
    assert np.array_equal(with_cb, without), "the callback changed the computed result — it must not"


def test_cell_analysis_reports_progress_and_None_is_a_no_op():
    from pycat.toolbox.feature_analysis_tools import cell_analysis_func
    labeled_cells, _, image = _synthetic()

    calls = []
    labeled_a, _ = cell_analysis_func(image, labeled_cells, None, _data_instance(),
                                      progress_callback=lambda done, total: calls.append((done, total)))
    labeled_b, _ = cell_analysis_func(image, labeled_cells, None, _data_instance(),
                                      progress_callback=None)

    assert calls and calls[-1] == (2, 2), "the per-cell loop must report progress ending at (2, 2)"
    assert np.array_equal(labeled_a, labeled_b), "progress reporting must not change the labelling"
