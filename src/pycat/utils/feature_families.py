"""**A view over the measurement layer: which family a column belongs to, and how to group a table.**

The ontology (`measurement_ontology`) says what a measurement MEANS; this says how measurements ORGANIZE —
into families (Geometry, Intensity, Material-state, Spatial, …). The Feature Explorer's left panel,
redundancy analysis's within-family scope, and any "export only the geometry columns" action all consume
this grouping.

**The ontology is truth; the substring map is a labelled guess.** `family_for_column` resolves a column's
family from the ontology FIRST (an authoritative, hand-assigned `MeasurementDef.family`), and only falls
back to a curated substring map for the many emitted columns that are not ontology entries. Every
assignment carries its `source` (`'ontology'` | `'inferred'` | `None`) so a guessed grouping is never
mistaken for a defined one — the same derived-vs-inferred honesty the tag hook uses.

**Ungrouped is honest.** A column whose family is genuinely ambiguous stays `None` and lands in the
Ungrouped bucket; a *wrong* family is more misleading than an absent one. This is a view — it never
reorganizes the ontology or the emitted tables.
"""
from __future__ import annotations

import dataclasses

from pycat.utils.measurement_ontology import MEASUREMENTS, FeatureFamily

# Canonical family order for display/grouping = the enum's definition order. The Ungrouped (None) bucket
# always sorts LAST, after every named family.
CANONICAL_ORDER: tuple[FeatureFamily, ...] = tuple(FeatureFamily)


@dataclasses.dataclass(frozen=True)
class FamilyAssignment:
    """A column's family and WHERE that family came from — so a guess is never read as a definition."""
    family: FeatureFamily | None
    source: str | None                # 'ontology' (authoritative) | 'inferred' (substring guess) | None


# ── The substring fallback — a curated, ORDERED map (most specific families first) ────────────────────
#
# Ordered because a name can contain more than one keyword; the first family whose keyword matches wins.
# Scientific families are checked before the generic geometry/intensity so e.g. 'partition_intensity'
# resolves to PARTITION, not INTENSITY. Keywords are matched as lowercased substrings. Kept deliberately
# conservative: a keyword only appears here when its presence is a confident signal — an ambiguous token
# (bare 'ratio', 'value', 'score') is intentionally ABSENT so such columns fall through to None/Ungrouped.
_SUBSTRING_MAP: tuple[tuple[FeatureFamily, tuple[str, ...]], ...] = (
    (FeatureFamily.COLOCALIZATION, ('pearson', 'manders', 'coloc', 'overlap_coef', 'costes')),
    (FeatureFamily.MATERIAL,       ('viscos', 'diffus', 'msd', 'mobile_frac', 't_half', 'tau_half',
                                    'anomalous', 'recovery')),
    (FeatureFamily.SPATIAL,        ('ripley', 'nn_', 'nearest_neighbor', 'nearest_neighbour', 'pcf',
                                    'pair_correlation', 'g_of_r', 'point_density', 'clustering')),
    (FeatureFamily.PARTITION,      ('partition', 'enrichment', 'k_p', 'kp_', 'delta_g', 'deltag',
                                    'free_energy', 'concentration', 'dense_dilute')),
    (FeatureFamily.QC,             ('reliability', 'stability', 'localization_precision', 'precision_nm',
                                    'qc_', 'quality', '_flag', 'flag_')),
    (FeatureFamily.TOPOLOGY,       ('persistence', 'connected', 'scale_space', 'topolog', 'betti',
                                    'euler_number')),
    (FeatureFamily.GEOMETRY,       ('area', 'diameter', 'eccentric', 'solidity', 'perimeter', 'convex',
                                    'axis_length', 'extent', 'circularity', 'aspect_ratio', 'roundness',
                                    'feret')),
    (FeatureFamily.INTENSITY,      ('intensity', 'contrast', 'brightness', 'mean_gray', 'integrated_den')),
)


def _infer_family(name_lc: str) -> FeatureFamily | None:
    for family, needles in _SUBSTRING_MAP:
        if any(n in name_lc for n in needles):
            return family
    return None


def classify_column(name) -> FamilyAssignment:
    """Resolve ``name``'s family with its provenance.

    Ontology FIRST (authoritative `MeasurementDef.family`), then the substring fallback (a labelled
    guess), else ``None``. The returned ``source`` distinguishes a defined family from an inferred one.
    """
    if not name:
        return FamilyAssignment(None, None)
    key = str(name)
    m = MEASUREMENTS.get(key)
    if m is not None and m.family is not None:
        return FamilyAssignment(m.family, 'ontology')
    inferred = _infer_family(key.lower())
    if inferred is not None:
        return FamilyAssignment(inferred, 'inferred')
    return FamilyAssignment(None, None)


def family_for_column(name) -> FeatureFamily | None:
    """The family for a column — ontology-first, substring fallback, else ``None`` (see `classify_column`)."""
    return classify_column(name).family


def group_columns_by_family(columns) -> dict:
    """Partition ``columns`` into families in canonical order, with an Ungrouped (``None``) bucket last.

    Nothing is dropped: the union of all buckets equals the input, and each column's original order is
    preserved WITHIN its bucket. Only non-empty buckets appear; the ``None`` bucket is included only when
    something is genuinely unclassifiable. This is what the Feature Explorer's left panel and an
    "export family X" action consume.
    """
    buckets: dict = {}
    for col in columns:
        fam = family_for_column(col)
        buckets.setdefault(fam, []).append(col)
    # Re-emit in canonical family order (enum order), then the Ungrouped/None bucket last.
    ordered: dict = {}
    for fam in CANONICAL_ORDER:
        if fam in buckets:
            ordered[fam] = buckets[fam]
    if None in buckets:
        ordered[None] = buckets[None]
    return ordered
