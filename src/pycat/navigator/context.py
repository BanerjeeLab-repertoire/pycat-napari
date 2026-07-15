"""
context.py
==========

The propagating analysis context (PDF2's central idea: "every answer the user
gives updates a global analysis context; every tool asks 'is the context
sufficient for me?'").

Two design decisions here that go beyond the PDFs, both justified in the
stress-test doc:

1. **Every context field carries provenance and confidence** (failure mode #5:
   "don't ask, infer" is great until an inference is silently wrong). A field
   inferred from metadata with 0.6 confidence is *not* the same as one the user
   confirmed. Low-confidence inferences can be surfaced for confirmation instead
   of being trusted blindly.

2. **Data/context requirements are separated from product requirements**
   (failure mode: the PDFs conflate ``requires: time_series`` — a fact about the
   loaded data — with ``requires: labels`` — a product another module makes).
   Context requirements are answered by *this* object; product requirements are
   answered by the planner. See ``contracts.ModuleContract``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional


class Source(str, Enum):
    """Where a context fact came from — determines whether we trust it or ask."""
    USER = "user"            # explicitly answered / confirmed  -> confidence 1.0
    METADATA = "metadata"    # read from file metadata          -> usually high
    INFERRED = "inferred"    # heuristic guess from the data    -> often low
    MODULE = "module"        # written by a module that ran     -> high
    DEFAULT = "default"      # assumed default                  -> low


# how much we trust each source by default (a field may override its own value)
_DEFAULT_CONFIDENCE = {
    Source.USER: 1.0,
    Source.METADATA: 0.9,
    Source.MODULE: 0.95,
    Source.INFERRED: 0.55,
    Source.DEFAULT: 0.3,
}


@dataclass
class Fact:
    """A single context fact with provenance."""
    value: Any
    source: Source = Source.USER
    confidence: Optional[float] = None
    note: str = ""

    def __post_init__(self):
        if self.confidence is None:
            self.confidence = _DEFAULT_CONFIDENCE[self.source]

    @property
    def trusted(self) -> bool:
        return self.confidence >= 0.75

    def __repr__(self) -> str:
        return f"{self.value!r} ({self.source.value}, c={self.confidence:.2f})"


class AnalysisContext:
    """A merged Experiment / Image / Object / Measurement / Output context.

    The PDFs describe these as separate objects; in practice they share one
    key-value namespace with provenance, which is simpler and avoids the
    question of "which context owns this fact." Categories are kept only as
    documentation via ``CATEGORY``.
    """

    # canonical context keys, grouped for documentation / the sidebar UI
    CATEGORY: Dict[str, tuple[str, ...]] = {
        "experiment": ("system", "modality", "analysis_goal"),
        "image": ("dimensionality", "channels", "channel_labels", "axes",
                  "voxel_size", "time_points", "processing_history"),
        "object": ("has_objects", "labels_available", "object_target"),
        "measurement": ("selected_observables",),
        "output": ("desired_outputs",),
    }

    def __init__(self):
        self._facts: Dict[str, Fact] = {}

    # -- read/write -------------------------------------------------------- #
    def set(self, key: str, value: Any, source: Source = Source.USER,
            confidence: Optional[float] = None, note: str = "") -> "AnalysisContext":
        self._facts[key] = Fact(value, source, confidence, note)
        return self

    def get(self, key: str, default: Any = None) -> Any:
        f = self._facts.get(key)
        return f.value if f is not None else default

    def fact(self, key: str) -> Optional[Fact]:
        return self._facts.get(key)

    def known(self, key: str) -> bool:
        return key in self._facts

    def trusted(self, key: str) -> bool:
        """Known AND high-confidence. Low-confidence inferences are 'known but
        not trusted' — a question engine may still want to confirm them."""
        f = self._facts.get(key)
        return f is not None and f.trusted

    def low_confidence_fields(self, threshold: float = 0.75):
        """Fields worth confirming with the user (failure mode #5)."""
        return {k: f for k, f in self._facts.items() if f.confidence < threshold}

    # -- context-requirement predicates ------------------------------------ #
    # A ModuleContract.requires_context is a list of these keys; each maps to a
    # predicate over the context. This is the single source of truth for what
    # "time_series" or "two_channels" actually *means*.
    PREDICATES: Dict[str, Callable[["AnalysisContext"], Optional[bool]]] = {}

    def context_requirement(self, key: str) -> Optional[bool]:
        """Evaluate a named context requirement.
        Returns True (satisfied), False (violated), or None (unknown -> ask)."""
        pred = self.PREDICATES.get(key)
        if pred is None:
            raise KeyError(f"unknown context requirement {key!r}")
        return pred(self)

    def snapshot(self) -> Dict[str, Fact]:
        return dict(self._facts)

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={f}" for k, f in self._facts.items())
        return f"AnalysisContext({inner})"


# --------------------------------------------------------------------------- #
# Named context requirements. Returning None means "the data hasn't told us    #
# and the user hasn't said" -> the question engine turns it into a question.   #
# --------------------------------------------------------------------------- #
def _req_time_series(ctx: AnalysisContext) -> Optional[bool]:
    if ctx.known("axes"):
        return "time" in (ctx.get("axes") or [])
    if ctx.known("time_points"):
        tp = ctx.get("time_points")
        return tp is not None and tp > 1
    return None


def _req_two_channels(ctx: AnalysisContext) -> Optional[bool]:
    if ctx.known("channels"):
        return (ctx.get("channels") or 0) >= 2
    return None


def _req_z_stack(ctx: AnalysisContext) -> Optional[bool]:
    if ctx.known("axes"):
        return "z" in (ctx.get("axes") or [])
    return None


def _req_calibrated(ctx: AnalysisContext) -> Optional[bool]:
    if ctx.known("voxel_size"):
        return ctx.get("voxel_size") is not None
    return None


def _req_fluorescence(ctx: AnalysisContext) -> Optional[bool]:
    if ctx.known("modality"):
        return ctx.get("modality") == "fluorescence"
    return None


AnalysisContext.PREDICATES = {
    "time_series": _req_time_series,
    "two_channels": _req_two_channels,
    "z_stack": _req_z_stack,
    "calibrated": _req_calibrated,
    "fluorescence": _req_fluorescence,
}
