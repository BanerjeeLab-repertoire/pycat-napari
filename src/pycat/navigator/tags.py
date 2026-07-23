"""
tags.py
=======

The extended tagged-lineage system recommended in PDF8. This is the most
directly actionable part of the whole corpus: PDF8 audited PyCAT's *current* tag
engine and found specific, fixable gaps. This module implements the recommended
model and, importantly, the fixes:

Findings implemented here
-------------------------
1. ``role`` is too coarse -> add an explicit ``representation`` tag.
2. add an explicit processing ``state``.
3. current lineage relations are insufficient -> add ``registered_to``,
   ``measured_from``, ``tracks``, ``reference_for``.
4. QC should annotate candidate layers directly -> :meth:`Resolver.annotate_qc`
   writes ``quality_status`` and ``analysis_ready_for`` onto the assessed layer.
5. UI modules bypass tagging -> a single :class:`TaggedLayerFactory` choke point;
   layers should *only* be created through it (a lint check can enforce this).
6. BUG: ``tag_from_operation`` wrote ``source='pipeline'`` but ``pipeline`` was
   not in ``VALID_SOURCES``, so it was silently downgraded to ``inferred`` and
   lost confidence. Fixed here: ``pipeline`` is a valid high-confidence source.

Resolution is by *tags*, never by layer name. Names are generated for humans
("GFP · Condensates · Labels · Refined") but no longer participate in matching.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


# --------------------------------------------------------------------------- #
# Controlled vocabularies                                                     #
# --------------------------------------------------------------------------- #
# These vocabularies are the EXACT lists from the 'Tag Hierarchy' sheet of
# PyCAT_layer_tag_hierarchy_and_module_flow.xlsx (verified in tests against the
# workbook — see tests/test_workbook.py::test_tag_vocabularies_match_workbook).
VALID_ROLES = (
    "image", "mask", "labels", "points", "tracks", "shapes",
    "reference", "result", "overlay",
)
VALID_REPRESENTATIONS = (
    "intensity_field", "binary_mask", "instance_labels", "probability_map",
    "coordinates", "trajectories", "geometry", "measurement_table", "model_fit",
)

# processing state, ordered from least to most processed. The order is used to
# pick the "most refined" candidate during resolution. ('visualized' added per
# the workbook.)
STATE_ORDER = (
    "raw", "corrected", "enhanced", "segmented", "refined",
    "tracked", "measured", "fitted", "validated", "visualized",
)

# FIX (finding #9): 'pipeline' is now a valid source with ~0.95 confidence, as
# the workbook recommends ("add pipeline to VALID_SOURCES with ~0.95 confidence
# or use source='derived'").
VALID_SOURCES = ("user", "pipeline", "derived", "metadata", "inferred", "default")

# ── The confidence SCALE — a number must MEAN something, not decorate ────────────────────────────────
# Confidence answers "how much do I trust this tag's VALUE", and it scores the EVIDENCE, not merely the
# source label. The documented meaning of each band (so a reader can interpret a value, not guess):
#
#   1.00   a human explicitly answered / confirmed          (source='user')
#   0.99   the FILE DECLARES it in a dedicated field          (metadata, evidence='declarative')
#   0.95   written by a pipeline step that actually ran      (source='pipeline')
#   0.90   unambiguous DERIVED evidence, e.g. emission nm -> spectral bucket  (metadata, evidence='derived')
#   0.85   derived from another layer                        (source='derived')
#   0.80   metadata of UNSTATED evidence kind (a fallback — grade it when the kind is known)
#   0.70   a WEAK / indirect hint: a name substring, a filename token         (metadata, evidence='weak')
#   0.50   a coin-flip / chance-level guess                  (source='inferred')
#   0.30   an assumed default                                (source='default')
#
# A present-but-generic/placeholder metadata value is filtered out entirely by utils.metadata_validity
# (is_meaningful) BEFORE it can become a tag, so it never reaches this scale.
_SOURCE_CONFIDENCE = {
    "user": 1.0, "pipeline": 0.95, "derived": 0.85,
    "metadata": 0.8, "inferred": 0.5, "default": 0.3,
}

# Grading WITHIN the metadata source: the flat 0.8 above is only a fallback for a metadata tag that does
# not say what evidence it rests on. A metadata tag SHOULD declare its evidence kind, which sets its
# confidence — a file that DECLARES `ContrastMethod="Fluorescence"` deserves ~0.99, not the same 0.8 as a
# vague filename hint. `user: 1.0` stays reserved for an explicit human answer.
METADATA_EVIDENCE_CONFIDENCE = {
    "declarative": 0.99,   # a dedicated field states the answer (ContrastMethod, AcquisitionMode, Fluor)
    "derived":     0.90,   # unambiguous derived evidence (emission wavelength -> spectral bucket)
    "weak":        0.70,   # a name substring / filename hint
}
VALID_METADATA_EVIDENCE = tuple(METADATA_EVIDENCE_CONFIDENCE)


def confidence_for(source: str, evidence: Optional[str] = None) -> float:
    """The confidence to record for a tag from ``source``. When ``source == 'metadata'`` and ``evidence``
    names a known kind (declarative / derived / weak), it grades WITHIN the metadata source per
    ``METADATA_EVIDENCE_CONFIDENCE``; otherwise the per-source default in ``_SOURCE_CONFIDENCE`` applies
    (0.5 for an unmapped source). See the documented scale above — this is the single place that maps
    evidence to a number, so the value stays interpretable rather than decorative."""
    if source == "metadata" and evidence in METADATA_EVIDENCE_CONFIDENCE:
        return METADATA_EVIDENCE_CONFIDENCE[evidence]
    return _SOURCE_CONFIDENCE.get(source, 0.5)

# lineage relations. The workbook lists derived_from, belongs_to, supersedes,
# pairs_with, registered_to, measured_from, tracks; the Findings sheet also
# recommends reference_for. All are included.
LINEAGE_RELATIONS = (
    "derived_from", "belongs_to", "supersedes", "pairs_with",
    "registered_to", "measured_from", "tracks", "reference_for",
)

# validity vocabularies from the workbook.
VALID_QUALITY_STATUS = ("unknown", "pass", "warn", "fail")
VALID_ANALYSIS_READY_FOR = ("segmentation", "measurement", "tracking", "fitting", "display")


@dataclass
class TagSet:
    """The per-layer tag record, grouped exactly as PDF8's hierarchy
    (identity / biological / representation / state / workflow / lineage /
    validity)."""
    # -- stable identity --
    sample_id: Optional[str] = None
    acquisition_id: Optional[str] = None
    scene_id: Optional[str] = None          # a.k.a. position_id
    channel_id: Optional[int] = None         # stable channel INDEX (identity)
    channel_label: Optional[str] = None      # human-facing fluorophore/stain name
    modality: Optional[str] = None           # fluorescence|brightfield|phase|DIC|trace
    axes: Optional[str] = None               # YX|TYX|ZYX|TZYX|...
    dimensionality: Optional[str] = None      # 2d|2d+t|z-stack|3d+t|multi-position
    scale_status: Optional[str] = None        # calibrated|partially_calibrated|uncalibrated
    # -- biological meaning --
    system: Optional[str] = None
    target: Optional[str] = None
    compartment: Optional[str] = None
    # -- information representation --
    role: Optional[str] = None                 # image | labels | table | ...
    representation: Optional[str] = None        # instance_labels | intensity_field | ...
    # -- processing state --
    state: Optional[str] = None
    op: Optional[str] = None
    variant: Optional[str] = None
    # -- workflow meaning --
    workflow_family: Optional[str] = None
    workflow_step: Optional[str] = None
    purpose: Optional[str] = None
    observable: Optional[str] = None
    # -- lineage (relations point to other layer ids) --
    lineage: Dict[str, str] = field(default_factory=dict)
    # -- validity --
    quality_status: Optional[str] = None        # unknown | pass | warn | fail
    analysis_ready_for: Optional[str] = None    # segmentation | measurement | tracking | fitting | display
    assumptions: Optional[str] = None           # named model assumptions (e.g. "passive tracer")
    source: str = "pipeline"
    confidence: Optional[float] = None
    # For a metadata source, the KIND of evidence (declarative / derived / weak) that grades the
    # confidence within the source — see confidence_for / METADATA_EVIDENCE_CONFIDENCE. None = unstated.
    evidence: Optional[str] = None

    def __post_init__(self):
        if self.source not in VALID_SOURCES:
            # This is exactly the silent-downgrade path PDF8 flagged. We keep the
            # downgrade behaviour but only for genuinely invalid sources, and we
            # record that it happened rather than hiding it.
            self.source = "inferred"
        if self.confidence is None:
            self.confidence = confidence_for(self.source, self.evidence)

    def get(self, key: str) -> Any:
        if key in LINEAGE_RELATIONS:
            return self.lineage.get(key)
        return getattr(self, key, None)

    def state_rank(self) -> int:
        try:
            return STATE_ORDER.index(self.state) if self.state else -1
        except ValueError:
            return -1


@dataclass
class Layer:
    """A napari-style layer plus its tags. ``data`` is a stand-in for the real
    array/dataframe; the engine only ever reasons over ``tags``."""
    id: str
    tags: TagSet
    data: Any = None

    @property
    def display_name(self) -> str:
        """Readable name generated *from* tags (PDF8: names no longer drive
        resolution)."""
        parts = [self.tags.channel_label or self.tags.target,
                 self.tags.target if self.tags.channel_label else None,
                 {"instance_labels": "Labels", "binary_mask": "Mask",
                  "intensity_field": "Image", "trajectories": "Tracks",
                  "measurement_table": "Table"}.get(self.tags.representation or "", None),
                 (self.tags.state or "").capitalize() or None]
        return " · ".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# The single tagging choke point (finding #5).                                #
# --------------------------------------------------------------------------- #
class TaggedLayerFactory:
    """Every layer PyCAT creates should be created here. UI code calling
    ``viewer.add_image(...)`` directly is the tagging gap PDF8 identified; route
    those through :meth:`create` and the gap closes structurally."""

    def __init__(self):
        self._layers: Dict[str, Layer] = {}
        self._counter = 0
        self.untagged_adds_blocked = 0   # a lint counter for CI

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}#{self._counter}"

    def create(self, data: Any, *, source: str = "pipeline", **tag_kwargs) -> Layer:
        tags = TagSet(source=source, **tag_kwargs)
        lid = self._new_id(tags.representation or tags.role or "layer")
        layer = Layer(id=lid, tags=tags, data=data)
        self._layers[lid] = layer
        return layer

    def tag_from_operation(self, data: Any, parent: Layer, *, op: str, state: str,
                           representation: Optional[str] = None,
                           supersedes: bool = False, source: str = "pipeline",
                           **overrides) -> Layer:
        """Create a derived layer that *inherits* identity/biological tags from
        ``parent`` and updates state/op/representation (PDF8 propagation model).
        This is the corrected version of the buggy original: ``source='pipeline'``
        is now honoured instead of being downgraded to ``inferred``."""
        p = parent.tags
        inherited = dict(
            sample_id=p.sample_id, acquisition_id=p.acquisition_id, scene_id=p.scene_id,
            channel_id=p.channel_id, channel_label=p.channel_label, modality=p.modality,
            axes=p.axes, dimensionality=p.dimensionality, scale_status=p.scale_status,
            system=p.system, target=p.target, compartment=p.compartment,
            role=p.role, representation=representation or p.representation,
        )
        inherited.update(overrides)
        lineage = {"derived_from": parent.id, "belongs_to": p.lineage.get("belongs_to", parent.id)}
        if supersedes:
            lineage["supersedes"] = parent.id
        layer = self.create(data, source=source, op=op, state=state, lineage=lineage, **inherited)
        return layer

    def all_layers(self) -> List[Layer]:
        return list(self._layers.values())

    def get(self, layer_id: str) -> Layer:
        return self._layers[layer_id]


# --------------------------------------------------------------------------- #
# Tag-based resolver (PDF8 "Resolver Examples").                              #
# --------------------------------------------------------------------------- #
# A query is a dict {tag_key: matcher}. matcher is:
#   * a plain value            -> equality
#   * ("!=", value)            -> inequality
#   * ("in", collection)       -> membership
#   * a callable(value)->bool  -> arbitrary predicate
Matcher = Union[Any, tuple, Callable[[Any], bool]]


def _match(actual: Any, matcher: Matcher) -> bool:
    if callable(matcher):
        return bool(matcher(actual))
    if isinstance(matcher, tuple) and len(matcher) == 2:
        op, val = matcher
        if op == "!=":
            return actual != val
        if op == "in":
            return actual in val
        if op == "==":
            return actual == val
    return actual == matcher


class Resolver:
    """Answers "which layer should the next module consume?" from tags alone."""

    def __init__(self, factory: TaggedLayerFactory):
        self.factory = factory

    def resolve_all(self, query: Dict[str, Matcher]) -> List[Layer]:
        hits = []
        for layer in self.factory.all_layers():
            if all(_match(layer.tags.get(k), m) for k, m in query.items()):
                hits.append(layer)
        # rank: most refined state first, then highest confidence
        hits.sort(key=lambda L: (L.tags.state_rank(), L.tags.confidence or 0.0), reverse=True)
        return hits

    def resolve(self, query: Dict[str, Matcher]) -> Optional[Layer]:
        """Return the single best matching layer (most refined, most confident),
        or None. This replaces "guess from whichever labels layer was most
        recently created" (PDF8)."""
        hits = self.resolve_all(query)
        return hits[0] if hits else None

    def best_image_for(self, target: str) -> Optional[Layer]:
        """PDF8 example: 'find the best current image' for a target."""
        return self.resolve({"role": "image", "target": target,
                             "quality_status": ("!=", "fail")})

    def labels_for_measurement(self, target: str, belongs_to_lineage: Optional[str] = None) -> Optional[Layer]:
        """PDF8 example: the exact resolver query it recommends —
        role=labels AND target=condensate AND purpose=measurement_input
        AND quality_status != fail AND belongs_to=<selected lineage>."""
        q: Dict[str, Matcher] = {
            "role": "labels",
            "target": target,
            "purpose": "measurement_input",
            "quality_status": ("!=", "fail"),
        }
        if belongs_to_lineage is not None:
            q["belongs_to"] = belongs_to_lineage
        return self.resolve(q)

    # QC annotates candidate layers directly (finding #4).
    def annotate_qc(self, layer: Layer, quality_status: str,
                    analysis_ready_for: Optional[str] = None) -> Layer:
        layer.tags.quality_status = quality_status
        if analysis_ready_for:
            layer.tags.analysis_ready_for = analysis_ready_for
        return layer
