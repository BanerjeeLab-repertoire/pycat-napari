"""
**One plotting interface. Three backends. The same brushing — or an honest refusal.**

What "addressable in the same way" actually requires
---------------------------------------------------
Brushing needs three things from a plot: an **artist** whose elements map 1:1 to the DataFrame's
rows, a **pick event** that reports *which* element was clicked, and somewhere to hang the
**ObjectRefs**.

The libraries deliver those very differently, and **the differences are not cosmetic**:

============  =========================================  ==============================
backend       how a click reports a row                  what it costs
============  =========================================  ==============================
matplotlib    ``mpl_connect('pick_event')`` → ``ind``    nothing
seaborn       **it IS matplotlib** — same canvas,        nothing; the artist must be
              same event                                 found inside the Axes
plotly        a **JavaScript** callback in a browser     **a Python↔JS bridge**
============  =========================================  ==============================

The one thing every backend must get right
------------------------------------------
**Do the artist's points still correspond, in order, to the DataFrame's rows?**

If a library reorders, groups or drops rows while drawing, then *"point 3"* is not *"row 3"* — and
a click resolves to **the wrong object, lands, and says nothing.** That is not hypothetical:
PyCAT's own ``plot_focus_diagnostic`` groups by interpretation and draws each group as a separate
artist, and a naive index map there **would** have pointed at the wrong condensate.

So the order is **verified at wire time, not assumed** — and when it cannot be trusted, **brushing
is refused.**
"""

import numpy as np
import pandas as pd
import pytest


def _table():
    return pd.DataFrame(dict(
        x=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        y=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        grp=['a', 'b', 'a', 'b', 'a', 'b'],
        label=[101, 102, 103, 104, 105, 106]))


@pytest.mark.core
def test_a_reordered_artist_is_REFUSED_not_silently_mapped():
    """**This is the safety property that makes multi-backend brushing possible at all.**

    A click that lands on the wrong object is worse than a click that does nothing: **it lands**,
    and nothing says so. So if the drawn points are not in DataFrame order, brushing refuses.
    """
    backends = pytest.importorskip("pycat.utils.plot_backends")

    table = _table()

    in_order = np.array([[table.x[i], table.y[i]] for i in range(6)])
    ok, _ = backends._verify_row_order(in_order, table, 'x', 'y')
    assert ok, "points drawn in DataFrame order must be accepted"

    shuffled = np.array([[table.x[i], table.y[i]] for i in [3, 1, 5, 0, 4, 2]])
    ok, message = backends._verify_row_order(shuffled, table, 'x', 'y')

    assert not ok, (
        "a REORDERED artist was accepted. Point N would resolve to row N, which is a DIFFERENT "
        "object — and the click would land on it silently."
    )
    assert 'not in DataFrame order' in message

    too_few = np.array([[table.x[i], table.y[i]] for i in range(3)])
    ok, message = backends._verify_row_order(too_few, table, 'x', 'y')
    assert not ok and 'do not correspond' in message


@pytest.mark.core
@pytest.mark.parametrize("backend", ['matplotlib', 'seaborn'])
def test_the_matplotlib_and_seaborn_scatters_map_1_to_1_to_the_rows(backend):
    """**Seaborn IS matplotlib.** Same canvas, same pick event — so the brushing is the same.

    The only real work is *finding* the artist inside the Axes it built, and **checking that its
    points are still in DataFrame order.**
    """
    import matplotlib
    matplotlib.use('Agg')

    backends = pytest.importorskip("pycat.utils.plot_backends")
    if backend == 'seaborn':
        pytest.importorskip("seaborn")

    table = _table()
    _figure, artist, ok, message = backends.scatter(table, 'x', 'y', backend=backend)

    assert ok, f"the {backend} scatter could not be trusted to map to rows: {message}"
    assert artist is not None

    drawn = np.asarray(artist.get_offsets())
    assert len(drawn) == len(table)
    assert np.allclose(drawn[:, 0], table.x.values), (
        f"{backend} drew the points out of order — a click would resolve to the wrong object"
    )


@pytest.mark.core
def test_seaborn_hue_keeps_ONE_artist_in_row_order():
    """**Verified, not assumed.**

    A grouping backend *could* split the data into one artist per hue level — and then an index
    into one artist is an index into a **subset**, not into the table. Modern seaborn does not:
    it keeps one collection, in DataFrame order.

    **This test is what keeps that verified.** If a future seaborn changes, it fails here rather
    than silently pointing every click at the wrong object.
    """
    import matplotlib
    matplotlib.use('Agg')

    backends = pytest.importorskip("pycat.utils.plot_backends")
    pytest.importorskip("seaborn")

    table = _table()
    _figure, artist, ok, message = backends.scatter(
        table, 'x', 'y', backend='seaborn', hue='grp')

    if not ok:
        # A future seaborn that splits by hue lands here — and REFUSING is the correct behaviour.
        assert 'separate artists' in message or 'not in DataFrame order' in message, (
            f"seaborn's hue plot was refused for an unexpected reason: {message}"
        )
        return

    drawn = np.asarray(artist.get_offsets())
    assert np.allclose(drawn[:, 0], table.x.values), (
        "seaborn kept one artist under hue but REORDERED it — this is the dangerous case, and "
        "the row-order check must catch it"
    )


@pytest.mark.core
def test_the_plotly_hover_carries_the_object_identity():
    """**Plotly's click lives in JavaScript.** So the identity goes in the hover instead.

    Reaching napari from a plotly click needs a ``QWebEngineView`` and a ``QWebChannel`` — a heavy
    dependency and a **real Qt risk** in an app that already has a user hitting OpenGL/Qt
    rendering failures.

    So the identity is put where it works **with no bridge at all**: the user hovers a point and
    sees *which object it is* — its label, its frame, the file it came from. **That is most of the
    value of brushing, and it costs nothing.**

    .. note::

       **This path was never run.** The sandbox it was written in has no network, so plotly could
       not be installed. The matplotlib and seaborn paths WERE verified. *"It should work" is not
       "it was run"* — and this test exercises it the moment plotly is present.
    """
    # NOTE: the core runner's `importorskip` deliberately RAISES rather than skipping — a missing
    # dependency silently hiding a test is how five of eight tests went unchecked once
    # (tools/run_core_tests.py). So plotly's absence is handled explicitly, and ONLY here, because
    # plotly is a genuinely OPTIONAL backend.
    try:
        import plotly  # noqa: F401
    except ImportError:
        pytest.skip("plotly is an optional backend and is not installed")

    backends = pytest.importorskip("pycat.utils.plot_backends")
    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    table = pd.DataFrame(dict(
        label=[1, 2, 3], area_um2=[1.0, 2.0, 3.0], partition_coeff=[2.0, 3.0, 4.0],
        bbox_y0=[0, 20, 40], bbox_x0=[0, 20, 40],
        bbox_y1=[10, 30, 50], bbox_x1=[10, 30, 50],
        source_path='/data/movie.tif'))

    refs = ref_mod.refs_from_dataframe(table)
    figure = backends.plotly_scatter(table, 'area_um2', 'partition_coeff', refs=refs)

    assert len(figure._pycat_object_refs) == 3, (
        "the refs must travel with the figure, so a bridge — if one is ever added — has "
        "something to resolve"
    )

    custom = np.asarray(figure.data[0].customdata)
    assert custom.shape[0] == 3, "every point must carry its identity in the hover"
    assert list(custom[:, 0]) == [1, 2, 3], (
        f"the hover must name the OBJECT each point is; got {custom[:, 0]}"
    )


@pytest.mark.core
def test_an_unavailable_backend_says_WHY():
    """**An option that silently fails is worse than one that is not there.**"""
    backends = pytest.importorskip("pycat.utils.plot_backends")

    status = backends.available_backends()

    assert status['matplotlib'][0], "matplotlib is a hard dependency"

    for name, (ok, message) in status.items():
        if not ok:
            assert message, f"'{name}' is unavailable and gives no reason"

    # And plotly, when present WITHOUT QtWebEngine, must say that the click will not work —
    # rather than letting the user click and wonder why nothing happens.
    ok, message = status['plotly']
    if ok and message:
        assert 'QtWebEngine' in message and 'hover' in message, (
            f"plotly-without-a-bridge must explain what does and does not work; got: {message!r}"
        )
