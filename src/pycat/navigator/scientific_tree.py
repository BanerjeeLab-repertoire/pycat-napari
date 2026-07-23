"""
scientific_tree.py  —  the curated scientific decision graph
============================================================

Review finding #2 (question tree loaded but unused) and #3 (root overcommits the
user). Both are fixed here: the real Q001–Q060 tree from the ``Question Tree``
sheet is *navigated*, and a broad answer selects a **branch**, not a bag of
terminal observables. Observables are populated only when a leaf is reached.

Why a curated tree at all (not purely registry-derived questions): a module
registry can infer "I need two channels", but it cannot infer the *scientific*
distinction "are you asking whether intensities covary, whether A overlaps B
directionally, or whether discrete objects associate?" — that is Q020→Q021/Q022
in the sheet. Those distinctions are curated knowledge. So the engine is
hybrid: this tree drives scientific branching; module requirements drive only
missing-context questions (see ``question_engine.HybridQuestionEngine``).

The navigation (`next` pointers) is data from the sheet. The only curated code
here is the leaf→observable mapping, because the sheet expresses outcomes as
prose ("Counting workflow", "Coarsening fit") rather than controlled observable
values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .loader import QNode, load_question_tree

RETURN = "RETURN"


# Leaf → controlled observables. Keyed by node id; the value is either a flat
# list (any terminal response of that node yields it) or a dict of
# response-keyword → observables when the node's responses diverge. Grounded in
# the sheet's "Generated outcome" / "Tools" columns.
_LEAF_OBSERVABLES: Dict[str, object] = {
    # -- objects --
    "Q011": ["count"],                                   # abundance aggregation
    "Q012": {"yes": ["size", "shape"], "conventional": ["size", "shape"],
             "no": ["morphology"], "branching": ["morphology"]},
    "Q013": {"enrichment": ["partitioning"], "inside": ["partitioning"],
             "absolute": ["intensity"], "molecule": ["intensity"],
             "raw": ["intensity"], "integrated": ["intensity"]},
    "Q014": ["size"],                                    # distribution / subpopulations
    # -- spatial --
    "Q021": ["colocalization"],
    "Q022": ["colocalization"],
    "Q023": ["clustering"],
    "Q024": ["spatial_organization"],
    "Q025": ["spatial_organization"],
    # -- dynamics --
    "Q031": ["motion"],
    "Q032": {"coarsening": ["coarsening"], "size": ["coarsening"],
             "lifetime": ["count"], "birth": ["count"], "unknown": ["count", "size"]},
    "Q033": ["fusion"],
    "Q034": ["mobile_fraction"],
    "Q035": ["colocalization", "spatial_organization"],
    # -- biophysics --
    "Q041": ["intensity"],                               # molecular counting
    "Q042": ["diffusion", "viscosity"],
    "Q043": ["saturation_concentration", "partitioning"],
    "Q044": ["motion"],                                  # force spectroscopy (no clean observable)
    # -- networks --
    "Q060": {"length": ["morphology"], "width": ["morphology"],
             "connectivity": ["topology", "connectivity"], "graph": ["topology"],
             "fractal": ["topology"], "lacunarity": ["topology"]},
    # some Q030 responses go straight to a workflow without a sub-node
    "Q030": {"fusion": ["fusion"], "recovery": ["mobile_fraction"]},
}


@dataclass
class TreeState:
    qid: str = "Q001"
    observables: List[str] = field(default_factory=list)
    target: Optional[str] = None
    done: bool = False
    trail: List[Tuple[str, str]] = field(default_factory=list)   # (qid, response)


class ScientificTree:
    def __init__(self, nodes: Optional[Dict[str, QNode]] = None):
        # Load the curated question tree LAZILY — on first access, not at construction. `load_question_tree`
        # reads the shipped workbook via openpyxl (optional), so an eager load coupled merely CONSTRUCTING a
        # NavigatorSession to that optional dependency, even for a session that only edits/compiles a plan and
        # never asks a question. Deferring it keeps session construction workbook-free; only actually DRIVING
        # the tree needs openpyxl.
        self._nodes = nodes

    @property
    def nodes(self) -> Dict[str, QNode]:
        if self._nodes is None:
            self._nodes = load_question_tree()
        return self._nodes

    # ------------------------------------------------------------------ #
    def node(self, qid: str) -> Optional[QNode]:
        return self.nodes.get(qid)

    def root(self) -> QNode:
        return self.nodes["Q001"]

    def responses(self, qid: str) -> List[dict]:
        n = self.nodes.get(qid)
        return n.responses if n else []

    # ------------------------------------------------------------------ #
    def _observables_for(self, qid: str, response_text: str) -> List[str]:
        spec = _LEAF_OBSERVABLES.get(qid)
        if spec is None:
            return []
        if isinstance(spec, list):
            return list(spec)
        low = response_text.lower()
        for keyword, obs in spec.items():
            if keyword in low:
                return list(obs)
        return []

    def advance(self, state: TreeState, response_text: str) -> TreeState:
        """Apply a response at the current node. Returns the updated state:
        either moved to the next scientific node, or marked done with
        observables populated from the reached leaf."""
        node = self.nodes.get(state.qid)
        if node is None:
            state.done = True
            return state
        # match the chosen response (exact, else case-insensitive substring)
        chosen = next((r for r in node.responses if r["response"] == response_text), None)
        if chosen is None:
            low = response_text.lower()
            chosen = next((r for r in node.responses
                           if low in r["response"].lower() or r["response"].lower() in low), None)
        if chosen is None:
            raise ValueError(f"{response_text!r} is not a response at {state.qid}")

        state.trail.append((state.qid, chosen["response"]))
        nxt = (chosen.get("next") or "").strip()

        # observables from THIS response, if it is (or leads to) a leaf
        state.observables += [o for o in self._observables_for(state.qid, chosen["response"])
                              if o not in state.observables]

        if nxt in ("", RETURN) or nxt not in self.nodes:
            # terminal: if the response mapped to no observable but the target
            # node has a flat leaf map, try that node too.
            if nxt not in ("", RETURN) and nxt not in self.nodes:
                state.observables += [o for o in self._observables_for(nxt, chosen["response"])
                                      if o not in state.observables]
            state.done = True
            return state

        state.qid = nxt
        # a branch node that also carries a leaf mapping for this response has
        # already contributed observables; keep navigating.
        return state

    def walk(self, responses: List[str], target: Optional[str] = None) -> TreeState:
        """Convenience: apply a full list of responses from the root."""
        st = TreeState(target=target)
        for r in responses:
            if st.done:
                break
            self.advance(st, r)
        return st
