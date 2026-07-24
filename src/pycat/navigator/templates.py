"""**Save an answered guided plan as a reusable method template** (selection_scale Part 3).

The navigator's payoff: the questionnaire produces a plan, and a plan the user likes should be reusable on
other data without re-answering. A guided template *is* a preset with provenance about how it was authored —
so it follows the ``analysis_presets`` discipline (it records WHERE it came from: the answers that produced
it) but is a **user-level, per-session-surviving** artefact, so it lives in the general ``user_settings``
service rather than the hardcoded, sensitivity-validated ``ANALYSIS_PRESETS`` registry.

**What is stored, and what is deliberately NOT.** A template stores the ANSWERS (the intent: observables,
target, and the question trail) plus the step names the plan produced and any parameters the user set. It does
**not** store the quality-gate verdicts. Those are a property of the DATA, not the plan — a template runnable
on one dataset may be blocked on another — so applying a template re-compiles the intent against the new
data's context, which re-evaluates every gate. Carrying a verdict over would assert quality that was never
checked here. A corrupt template entry degrades to "not available" (skipped), never a crash.
"""
from __future__ import annotations

import dataclasses

from .contracts import AnalysisIntent

#: user_settings key holding ``{name: serialized_template}``.
_STORE_KEY = "navigator.templates"


@dataclasses.dataclass(frozen=True)
class GuidedTemplate:
    """A saved guided plan. ``question`` is the answer trail that produced it — its provenance, so a user
    revisiting the template can see WHY the steps are there and edit the answers to regenerate rather than
    hand-edit steps. ``steps`` is the ordered step names the plan produced (for display / provenance);
    ``parameters`` are any values the user adjusted. Gate verdicts are intentionally absent — they are
    re-evaluated against the data a template is applied to."""
    name: str
    observables: tuple = ()
    target: str = None
    question: str = ""
    steps: tuple = ()
    parameters: dict = dataclasses.field(default_factory=dict)


def template_from_plan(name, intent, plan, *, parameters=None) -> GuidedTemplate:
    """Build a template from an answered ``intent`` and the ``plan`` it compiled to (the answers + the ordered
    step names). No verdicts are captured — see the module docstring."""
    return GuidedTemplate(
        name=str(name),
        observables=tuple(getattr(intent, "observables", ()) or ()),
        target=getattr(intent, "target", None),
        question=getattr(intent, "question", "") or "",
        steps=tuple(s.name for s in getattr(plan, "steps", ())),
        parameters=dict(parameters or {}))


def intent_from_template(template) -> AnalysisIntent:
    """Reconstruct the answers as a fresh :class:`AnalysisIntent`. Compile this against the CURRENT data's
    context to re-run every quality gate — a template blocked on the new data reports it, because the verdicts
    were never stored."""
    return AnalysisIntent(target=template.target, observables=list(template.observables),
                          question=template.question)


# ── persistence via user_settings (survives sessions, applies across datasets) ───────────────────────

def _store(store):
    if store is not None:
        return store
    from pycat.utils.user_settings import settings
    return settings()


def _serialize(t: GuidedTemplate) -> dict:
    return {"name": t.name, "observables": list(t.observables), "target": t.target,
            "question": t.question, "steps": list(t.steps), "parameters": dict(t.parameters)}


def _deserialize(d) -> "GuidedTemplate | None":
    try:
        return GuidedTemplate(
            name=str(d["name"]),
            observables=tuple(d.get("observables") or ()),
            target=d.get("target"),
            question=str(d.get("question") or ""),
            steps=tuple(d.get("steps") or ()),
            parameters=dict(d.get("parameters") or {}))
    except Exception:      # broad-ok: optional_probe — a corrupt entry degrades to "not available", not a crash
        return None


def _read_all(store) -> dict:
    raw = _store(store).get(_STORE_KEY, {}) or {}
    return raw if isinstance(raw, dict) else {}


def save_template(template: GuidedTemplate, *, store=None) -> GuidedTemplate:
    """Persist ``template`` under its name (overwriting a same-named one). Returns it."""
    s = _store(store)
    all_ = _read_all(s)
    all_[template.name] = _serialize(template)
    s.set(_STORE_KEY, all_)
    return template


def list_templates(*, store=None) -> list:
    """Every saved template, name-sorted; corrupt entries are skipped (degrade to 'not available')."""
    out = [_deserialize(v) for v in _read_all(store).values()]
    return sorted((t for t in out if t is not None), key=lambda t: t.name.lower())


def load_template(name, *, store=None):
    """The named template, or ``None`` if absent or corrupt."""
    return _deserialize(_read_all(store).get(name))


def delete_template(name, *, store=None) -> bool:
    """Remove the named template. Returns True if it existed."""
    s = _store(store)
    all_ = _read_all(s)
    if name not in all_:
        return False
    del all_[name]
    s.set(_STORE_KEY, all_)
    return True


def rename_template(old_name, new_name, *, store=None) -> bool:
    """Rename ``old_name`` to ``new_name``. No-op (False) if the source is absent or the target already
    exists — renaming must never silently overwrite another template."""
    s = _store(store)
    all_ = _read_all(s)
    if old_name not in all_ or new_name in all_ or not str(new_name).strip():
        return False
    entry = dict(all_.pop(old_name))
    entry["name"] = str(new_name)
    all_[str(new_name)] = entry
    s.set(_STORE_KEY, all_)
    return True
