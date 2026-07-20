# Claude Code spec — Explicit operation context (retire the stack walk)

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Replaces the
layer-tag hook's stack-inspection mechanism with an explicit context. The external audit classified
`_op_from_stack` as *"clever… but more fragile than explicit execution context"* and *"transitional
infrastructure."* **That fragility is no longer theoretical — the off-thread execution shipped in
1.6.139/140 breaks it silently.** Additive and back-compatible; the stack walk stays as a fallback.

## The mechanism today (verified)
`layer_tag_hook._op_from_stack()` (`:113`) walks up to 15 frames looking for a function carrying
`__pycat_op__`, and is called once (`:238`) when a layer is created. The hook's own comment states the
value hierarchy precisely:

> *"From the stack: **definitional**. From the name: **a guess**, and marked as one. An absent tag is
> honest; a guessed one is a lie that will be queried as truth."*

So when the stack walk succeeds, the layer gets `source='derived'` (authoritative). When it fails, the
hook falls back to `_op_from_name()` — a substring match on the layer name — tagged `source='inferred'`.

## Why this now breaks (the concrete failure)
`utils/operation_runner.py` (shipped 1.6.139, adopted 1.6.140) explicitly separates the threads:

> *"the compute runs on `qt_worker.run_with_progress`'s worker thread… **The worker must NOT touch
> napari/Qt.** … `on_result` fires on the MAIN thread. **Layer creation** and widget updates belong in
> `on_result`, never in the worker."*

That is correct threading design — and it means **the decorated operation function is no longer on the
stack when the layer is created.** The compute frame has already returned; `on_result` is a callback
invoked from the runner. So `_op_from_stack()` returns `None`, and tagging silently degrades from
definitional to a name-substring guess.

The degradation is **silent and gets worse as more widgets adopt the runner** — which is the current
direction. Off-thread execution already exists in `scene_switcher`, `ui_modules`, `invitro_bf_ui`,
`brightfield_ui`, and the four VPT adapters. Every migration quietly moves more layers from
`source='derived'` to `source='inferred'`.

Other stated failure modes (decorator wrappers, partial functions, renamed helper frames, compute-then-
add-later patterns) apply equally, but threading is the one that is definitely live today.

## The fix — an explicit context, with the stack walk retained as fallback
### Part A — `operation_context`
Add to `utils/tag_registry.py` (or `layer_tag_hook.py` — wherever `__pycat_op__` is already known):
```python
_ACTIVE_OP = contextvars.ContextVar('pycat_active_op', default=None)

@contextmanager
def operation_context(op: str):
    """Declare the operation responsible for layers created inside this block."""
    token = _ACTIVE_OP.set(op)
    try:
        yield
    finally:
        _ACTIVE_OP.reset(token)

def active_operation() -> str | None:
    return _ACTIVE_OP.get()
```
**Use `contextvars`, not a module global** — a plain global would leak across threads and produce the
*wrong* op, which is worse than none. `contextvars` propagates correctly into `asyncio` and is
thread-isolated by default; where a worker must carry the caller's context, copy it explicitly.

### Part B — the hook prefers the explicit context
In `layer_tag_hook` (`:238`), the resolution order becomes:
1. `active_operation()` — **definitional**, `source='derived'`
2. `_op_from_stack()` — definitional, `source='derived'` (unchanged fallback)
3. `_op_from_name()` — a guess, `source='inferred'` (unchanged)
4. nothing — absent, which the hook rightly treats as honest

**Do not remove the stack walk.** It still works for direct synchronous calls and is the compatibility
bridge for every un-migrated path.

### Part C — the `@tags_layer` decorator sets the context automatically
The decorator already knows the op. Have it wrap the call in `operation_context(op)`, so every
decorated function *synchronously* creating a layer is covered with no call-site change. This makes
Part D's manual work small.

### Part D — thread the context through `operation_runner`
`OperationRunner.execute` should capture `active_operation()` at call time and re-establish it around
`on_result`, so a layer created in the result callback is still attributed to the operation that
produced the data. This is the fix for the concrete breakage; it is a few lines and benefits every
current and future runner adoption.

## Tests
- `operation_context` sets/restores; nesting works; an exception inside restores correctly.
- **A layer created inside `operation_context('cellpose')` is tagged `op=cellpose` with
  `source='derived'`** — the core assertion.
- **The regression this spec exists for:** a layer created in an `on_result` callback after an
  `operation_runner.execute` is tagged **definitionally**, not name-guessed. Assert `source='derived'`.
  This test would fail on today's tree.
- Thread isolation: an op set in one thread does not leak into a layer created in another.
- Fallback intact: a layer created by a decorated function called directly (no explicit context) is
  still tagged via the stack walk.
- Name-inference still marks `source='inferred'` — the honesty distinction is preserved.

## Steps
1. `operation_context` / `active_operation` using `contextvars`.
2. Hook resolution order: explicit → stack → name → absent.
3. `@tags_layer` sets the context around the wrapped call.
4. `operation_runner` captures and re-establishes the context around `on_result`.
5. Tests above; full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG — state plainly that off-thread
   execution was silently degrading op tags from definitional to guessed, and that this restores them.

## Definition of done
- An explicit `operation_context` exists and takes precedence in the hook.
- `@tags_layer` establishes it automatically for synchronous calls.
- Layers created in `operation_runner`'s `on_result` are tagged definitionally.
- The stack walk remains as a working fallback; name inference still marks itself as a guess.
- Full `pytest -m core` green.

## Cautions
- **`contextvars`, not a module global.** A global leaks across threads and mis-attributes layers —
  strictly worse than an absent tag, and it would violate the hook's own "an absent tag is honest"
  principle.
- **Keep the stack walk.** Removing it would regress every path not yet migrated.
- Preserve the `derived` vs `inferred` distinction exactly — it is the difference between a fact and a
  guess, and downstream resolution depends on it.
- Don't attempt to convert every call site to explicit contexts. The decorator (Part C) covers most;
  the runner (Part D) covers the broken case. Manual contexts are for the residue only.
