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
from functools import lru_cache
from pathlib import Path
from typing import Optional

_BINDINGS_PATH = Path(__file__).parent / "data" / "section_bindings.json"


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
