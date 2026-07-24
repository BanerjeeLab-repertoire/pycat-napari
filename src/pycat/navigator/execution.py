"""**The gate-respecting execution ORDER of a compiled navigator plan** (selection_scale Part 2).

The planner produces a quality-gated `Plan`; this turns it into the sequence things must run in and each
step's runnability, honouring the gate semantics increment 2 computed:

* **QC probes run first** (the planner prepended them for the UNKNOWN gates they resolve);
* a **blocker** stops the run at that step — it does not run, and nothing after it does either, because the
  downstream steps depend on a product it never produced — with the stated reason;
* a **soft caveat** (amber) runs, but the caveat is carried on the step;
* every other step runs.

**It deliberately does NOT invoke the science.** PyCAT's operations have bespoke, panel-collected signatures
(e.g. ``segment_subcellular_objects(original_image, pre_processed_image, cell_mask, cell_label, ball_radius,
…)``) threaded from prior steps and tuned by the user — there is no uniform "run this op" path, so producing a
step's *result* is what its method panel does. This module is the gate-respecting plan a runner (or a guided
hand-off to the real panels — the "same computation" route) drives; the per-operation execution ADAPTERS that
would let the Run button compute each step end to end are a separate, larger layer. Qt-free and testable, so
the ordering/gating contract is pinned even while the adapters are built."""
from __future__ import annotations

import dataclasses

from .session import plan_rows


#: How a plan row's gate state maps to an execution status.
_STATUS = {"probe": "run", "ok": "run", "downgraded": "caveat", "blocked": "blocked"}


@dataclasses.dataclass(frozen=True)
class ExecStep:
    """One step in execution order. ``status`` is:

    * ``'run'`` — runs (a clean step or a QC probe);
    * ``'caveat'`` — runs, but a soft gate is VIOLATED and its reason is carried;
    * ``'blocked'`` — a hard precondition is unmet; the run stops here with ``reason``;
    * ``'skipped'`` — after a blocker: depends (directly or transitively) on the step that could not run.
    """
    name: str
    kind: str                 # 'probe' | 'step'
    status: str
    reason: str = ""


def execution_order(plan) -> list:
    """The ordered :class:`ExecStep` sequence for ``plan``, honouring the gate semantics. Probes lead; the
    first blocker halts the run (it is ``'blocked'`` and everything after is ``'skipped'``); soft caveats run
    with their reason attached. This is the contract a runner or a guided panel hand-off executes."""
    rows = plan_rows(plan)
    out = []
    halted = False
    for row in rows:
        if halted:
            out.append(ExecStep(row.name, row.kind, "skipped",
                                "does not run — an earlier step it depends on is blocked"))
            continue
        status = _STATUS.get(row.state, "run")
        # the reason for a caveat/blocker is the first matching gate note on the row
        reason = ""
        if status in ("caveat", "blocked"):
            want = "downgraded" if status == "caveat" else "blocked"
            note = next((n for n in row.gates if n.kind == want), None)
            reason = note.reason if note is not None else row.reason
        out.append(ExecStep(row.name, row.kind, status, reason))
        if status == "blocked":
            halted = True
    return out


def is_runnable(plan) -> bool:
    """True if the plan can run to completion — no step is blocked. (A caveat does not block; an unmet hard
    precondition does.)"""
    return not any(s.status == "blocked" for s in execution_order(plan))


def first_blocker(plan):
    """The first :class:`ExecStep` that halts the run, or ``None`` — what the Run button reports when disabled."""
    return next((s for s in execution_order(plan) if s.status == "blocked"), None)


def execution_briefing(plan) -> str:
    """A one-paragraph, gate-aware summary of what running the plan would do — the honest text the guided panel
    shows in place of a silent action, naming the order, the caveats, and any blocker with its reason."""
    steps = execution_order(plan)
    runnable = [s for s in steps if s.status in ("run", "caveat")]
    order = " → ".join(s.name for s in runnable) if runnable else "(nothing runnable)"
    lines = [f"Run order: {order}."]
    for s in steps:
        if s.status == "caveat":
            lines.append(f"• {s.name}: runs, with a caveat — {s.reason}")
        elif s.status == "blocked":
            lines.append(f"⛔ {s.name}: cannot run — {s.reason}. The run stops here.")
    return "\n".join(lines)
