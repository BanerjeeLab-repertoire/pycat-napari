"""**The operation vocabulary is a GRAPH, and this is what makes the `inputs` field real.**

OperationSpec increment 1 deferred `inputs` with an explicit rule: *"a field that nothing checks is
exactly the drift this effort exists to prevent."* Increment 2 adds `inputs` — the role(s)/target(s)
an operation CONSUMES — and with `produces` that turns the flat list of 79 operations into a directed
graph (`op_a.produces -> op_b.inputs`). Increments 3–5 (batch composition, subsystem generation,
runnability gating) all need that graph. This test is the validation that lets the field ship.

Four checks:
1. **No dangling edges** — every declared role input is produced by some operation, or is a ROOT role
   (loaded from a file, not from another layer). An input nothing can make is named with its op.
2. **Vocabulary agreement** — every input is a registered `ROLES`/`TARGETS` value; no free strings.
3. **Traversability** — from the root role(s) the reachable operations are computable; an operation
   that declares inputs but is unreachable from a root is *reported*, not failed (it may be a
   legitimate UI-only op) — a real smell worth surfacing.
4. **Coverage ratchet** — the count of operations declaring `inputs` may not DECREASE. This is how
   the declarations populate incrementally (a first unambiguous tranche now, more later) without
   silently regressing — the downward-only idiom of the complexity budget, inverted to a floor.

Staged population is expected: not all 79 ops declare `inputs` yet. Declaring an input you had to
guess is worse than declaring none, so only the unambiguous image-consuming tranche is annotated.
"""

import pytest

pytestmark = pytest.mark.base

from pycat.navigator.operation_spec import iter_operation_specs
from pycat.utils.tag_registry import ROLES, TARGETS


# The ROOT roles: a layer of this role enters the graph from a FILE, not from another operation. Named
# explicitly here (not inferred) — today the only root is a loaded image.
_ROOT_ROLES = frozenset({'image'})

# Coverage floor — the number of operations that declare `inputs` today. RATCHET: raise it when a new
# tranche is annotated; it must never decrease. (A lower bound, the complexity budget's ceiling
# inverted — same downward-only discipline, so a dropped declaration fails loudly.)
_MIN_OPS_WITH_INPUTS = 23


def _specs():
    specs = iter_operation_specs()
    assert specs, "the operation registry is empty — nothing to validate"
    return specs


def test_no_dangling_edges_every_input_role_is_produced_or_a_root():
    """A declared input role must be makeable — produced by some op, or a root loaded from disk."""
    specs = _specs()
    produced_roles = {s.produces for s in specs}

    dangling = []
    for spec in specs:
        for inp in spec.inputs:
            # Role-typed inputs are the edges: they must be produced by an op or be a root. Target-
            # typed inputs describe what a layer is OF and ride along by tag propagation, so they are
            # not subject to the produced-by-an-op rule.
            if inp in ROLES and inp not in produced_roles and inp not in _ROOT_ROLES:
                dangling.append(f"{spec.id} consumes role {inp!r}, which no operation produces "
                                f"and which is not a root role {sorted(_ROOT_ROLES)}")
    assert not dangling, (
        "operations declare an input nothing can supply (a dangling graph edge):\n  "
        + "\n  ".join(dangling))


def test_every_input_is_in_the_role_or_target_vocabulary():
    """No free strings — an input is drawn from the SAME `ROLES`/`TARGETS` vocabulary as everything
    else. (Enforced at import too; asserted here as the graph-level guarantee.)"""
    offenders = []
    for spec in _specs():
        for inp in spec.inputs:
            if inp not in ROLES and inp not in TARGETS:
                offenders.append(f"{spec.id}.inputs has {inp!r}, not in ROLES {ROLES} "
                                 f"or TARGETS {TARGETS}")
    assert not offenders, "\n  ".join([""] + offenders)


def test_the_graph_is_traversable_from_the_roots():
    """From the root role(s), the reachable operations are computable by fixpoint; an input-bearing
    op that is unreachable is REPORTED (a smell), not failed."""
    specs = _specs()
    available = set(_ROOT_ROLES)
    reachable = set()

    # Fixpoint: an op runs once all its inputs are available; running it makes its `produces` available.
    changed = True
    while changed:
        changed = False
        for spec in specs:
            if spec.id in reachable:
                continue
            role_inputs = [i for i in spec.inputs if i in ROLES]
            if all(i in available for i in role_inputs):
                reachable.add(spec.id)
                if spec.produces not in available:
                    available.add(spec.produces)
                changed = True

    # Every root (no role inputs) is trivially reachable, so an UNreachable op must declare inputs.
    unreachable = sorted(s.id for s in specs
                         if s.id not in reachable and any(i in ROLES for i in s.inputs))
    if unreachable:
        # A report, not a failure — surfaced so it is not invisible.
        print(f"[operation graph] {len(unreachable)} input-bearing op(s) unreachable from roots "
              f"{sorted(_ROOT_ROLES)}: {unreachable}")

    # The graph must at least be non-trivial: the annotated tranche IS reachable (they consume 'image').
    reachable_with_inputs = [s.id for s in specs if s.inputs and s.id in reachable]
    assert reachable_with_inputs, "no input-declaring operation is reachable from a root — the graph "\
                                  "has edges but none connect to where data enters"


def test_the_input_declaration_count_does_not_regress():
    """A downward-only FLOOR on how many ops declare `inputs`. Staged population raises it; a dropped
    declaration (a regression) fails here."""
    count = sum(1 for s in _specs() if s.inputs)
    assert count >= _MIN_OPS_WITH_INPUTS, (
        f"only {count} operations declare `inputs`, below the floor of {_MIN_OPS_WITH_INPUTS}. A "
        f"declaration was removed — that is a graph regression. (If you INTENTIONALLY removed one, "
        f"lower the floor with a reason; it is a ratchet, not a constant.)")
    # Not an equality assert: annotating more ops must not require editing this test each time.


def test_a_declared_input_is_actually_a_graph_edge_to_a_real_producer():
    """Beyond 'not dangling': at least one annotated op's input is satisfied by a genuine PRODUCER
    (not only by the root), so the graph has a real op→op edge and increment 3 has something to
    compose. The image filters both consume and produce 'image', so 'image' is such an edge."""
    specs = _specs()
    producers_of = {}
    for s in specs:
        producers_of.setdefault(s.produces, []).append(s.id)

    edges = []
    for s in specs:
        for inp in s.inputs:
            real_producers = [p for p in producers_of.get(inp, []) if p != s.id]
            if real_producers:
                edges.append((real_producers[0], inp, s.id))
    assert edges, ("no operation consumes a role another operation produces — the vocabulary is "
                   "annotated but still not a connected graph")
