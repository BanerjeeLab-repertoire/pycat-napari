"""**Batch replay, made auditable against the operation vocabulary** (OperationSpec increment 3).

A batch step and a catalog operation are different abstraction levels — a *step* is a workflow stage
(`condensate_segmentation`), an *operation* is a layer-producing transform (`subcellular_segment`).
Measured in the increment-2 survey: `batch_step_registry._STEP_MAP` and the op catalog have **zero name
overlap**, because they name different things. So they are NOT unified; the honest relationship is
**composition** — a step *invokes* one or more operations — and that mapping is DECLARED
(`_STEP_OPERATIONS`), not inferred.

This is the validation that makes the declaration real: it ties batch replay to the operation graph
built in increment 2, so **renaming or removing an operation breaks the build here** instead of
silently breaking replay at run time. It is the prerequisite for ever *generating* batch steps
(increment 4+).

The operation vocabulary a step may name is the whole registry `build_operation_registry` knows: the
catalog's layer ops (`@tags_layer` / UI) **and** the curated measure/interpret ops. A step legitimately
invokes either kind (`cellpose_segmentation` → the `cellpose` layer op; `ivf_size_distribution` → the
`invitro.size_distribution` measure op).

Staged population, as in increment 2: only steps whose invoked ops are unambiguous are declared, behind
a downward-only coverage floor. A guessed mapping is worse than none.
"""

import pytest

pytestmark = pytest.mark.core

from pycat.batch_step_registry import _STEP_MAP, _STEP_OPERATIONS, step_operations
from pycat.navigator.op_catalog import _measure_ops, load_operation_catalog

# Coverage floor — how many steps declare their operations today. RATCHET: raise it as more steps are
# mapped; it must never decrease (a dropped mapping is a regression). Same discipline as the operation
# graph's input floor and the complexity budget's ceiling.
_MIN_STEPS_DECLARED = 10


def _operation_vocabulary():
    """Every operation id the registry knows: catalog layer ops ∪ curated measure/interpret ops."""
    catalog = {o["op"] for o in load_operation_catalog()}
    measure = {o["id"] for o in _measure_ops()}
    return catalog, measure, (catalog | measure)


def test_every_declared_operation_exists_in_the_vocabulary():
    """The whole point: a step names an op that no longer exists → this fails, naming the (step, op).

    This is what makes replay auditable — rename `cellpose` and the composition that references it is a
    build failure, not a silent replay break.
    """
    _catalog, _measure, universe = _operation_vocabulary()
    dangling = []
    for step, ops in _STEP_OPERATIONS.items():
        for op in ops:
            if op not in universe:
                dangling.append(f"step {step!r} invokes {op!r}, which is not a catalog or measure "
                                f"operation — it was renamed or removed")
    assert not dangling, (
        "batch step → operation composition references operations that do not exist:\n  "
        + "\n  ".join(dangling)
        + "\n\nEither the op was renamed (update _STEP_OPERATIONS) or the mapping was wrong.")


def test_every_declared_step_is_a_real_step():
    """A composition entry for a step name that is not in `_STEP_MAP` is stale — a step renamed or
    removed without updating its mapping. Fails, naming it."""
    unknown = sorted(s for s in _STEP_OPERATIONS if s not in _STEP_MAP)
    assert not unknown, (
        f"{len(unknown)} composition entr(y/ies) name a step not in _STEP_MAP: {unknown}. "
        f"The step was renamed or removed; update _STEP_OPERATIONS to match.")


def test_no_declaration_is_empty():
    """An empty tuple is ambiguous — it cannot be told from 'not yet declared'. A step that invokes no
    registered op is LEFT OUT of the map, not declared empty, so every present entry names ≥1 op."""
    empties = sorted(s for s, ops in _STEP_OPERATIONS.items() if not ops)
    assert not empties, (
        f"these steps are declared with an EMPTY operation tuple: {empties}. Omit a step that "
        f"invokes no registered operation instead of declaring it empty — empty is indistinguishable "
        f"from undeclared, which the coverage floor relies on.")


def test_the_operations_are_a_deduplicated_set_of_lowercase_ids():
    """Each mapping is a clean set of ids — no duplicates (a copy-paste slip), no case variants (the
    exact vocabulary rot the tag registry exists to prevent, one level up)."""
    problems = []
    for step, ops in _STEP_OPERATIONS.items():
        if len(set(ops)) != len(ops):
            problems.append(f"{step}: duplicate op in {ops}")
        for op in ops:
            if op != op.lower():
                problems.append(f"{step}: {op!r} is not lower-case")
    assert not problems, "\n  ".join([""] + problems)


def test_the_composition_coverage_does_not_regress():
    """A downward-only floor on how many steps declare their operations. Staged population raises it; a
    dropped mapping fails here."""
    declared = len(_STEP_OPERATIONS)
    assert declared >= _MIN_STEPS_DECLARED, (
        f"only {declared} batch steps declare their operations, below the floor of "
        f"{_MIN_STEPS_DECLARED}. A composition mapping was removed — a regression. (If intentional, "
        f"lower the floor with a reason; it is a ratchet, not a constant.)")


def test_the_accessor_agrees_with_the_declaration():
    """`step_operations(name)` is the public read of the map; an undeclared step returns ()."""
    for step, ops in _STEP_OPERATIONS.items():
        assert step_operations(step) == ops
    assert step_operations('a_step_that_does_not_exist') == ()


def test_undeclared_steps_are_reported_not_hidden():
    """Informational: surface which `_STEP_MAP` steps have no composition yet, so the remaining work is
    visible rather than silently absent. Not a failure — most skip-stubs invoke nothing."""
    undeclared = sorted(s for s in _STEP_MAP if s not in _STEP_OPERATIONS)
    print(f"[batch composition] {len(_STEP_OPERATIONS)}/{len(_STEP_MAP)} steps mapped; "
          f"{len(undeclared)} not yet declared: {undeclared}")
    # The map must at least be a strict subset of real steps (guarded above) and non-empty.
    assert _STEP_OPERATIONS, "no batch step declares its operations"
