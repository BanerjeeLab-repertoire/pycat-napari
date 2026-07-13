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


@pytest.mark.core
def test_a_grouped_scatter_needs_PER_GROUP_refs_or_it_resolves_to_the_WRONG_OBJECT():
    """**The silent mis-indexing trap**, and it would never be noticed.

    ``plot_focus_diagnostic`` draws its scatter **per group** (bright / sharp-dim / blurry-dim).
    matplotlib reports the index **within the picked artist** — and each group is its own artist.

    A single flat list of refs would therefore be mis-indexed: clicking the **third green point**
    resolves to the **third row of the whole table**, which is a **different object**. The click
    would open an image, the image would look plausible, and it would be the wrong object.

    Measured on a real grouped scatter: the click resolves to **object 4** with per-group refs,
    and would have given **object 1** with a flat list.
    """
    import matplotlib
    matplotlib.use('Agg')

    plots = pytest.importorskip("pycat.toolbox.analysis_plots")

    rng = np.random.default_rng(0)
    n = 30
    table = pd.DataFrame(dict(
        label=range(n),
        intensity_ratio=rng.uniform(0.3, 1.2, n),
        sharpness_ratio=rng.uniform(0.4, 1.1, n),
        interpretation=rng.choice(['bright',
                                   'sharp_dim (likely nucleation/growth)',
                                   'blurry_dim (likely below focus)'], n),
        bbox_y0=rng.integers(0, 80, n), bbox_x0=rng.integers(0, 80, n)))
    table['bbox_y1'] = table.bbox_y0 + 16
    table['bbox_x1'] = table.bbox_x0 + 16

    picked = []
    figure = plots.plot_focus_diagnostic(table, source_path='/tmp/x.tif',
                                         on_select=picked.append)

    first_group_name = sorted(table.interpretation.unique())[0]
    first_group = table[table.interpretation == first_group_name]

    class _PickEvent:
        artist = figure.axes[0].collections[0]
        ind = [1]

        class canvas:
            @staticmethod
            def draw_idle():
                pass

    figure.canvas.callbacks.process('pick_event', _PickEvent())

    assert picked, "the grouped scatter is not pickable"
    ref = picked[0]

    expected = first_group.iloc[1]
    assert ref.object_id == int(expected.label), (
        f"the click resolved to object {ref.object_id}; the second point of that GROUP is object "
        f"{int(expected.label)}. A flat ref list would give object {int(table.iloc[1].label)} — "
        f"**the wrong object, silently.**"
    )


@pytest.mark.core
def test_the_bbox_survives_regionprops_table_into_an_ObjectRef():
    """**The main cell and puncta tables go through ``regionprops_table``, not a loop.**

    skimage expands ``'bbox'`` into ``bbox-0..bbox-3`` — hyphenated names that are awkward in a
    DataFrame (``df.bbox-0`` is a subtraction). They are renamed once, where they are produced, to
    the ``bbox_y0..bbox_x1`` that ``ObjectRef`` reads.
    """
    import skimage as sk

    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    labels = np.zeros((64, 64), np.int32)
    labels[10:20, 30:45] = 1
    image = np.where(labels > 0, 900.0, 100.0)

    table = pd.DataFrame(sk.measure.regionprops_table(
        labels, intensity_image=image, properties=('label', 'area', 'bbox')))

    assert 'bbox-0' in table.columns, "skimage's raw column naming has changed"

    table = ref_mod.normalise_bbox_columns(table)
    assert 'bbox_y0' in table.columns and 'bbox-0' not in table.columns

    ref = ref_mod.ObjectRef.from_row(table.iloc[0], source_path='/tmp/x.tif')
    assert ref.bbox == (10, 30, 20, 45), f"the bbox came through as {ref.bbox}"
    assert ref.is_resolvable_offline()


# ── The bbox sweep must STAY complete ─────────────────────────────────────────────────────

@pytest.mark.core
def test_a_ref_points_at_the_OBJECT_and_not_at_its_PARENT():
    """**A ref that points at the wrong object is worse than one that points at nothing.**

    The click **lands** — on the wrong thing — and nothing says so.

    A first version listed ``cell_label`` as a fallback for ``object_id``. On a puncta table,
    whose column is ``punctum_label``, that fallback fired: **four different puncta all came back
    as object 1**, because they all live in cell 1.

    The object's own identity and its parent's are **different questions**, and they get different
    fields.
    """
    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    table = pd.DataFrame([
        dict(punctum_label=1, cell_label=1, bbox_y0=16, bbox_x0=16, bbox_y1=25, bbox_x1=25),
        dict(punctum_label=2, cell_label=1, bbox_y0=16, bbox_x0=40, bbox_y1=25, bbox_x1=49),
        dict(punctum_label=3, cell_label=1, bbox_y0=40, bbox_x0=16, bbox_y1=49, bbox_x1=25),
    ])

    refs = ref_mod.refs_from_dataframe(table, source_path='/tmp/x.tif')
    object_ids = [ref.object_id for ref in refs]

    assert len(set(object_ids)) == 3, (
        f"three different puncta resolved to object ids {object_ids}. They all live in cell 1, "
        f"and the ref is reporting the CELL's id — so every click would land on the same object."
    )
    assert all(ref.parent_id == 1 for ref in refs), (
        "the parent (the cell) must still be recorded — it is a different question, not a "
        "competing answer"
    )


@pytest.mark.core
def test_the_per_object_results_tables_KEEP_the_bbox():
    """**24 of 25 regionprops call sites were discarding it.**

    A per-object results table without a bbox is a table whose rows **cannot be turned back into
    an image** — which is the difference between a plot you can click and a plot you can only look
    at. In **batch** it is the only route back to the object at all, because the layer is gone.

    This test reads the source, so a **new** per-object table that forgets the bbox is caught at
    the moment it is written.

    *(Per-FRAME and per-CELL aggregates are correctly excluded: a row that summarises forty
    objects has no single object to point at, and giving it a bbox would be a lie.)*
    """
    import ast

    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    # The per-object tables — a `prop` is in scope where the row is built, so there IS a single
    # object to point at.
    should_keep_bbox = {
        'spatial_metrology_tools.py',
        'label_and_mask_tools.py',
        'brightfield_tools.py',
        'dynamic_spatial_tools.py',
        'morphological_complexity_tools.py',
        'condensate_physics_tools.py',
        'zstack_segmentation_tools.py',
    }

    missing = []
    for name in sorted(should_keep_bbox):
        path = toolbox / name
        if not path.exists():
            continue
        source = path.read_text(encoding='utf-8', errors='ignore')
        if 'regionprops' not in source:
            continue
        if 'bbox' not in source:
            missing.append(name)

    assert not missing, (
        f"these modules build per-object results tables from regionprops and keep NO bbox: "
        f"{missing}. Their rows cannot be turned back into an image — regionprops provides the "
        f"bbox free, and discarding it is what makes a plot unclickable."
    )


# ── The PlottingWidget is the natural wiring point ────────────────────────────────────────

@pytest.mark.core
def test_a_scatter_of_a_per_object_table_is_brushable_and_an_AGGREGATE_is_not():
    """**The widget wires itself, and it declines when a point is not an object.**

    ``PlottingWidget`` lets the user pick **any** results DataFrame and **any** two columns. When a
    row of that table is one object — which every per-object table now is (1.5.495) — **each point
    IS an object**, and the click means something.

    **When a row is an aggregate, it is not.** A per-frame summary row averages forty objects;
    there is no single object to point at. The widget **declines silently** rather than wiring a
    click that would land somewhere arbitrary.

    *A click that lands on the wrong object is worse than a click that does nothing — it lands,
    and nothing says so.*

    The tell is the **bbox**: a row that can be located in an image has one; a row that summarises
    forty objects cannot.
    """
    ref_mod = pytest.importorskip("pycat.utils.object_ref")

    per_object = pd.DataFrame(dict(
        label=[1, 2, 3], area_um2=[1.0, 2.0, 3.0], partition_coeff=[2.0, 3.0, 4.0],
        bbox_y0=[0, 20, 40], bbox_x0=[0, 20, 40],
        bbox_y1=[10, 30, 50], bbox_x1=[10, 30, 50],
        source_path='/tmp/x.tif'))

    per_frame = pd.DataFrame(dict(
        frame=[0, 1, 2], n_droplets=[12, 14, 15], mean_radius_um=[1.1, 1.2, 1.3]))

    object_refs = ref_mod.refs_from_dataframe(per_object)
    assert all(r.is_resolvable_offline() for r in object_refs), (
        "a per-object table must yield refs that resolve to an image"
    )
    assert [r.object_id for r in object_refs] == [1, 2, 3]

    aggregate_refs = ref_mod.refs_from_dataframe(per_frame)
    assert not any(r.is_resolvable_offline() for r in aggregate_refs), (
        "a per-FRAME summary row averages many objects. It must NOT resolve to one — there is no "
        "single object behind it, and a click that lands on an arbitrary one is worse than a "
        "click that does nothing."
    )


@pytest.mark.core
def test_the_ensemble_plots_are_NOT_made_pickable():
    """**"Wire the 13 unpickable plots" was the wrong goal**, and the code says why.

    A point on a **Ripley curve is a radius**. A **histogram bar holds twelve condensates**. A
    **FRAP point is a timepoint**. A **molecular-counting point is a variance bin.**

    **There is no object behind any of them**, and making them pickable would be a lie: the user
    clicks expecting an image and gets whichever row happened to sit at that index.

    The brushable view of per-object data is a **scatter** — which is what ``PlottingWidget``
    builds. **One wiring point, covering every per-object table**, instead of fifteen fixed
    figures.
    """
    plots = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox" / "analysis_plots.py"
    source = plots.read_text(encoding='utf-8', errors='ignore')

    # The ensemble plots must NOT call add_brushing.
    #
    # NOTE: `plot_focus_diagnostic` is deliberately NOT in this list, and a first version of this
    # test wrongly put it there. **Its points are not ensemble points** -- it is a QC scatter where
    # **each point is one image/field**, and the thing a user wants when they click a
    # blurry-looking point is *that field*. That IS resolvable, and it is correctly brushed.
    #
    # The distinction is not "curve vs scatter". It is: **does one point correspond to one thing
    # you could show?** A FRAP timepoint does not. A QC point does.
    for function_name in ('plot_frap_recovery', 'plot_coarsening', 'plot_molecular_counting',
                          'plot_km_survival'):
        start = source.find(f'def {function_name}(')
        assert start > 0, f"{function_name} is gone"
        end = source.find('\ndef ', start + 1)
        body = source[start:end if end > 0 else len(source)]

        assert 'add_brushing(' not in body, (
            f"{function_name} was made pickable. Its points are timepoints, frequencies or bins — "
            f"**not objects**. A click would land on whichever row sat at that index, and the "
            f"user would think they were looking at the object they clicked."
        )
