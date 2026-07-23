"""**A click asked "what is this?" and the view left.**

Every click on a brushable plot did the same thing: mark the object, and take you to it — moving the
camera and jumping the frame. Neither was optional; the frame step was not even behind the `centre`
flag. That is the "abrupt navigation" complaint, and it made exploratory clicking cost you your
place every time.

The overlay (increment 5 part 1) is what lets those come apart. The object is outlined **where it
sits**, so seeing *which* one it is no longer means going there. Going there is a separate
intention, with its own gestures:

* hover / plain click -> mark it, in place;
* double-click -> take me there;
* shift-click -> add to the selection;
* Escape -> nothing is selected.

Camera-follow is available as a preference and **off by default**.
"""

# Third party imports
import numpy as np
import pandas as pd
import pytest


pytestmark = pytest.mark.base


class _Layers(list):
    def __init__(self, items=()):
        super().__init__(items)
        self.selection = set()

    def __contains__(self, key):
        if isinstance(key, str):
            return any(getattr(l, 'name', None) == key for l in self)
        return list.__contains__(self, key)

    def __getitem__(self, key):
        if isinstance(key, str):
            for layer in self:
                if getattr(layer, 'name', None) == key:
                    return layer
            raise KeyError(key)
        return list.__getitem__(self, key)

    def remove(self, key):
        list.remove(self, self[key] if isinstance(key, str) else key)


class _Overlay:
    def __init__(self, data, name=None, **kw):
        self.data = data
        self.name = name
        self.visible = True
        self.metadata = {}
        for k, v in kw.items():
            setattr(self, k, v)


class _Viewer:
    class _Dims:
        ndim = 3
        point = (0, 0, 0)
        current_step = (0, 0, 0)

    class _Cam:
        center = (0.0, 0.0, 0.0)

    def __init__(self):
        self.layers = _Layers()
        self.dims = self._Dims()
        self.camera = self._Cam()

    def _add(self, data, **kw):
        layer = _Overlay(data, **kw)
        self.layers.append(layer)
        return layer

    def add_shapes(self, data, **kw):
        return self._add(data, **kw)

    def add_points(self, data, **kw):
        return self._add(data, **kw)


class _Pick:
    """A matplotlib pick_event, as `make_pickable` reads it."""

    class _Mouse:
        def __init__(self, dblclick=False, key=None):
            self.dblclick = dblclick
            self.key = key

    class _Canvas:
        def draw_idle(self):
            pass

    def __init__(self, artist, index, dblclick=False, key=None):
        self.artist = artist
        self.ind = [index]
        self.mouseevent = self._Mouse(dblclick, key)
        self.canvas = self._Canvas()


class _KeyPress:
    class _Canvas:
        def draw_idle(self):
            pass

    def __init__(self, key):
        self.key = key
        self.canvas = self._Canvas()


@pytest.fixture
def plot():
    """A real figure + picking scatter, with the handlers `make_pickable` registers captured."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    artist = ax.scatter(np.arange(10), np.arange(10), s=60, picker=5)
    handlers = {}
    fig.canvas.mpl_connect = lambda name, fn: handlers.setdefault(name, fn)
    yield fig, artist, handlers
    plt.close(fig)


def _refs(n=10, frame=7):
    from pycat.utils.object_ref import refs_from_dataframe
    return refs_from_dataframe(pd.DataFrame({
        'label': np.arange(1, n + 1),
        'frame': np.full(n, frame),
        'bbox_y0': np.arange(n), 'bbox_x0': np.arange(n),
        'bbox_y1': np.arange(n) + 4, 'bbox_x1': np.arange(n) + 4,
    }), source_path='a.tif')


def _overlay_of(fig, artist):
    for collection in fig.axes[0].collections:
        if collection is not artist:
            return collection
    return None


def test_a_plain_click_does_NOT_yank_the_viewer(plot):
    """The complaint, pinned. Marking an object and navigating to it are different acts."""
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    viewer = _Viewer()

    make_pickable(fig, artist, _refs(), viewer=viewer)      # no manager -> follow OFF
    handlers['pick_event'](_Pick(artist, 3))

    assert viewer.dims.current_step == (0, 0, 0), 'a plain click jumped the frame'
    assert viewer.camera.center == (0.0, 0.0, 0.0), 'a plain click moved the camera'
    assert 'Selection' in viewer.layers, 'the object was not marked where it sits'


def test_a_DOUBLE_click_navigates_because_it_asked_to(plot):
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    viewer = _Viewer()

    make_pickable(fig, artist, _refs(), viewer=viewer)
    handlers['pick_event'](_Pick(artist, 3, dblclick=True))

    assert viewer.dims.current_step[0] == 7, (
        'a double-click is an explicit "take me there" and it did not move the frame')


def test_the_FOLLOW_preference_turns_plain_click_navigation_back_on(plot):
    """Opt-in, not gone. Session-only (`central_manager.follow_selection`): PyCAT has no preference
    persistence, and inventing one for a checkbox is its own piece of work."""
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    viewer = _Viewer()

    class _CM:
        follow_selection = True

    make_pickable(fig, artist, _refs(), viewer=viewer, central_manager=_CM())
    handlers['pick_event'](_Pick(artist, 3))

    assert viewer.dims.current_step[0] == 7, 'the follow preference did not re-enable navigation'


def test_a_MISSING_preference_reads_as_do_not_yank(plot):
    """Plenty of callers have no manager. A missing preference must read as "leave my view alone",
    not crash and not navigate."""
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    viewer = _Viewer()

    class _Bare:
        pass

    make_pickable(fig, artist, _refs(), viewer=viewer, central_manager=_Bare())
    handlers['pick_event'](_Pick(artist, 3))

    assert viewer.dims.current_step == (0, 0, 0)


def test_SHIFT_click_ADDS_to_the_selection(plot):
    """`selected_label` could never do this — it holds one integer. An overlay holds k."""
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    make_pickable(fig, artist, _refs())

    handlers['pick_event'](_Pick(artist, 2))
    handlers['pick_event'](_Pick(artist, 5, key='shift'))

    marked = np.asarray(_overlay_of(fig, artist).get_offsets())
    assert len(marked) == 2, f'shift-click marked {len(marked)} points, not 2'
    assert {tuple(p) for p in marked} == {(2.0, 2.0), (5.0, 5.0)}


def test_a_PLAIN_click_after_a_shift_click_REPLACES_the_selection(plot):
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    make_pickable(fig, artist, _refs())

    handlers['pick_event'](_Pick(artist, 2))
    handlers['pick_event'](_Pick(artist, 5, key='shift'))
    handlers['pick_event'](_Pick(artist, 8))                 # plain — starts over

    marked = np.asarray(_overlay_of(fig, artist).get_offsets())
    assert len(marked) == 1 and tuple(marked[0]) == (8.0, 8.0)


def test_ESCAPE_clears_the_selection(plot):
    """**Escape means nothing is selected**, not "nothing happened"."""
    from pycat.utils.brushing import make_pickable

    fig, artist, handlers = plot
    viewer = _Viewer()

    make_pickable(fig, artist, _refs(), viewer=viewer)
    handlers['pick_event'](_Pick(artist, 3))
    assert 'Selection' in viewer.layers

    handlers['key_press_event'](_KeyPress('escape'))

    assert viewer.layers['Selection'].visible is False, 'Escape left the viewer overlay showing'
    assert _overlay_of(fig, artist).get_visible() is False, 'Escape left the plot overlay showing'


def test_wiring_a_plot_does_not_MATERIALISE_the_lazy_refs(plot):
    """**Increment 4's lazy refs were defeated by this very function.**

    `make_pickable` ended with `figure._pycat_object_refs = list(refs)`, which rebuilds every ref the
    `LazyRefs` sequence exists to avoid — measured at 3.0 s for 50 000 points. So the 6.4-second
    stall increment 4 reported as fixed was still there end-to-end, in the one function that wires
    every brushable plot. Measuring `refs_from_dataframe` alone said it was fixed; driving the real
    path said otherwise.
    """
    from pycat.utils import object_ref as ref_mod
    from pycat.utils.brushing import make_pickable

    fig, artist, _handlers = plot
    built = []
    original = ref_mod.ObjectRef.from_row

    def _counting(row, **kw):
        built.append(1)
        return original(row, **kw)

    ref_mod.ObjectRef.from_row = staticmethod(_counting)
    try:
        make_pickable(fig, artist, _refs(10))
    finally:
        ref_mod.ObjectRef.from_row = original

    assert built == [], (
        f'wiring the plot built {len(built)} refs — `list(refs)` is back and the lazy construction '
        f'is defeated')
    assert len(fig._pycat_object_refs) == 10, 'the refs must still be reachable from the figure'
