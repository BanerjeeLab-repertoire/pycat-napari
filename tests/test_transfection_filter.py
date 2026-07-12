"""
The transfection filter must not depend on the camera pedestal.

Why this had never been tested
------------------------------
``filter_cells_by_transfection`` decides **which cells are analysed at all** — it runs before
everything else, so a mistake here is a selection effect on the entire dataset.

Until 1.5.415 its SNR was a bare **ratio**, ``mean_cell / background``. The camera pedestal adds
a constant to every pixel and carries no signal, but it appears in **both** the numerator and
the denominator, so it drags the ratio toward 1. With the same cells and the same true
expression:

==========  =========  ==========  ==========  ===========  ====================
pedestal    expr = 0   expr = 15   expr = 60   expr = 200   transfected fraction
==========  =========  ==========  ==========  ==========   ====================
0           drop       drop        **KEEP**    **KEEP**     0.50
100         drop       drop        drop        KEEP         0.25
**500**     drop       drop        drop        **drop**     **0.00**
**2000**    drop       drop        drop        **drop**     **0.00**
==========  =========  ==========  ==========  ==========   ====================

**On a camera with a 500-count pedestal, every transfected cell was called untransfected.**

The fix — a background-subtracted contrast, ``(mean_cell − background) / noise_sd`` — was
measured against synthetic data, but **nothing in the codebase exercised it**, because
``ts_cellpose_tools`` imported napari and Qt at module scope and could not be imported without
a GUI. (Neither this function nor ``apply_transfection_filter_to_stack`` uses a single Qt
symbol.) Decoupled in 1.5.441; this is the first test.
"""

import numpy as np
import pytest

# Four cells: one untransfected, three with real and unchanging expression.
_CELLS = [(50, 50, 0.0), (50, 150, 15.0), (150, 50, 60.0), (150, 150, 200.0)]


def _scene(pedestal, seed=0):
    rng = np.random.default_rng(seed)
    h = w = 200
    yy, xx = np.mgrid[0:h, 0:w]

    img = np.full((h, w), float(pedestal) + 20.0)
    mask = np.zeros((h, w), np.int32)
    for i, (cy, cx, expression) in enumerate(_CELLS, start=1):
        sel = ((yy - cy) ** 2 + (xx - cx) ** 2) < 400
        mask[sel] = i
        img[sel] += expression

    return mask, img + rng.normal(0, 5.0, (h, w))


@pytest.mark.core
@pytest.mark.parametrize("pedestal", [0, 100, 500, 2000])
def test_transfection_call_is_pedestal_invariant(pedestal):
    """The same cells must get the same verdict on any camera."""
    ts = pytest.importorskip("pycat.toolbox.ts_cellpose_tools")

    mask, img = _scene(pedestal)
    _kept, _dropped, df, fraction = ts.filter_cells_by_transfection(mask, img)

    verdicts = {int(r.cell_label): bool(r.transfected) for r in df.itertuples()}

    assert not verdicts[1], (
        f"cell 1 has ZERO expression and was called transfected (pedestal {pedestal})"
    )
    for label in (2, 3, 4):
        expression = _CELLS[label - 1][2]
        assert verdicts[label], (
            f"cell {label} (expression {expression:.0f} counts above background) was called "
            f"UNTRANSFECTED on a pedestal of {pedestal}. This is the 1.5.415 failure: a bare "
            f"mean/background RATIO is dragged toward 1 by the pedestal, so on a 500-count "
            f"sensor every transfected cell was rejected. The gate must be a CONTRAST — "
            f"(mean_cell - background) / noise_sd — which is pedestal-invariant."
        )

    assert fraction == pytest.approx(0.75), (
        f"transfected fraction {fraction:.2f} at pedestal {pedestal}, expected 0.75. Before "
        f"the fix it went 0.50 -> 0.25 -> 0.00 as the pedestal rose, on identical cells."
    )
