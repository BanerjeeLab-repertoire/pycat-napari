"""Navigator op-id → UI section-builder mapping (Spec 1.1 — the join that lets the Navigator generate a panel).

A PyCAT method panel is an ordered sequence of ``_add_*`` section builders; a Navigator plan is an ordered
sequence of steps. ``GeneratedMethodUI`` (Spec 1.2) joins them by calling the bound builder for each step's
op-id. This module is the lookup, and it is deliberately **explicit data** — ``data/section_bindings.json``,
following the ``layer_bindings.json`` precedent — not a runtime name-similarity guess. Keying is by the id a
``PlanStep`` carries: catalog op-ids for enhancement/segmentation/labels, measure-op ids
(``feature_analysis.cell_analysis`` …) for analysis steps.

``builder_for`` returns ``None`` for an unmapped or unresolvable op — never raises, never guesses. That is the
same refuse-to-guess discipline the execution adapters use: a caller renders a visible placeholder for the
missing step rather than silently dropping it or wiring the wrong control.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

_BINDINGS_PATH = Path(__file__).parent / "data" / "section_bindings.json"


@dataclass(frozen=True)
class PlannedSection:
    """One section of a generated method panel: the plan step's op-id, the ``_add_*`` builder and its owner
    (``None`` when the step has no section), and whether it is a gap (render a placeholder, never drop it).

    Deliberately static — it names the builder rather than binding it, so this whole structure is computable
    from a plan alone with no ``central_manager`` and no Qt. ``GeneratedMethodUI`` binds ``builder_name`` to a
    live callable at render time via :func:`builder_for`."""
    op_id: str
    builder_name: Optional[str]
    owner: Optional[str]
    gap: bool


@lru_cache(maxsize=1)
def _sections() -> dict:
    """The ``sections`` table from section_bindings.json (cached). Returns ``{}`` if the file is missing or
    malformed rather than raising at import — an absent mapping degrades to 'everything is a declared gap',
    which the generated panel renders as placeholders, not a crash."""
    try:
        with open(_BINDINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    sections = data.get("sections", {})
    return sections if isinstance(sections, dict) else {}


def section_for(op_id: str) -> Optional[dict]:
    """The ``{"builder": ..., "owner": ...}`` binding for an op-id, or ``None`` when it is unmapped. None is a
    real answer (this step has no interactive section), never a signal to guess one."""
    binding = _sections().get(op_id)
    return dict(binding) if isinstance(binding, dict) else None


def mapped_op_ids() -> frozenset:
    """Every op-id that has a section binding — the coverage ratchet (test_section_coverage) reads this."""
    return frozenset(_sections().keys())


def resolve_plan_sections(plan) -> List[PlannedSection]:
    """Walk a compiled plan in EXECUTION ORDER and return the ordered sections a generated panel must render.

    This is the join at the heart of the generated-method widget: a plan is an ordered list of steps, a panel is
    an ordered list of sections. It is pure over ``(plan, section_bindings)`` — no ``central_manager``, no Qt — so
    the plan→panel decision is fully testable headlessly; the Qt panel resolves each ``builder_name`` to a bound
    callable at render time. A step with no binding comes back with ``gap=True`` (and ``builder_name is None``) so
    the panel renders a visible placeholder naming the step rather than silently dropping it — dropping a step the
    plan said was necessary would be a scientific-integrity failure, not a UI gap. Order is the executor's, never
    re-derived."""
    from pycat.navigator.execution import execution_order

    sections: List[PlannedSection] = []
    for step in execution_order(plan):
        op_id = step.name
        binding = section_for(op_id)
        if binding:
            sections.append(PlannedSection(op_id, binding["builder"], binding["owner"], gap=False))
        else:
            sections.append(PlannedSection(op_id, None, None, gap=True))
    return sections


def placeholder_text(op_id: str) -> str:
    """The message a generated panel shows IN PLACE OF an unmapped step (`gap=True`). It names the step and says
    to run it from its own panel, so a step the plan deemed necessary is visibly deferred, never silently dropped
    — dropping it would be a scientific-integrity failure. Pure text, so the exact wording is tested headlessly;
    the Qt panel only wraps it in a QLabel."""
    label = op_id.split(".")[-1].replace("_", " ").strip() or op_id
    return (f"⚠  {label} — no panel section is wired for this step yet.\n"
            f"    Run it from its own method panel, then continue here.")


def builder_for(central_manager, op_id: str):
    """Resolve an op-id to a BOUND section-builder callable on ``central_manager``, or ``None``.

    Returns ``None`` — never raises, never falls back to a name-similarity guess — when the op is unmapped, the
    owner attribute is absent on ``central_manager``, or the named builder method does not exist. The caller
    (``GeneratedMethodUI``) turns a ``None`` into a visible placeholder naming the step and where to run it, so a
    plan step is never silently dropped."""
    binding = section_for(op_id)
    if not binding:
        return None
    owner = getattr(central_manager, binding.get("owner", ""), None)
    if owner is None:
        return None
    builder = getattr(owner, binding.get("builder", ""), None)
    return builder if callable(builder) else None
