"""**Runnability gating — an operation says what it NEEDS, and can be gated with a stated reason.**

OperationSpec increment 5. `inputs` (increment 2) said which *layers* an operation consumes; this adds
`requirements` — the *data/environment* preconditions it needs to be runnable at all: a 3D z-stack, a
time axis, a calibrated pixel size, two channels, a GPU. Declared on `@tags_layer` from the controlled
`tag_registry.REQUIREMENTS` vocabulary (each value carrying a human-readable reason), and validated
here — because, per increment 1's rule, a field nothing checks is exactly the drift this effort exists
to prevent.

The point of declaring it: a consumer can gate an operation **before** the click and **say why**
(`runnability(spec, available)` → *"needs a 3D z-stack"*), instead of letting it fail at run time. That
is what lets the UI grey an op out with an explanation.

Four checks:
1. **Vocabulary agreement** — every declared requirement is in `REQUIREMENTS`; no free strings.
2. **Every requirement has a reason** — the vocabulary is a name→reason map, so the UI always has
   something to show; a requirement with no reason is a gate that cannot explain itself.
3. **Gating with a stated reason** — `runnability` returns `(False, reason)` naming what is missing and
   `(True, "")` once satisfied.
4. **Coverage ratchet** — the count of ops declaring `requirements` does not decrease.

Staged population, as in increment 2: only unambiguous preconditions are declared (the 3D ops need a
z-stack; the temporal ops need a time axis). A guessed requirement is worse than none.
"""

import pytest

pytestmark = pytest.mark.core

from pycat.navigator.operation_spec import (
    iter_operation_specs, runnability, unmet_requirements)
from pycat.utils.tag_registry import REQUIREMENTS, REQUIREMENT_NAMES

# Coverage floor — how many ops declare `requirements` today. RATCHET: raise it as more are annotated;
# it must never decrease. Same downward-only discipline as the input floor and the complexity budget.
_MIN_OPS_WITH_REQUIREMENTS = 8


def _specs():
    specs = iter_operation_specs()
    assert specs, "the operation registry is empty — nothing to validate"
    return specs


def test_every_declared_requirement_is_in_the_vocabulary():
    """No free strings — a requirement is from the controlled `REQUIREMENTS` set (enforced at import
    too; asserted here as the spec-level guarantee)."""
    offenders = []
    for spec in _specs():
        for req in spec.requirements:
            if req not in REQUIREMENTS:
                offenders.append(f"{spec.id}.requirements has {req!r}, not in {REQUIREMENT_NAMES}")
    assert not offenders, "\n  ".join([""] + offenders)


def test_every_requirement_in_the_vocabulary_has_a_human_reason():
    """The vocabulary is a name→reason map; the reason is what a gate SHOWS. A blank reason is a gate
    that cannot explain itself — the whole point of the field."""
    blank = sorted(name for name, reason in REQUIREMENTS.items() if not (reason or "").strip())
    assert not blank, (
        f"these requirements have no human-readable reason: {blank}. Every requirement must be able "
        f"to say what it needs, or the UI can gate but not explain.")


def test_runnability_gates_with_a_STATED_REASON():
    """The behaviour that makes the field worth declaring: an op with a requirement is NOT runnable
    when the precondition is absent, and the reason names it in human terms; it IS runnable once
    present."""
    by_id = {s.id: s for s in _specs()}

    # cellpose_3d needs a z-stack — a concrete, unambiguous case.
    spec = by_id.get("cellpose_3d")
    assert spec is not None and "z_stack" in spec.requirements, (
        "cellpose_3d should declare a z_stack requirement — the tranche changed; update this test")

    ok, reason = runnability(spec, available=set())
    assert ok is False and "z-stack" in reason.lower(), (
        f"a 3D op with no z-stack must be gated with a reason naming it; got ({ok!r}, {reason!r})")

    ok, reason = runnability(spec, available={"z_stack"})
    assert ok is True and reason == "", (
        f"once the z-stack is present the op must be runnable; got ({ok!r}, {reason!r})")

    # unmet_requirements is the raw form the reason is built from.
    assert unmet_requirements(spec, set()) == ("z_stack",)
    assert unmet_requirements(spec, {"z_stack"}) == ()


def test_an_op_with_no_requirements_is_always_runnable():
    """A root/plain op declares nothing and is never gated — the default must not accidentally block."""
    by_id = {s.id: s for s in _specs()}
    plain = by_id.get("clahe")            # a plain filter, no environmental precondition
    assert plain is not None and not plain.requirements
    assert runnability(plain, available=set()) == (True, "")


def test_a_multi_requirement_reason_reads_naturally():
    """If an op ever needs more than one thing, the gate lists them readably (a synthetic spec — no
    real op declares two yet, but the reason-builder must be correct for when one does)."""
    from pycat.navigator.operation_spec import OperationSpec
    spec = OperationSpec(id="_probe", role="image", summary="", target=None, produces="image",
                         aliases=(), registered_by=None, inputs=(),
                         requirements=("z_stack", "pixel_size"))
    ok, reason = runnability(spec, available=set())
    assert ok is False
    assert reason == "needs a 3D z-stack and a calibrated pixel size (microns per pixel)", reason


def test_the_requirement_declaration_count_does_not_regress():
    """A downward-only floor on how many ops declare `requirements`. Staged population raises it; a
    dropped declaration fails here."""
    count = sum(1 for s in _specs() if s.requirements)
    assert count >= _MIN_OPS_WITH_REQUIREMENTS, (
        f"only {count} operations declare `requirements`, below the floor of "
        f"{_MIN_OPS_WITH_REQUIREMENTS}. A declaration was removed — a gating regression. (If "
        f"intentional, lower the floor with a reason; it is a ratchet, not a constant.)")
