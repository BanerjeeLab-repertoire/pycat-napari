"""**The dispatcher, once, for everyone.**

PyCAT had two implementations of linked selection and the good one was unreachable:

* `vpt_ui._select_track` — mature, in production, three-way (MSD curve ↔ table ↔ bead) — with its
  view list **hardcoded** to `'plot' | 'image' | 'table'`, so nothing else could join it.
* `brushing.SelectionHub` — the generic lift of it, **never used in production**, and the lift had
  dropped the guard that matters: it released its busy flag synchronously in `finally`, which is
  exactly the bug VPT's docstring documents having fixed. It would have oscillated the first time a
  real Qt view was wired to it. It never was, so nobody found out.

`SelectionService` is VPT's dispatcher generalised: the hub's subscriber registry, VPT's guards.

These tests exercise the service directly with an injected `defer`, so they need no Qt and no event
loop — the deferral point is the whole subtlety, and a test that could not control it would be
testing a deadlock.
"""

# Third party imports
import pytest


pytestmark = pytest.mark.base


def _service(defer=None):
    """A service whose delayed release fires when we say.

    The real one posts the release behind the Qt queue. `defer=lambda fn: fn()` collapses that to
    "immediately", which is the honest stand-in for "the queue drained".
    """
    from pycat.utils.selection_service import SelectionService
    return SelectionService(defer=defer if defer is not None else (lambda fn: fn()))


def _sel(*ids, source='', mode='selected', generation=0):
    from pycat.utils.selection_service import Selection
    return Selection(entity_ids=tuple(ids), primary_id=ids[0] if ids else None,
                     mode=mode, source_view=source, generation=generation)


def test_the_SOURCE_view_never_receives_its_own_selection():
    """**That is the loop.** A view that re-highlights from its own action fires its own emit and
    comes straight back."""
    service = _service()
    heard = {'plot': 0, 'table': 0, 'image': 0}

    for name in heard:
        service.subscribe(name, lambda s, n=name: heard.__setitem__(n, heard[n] + 1))

    service.select(_sel('a', source='plot'))

    assert heard['plot'] == 0, "the initiating view was called back — that is the loop"
    assert heard['table'] == 1 and heard['image'] == 1, "the other views must each update once"


def test_a_view_that_ECHOES_does_not_loop():
    """The re-entrancy guard: the highlight this selection causes in view B must not fire B's own
    emit back through the service."""
    service = _service()
    calls = {'plot': 0, 'table': 0}

    def _plot(selection):
        calls['plot'] += 1
        service.select(_sel('b', source='plot'))      # the echo

    def _table(selection):
        calls['table'] += 1
        service.select(_sel('c', source='table'))     # the echo

    service.subscribe('plot', _plot)
    service.subscribe('table', _table)

    service.select(_sel('a', source='plot'))

    assert calls['plot'] == 0
    assert calls['table'] == 1, "an echo re-entered and propagated again"


def test_the_busy_flag_is_RELEASED_so_the_next_real_selection_lands():
    service = _service()
    seen = []
    service.subscribe('table', lambda s: seen.append(s.entity_ids))

    service.select(_sel('a', source='plot'))
    service.select(_sel('b', source='plot'))

    assert seen == [('a',), ('b',)], "the second selection was swallowed"


def test_a_NEVER_FIRING_defer_wedges_the_service_which_is_why_the_fallback_exists():
    """**The trap the synchronous fallback exists for.**

    `QTimer.singleShot` needs a running event loop. Headless — a batch run, a test — there is none,
    so a deferral that never fires would leave the service busy forever and silently swallow every
    later selection. `_qt_defer` falls back to calling inline for exactly this reason; this pins
    what would happen without it.
    """
    service = _service(defer=lambda fn: None)         # a deferral that never fires
    seen = []
    service.subscribe('table', lambda s: seen.append(s.entity_ids))

    assert service.select(_sel('a', source='plot')) is True
    assert service.select(_sel('b', source='plot')) is False, (
        "the service was not wedged — then this test is not pinning the hazard it claims to")
    assert seen == [('a',)]


def test_a_STALE_callback_can_tell_it_lost_the_race():
    """The generation counter is monotonic, so a slow view can compare what it is drawing against
    what is current instead of drawing a selection the user has moved on from."""
    service = _service()
    generations = []
    service.subscribe('plot', lambda s: generations.append(s.generation))

    service.select(_sel('a', source='table', generation=service.next_generation()))
    service.select(_sel('b', source='table', generation=service.next_generation()))

    assert generations == sorted(generations) and len(set(generations)) == 2
    assert service.selected.generation == generations[-1]


def test_closing_a_DATASET_drops_a_selection_that_named_it():
    """A selection outliving its data resolves to whatever now sits at that id — the same class of
    wrongness as row-position matching."""
    service = _service()
    service.select(_sel('C:/data/a.tif/cell_analysis/cell/0/1', source='table'))
    assert service.selected is not None

    assert service.invalidate_dataset('C:/data/other.tif') is False
    assert service.selected is not None, "an unrelated dataset closing dropped the selection"

    assert service.invalidate_dataset('C:/data/a.tif') is True
    assert service.selected is None


def test_a_subscriber_is_held_WEAKLY_so_a_closed_view_is_not_kept_alive():
    """A plot dock that is closed must not be kept alive by having once wanted to hear about
    selections — nor keep receiving them."""
    import gc

    service = _service()
    heard = []

    class _View:
        def on_selection(self, selection):
            heard.append(selection.entity_ids)

    view = _View()
    service.subscribe('plot', view.on_selection)
    service.select(_sel('a', source='table'))
    assert heard == [('a',)]

    del view
    gc.collect()

    service.select(_sel('b', source='table'))
    assert heard == [('a',)], "a garbage-collected view was still being called"
    assert 'plot' not in service._subscribers, "the dead subscriber was not dropped"


def test_a_BOUND_METHOD_subscriber_survives_while_its_object_does():
    """**The classic weak-callback bug.** A plain `weakref.ref` to a bound method is dead on
    arrival — the bound method is created fresh per attribute access and nothing else holds it — so
    a naive weak registry silently never fires. `WeakMethod` holds the instance instead."""
    service = _service()
    heard = []

    class _View:
        def on_selection(self, selection):
            heard.append(selection.entity_ids)

    view = _View()                      # kept alive for the whole test
    service.subscribe('plot', view.on_selection)
    service.select(_sel('a', source='table'))

    assert heard == [('a',)], (
        "a bound-method subscriber never fired — the weak reference died immediately")


def test_a_view_that_THROWS_does_not_take_the_others_down():
    service = _service()
    heard = []
    service.subscribe('broken', lambda s: (_ for _ in ()).throw(RuntimeError('boom')))
    service.subscribe('fine', lambda s: heard.append(s.entity_ids))

    service.select(_sel('a', source='table'))
    assert heard == [('a',)], "one dead view stopped the others from updating"


def test_an_EMPTY_selection_does_nothing():
    service = _service()
    heard = []
    service.subscribe('plot', lambda s: heard.append(s))
    assert service.select(_sel(source='table')) is False
    assert heard == []


def test_the_hub_is_now_a_SHIM_over_the_service_not_a_second_implementation():
    """`SelectionHub` is the ObjectRef-shaped face `make_pickable` already speaks. Making it a
    shim is what stops it drifting from the dispatcher again — and it inherits VPT's delayed
    release, which its own copy had dropped."""
    from pycat.utils.brushing import SelectionHub
    from pycat.utils.object_ref import ObjectRef
    from pycat.utils.selection_service import SelectionService

    service = SelectionService(defer=lambda fn: fn())
    hub = SelectionHub(service=service)
    assert hub.service is service

    calls = {'plot': 0, 'table': 0}
    hub.register_view('plot', lambda ref: calls.__setitem__('plot', calls['plot'] + 1))
    hub.register_view('table', lambda ref: calls.__setitem__('table', calls['table'] + 1))

    hub.select(ObjectRef(object_id=1), source='plot')

    assert calls['plot'] == 0, "the initiating view must NOT be called back — that is the loop"
    assert calls['table'] == 1, "the other view must be updated exactly once"
    assert hub.selected is not None


def test_a_generic_plot_and_a_VPT_style_view_share_ONE_dispatcher():
    """**The generalisation, stated as a test.** A plot elsewhere in PyCAT emits through the same
    service VPT uses, and VPT's views hear it — which is the whole point of promoting the
    dispatcher out of `vpt_ui` rather than leaving every plot to reimplement it."""
    from pycat.utils.brushing import SelectionHub
    from pycat.utils.object_ref import ObjectRef

    service = _service()
    vpt_heard = []
    service.subscribe('vpt.image', lambda s: vpt_heard.append(s.entity_ids))

    hub = SelectionHub(service=service)          # a generic plot, speaking the ObjectRef API
    ref = ObjectRef(object_id=3, entity_id='C:/a.tif/cell_analysis/cell/0/3')
    hub.select(ref, source='some.scatter')

    assert vpt_heard == [('C:/a.tif/cell_analysis/cell/0/3',)], (
        "a generic plot's selection did not reach a subscriber outside its own module")


def test_a_ref_from_a_STAMPED_table_carries_its_name_into_the_selection():
    """The service is keyed on increment-2 ids, not row position — that is what makes a selection
    survive a sort. `from_row` picks the name up off the hidden column."""
    import pandas as pd
    from pycat.utils.entity_ref import stamp_entity_ids
    from pycat.utils.object_ref import refs_from_dataframe

    df = stamp_entity_ids(pd.DataFrame({'label': [1, 2]}), entity_type='cell',
                          source_path='C:/a.tif', operation_id='cell_analysis', frame=0)
    refs = refs_from_dataframe(df, source_path='C:/a.tif')

    assert refs[1].entity_id == 'C:/a.tif/cell_analysis/cell/0/2'
    assert refs[0].entity_id != refs[1].entity_id


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Increment 4 — the expensive half is coalesced
# ═══════════════════════════════════════════════════════════════════════════════════════════


def test_a_BURST_of_selections_costs_ONE_expensive_resolve():
    """**Dragging across a scatter emits a selection per point.**

    The cheap feedback (move a marker, select a row) must land on every one or the UI feels dead —
    it costs microseconds. The expensive one — reading pixels for a crop — must not: the user only
    ever looks at the last one, and doing the rest means a file read per hover.
    """
    from pycat.utils.selection_service import SelectionService

    flush = []
    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: flush.append(fn))

    cheap, expensive = [], []
    service.subscribe('table', lambda s: cheap.append(s.entity_ids[0]))
    service.subscribe_deferred('image', lambda s: expensive.append(s.entity_ids[0]))

    for name in ('a', 'b', 'c', 'd'):        # a drag
        service.select(_sel(name, source='plot'))

    assert cheap == ['a', 'b', 'c', 'd'], "cheap feedback was dropped — the UI would feel dead"
    assert expensive == [], "an image was resolved before the burst had settled"

    flush[-1]()                               # the trailing edge fires
    assert expensive == ['d'], (
        f"expected ONE resolve, for the most recent selection; got {expensive}")


def test_the_deferred_subscriber_also_skips_its_OWN_selection():
    from pycat.utils.selection_service import SelectionService

    flush = []
    service = SelectionService(defer=lambda fn: fn(), debounce=lambda fn: flush.append(fn))
    heard = []
    service.subscribe_deferred('plot', lambda s: heard.append(s.entity_ids))

    service.select(_sel('a', source='plot'))
    for fn in flush:
        fn()
    assert heard == [], "a view was made to resolve the image for its own click"


# ═══════════════════════════════════════════════════════════════════════════════════════════
# Interaction layer — hover / selected / pinned are ONE state (Gap 1)
# ═══════════════════════════════════════════════════════════════════════════════════════════
#
# Selection used to be a single object: no multi-select, no pin-while-exploring, no independent hover.
# `SelectionState` makes the three real and independent, published as one value. Back-compat is
# mandatory (covered by every test above, which still passes) — these pin the NEW behaviour.


def test_TOGGLE_adds_then_removes_from_the_selection():
    """Ctrl-click a comparison set: toggle adds, toggle again removes."""
    s = _service()
    s.toggle('a', source='plot')
    assert s.state.selected == frozenset({'a'}) and s.state.primary == 'a'
    s.toggle('b', source='plot')
    assert s.state.selected == frozenset({'a', 'b'})
    s.toggle('a', source='plot')
    assert s.state.selected == frozenset({'b'}), "toggling a selected entity did not remove it"


def test_CLEAR_empties_selection_and_hover_but_KEEPS_pins():
    """Escape clears what you were looking at without throwing away what you pinned to compare."""
    s = _service()
    s.select_entity('a', source='plot')
    s.pin('p', source='plot')
    s.hover('h', source='plot')

    s.clear_selection(source='plot')

    assert s.state.selected == frozenset() and s.state.hovered is None
    assert s.state.pinned == frozenset({'p'}), "clear threw away the pins"


def test_HOVER_is_independent_of_the_selection():
    s = _service()
    s.select_entity('a', source='plot')
    s.hover('b', source='plot')
    assert s.state.selected == frozenset({'a'}), "hovering changed the selection"
    assert s.state.hovered == 'b'


def test_one_command_is_ONE_generation_and_ONE_publish():
    s = _service()
    gens = []
    s.subscribe('view', lambda st: gens.append(st.generation))
    g0 = s.state.generation

    s.toggle('a', source='other')

    assert len(gens) == 1, "a single command published more than once"
    assert s.state.generation != g0 and gens[-1] == s.state.generation


def test_a_command_still_reaches_the_OLD_subscribers_through_the_back_compat_interface():
    """A subscriber written against the old `Selection` (reads `.entity_ids`) fires unchanged when a
    NEW command drives the state."""
    s = _service()
    heard = []
    s.subscribe('table', lambda st: heard.append(st.entity_ids))
    s.select_entity('a', source='plot')
    assert heard == [('a',)]


def test_a_command_skips_its_OWN_source_view():
    s = _service()
    heard = {'plot': 0, 'table': 0}
    for n in heard:
        s.subscribe(n, lambda st, k=n: heard.__setitem__(k, heard[k] + 1))
    s.toggle('a', source='plot')
    assert heard['plot'] == 0 and heard['table'] == 1


def test_SelectionState_reads_like_the_old_Selection():
    from pycat.utils.selection_service import SelectionState
    st = SelectionState(selected=frozenset({'x', 'a'}), primary='a')
    assert st.primary_id == 'a' and not st.is_empty
    assert st.entity_ids == ('a', 'x'), "entity_ids must be primary-first then sorted (stable order)"
