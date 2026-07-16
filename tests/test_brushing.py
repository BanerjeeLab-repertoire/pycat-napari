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
def test_every_per_object_results_LOOP_keeps_the_bbox():
    """**A file-level check is not a loop-level check** — and that is how one survived.

    The first version of this test asked whether each *module* contained the word ``bbox``. That
    passed on ``brightfield_tools`` — which mentions it in a docstring — **while
    ``bf_condensate_metrics``, a per-condensate results loop inside it, kept no bbox at all.** The
    guard was satisfied by a comment.

    This version walks the **AST**: every ``for prop in regionprops(...)`` loop that builds a
    results row must keep the bounding box. ``regionprops`` hands it over free, and **a row without
    it cannot be turned back into an image** — which in **batch** is the only route back to the
    object at all, because the layer is gone.

    *(Per-FRAME and per-CELL aggregates are correctly skipped: a row that summarises forty objects
    has no single object to point at, and giving it a bbox would be a lie.)*
    """
    import ast
    import re

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

    missing = []
    for path in sorted(source_root.rglob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue

            iterator = ast.get_source_segment(source, node.iter) or ''
            if 'regionprops' not in iterator:
                continue

            body = ast.get_source_segment(source, node) or ''

            # Does this loop build a PER-OBJECT results row? (A `prop` is in scope, so there IS a
            # single object to point at.)
            builds_row = bool(re.search(
                r'(rows|records|out)\.append\(\s*(dict\(|\{)|record\s*=\s*dict\(', body))
            if not builds_row:
                continue

            # ── Look at the CODE, not the text ──────────────────────────────
            #
            # A first version checked `'bbox' in body`. **A COMMENT mentioning the bbox satisfied
            # that** — and the loop in `bf_condensate_metrics` has one. So the "stronger" guard
            # was exactly as weak as the file-level one it replaced, and *I verified it by
            # deleting the real line and watching it still pass.*
            #
            # **A guard that a comment can satisfy is not a guard.** This walks the loop's own
            # AST and looks for a real call.
            keeps_bbox = False
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, 'id', '') or getattr(inner.func, 'attr', '')
                    if 'bbox' in name.lower():
                        keeps_bbox = True
                        break
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value.startswith('bbox_'):
                        keeps_bbox = True
                        break

            if not keeps_bbox:
                missing.append(f"{path.relative_to(source_root)}:{node.lineno}")

    assert not missing, (
        f"these per-object results loops keep NO bounding box: {missing}\n\n"
        f"Their rows cannot be turned back into an image — regionprops provides the bbox free, "
        f"and discarding it is what makes a plot unclickable. Add "
        f"`**bbox_columns_from_regionprops(prop)` to the row."
    )


@pytest.mark.core
def test_the_bbox_import_is_PRESENT_wherever_it_is_USED():
    """**A file edited in a sandbox and left out of a release is a file that does not exist.**

    ``condensate_physics_tools``, ``feature_analysis_tools`` and ``segmentation_tools`` all had
    the bbox sweep applied (1.5.495) and **none of the three was included in the release bundle.**
    The repo therefore had a test asserting a property of three files that had never been shipped,
    and CI caught it — which is the entire point of a test that reads the source rather than the
    behaviour.

    This test closes the loop the other way: if a module *calls* the bbox helper, it must also
    *import* it. That catches a half-applied sweep — the failure mode where the call sites land
    and the import does not, which fails at **runtime**, not at import.
    """
    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    broken = []
    for path in sorted(toolbox.glob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')

        uses = ("bbox_columns_from_regionprops(" in source
                or "_bbox_cols(" in source
                or "normalise_bbox_columns(" in source)
        if not uses:
            continue

        if "from pycat.utils.object_ref import" not in source:
            broken.append(path.name)

    assert not broken, (
        f"these modules CALL a bbox helper and never IMPORT it: {broken}. The sweep was applied "
        f"to the call sites and not to the import — which fails at RUNTIME, not at import, so "
        f"nothing catches it until a user runs that analysis."
    )


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Brushing increment 1 — the two ways brushing was HARMFUL
# ═══════════════════════════════════════════════════════════════════════════════════════════


class _RecordingLazyStack:
    """A lazy stack with the real refusing `__array__`, that records any full-read attempt.

    This is the shape of every PyCAT lazy wrapper (`_TiffPageStack`, `_ImsReader*`,
    `_LazyArraySource`): indexable per plane, and `__array__` refuses rather than quietly
    materialising an acquisition. See `test_no_eager_reads.py`.
    """

    def __init__(self, arr):
        self._a = arr
        self.shape = arr.shape
        self.ndim = arr.ndim
        self.dtype = np.dtype('float32')
        self.full_read_attempts = 0

    def __getitem__(self, key):
        return self._a[key]

    def __len__(self):
        return self.shape[0]

    def __array__(self, dtype=None):
        self.full_read_attempts += 1
        from pycat.file_io.lazy_guard import refuse_implicit_full_read
        refuse_implicit_full_read(self)


class _StubLayer:
    def __init__(self, name, data, role, layer_id=None):
        from pycat.utils.layer_tags import tag_layer
        self.name = name
        self.data = data
        self.metadata = {}
        self.selected_label = None
        self.show_selected_label = False
        tag_layer(self, 'role', role, source='inferred')
        if layer_id:
            self.metadata['pycat_layer_id'] = layer_id


class _StubLayers(list):
    def __init__(self, items):
        super().__init__(items)
        self.selection = set()


class _StubViewer:
    class _Dims:
        point = ()
        current_step = (0, 0, 0)

    class _Cam:
        center = (0.0, 0.0, 0.0)

    def __init__(self, layers):
        self.layers = _StubLayers(layers)
        self.dims = self._Dims()
        self.camera = self._Cam()


@pytest.mark.core
def test_a_click_CROPS_without_materialising_the_acquisition():
    """**One click asked for the whole acquisition to take an 8-pixel crop.**

    `crop_for_ref` did `data = np.asarray(layer.data)` and *then* sliced. On a lazy TIFF/IMS/CZI
    layer that is the `np.asarray(layer.data)` materialisation trap, in the brushing path.

    On the current tree it is worse than slow: every lazy wrapper's `__array__` **refuses**, so the
    eager read did not freeze — it raised, the surrounding `except` abandoned the live-layer path
    entirely, and the click silently fell through to re-reading the FILE. With the file moved, the
    user was told *"The source file is gone"* while the layer sat open in the viewer.

    The fix is only the order: index the plane → slice the window → `np.asarray` the tiny crop.
    """
    from pycat.utils.brushing import crop_for_ref
    from pycat.utils.object_ref import ObjectRef

    stack = _RecordingLazyStack(
        np.random.default_rng(0).random((40, 128, 128)).astype(np.float32))
    viewer = _StubViewer([_StubLayer('movie', stack, 'image')])

    ref = ObjectRef(object_id=3, frame=10, bbox=(20, 30, 28, 38),
                    source_path='/nonexistent/gone.tif')
    crop, message = crop_for_ref(ref, viewer=viewer, pad_px=8)

    assert stack.full_read_attempts == 0, (
        "`np.asarray` fired on the lazy layer — one click tried to load the entire acquisition "
        "to take an 8-pixel crop.")
    assert crop is not None, (
        f"the crop came back empty ({message!r}) even though the layer is open in the viewer — "
        f"the eager read raised and the live-layer path was abandoned.")
    assert crop.shape == (24, 24)
    # The crop is the real pixels, from the right plane.
    assert np.array_equal(crop, stack._a[10][12:36, 22:46])


@pytest.mark.core
def test_an_object_resolves_to_ITS_OWN_layer_not_merely_the_FIRST_one():
    """**With two segmentations open, a punctum from analysis B highlighted an object in mask A.**

    `resolve_in_viewer` took the first layer with a labels/mask role and set
    `selected_label = ref.object_id` on it. **A label value is only meaningful inside one mask** —
    label 7 exists in every segmentation that has seven objects, and they are not the same object.

    Nothing about the result looked wrong: the user is shown the wrong object as if it were right.
    That is a scientific error, not a UX wrinkle.
    """
    from pycat.utils.object_ref import ObjectRef, resolve_in_viewer

    mask_a = _StubLayer('Segmentation A', np.zeros((32, 32), np.uint16), 'labels', 'aaaa1111')
    mask_b = _StubLayer('Segmentation B', np.zeros((32, 32), np.uint16), 'labels', 'bbbb2222')
    viewer = _StubViewer([mask_a, mask_b])

    # The object came from B — the SECOND layer — and now says so.
    ref = ObjectRef(object_id=7, bbox=(1, 1, 5, 5), source_layer_id='bbbb2222')
    assert resolve_in_viewer(ref, viewer, centre=False) is True

    assert mask_b.selected_label == 7, "the object did not resolve to the layer it came from"
    assert mask_a.selected_label is None, (
        "an UNRELATED segmentation was highlighted — label 7 in mask A is not the same object as "
        "label 7 in mask B")


@pytest.mark.core
def test_a_LEGACY_ref_still_resolves_but_says_it_GUESSED():
    """Additive: a ref with no `source_layer_id` (every ref today, until increment 2 fills it)
    keeps the old first-match behaviour — but a silently-wrong highlight becomes a visibly
    degraded one."""
    from pycat.utils.object_ref import ObjectRef, layers_for_ref, resolve_in_viewer

    mask_a = _StubLayer('Segmentation A', np.zeros((32, 32), np.uint16), 'labels', 'aaaa1111')
    mask_b = _StubLayer('Segmentation B', np.zeros((32, 32), np.uint16), 'labels', 'bbbb2222')
    viewer = _StubViewer([mask_a, mask_b])

    legacy = ObjectRef(object_id=7, bbox=(1, 1, 5, 5))          # no source_layer_id
    assert resolve_in_viewer(legacy, viewer, centre=False) is True
    assert mask_a.selected_label == 7                            # old behaviour preserved

    _candidates, note = layers_for_ref(legacy, viewer)
    assert note and 'may not be the right one' in note, (
        "the fallback was silent — the whole point is that a guess announces itself")


@pytest.mark.core
def test_a_ref_whose_layer_is_CLOSED_resolves_to_NOTHING_rather_than_the_wrong_thing():
    """The ref knows its layer, and that layer is not open. The honest answer is "not here" —
    quietly using a different mask is the original bug wearing a new hat."""
    from pycat.utils.object_ref import ObjectRef, layers_for_ref

    mask_a = _StubLayer('Segmentation A', np.zeros((32, 32), np.uint16), 'labels', 'aaaa1111')
    viewer = _StubViewer([mask_a])

    ref = ObjectRef(object_id=7, bbox=(1, 1, 5, 5), source_layer_id='cccc3333')   # not open
    candidates, note = layers_for_ref(ref, viewer)

    assert candidates == [], "a ref whose own layer is closed grabbed a different mask"
    assert 'not open' in note


@pytest.mark.core
def test_source_layer_id_is_OPTIONAL_so_every_existing_ref_still_works():
    """`ObjectRef` is frozen and constructed all over the codebase. The field is additive and
    defaulted; increment 2 fills it."""
    from pycat.utils.object_ref import ObjectRef

    ref = ObjectRef(object_id=1, frame=0, bbox=(0, 0, 4, 4), source_path='x.tif')
    assert ref.source_layer_id is None
    assert 'source_layer_id' in ref.to_dict()
