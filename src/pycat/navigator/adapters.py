"""
adapters.py  —  the seam between the navigator and real PyCAT
=============================================================

Review findings #7 and #8: the navigator should not ship a competing tag system,
and the planner should reuse an existing suitable layer before planning a
producer. Both are addressed by depending on a thin **protocol** that real PyCAT
satisfies with its existing machinery, plus an in-memory implementation for
standalone use and tests.

* ``LayerResolverProtocol`` — "does a layer satisfying this capability already
  exist?" In real PyCAT this is implemented by wrapping
  ``pycat.utils.tag_resolver.resolve(viewer, query)`` (which already returns a
  layer + a ``certain/likely/ambiguous/none`` confidence + a reason). The
  navigator NEVER imports napari; it only speaks this protocol.
* ``capability_to_query`` — translate a typed ``Capability`` into the tag query
  the real resolver understands (``role`` / ``representation`` / ``target``).
* ``InMemoryLayerResolver`` — a standalone implementation over a list of tagged
  session layers, used by the demo and tests. It mirrors the real resolver's
  confidence contract.

Integration boundary: replace ``InMemoryLayerResolver`` with a
``TagResolverAdapter`` that calls ``resolve(viewer, query)`` — see
``docs`` / ``CODE_FINDINGS.md``. Everything above that line is standalone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from .capabilities import Capability, representation_satisfies
from .tags import STATE_ORDER

# confidence contract — identical vocabulary to pycat.utils.tag_resolver
CERTAIN, LIKELY, AMBIGUOUS, NONE = "certain", "likely", "ambiguous", "none"

# representation -> napari-style role, for the tag query the real resolver reads.
_REPR_TO_ROLE = {
    "intensity_field": "image", "probability_map": "image", "raw_image": "image",
    "binary_mask": "mask", "instance_labels": "labels",
    "coordinates": "points", "trajectories": "tracks", "geometry": "shapes",
    "measurement_table": "result", "model_fit": "result",
}


@dataclass
class LayerBinding:
    capability: Capability
    layer_id: Optional[str]
    layer_name: Optional[str]
    confidence: str            # CERTAIN | LIKELY | AMBIGUOUS | NONE
    reason: str

    @property
    def usable(self) -> bool:
        # mirror autopopulate(): bind on certain, or likely (with a note)
        return self.confidence in (CERTAIN, LIKELY)


def capability_to_query(cap: Capability) -> Dict[str, object]:
    """Typed capability -> tag query dict (role/representation/target)."""
    q: Dict[str, object] = {"representation": cap.kind,
                            "role": _REPR_TO_ROLE.get(cap.kind, cap.kind)}
    tgt = cap.target()
    if tgt and tgt != "*":
        q["target"] = tgt
    return q


class LayerResolverProtocol(Protocol):
    def find(self, cap: Capability) -> LayerBinding:
        """Return the best existing layer satisfying ``cap`` (or a NONE binding)."""
        ...


# --------------------------------------------------------------------------- #
# Standalone implementation (demo/tests). Real PyCAT uses a TagResolverAdapter. #
# --------------------------------------------------------------------------- #
@dataclass
class SessionLayer:
    name: str
    representation: str
    target: Optional[str] = None
    state: Optional[str] = None
    quality_status: str = "unknown"

    def _rank(self) -> int:
        try:
            return STATE_ORDER.index(self.state) if self.state else -1
        except ValueError:
            return -1


class InMemoryLayerResolver:
    """Resolves capabilities against an in-memory list of tagged layers, with
    the same confidence semantics as the real resolver: exactly one match is
    CERTAIN, several is LIKELY (prefer the most-refined), none is NONE, and a
    failed-QC layer is never used."""

    def __init__(self, layers: Optional[List[SessionLayer]] = None):
        self.layers: List[SessionLayer] = list(layers or [])

    def add(self, layer: SessionLayer) -> "InMemoryLayerResolver":
        self.layers.append(layer)
        return self

    def find(self, cap: Capability) -> LayerBinding:
        want_target = cap.target()
        cands = []
        for L in self.layers:
            if not representation_satisfies(L.representation, cap.kind):
                continue
            if want_target and want_target != "*" and L.target not in (want_target, None):
                continue
            if L.quality_status == "fail":
                continue
            cands.append(L)
        if not cands:
            return LayerBinding(cap, None, None, NONE,
                                f"no existing layer satisfies {cap}")
        cands.sort(key=lambda L: L._rank(), reverse=True)
        if len(cands) == 1:
            L = cands[0]
            return LayerBinding(cap, L.name, L.name, CERTAIN,
                                f"'{L.name}' is the only layer satisfying {cap}")
        best = cands[0]
        return LayerBinding(cap, best.name, best.name, LIKELY,
                            f"{len(cands)} layers satisfy {cap}; chose '{best.name}' "
                            f"(most refined: state={best.state}). Check this is intended.")
