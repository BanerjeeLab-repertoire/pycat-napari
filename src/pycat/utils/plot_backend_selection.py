"""**Which plot backend, and what a backend can honestly offer — the decisions, no Qt.**

Plot-backend parity (audit §4) wants PyQtGraph for high-performance *interactive* scatter above a size
threshold, matplotlib kept as the canonical *publication* backend, and Plotly scoped honestly (full
click-to-napari only where QtWebEngine exists; otherwise hover/identity, and no dead click affordance). The
substance of that is Qt interaction, but the *decisions* are small and pure — the threshold choice, the
record of which backend rendered, and the honest capability scope. Those live here, Qt-free and testable, so
the interactive layers dispatch on one agreed answer.
"""
from __future__ import annotations

import importlib.util

MATPLOTLIB = 'matplotlib'
PYQTGRAPH = 'pyqtgraph'

#: Above this many points, an INTERACTIVE scatter defaults to PyQtGraph (matplotlib below it). Configurable;
#: matplotlib remains the publication backend regardless of this.
DEFAULT_SCATTER_THRESHOLD = 5000


def _spec_available(name) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:      # broad-ok: optional_probe — a weird import environment just means 'not available'
        return False


def pyqtgraph_available() -> bool:
    return _spec_available('pyqtgraph')


def qtwebengine_available() -> bool:
    """QtWebEngine backs Plotly's full click-to-napari. Try both Qt bindings PyCAT may use."""
    return _spec_available('PyQt5.QtWebEngineWidgets') or _spec_available('qtpy.QtWebEngineWidgets')


def choose_scatter_backend(n_points, *, threshold=DEFAULT_SCATTER_THRESHOLD, pyqtgraph=None) -> str:
    """The backend for an INTERACTIVE scatter of ``n_points``: ``'pyqtgraph'`` above ``threshold`` where it is
    available (high-performance interaction), else ``'matplotlib'``. Matplotlib stays the canonical
    PUBLICATION backend — this decides interactive exploration only. ``pyqtgraph`` overrides the availability
    probe (for tests / a forced choice)."""
    available = pyqtgraph_available() if pyqtgraph is None else bool(pyqtgraph)
    if int(n_points) > int(threshold) and available:
        return PYQTGRAPH
    return MATPLOTLIB


def backend_provenance(backend, n_points, *, threshold=DEFAULT_SCATTER_THRESHOLD) -> dict:
    """A record of WHICH backend rendered and why — so a figure/session can say how it was drawn (the audit's
    'record which backend rendered')."""
    return {'backend': str(backend), 'n_points': int(n_points), 'threshold': int(threshold),
            'reason': ('above interactive threshold' if int(n_points) > int(threshold) else 'below threshold')}


def plotly_interaction_scope(qtwebengine=None) -> str:
    """What a Plotly view can HONESTLY offer: ``'click'`` (full click-to-napari) only where QtWebEngine is
    present; otherwise ``'hover_only'`` — identity-bearing hover, but no dead click affordance. ``qtwebengine``
    overrides the probe."""
    have = qtwebengine_available() if qtwebengine is None else bool(qtwebengine)
    return 'click' if have else 'hover_only'
