"""**Plot-backend decisions (backend_parity Parts 2–3, headless slice) — threshold, provenance, honest scope.**

The Qt interaction is deferred; these pin the pure decisions: an interactive scatter defaults to PyQtGraph
only above the threshold AND where it is available (matplotlib otherwise, and always for publication); the
render records which backend it used; and Plotly advertises click-to-napari only where QtWebEngine exists —
no dead affordance.
"""
import pytest

from pycat.utils.plot_backend_selection import (
    choose_scatter_backend, backend_provenance, plotly_interaction_scope,
    pyqtgraph_available, qtwebengine_available, MATPLOTLIB, PYQTGRAPH, DEFAULT_SCATTER_THRESHOLD)

pytestmark = pytest.mark.core


def test_below_threshold_is_matplotlib_even_when_pyqtgraph_is_available():
    assert choose_scatter_backend(100, pyqtgraph=True) == MATPLOTLIB
    assert choose_scatter_backend(DEFAULT_SCATTER_THRESHOLD, pyqtgraph=True) == MATPLOTLIB   # not strictly above


def test_above_threshold_prefers_pyqtgraph_only_when_available():
    assert choose_scatter_backend(50_000, pyqtgraph=True) == PYQTGRAPH
    assert choose_scatter_backend(50_000, pyqtgraph=False) == MATPLOTLIB   # unavailable → fall back, no crash


def test_the_threshold_is_configurable():
    assert choose_scatter_backend(1500, threshold=1000, pyqtgraph=True) == PYQTGRAPH
    assert choose_scatter_backend(1500, threshold=2000, pyqtgraph=True) == MATPLOTLIB


def test_backend_provenance_records_which_and_why():
    p = backend_provenance(PYQTGRAPH, 50_000, threshold=5000)
    assert p['backend'] == 'pyqtgraph' and p['n_points'] == 50_000 and 'above' in p['reason']
    q = backend_provenance(MATPLOTLIB, 100, threshold=5000)
    assert 'below' in q['reason']


def test_plotly_scope_is_honest_about_qtwebengine():
    assert plotly_interaction_scope(qtwebengine=True) == 'click'
    assert plotly_interaction_scope(qtwebengine=False) == 'hover_only'    # no dead click affordance


def test_availability_probes_return_a_bool_and_never_crash():
    assert isinstance(pyqtgraph_available(), bool)
    assert isinstance(qtwebengine_available(), bool)
