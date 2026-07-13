"""
**Plot → object → image.** And in batch, where the session is gone.

The two requirements, and why they are one mechanism
----------------------------------------------------
Gable asked for two things:

1. *"extensible so that as I write more plots this can be extended easily to them"*
2. *"batch a data set and select points in the resulting plot and see the data and bounded
   images"*

**The second one is what forces the design.** A point in a batch plot points at an object in an
image that **is not loaded**, produced by a segmentation that **is not in memory**. *"Highlight the
layer"* is not available.

So the identity a point carries cannot be a live reference. It has to be **serialisable**, and it
has to answer: *which file, which frame, which object, and where in the frame.*

**The bbox is the piece that makes it work.** With it, a batch plot reads the object's region
straight out of the file — **no reload of the full stack, and no re-segmentation.** Without it, the
only route back to the object is to redo the analysis.

And it is **free**: ``regionprops`` hands over ``prop.bbox`` at every segmentation site. **25 files
call regionprops; one keeps the bbox.** It is being discarded everywhere.

What already existed
--------------------
A complete three-way brushing hub is **already in ``vpt_ui``** — plot ↔ image ↔ table, keyed on
``track_id``, with a re-entrancy guard. It is well built, **and it is welded to VPT**: 2 of PyCAT's
15 plots are pickable and the other 13 are pictures.

So it is lifted out and keyed on an ``ObjectRef``, and a new plot joins by supplying its refs.
"""

import pathlib
import tempfile

import numpy as np
import pandas as pd
import pytest


def _batch_of_files(n_files=3, size=128):
    """Simulate a batch run: several files, each segmented, **keeping the bbox.**"""
    import skimage as sk
    import tifffile

    from pycat.utils.object_ref import bbox_columns_from_regionprops

    folder = pathlib.Path(tempfile.mkdtemp())
    yy, xx = np.mgrid[0:size, 0:size]

    rows = []
    for index in range(n_files):
        path = folder / f"cond_{index}.tif"
        rng = np.random.default_rng(index)

        image = np.full((size, size), 100.0)
        labels = np.zeros((size, size), np.int32)
        for i in range(4):
            cy, cx = rng.integers(20, size - 20, size=2)
            radius = int(rng.integers(6, 14))
            spot = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) < radius
            image[spot] = 800.0 + 200 * i
            labels[spot] = i + 1

        tifffile.imwrite(path, image.astype(np.float32))

        for prop in sk.measure.regionprops(labels, intensity_image=image):
            row = dict(source_path=str(path), label=int(prop.label),
                       area_um2=float(prop.area) * 0.01,
                       mean_intensity=float(prop.intensity_mean))
            row.update(bbox_columns_from_regionprops(prop))     # <-- the line that matters
            rows.append(row)

    return pd.DataFrame(rows)


@pytest.mark.core
def test_a_batch_plot_point_becomes_an_IMAGE_with_no_session():
    """**This is the requirement.** *"Select points in the resulting plot and see the bounded
    images"* — over a dataset that is **not loaded**.

    The crop is read out of the file, at the bbox the row carried. **No session, no reload of the
    full stack, no re-segmentation** — and the crop's peak intensity matches the value in the
    table, which is how we know it found the right object.
    """
    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    table = _batch_of_files()
    refs = ref_mod.refs_from_dataframe(table)

    assert all(ref.is_resolvable_offline() for ref in refs), (
        "every row of a batch result must be resolvable OFFLINE. A ref with a track_id and "
        "nothing else is fine interactively and USELESS in batch."
    )

    for index in (2, 5, 9):
        crop, message = ref_mod.resolve_offline(refs[index], pad_px=4)

        assert crop is not None, f"point {index} could not be turned back into an image: {message}"
        assert crop.max() == pytest.approx(table.iloc[index].mean_intensity, rel=0.01), (
            f"the crop for point {index} peaks at {crop.max():.0f}, but the table says that "
            f"object's intensity is {table.iloc[index].mean_intensity:.0f}. **The crop found the "
            f"wrong object.**"
        )


@pytest.mark.core
def test_a_ref_WITHOUT_a_bbox_says_WHY_it_cannot_be_resolved():
    """*"Nothing happened"* is the worst possible answer to a click.

    A point that carries a ``track_id`` and no bbox works **interactively** and cannot work in
    **batch** — and the user needs to be told that, not left clicking a dead plot.
    """
    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    ref = ref_mod.ObjectRef(object_id=3, track_id=7, source_path='/tmp/whatever.tif')  # no bbox

    crop, message = ref_mod.resolve_offline(ref)

    assert crop is None
    assert 'bounding box' in message and 'regionprops' in message, (
        f"the message must say WHY and what to do about it; got: {message!r}"
    )


@pytest.mark.core
def test_a_NEW_plot_becomes_brushable_in_ONE_line():
    """**The extensibility requirement.**

    A plot nobody has written yet becomes brushable by doing exactly two things::

        points = ax.scatter(x, y, picker=5)
        make_pickable(fig, points, refs_from_dataframe(df))

    **No hub edit, no registration, no callback plumbing.** The plot supplies the identity behind
    its points; the hub does the rest.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    ref_mod = pytest.importorskip("pycat.utils.object_ref")
    brushing = pytest.importorskip("pycat.utils.brushing")

    rng = np.random.default_rng(0)
    table = pd.DataFrame(dict(
        label=range(40), frame=rng.integers(0, 5, 40),
        bbox_y0=rng.integers(0, 90, 40), bbox_x0=rng.integers(0, 90, 40),
        area_um2=rng.lognormal(1, 0.5, 40),
        partition_coeff=rng.lognormal(1.2, 0.4, 40),
        source_path='/tmp/fake.tif'))
    table['bbox_y1'] = table.bbox_y0 + 20
    table['bbox_x1'] = table.bbox_x0 + 20

    figure, axis = plt.subplots()
    points = axis.scatter(table.area_um2, table.partition_coeff, picker=5)

    picked = []
    brushing.make_pickable(figure, points, ref_mod.refs_from_dataframe(table),
                           on_select=picked.append)

    class _PickEvent:
        artist = points
        ind = [17]

        class canvas:
            @staticmethod
            def draw_idle():
                pass

    figure.canvas.callbacks.process('pick_event', _PickEvent())

    assert picked, "clicking a point produced no selection"
    ref = picked[0]

    assert ref.object_id == 17, f"the click resolved to object {ref.object_id}, not 17"
    assert ref.frame == int(table.iloc[17].frame)
    assert ref.bbox == (int(table.iloc[17].bbox_y0), int(table.iloc[17].bbox_x0),
                        int(table.iloc[17].bbox_y1), int(table.iloc[17].bbox_x1))
    assert ref.is_resolvable_offline()

    # And the refs travel WITH the figure — so an export, a saved session or a batch report can
    # still answer "what is this point?".
    assert len(figure._pycat_object_refs) == 40


@pytest.mark.core
def test_the_hub_does_not_loop_when_a_view_echoes_a_selection():
    """**Without the re-entrancy guard, a click oscillates.**

    Each view calls ``select()`` with a ``source`` tag, and the hub updates the *other* views. The
    guard is what stops the highlight the hub triggers in view B from firing B's own emit and
    coming straight back. *(This design is lifted from ``vpt_ui``, which had it right.)*
    """
    ref_mod = pytest.importorskip("pycat.utils.object_ref")
    brushing = pytest.importorskip("pycat.utils.brushing")

    hub = brushing.SelectionHub()
    calls = {'plot': 0, 'table': 0}

    def _plot_view(ref):
        calls['plot'] += 1
        hub.select(ref, source='plot')          # a view that ECHOES — the loop risk

    def _table_view(ref):
        calls['table'] += 1
        hub.select(ref, source='table')

    hub.register_view('plot', _plot_view)
    hub.register_view('table', _table_view)

    hub.select(ref_mod.ObjectRef(object_id=1), source='plot')

    assert calls['plot'] == 0, "the initiating view must NOT be called back — that is the loop"
    assert calls['table'] == 1, "the other view must be updated exactly once"


@pytest.mark.core
def test_regionprops_bbox_is_kept_not_discarded():
    """**25 files call regionprops. One keeps the bbox.**

    Every results table that discards it is a table whose rows **cannot be turned back into an
    image** — and that is the difference between a plot you can click and a plot you can only look
    at.
    """
    import skimage as sk

    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    labels = np.zeros((64, 64), np.int32)
    labels[10:20, 30:45] = 1

    prop = sk.measure.regionprops(labels)[0]
    columns = ref_mod.bbox_columns_from_regionprops(prop)

    assert columns == dict(bbox_y0=10, bbox_x0=30, bbox_y1=20, bbox_x1=45), (
        f"the bbox columns are wrong: {columns}"
    )
