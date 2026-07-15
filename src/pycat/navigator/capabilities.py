"""
capabilities.py
===============

The *typed vocabulary* that lets PyCAT modules connect to one another
automatically.

Why this file exists
--------------------
The brainstorming PDFs propose that "workflows assemble themselves like a
package manager": every module advertises what it ``requires`` and ``provides``,
and the interface resolves dependencies. That only works if ``provides: labels``
on one module and ``requires: instance_labels`` on another are *known* to be
compatible. Free-text capability strings silently fail to connect (see the
stress-test doc, failure mode #2). So we give capabilities a controlled
representation vocabulary with an explicit compatibility lattice.

A ``Capability`` is a semantic *information product*, not a Python type. It is
(representation-kind + qualifier tags), e.g. ``instance_labels{target:condensate}``.
This mirrors PDF7's "semantic information contracts, not exhaustive Python type
signatures."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Iterable, Optional


# --------------------------------------------------------------------------- #
# Information role  (PDF7 legend: Create -> Transform -> Measure -> Interpret  #
# -> Communicate, with Coordinate + Infrastructure as supporting roles).      #
#                                                                             #
# NOTE: PDF5 proposes a *different* 3-way taxonomy (Detect / Measure /        #
# Compare). Those are two views of the same corpus. We make the 7-way role    #
# below the single normative enum (it is finer-grained and covers plumbing);  #
# the Detect/Measure/Compare framing is preserved only as documentation.      #
# Encoding both as first-class would double-classify modules.                 #
# --------------------------------------------------------------------------- #
class InformationRole(str, Enum):
    CREATE = "create"            # image -> objects (segmentation, detection)
    TRANSFORM = "transform"      # image -> image  (filter, background, register)
    MEASURE = "measure"          # objects -> feature tables (morphology, coloc)
    INTERPRET = "interpret"      # tables -> conclusions (stats, fits, models)
    COMMUNICATE = "communicate"  # anything -> figures / methods text / exports
    COORDINATE = "coordinate"    # QC, orchestration, context inference
    INFRASTRUCTURE = "infrastructure"  # IO, tagging, registry plumbing


# --------------------------------------------------------------------------- #
# Representation lattice.                                                      #
#                                                                             #
# These are the eight representations recommended in PDF8 ("add a separate    #
# representation tag") plus a couple of broadening super-kinds used only for  #
# compatibility matching. The ANCESTORS map answers the question: "if a       #
# module hands me an X, can I use it where a Y is required?"                   #
# --------------------------------------------------------------------------- #
class Representation(str, Enum):
    RAW_IMAGE = "raw_image"
    INTENSITY_FIELD = "intensity_field"
    PROBABILITY_MAP = "probability_map"
    BINARY_MASK = "binary_mask"
    INSTANCE_LABELS = "instance_labels"
    COORDINATES = "coordinates"
    TRAJECTORIES = "trajectories"
    GEOMETRY = "geometry"
    MEASUREMENT_TABLE = "measurement_table"
    MODEL_FIT = "model_fit"
    # broadening super-kinds (never produced directly, only required)
    IMAGE = "image"
    MASK = "mask"
    LABELS = "labels"
    TABLE = "table"


# provided-kind -> kinds it can ALSO satisfy when required.
# Read "instance_labels can be used where mask / labels / image-of-objects is asked."
_ANCESTORS: dict[str, tuple[str, ...]] = {
    Representation.RAW_IMAGE.value: (Representation.INTENSITY_FIELD.value, Representation.IMAGE.value),
    Representation.INTENSITY_FIELD.value: (Representation.IMAGE.value,),
    Representation.PROBABILITY_MAP.value: (Representation.IMAGE.value,),
    Representation.BINARY_MASK.value: (Representation.MASK.value,),
    # instance labels are strictly richer than a binary mask, so they satisfy
    # both MASK and LABELS requirements:
    Representation.INSTANCE_LABELS.value: (Representation.MASK.value, Representation.LABELS.value),
    Representation.COORDINATES.value: (),
    Representation.TRAJECTORIES.value: (Representation.COORDINATES.value,),  # a track is coords over time
    Representation.MEASUREMENT_TABLE.value: (Representation.TABLE.value,),
    Representation.MODEL_FIT.value: (Representation.TABLE.value,),
}


def representation_satisfies(provided: str, required: str) -> bool:
    """True if a layer of representation ``provided`` can satisfy a requirement
    for representation ``required`` (equal, or provided is a sub-kind)."""
    if provided == required:
        return True
    return required in _ANCESTORS.get(provided, ())


@dataclass(frozen=True)
class Capability:
    """A semantic information product produced or consumed by a module.

    ``kind``  : a Representation value (the "what shape is this data").
    ``tags``  : qualifier constraints such as ``target:condensate`` or
                ``axes:time``. On a *provides* capability these describe the
                product; on a *requires* capability they are constraints the
                provider must meet.
    """
    kind: str
    tags: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self):
        # normalise: accept Representation enums or plain strings
        object.__setattr__(self, "kind", self.kind.value if isinstance(self.kind, Representation) else str(self.kind))
        object.__setattr__(self, "tags", frozenset(self.tags))

    # -- matching ---------------------------------------------------------- #
    def satisfied_by(self, provider: "Capability") -> bool:
        """Does ``provider`` (a *provides* capability) satisfy *this* (a
        *requires* capability)?  Representation must be compatible and every
        required tag must be present on the provider — where a provider tag of
        the form ``key:*`` is a wildcard matching any value for that key. The
        wildcard lets a *source* module (e.g. the loaded acquisition) declare it
        can supply any biological target without enumerating them."""
        if not representation_satisfies(provider.kind, self.kind):
            return False
        for rt in self.tags:
            if rt in provider.tags:
                continue
            key = rt.split(":", 1)[0]
            if f"{key}:*" in provider.tags:
                continue
            return False
        return True

    def with_tags(self, extra: Iterable[str]) -> "Capability":
        return Capability(self.kind, self.tags | frozenset(extra))

    def target(self) -> Optional[str]:
        for t in self.tags:
            if t.startswith("target:"):
                return t.split(":", 1)[1]
        return None

    def __str__(self) -> str:
        if self.tags:
            return f"{self.kind}{{{','.join(sorted(self.tags))}}}"
        return self.kind


# Convenience constructors -------------------------------------------------- #
def cap(kind, *tags: str) -> Capability:
    return Capability(kind, frozenset(tags))


# --------------------------------------------------------------------------- #
# Scientific observables and questions live in controlled vocabularies too,   #
# so the question engine and module metadata cannot silently drift apart      #
# (stress-test failure mode #6). These are seeded from the PDFs and are meant #
# to be reconciled against PyCAT_question_tree_and_method_mapping.xlsx.       #
# --------------------------------------------------------------------------- #
class Observable(str, Enum):
    COUNT = "count"
    SIZE = "size"
    SHAPE = "shape"
    INTENSITY = "intensity"
    TEXTURE = "texture"
    LOCALIZATION = "localization"
    COLOCALIZATION = "colocalization"
    SPATIAL_ORGANIZATION = "spatial_organization"
    NEAREST_NEIGHBOR = "nearest_neighbor"
    CLUSTERING = "clustering"
    MOTION = "motion"
    DIFFUSION = "diffusion"
    FUSION = "fusion"
    COARSENING = "coarsening"
    PARTITIONING = "partitioning"
    VISCOSITY = "viscosity"
    ELASTICITY = "elasticity"
    SURFACE_TENSION = "surface_tension"
    SATURATION_CONCENTRATION = "saturation_concentration"
    MOBILE_FRACTION = "mobile_fraction"
    TOPOLOGY = "topology"
    CONNECTIVITY = "connectivity"
    MORPHOLOGY = "morphology"


# The scientist-language questions (PDF6 "Scientist Language" column). These
# are deliberately phrased as user intents, mapped to observables the modules
# actually deliver.
class Question(str, Enum):
    QUANTIFY_STRUCTURES = "quantify structures inside cells"
    CHANGE_OVER_TIME = "how something changes over time"
    SPATIAL_RELATION = "whether two things are spatially related"
    PHASE_SEPARATION = "measure phase separation or material properties"
    UNLABELED = "analyze unlabeled samples"
    EXPLORATORY = "i don't know what is interesting yet"
