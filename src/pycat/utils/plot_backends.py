"""
**One plotting interface. Three backends. The same brushing.**

Gable: *"integrate plotly and seaborn as well and make them available and addressable in the same
ways as matplotlib."*

What "addressable in the same way" actually requires
---------------------------------------------------
Brushing (1.5.494–496) needs exactly three things from a plot:

1. an **artist** whose elements map 1:1 to the DataFrame's rows
2. a **pick event** that reports *which element* was clicked — i.e. a row index
3. somewhere to attach the **ObjectRefs**, so a click can be resolved to an image

Each library delivers those differently, and **the differences are not cosmetic**:

============  ==========================================  =================================
backend       how a click reports a row                   what it costs
============  ==========================================  =================================
matplotlib    ``mpl_connect('pick_event')`` → ``ind``     nothing; it works today
seaborn       **it IS matplotlib** — same canvas,          nothing; the artist has to be
              same event                                   found inside the Axes it built
plotly        a **JavaScript** callback in a browser      **a Python↔JS bridge**
============  ==========================================  =================================

**Seaborn is nearly free.** It is a matplotlib front end: ``sns.scatterplot`` draws into an Axes,
and the pick event is the same one. The only real work is *finding* the artist it made — and
**verifying that its points are still in DataFrame order**, which this module does rather than
assume (see ``_verify_row_order``).

**Plotly is not.** It renders to HTML/JS. A click inside it cannot reach napari without a
``QWebEngineView`` and a ``QWebChannel`` — a **heavy optional dependency** and a **real Qt risk**
in an app that already has a user hitting OpenGL/Qt rendering failures.

So plotly is integrated **honestly**:

* **Interactive exploration works fully** — zoom, pan, legend filtering, and **hover that carries
  the object's identity**, so the user can *see* which object a point is **without any bridge**.
* **Click → napari is an optional upgrade.** It engages only if ``QtWebEngine`` is present, and
  **says so plainly when it is not** — rather than silently doing nothing, which is the failure
  mode that makes people think a feature is broken.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log


BACKENDS = ('matplotlib', 'seaborn', 'plotly', 'pyqtgraph')


def available_backends():
    """The backends that can actually be used **right now**, and why the others cannot."""
    status = {}

    try:
        import matplotlib  # noqa: F401
        status['matplotlib'] = (True, '')
    except Exception as exc:
        status['matplotlib'] = (False, f'matplotlib is not importable: {exc}')

    try:
        import seaborn  # noqa: F401
        status['seaborn'] = (True, '')
    except Exception as exc:
        status['seaborn'] = (False, f'seaborn is not installed: {exc}')

    try:
        import plotly  # noqa: F401
        try:
            from qtpy import QtWebEngineWidgets  # noqa: F401
            status['plotly'] = (True, '')
        except Exception:
            status['plotly'] = (
                True,
                "**Plotly is available, and clicking a point will not reach napari.** That needs "
                "QtWebEngine, which is not installed. Zoom, pan, legend filtering and "
                "hover-with-identity all work — the hover shows which object each point is, so "
                "the identity is visible even without the click.")
    except Exception as exc:
        status['plotly'] = (False, f'plotly is not installed: {exc}')

    from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_available
    if pyqtgraph_available():
        status['pyqtgraph'] = (True, '')
    else:
        status['pyqtgraph'] = (False, 'pyqtgraph is not installed: pip install pycat-napari[pyqtgraph]')

    return status


# ── The one thing every backend must get right ───────────────────────────────────────────

def _verify_row_order(artist_xy, df, x_col, y_col, tolerance=1e-9):
    """**Do the artist's points still correspond, in order, to the DataFrame's rows?**

    This is the question that decides whether brushing is correct or a **silent lie**. If a
    library reorders, groups or drops rows while drawing, then *"point 3"* is not *"row 3"* — and
    a click resolves to **the wrong object**, lands, and says nothing.

    It is not hypothetical: PyCAT's own ``plot_focus_diagnostic`` groups by interpretation and
    draws each group as a separate artist, and a naive index map there **would** have pointed at
    the wrong condensate.

    Modern seaborn keeps one artist in DataFrame order even under ``hue`` — **verified, not
    assumed**. This function is what keeps that verified: if a future version changes, brushing
    **refuses** rather than misleads.
    """
    if artist_xy is None or len(artist_xy) != len(df):
        return False, (
            f"the plot has {0 if artist_xy is None else len(artist_xy)} points and the table has "
            f"{len(df)} rows. **They do not correspond**, so a click cannot be resolved to a row "
            f"— it would land on whichever object happened to sit at that index.")

    try:
        expected_x = np.asarray(df[x_col], dtype=float)
        expected_y = np.asarray(df[y_col], dtype=float)
        drawn = np.asarray(artist_xy, dtype=float)

        finite = np.isfinite(expected_x) & np.isfinite(expected_y)
        if not finite.any():
            return True, ''          # nothing to check against

        dx = np.abs(drawn[finite, 0] - expected_x[finite])
        dy = np.abs(drawn[finite, 1] - expected_y[finite])

        scale_x = max(float(np.nanmax(np.abs(expected_x[finite]))), 1.0)
        scale_y = max(float(np.nanmax(np.abs(expected_y[finite]))), 1.0)

        if (dx > tolerance * scale_x * 1e6).any() or (dy > tolerance * scale_y * 1e6).any():
            return False, (
                "**The plotted points are not in DataFrame order.** The library reordered them "
                "while drawing, so point N is not row N — and a click would resolve to the wrong "
                "object, land, and say nothing.")
    except Exception as exc:
        debug_log('plot_backends: could not verify the row order', exc)
        return False, "The row order could not be verified, so brushing is refused."

    return True, ''


# ── matplotlib and seaborn: the same canvas, the same event ──────────────────────────────

def scatter(df, x_col, y_col, *, backend='matplotlib', ax=None, hue=None, **kwargs):
    """A scatter whose points are **guaranteed** to correspond, in order, to ``df``'s rows.

    Returns ``(figure, artist, ok, message)``. **``ok`` is False when the points cannot be trusted
    to map to rows**, and brushing must not be wired — because a click that lands on the wrong
    object is worse than one that does nothing.

    For ``backend='pyqtgraph'`` the first element is a Qt ``PlotWidget`` (not a matplotlib figure) and
    the second a ``ScatterPlotItem`` — the same 4-tuple shape, and the same row-order guarantee.
    """
    if backend == 'pyqtgraph':
        from pycat.utils.plot_backend_pyqtgraph import pyqtgraph_available, pyqtgraph_scatter
        if not pyqtgraph_available():
            return None, None, False, ("pyqtgraph is not installed — the interactive backend needs "
                                       "it (pip install pycat-napari[pyqtgraph]).")
        return pyqtgraph_scatter(df, x_col, y_col, hue=hue, title=kwargs.get('title'))

    import matplotlib.pyplot as plt

    if ax is None:
        figure, ax = plt.subplots()
    else:
        figure = ax.figure

    if backend == 'seaborn':
        import seaborn as sns
        # Seaborn draws into the Axes and returns it. The artist is the collection it added.
        before = set(id(c) for c in ax.collections)
        sns.scatterplot(data=df, x=x_col, y=y_col, hue=hue, ax=ax, picker=5, **kwargs)
        new = [c for c in ax.collections if id(c) not in before]

        if len(new) != 1:
            # More than one artist means seaborn SPLIT the data (by hue, style, size). Then an
            # artist index is an index into a SUBSET, not into the table — and resolving it
            # against the table would point at the wrong object.
            return figure, None, False, (
                f"Seaborn drew {len(new)} separate artists (it split the data, probably by hue). "
                f"**An index into one of them is not an index into the table**, so a click would "
                f"resolve to the wrong object. Plot without the grouping to brush, or brush the "
                f"groups separately.")
        artist = new[0]
        drawn = artist.get_offsets()

    else:
        artist = ax.scatter(df[x_col], df[y_col], picker=5, **kwargs)
        drawn = artist.get_offsets()

    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)

    ok, message = _verify_row_order(drawn, df, x_col, y_col)
    return figure, (artist if ok else None), ok, message


# ── plotly: exploration always; the click only with a bridge ──────────────────────────────

def plotly_scatter(df, x_col, y_col, *, refs=None, hue=None, title=None):
    """**Plotly, with the object identity in the hover — so it is visible without a bridge.**

    A click inside a plotly figure lives in JavaScript. Getting it back to napari needs a
    ``QWebEngineView`` and a ``QWebChannel``, which is a **heavy optional dependency** and a real
    risk in an app that already has Qt/OpenGL trouble on at least one machine.

    So the identity is put where it **does** work with no bridge at all: **the hover text.** The
    user moves the mouse over a point and sees *which object it is* — its label, its frame, and the
    file it came from. That is most of the value of brushing, and it costs nothing.

    Returns the plotly Figure. ``show()`` it, or embed it if QtWebEngine is available.

    .. warning::

       **This path is NOT verified end-to-end.** The sandbox this was written in has no network,
       so plotly could not be installed and the figure could not actually be built and inspected.

       The matplotlib and seaborn paths **were** verified — including the row-order check catching
       a deliberately reordered artist, and seaborn keeping one artist in DataFrame order under
       ``hue`` (**tested, not assumed**).

       This one is written from the plotly API and is **structurally straightforward** (a
       ``px.scatter`` with ``hover_data``), but *"it should work"* is not the same as *"it was
       run"*. **The first thing to do with it is run it.** The test
       ``test_the_plotly_hover_carries_the_object_identity`` will skip until plotly is installed,
       and will exercise it the moment it is.
    """
    import plotly.express as px

    frame = df.copy()

    # ── The identity goes into the HOVER ────────────────────────────────────────
    #
    # This is the part that makes plotly "addressable in the same way" without a JS bridge: the
    # point still knows what it is, and the user can still see it.
    hover_columns = []
    if refs is not None and len(refs) == len(frame):
        frame['_object'] = [r.object_id for r in refs]
        frame['_frame'] = [r.frame for r in refs]
        frame['_source'] = [
            (r.source_path.split('/')[-1].split('\\')[-1] if r.source_path else None)
            for r in refs]
        hover_columns = ['_object', '_frame', '_source']
    else:
        for candidate in ('label', 'object_id', 'track_id', 'cell_label', 'frame'):
            if candidate in frame.columns:
                hover_columns.append(candidate)

    figure = px.scatter(frame, x=x_col, y=y_col, color=hue,
                        hover_data=hover_columns or None,
                        title=title)

    # The refs travel with the figure, so anything downstream can still answer "what is this
    # point?" — including a QtWebEngine bridge, if one is ever added.
    if refs is not None:
        try:
            figure._pycat_object_refs = list(refs)
        except Exception:
            pass

    return figure
