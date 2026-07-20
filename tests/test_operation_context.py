"""**The layer-tag op comes from an EXPLICIT context now — the stack walk was silently degrading.**

`layer_tag_hook._op_from_stack` walked the call stack for a decorated function carrying `__pycat_op__`.
That only fires when the decorated function is STILL ON THE STACK when the layer is made. Off-thread
execution (`operation_runner`, shipped 1.6.139) breaks it: the compute frame has already returned by the
time the result callback creates the layer, so the walk finds nothing and the tag silently degrades from
definitional (`source='derived'`) to a name-substring guess (`source='inferred'`).

`operation_context` replaces the implicit stack walk with an explicit declaration. These tests pin: the
context sets/nests/restores; a layer made inside it is tagged definitionally; **a layer made in an
`operation_runner` result callback is tagged definitionally** (the regression this exists for — it fails
on the pre-fix tree); the op is thread-isolated (a module global would leak and mis-attribute); and the
stack walk + name inference still behave exactly as before (fallback intact, a guess still marked a guess).
"""
import threading
import types

import pytest

from pycat.utils import tag_registry
from pycat.utils.operation_runner import OperationRunner
from pycat.utils.tag_registry import active_operation, operation_context

pytestmark = pytest.mark.core


class _PydanticLikeViewer:
    """A Viewer that refuses ``setattr`` — as napari's pydantic model does (see test_tag_hook_installs)."""

    def __init__(self):
        object.__setattr__(self, 'layers', [])

    def __setattr__(self, name, value):
        raise ValueError(f"1 validation error for Viewer\n{name}\n  Object has no attribute '{name}'")

    def _make(self, kind, **kwargs):
        layer = types.SimpleNamespace(name=kwargs.get('name', kind), metadata={})
        self.layers.append(layer)
        return layer

    def add_image(self, data, **kwargs):
        return self._make('image', **kwargs)

    def add_labels(self, data, **kwargs):
        return self._make('labels', **kwargs)


def _hooked_viewer():
    from pycat.utils import layer_tag_hook
    return layer_tag_hook.install(_PydanticLikeViewer())


def _op_record(layer):
    """The full ``op`` tag record (key/value/source), or None if the layer has no op tag."""
    from pycat.utils.layer_tags import get_tags
    for t in get_tags(layer):
        if t.get('key') == 'op':
            return t
    return None


# ── Part A: the context sets, nests, and restores ─────────────────────────────────────────────
def test_operation_context_sets_nests_and_restores():
    assert active_operation() is None
    with operation_context('clahe'):
        assert active_operation() == 'clahe'
        with operation_context('otsu'):           # nesting: the inner op wins
            assert active_operation() == 'otsu'
        assert active_operation() == 'clahe'       # ...and the outer is restored on exit
    assert active_operation() is None


def test_an_exception_inside_the_context_still_restores_it():
    assert active_operation() is None
    with pytest.raises(RuntimeError):
        with operation_context('clahe'):
            raise RuntimeError('boom')
    assert active_operation() is None, "the context must restore even when the block raises (finally)"


# ── Part B: a layer created inside the context is tagged DEFINITIONALLY ────────────────────────
def test_a_layer_created_inside_operation_context_is_derived():
    """The core assertion. The layer name carries NO name-hint, so a `derived` op can only have come
    from the explicit context, not the name-substring fallback."""
    viewer = _hooked_viewer()
    with operation_context('clahe'):
        layer = viewer.add_image([[1.0, 2.0], [3.0, 4.0]], name='a plain name with no hint')
    rec = _op_record(layer)
    assert rec and rec['value'] == 'clahe' and rec['source'] == 'derived', (
        f"expected op=clahe source=derived from the explicit context, got {rec}")


# ── The regression this spec exists for: a layer created in an operation_runner result callback ─
def test_a_layer_made_in_an_on_result_callback_is_tagged_definitionally(monkeypatch):
    """**Fails on the pre-fix tree.** The compute runs off-thread and its frame is gone by the time
    `on_result` creates the layer, so the stack walk finds nothing. We reproduce that boundary: the
    caller's op context is torn down after the compute returns (as it would be once the handler yields to
    the event loop), then `on_result` fires. The runner must have CAPTURED the op at call time and
    re-established it around `on_result`, so the layer is `derived`, not name-guessed."""
    viewer = _hooked_viewer()
    runner = OperationRunner()
    captured = {}

    def on_result(_result):
        layer = viewer.add_image([[1.0, 2.0]], name='result layer, no name hint')
        captured['rec'] = _op_record(layer)

    # Model the off-thread boundary: after the compute returns, the caller's context is no longer active
    # (the decorated handler has yielded to the event loop). `run_with_progress` is where the compute is
    # driven, so we tear the context down there — exactly the window in which the stack walk went blind.
    token = tag_registry._ACTIVE_OP.set('clahe')          # enter the caller's op context
    reset_done = {'v': False}

    def fake_run_with_progress(work, **_kw):
        value = work(lambda _d, _t: None)                 # "compute" (synchronous stand-in for the worker)
        tag_registry._ACTIVE_OP.reset(token)              # caller context gone — as in the real async case
        reset_done['v'] = True
        return value

    monkeypatch.setattr('pycat.utils.qt_worker.run_with_progress', fake_run_with_progress)
    try:
        runner.execute(lambda: 'data', on_result=on_result)
    finally:
        if not reset_done['v']:
            tag_registry._ACTIVE_OP.reset(token)

    rec = captured.get('rec')
    assert rec and rec['value'] == 'clahe' and rec['source'] == 'derived', (
        f"a layer created in on_result must be tagged definitionally (the op the runner was invoked "
        f"under), not name-guessed — got {rec}. This is the concrete breakage the spec fixes.")


# ── Thread isolation: an op set on one thread must NOT leak into another ───────────────────────
def test_the_op_does_not_leak_across_threads():
    """`contextvars`, not a module global — precisely so an op set on the main thread cannot mis-attribute
    a layer created on a worker thread (a leaked wrong op is worse than an absent one)."""
    viewer = _hooked_viewer()
    seen = {}

    def worker():
        layer = viewer.add_image([[1.0, 2.0]], name='worker layer, no hint')
        seen['rec'] = _op_record(layer)

    with operation_context('clahe'):
        t = threading.Thread(target=worker)
        t.start()
        t.join()

    rec = seen.get('rec')
    assert rec is None, (
        f"the op set on the main thread leaked into a layer created on another thread ({rec}) — a module "
        f"global would do this; a ContextVar must not.")


# ── Fallback intact: the stack walk still attributes a directly-called decorated function ──────
def _stack_walk_caller(viewer):
    """A module-level function carrying ``__pycat_op__`` but NOT the decorator (so it sets no context) —
    isolates the stack walk. Called directly, the hook must still find its op by walking the stack."""
    return viewer.add_image([[1.0, 2.0]], name='another name with no hint')


_stack_walk_caller.__pycat_op__ = 'clahe'


def test_the_stack_walk_still_works_as_a_fallback():
    viewer = _hooked_viewer()
    assert active_operation() is None                     # no explicit context — force the fallback path
    layer = _stack_walk_caller(viewer)
    rec = _op_record(layer)
    assert rec and rec['value'] == 'clahe' and rec['source'] == 'derived', (
        f"the stack walk must remain a working fallback for un-migrated synchronous paths — got {rec}")


# ── Name inference still marks itself a guess ─────────────────────────────────────────────────
def test_name_inference_still_marks_source_inferred():
    """With no context and no decorated caller, the op can only be guessed from the name — and that
    honesty distinction (`inferred`, not `derived`) is the difference between a fact and a guess."""
    viewer = _hooked_viewer()
    assert active_operation() is None
    layer = viewer.add_image([[1.0, 2.0]], name='CLAHE result')   # only the NAME says clahe
    rec = _op_record(layer)
    assert rec and rec['value'] == 'clahe' and rec['source'] == 'inferred', (
        f"a name-guessed op must stay source='inferred' — got {rec}")
