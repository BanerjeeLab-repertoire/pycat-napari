"""**VPT results plots reflow when the dock is resized (results_figure_reflow Part 1).**

Reported by Shamli Manasvi: the 2×2 microrheology plots were authored at a fixed 11×8.5 print size and laid
out ONCE at draw (`tight_layout`), so dragging the dock wider stretched the canvas but never re-ran the
layout — squashed, overlapping axes that "won't stretch". The fix is a constrained-layout figure, which
recomputes subplot geometry on every resize. These pin the mechanism headlessly (matplotlib Agg, no Qt): the
figure factory uses constrained layout, its axes MOVE on resize (the core regression — test the resize, not
the initial draw), and it no longer ships the fixed print size.
"""
import pytest

pytestmark = pytest.mark.base


def test_the_results_figure_uses_constrained_layout():
    from pycat.toolbox.vpt.results_dock import _new_results_figure
    fig, axes = _new_results_figure()
    engine = fig.get_layout_engine()
    assert engine is not None and "constrained" in type(engine).__name__.lower()
    assert axes.shape == (2, 2)


def test_axes_geometry_reflows_on_resize():
    """The core regression: a resize must MOVE the axes. A one-shot layout would leave them fixed — which is
    exactly the reported 'I keep changing the box but it won't stretch' symptom."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from pycat.toolbox.vpt.results_dock import _new_results_figure

    fig, axes = _new_results_figure()
    canvas = FigureCanvasAgg(fig)
    fig.set_size_inches(4.0, 3.0)
    canvas.draw()
    before = axes[0, 0].get_position().bounds
    fig.set_size_inches(12.0, 8.0)
    canvas.draw()
    after = axes[0, 0].get_position().bounds
    assert before != after, "axes did not reflow on resize — the reported 'won't stretch' bug"


def test_the_figure_is_not_authored_at_the_fixed_print_size():
    # the bug was a hardcoded 11×8.5 print figure; the canvas should drive the size instead
    from pycat.toolbox.vpt.results_dock import _new_results_figure
    fig, _ = _new_results_figure()
    assert tuple(fig.get_size_inches()) != (11.0, 8.5)
