"""**What a measurement MEANS — definition, equation, units, provenance.**

`utils/measurement.py` already models the *value* side of a measurement (``Parameter`` with ``units``,
``uncertainty``, ``ParameterSource``). This is the missing *definitional* side: what the measurement
means, the equation behind it, and where it comes from — the metadata a Methods section or a figure
legend needs. Today that lives in scattered docstrings; this makes it machine-readable and attachable to
the emitted column.

**Transcribed, never invented.** Every entry's definition, equation, and units are transcribed from an
existing PyCAT docstring or the code itself (the sources are noted per entry). A ``reference`` is set
only where the citation is certain — a *wrong* equation or DOI in a registry destined for a Methods
section is worse than an absent one, so unsourced fields are left ``None`` for the domain expert to fill.

**Seeded, not complete** — only measurements that are scientific *claims* (not raw ``regionprops``
geometry) and already documented somewhere. Plain geometry (`area`, `eccentricity`, …) is delegated to
scikit-image's own documentation rather than duplicated here; that omission is deliberate.

This is NOT Methods-section generation and NOT the Measurement Reliability Index — both build on this and
both are larger.
"""
from __future__ import annotations

import dataclasses
import enum


class FeatureFamily(str, enum.Enum):
    """The organizing families over the measurement layer — a small, stable browsing/scoping schema.

    A ``str`` enum so a family serializes as its plain value (``'geometry'``) in JSON/CSV/manifests with
    no custom encoder. The order below IS the canonical display order (see ``feature_families``). Resist
    proliferation — a dozen families is a browsing aid; thirty would be a second taxonomy to maintain.
    """
    GEOMETRY       = 'geometry'        # area, diameter, eccentricity, solidity
    INTENSITY      = 'intensity'       # mean/total/max intensity, contrast, ratiometric
    PARTITION      = 'partition'       # K_p, enrichment, ΔG, concentration
    MATERIAL       = 'material_state'  # viscosity, diffusion, α, mobile fraction, t_half
    SPATIAL        = 'spatial'         # NN distance, Ripley L, PCF, density
    COLOCALIZATION = 'colocalization'  # Pearson, Manders, overlap
    TOPOLOGY       = 'topology'        # persistence, connectedness, scale-space
    QC             = 'qc'              # reliability, stability, precision floors, biological flags


@dataclasses.dataclass(frozen=True)
class MeasurementDef:
    key: str                          # the column/parameter name as EMITTED, e.g. 'partition_coefficient'
    display_name: str                 # 'Partition coefficient'
    definition: str                   # one sentence, plain language
    equation: str                     # 'K_p = I_dense / I_dilute'  (plain text)
    units: str                        # must AGREE with what the code emits (guarded by the units test)
    interpretation: str | None = None   # '>1 enrichment, ~1 no preference, <1 exclusion'
    caveats: tuple[str, ...] = ()       # e.g. '2D projection proxy — not a true volume fraction'
    reference: str | None = None      # set only when certain; a wrong citation is worse than none
    doi: str | None = None
    emitted: bool = True              # False = derived/reported-only (not a raw emitted column)
    family: FeatureFamily | None = None  # organizing family; None = deliberately unassigned (ambiguous)


def _def(**kw) -> MeasurementDef:
    return MeasurementDef(**kw)


MEASUREMENTS: dict[str, MeasurementDef] = {m.key: m for m in (
    # ── condensate partitioning (partition_enrichment_tools) ──────────────────────────────────
    _def(family=FeatureFamily.PARTITION,
         key='partition_coefficient', display_name='Partition coefficient',
         definition="How much brighter the dense (condensate) phase is than the dilute phase for the "
                    "same client, in the same image.",
         equation='K_p = mean(I in dense) / mean(I in dilute)',
         units='dimensionless',
         interpretation='>1 = enrichment (preference for the dense phase); ~1 = no preference; '
                        '<1 = exclusion.',
         caveats=('The dilute region is the cell mask AND NOT the dense mask — the same client, dense '
                  'vs dilute, in one image.',
                  'The ONLY legitimate background to subtract is the instrument/camera offset (from a '
                  'signal-free region OUTSIDE the cell, or a dark frame). The dilute phase is NOT '
                  'background — subtracting "the region outside the condensate" subtracts the '
                  'denominator from itself and destroys the measurement. An unremoved camera pedestal, '
                  'conversely, biases K toward 1.',)),
    _def(family=FeatureFamily.PARTITION,
         key='client_enrichment', display_name='Client enrichment',
         definition="The enrichment of a client in the dense phase relative to the dilute phase — the "
                    "partition coefficient, per object or per condition.",
         equation='enrichment = mean(I in dense) / mean(I in dilute)',
         units='dimensionless',
         interpretation='>1 = enriched in the condensate; ~1 = no preference; <1 = excluded.'),

    # ── transfer free energy (calibration.py) ─────────────────────────────────────────────────
    _def(family=FeatureFamily.PARTITION,
         key='delta_g_transfer', display_name='Transfer free energy (ΔG)',
         definition="The free energy of transferring a molecule from the dilute to the dense phase, "
                    "computed from the calibrated concentration ratio.",
         equation='ΔG = −RT · ln(K_p),  K_p = C_dense / C_dilute',
         units='kcal/mol',
         interpretation='More negative ΔG = stronger partitioning into the dense phase.',
         caveats=('Requires a valid concentration calibration; refused loudly (not defaulted) for a '
                  'non-positive or impossible concentration ratio.',)),

    # ── microrheology (vpt_tools / condensate_physics_tools) ──────────────────────────────────
    _def(family=FeatureFamily.MATERIAL,
         key='viscosity', display_name='Viscosity',
         definition="The apparent viscosity of the medium, from bead diffusion via the Stokes–Einstein "
                    "relation.",
         equation='η = kT / (6·π·R·D)',
         units='Pa·s',
         interpretation='Higher η = a more viscous (slower-diffusing) medium.',
         caveats=('Assumes a Newtonian medium and a known physical bead radius R; degraded by '
                  'fragmented or noisy MSDs.',),
         reference='Stokes–Einstein relation (Einstein, Ann. Phys. 1905)'),
    _def(family=FeatureFamily.MATERIAL,
         key='D_um2_per_s', display_name='Diffusion coefficient',
         definition="The bead diffusion coefficient from the ensemble mean-squared-displacement fit.",
         equation='MSD(τ) = 4·D·τ^α  (2D);  D is the fitted prefactor',
         units='µm²/s',
         interpretation='Higher D = faster diffusion.'),
    _def(family=FeatureFamily.MATERIAL,
         key='alpha', display_name='Anomalous exponent (α)',
         definition="The anomalous-diffusion exponent from the MSD power-law fit — how the MSD scales "
                    "with lag time.",
         equation='MSD(τ) = 4·D·τ^α',
         units='dimensionless',
         interpretation='α ≈ 1 = Brownian; α < 1 = subdiffusive (confined/crowded); α > 1 = superdiffusive.',
         caveats=('α is not interpretable when the power law does not fit (non-random residuals) — a '
                  'plateauing MSD from confinement fits as spurious subdiffusion.',)),

    # ── FRAP (frap_tools) ─────────────────────────────────────────────────────────────────────
    _def(family=FeatureFamily.MATERIAL,
         key='mobile_fraction', display_name='Mobile fraction',
         definition="The fraction of the BLEACHED material that recovers — i.e. is mobile — from the "
                    "fitted recovery plateau.",
         equation='mobile_fraction = (I_plateau − I_bleach) / (I_prebleach − I_bleach)',
         units='dimensionless',
         interpretation='Near 1 = fully mobile; near 0 = immobile / gel-like.'),
    _def(family=FeatureFamily.MATERIAL,
         key='t_half', display_name='Recovery half-time (τ½)',
         definition="The time for fluorescence to recover to half of its plateau, from the FRAP recovery "
                    "model fit.",
         equation='I(t) = (a + b·(t/τ½)) / (1 + t/τ½)',
         units='s',
         interpretation='Larger τ½ = slower recovery (less mobile / more viscous environment).'),

    # ── colocalization (obj_based_coloc / two_channel_coloc) ─────────────────────────────────
    _def(family=FeatureFamily.COLOCALIZATION,
         key='manders_m1', display_name="Manders' M1",
         definition="The fraction of channel-1 signal that overlaps channel-2 (co-occurrence, not "
                    "correlation).",
         equation='M1 = Σ(I1 where ch2 > threshold) / Σ(I1)',
         units='dimensionless',
         interpretation='0 = no co-occurrence; 1 = all of channel 1 lies where channel 2 is present.',
         caveats=('Reads 1.0 on pure noise if the threshold is below background — the threshold must be '
                  'set above background.',)),
    _def(family=FeatureFamily.COLOCALIZATION,
         key='manders_m2', display_name="Manders' M2",
         definition="The fraction of channel-2 signal that overlaps channel-1.",
         equation='M2 = Σ(I2 where ch1 > threshold) / Σ(I2)',
         units='dimensionless',
         interpretation='0 = no co-occurrence; 1 = all of channel 2 lies where channel 1 is present.'),
    _def(family=FeatureFamily.COLOCALIZATION,
         key='pearson', display_name="Pearson's coefficient",
         definition="The Pearson correlation of the two channels' pixel intensities — how linearly they "
                    "co-vary.",
         equation='r = cov(I1, I2) / (σ_I1 · σ_I2)',
         units='dimensionless',
         interpretation='+1 = perfect positive correlation; 0 = none; −1 = anti-correlation.'),

    # ── phase morphology (invitro_fluor) ─────────────────────────────────────────────────────
    _def(family=FeatureFamily.GEOMETRY,
         key='projected_area_fraction', display_name='Volume fraction (2D projected-area proxy)',
         definition="The fraction of the field occupied by the dense phase, from the 2D segmentation.",
         equation='φ_proj = area(dense) / area(total)',
         units='dimensionless',
         interpretation='Higher = more of the field is dense phase.',
         caveats=('This is a 2D PROJECTION, not a true volume fraction — the UI already warns "2D '
                  'projection, not a volume fraction". Do not report it as a volume fraction.',)),
    _def(family=FeatureFamily.INTENSITY,
         key='ratio', display_name='Intensity ratio (ratiometric)',
         definition="The two-channel intensity ratio, background-subtracted first — a proxy for an "
                    "environment-sensitive quantity (FRET-by-ratio; polarity / viscosity / pH from a "
                    "ratiometric dye).",
         equation='ratio = (N − b_N) / (D − b_D)',
         units='dimensionless',
         interpretation='Relative between conditions imaged identically; absolute meaning needs a dye '
                        'calibration.',
         caveats=('BACKGROUND FIRST: an un-subtracted offset in either channel bends the ratio toward 1.',
                  'mean-of-ratio and ratio-of-means answer different questions — the per-object table '
                  'reports both; do not read one without knowing which.',
                  'Uncorrected BLEED-THROUGH biases the ratio toward 1 — supply a linear coefficient from '
                  'a single-label control.',
                  'Where the denominator is near zero the ratio is NaN, not a spike; check the reported '
                  'thresholded fraction.'),
         emitted=False),
    # ── SMLM / localization clustering outputs (reported aggregates, not raw columns) ──────────────
    _def(family=FeatureFamily.QC,
         key='median_localization_precision_nm', display_name='Median localization precision',
         definition="The median single-molecule localization uncertainty across a localization set — the "
                    "resolution floor below which apparent clustering is not trustworthy.",
         equation='σ_loc = median(uncertainty_i)',
         units='nm',
         interpretation='Structure at length scales below this is not resolved; treat short-range '
                        'clustering with caution.',
         caveats=('This is a FLOOR, not the resolution — the PCF/Ripley analysis over-reports clustering '
                  'below it because each molecule is a fuzzy blob, not a point.',),
         emitted=False),
    _def(family=FeatureFamily.SPATIAL,
         key='ripley_l_max', display_name="Ripley's L peak (L(r) − r max)",
         definition="The maximum of the edge-corrected Ripley's L(r) − r over the tested radii — the "
                    "strength and scale of spatial clustering of the localizations.",
         equation='max_r [ L(r) − r ],  L(r) = √(K(r)/π)',
         units='µm',
         interpretation='> 0 indicates clustering at the peak radius; ≈ 0 is spatial randomness (CSR).',
         caveats=('Blink over-counting inflates apparent clustering — temporally merge before trusting '
                  'the peak (see the median localization precision floor).',),
         emitted=False),
    _def(family=FeatureFamily.SPATIAL,
         key='nn_median', display_name='Median nearest-neighbour distance',
         definition="The median distance from each localization to its nearest neighbour — a simple "
                    "summary of spatial density/clustering.",
         equation='median_i min_{j≠i} ‖x_i − x_j‖',
         units='µm',
         interpretation='Smaller = denser/more clustered. Compare against a CSR expectation for the same '
                        'density.',
         caveats=('Un-merged blinks shrink this artificially — one molecule localized repeatedly reads as '
                  'many near-coincident points.',),
         emitted=False),
    # ── Tier 2: common geometry / intensity (regionprops-derived) ──────────────────────────────────
    _def(family=FeatureFamily.GEOMETRY,
         key='area', display_name='Area',
         definition="The projected area of the object — its pixel count, scaled by the pixel size when "
                    "calibrated (scikit-image regionprops `area`).",
         equation='A = N_pixels × (pixel size)²',
         units='µm²',
         interpretation='Object size in the image plane.',
         caveats=('µm² ONLY when a pixel size is set; px² otherwise — the value is calibration-dependent.',
                  'A 2D projected area, not a volume.')),
    _def(family=FeatureFamily.GEOMETRY,
         key='equivalent_diameter', display_name='Equivalent diameter',
         definition="The diameter of a circle with the same area as the object (scikit-image "
                    "`equivalent_diameter_area`).",
         equation='d_eq = √(4A / π)',
         units='µm',
         interpretation='A single size scale for a roughly round object.',
         caveats=('µm only when a pixel size is set; px otherwise.',)),
    _def(family=FeatureFamily.GEOMETRY,
         key='eccentricity', display_name='Eccentricity',
         definition="The eccentricity of the ellipse with the same second moments as the object "
                    "(scikit-image `eccentricity`).",
         equation='e = √(1 − b² / a²),  a,b = major/minor semi-axes',
         units='dimensionless',
         interpretation='0 = circle, →1 = increasingly elongated.'),
    _def(family=FeatureFamily.GEOMETRY,
         key='solidity', display_name='Solidity',
         definition="The fraction of the object's convex hull that the object fills (scikit-image "
                    "`solidity`).",
         equation='solidity = area / convex_area',
         units='dimensionless',
         interpretation='1 = convex/compact; lower = concave or ragged.'),
    _def(family=FeatureFamily.INTENSITY,
         key='intensity_mean', display_name='Mean intensity',
         definition="The mean pixel intensity over the object.",
         equation='Ī = (1/N) Σ_i I_i  over the object pixels',
         units='a.u.',
         interpretation='Average signal; comparable across objects only within one identically-acquired '
                        'image.',
         caveats=('Arbitrary units — inherits every offset and gain in the acquisition; not comparable '
                  'across images without a calibration.',)),
    _def(family=FeatureFamily.INTENSITY,
         key='intensity_total', display_name='Integrated intensity',
         definition="The sum of pixel intensities over the object — total signal (mean × area).",
         equation='I_total = Σ_i I_i  over the object pixels',
         units='a.u.',
         interpretation='Total content; sensitive to both concentration and size.',
         caveats=('Arbitrary units; an un-subtracted background scales with area and inflates it.',)),
)}


def describe(key) -> MeasurementDef | None:
    """The full definition for an emitted measurement ``key``, or ``None`` if it is not in the ontology."""
    return MEASUREMENTS.get(key)


def units_for(key) -> str | None:
    """The ontology units for ``key`` (agreeing with what the code emits — see the units test), or None."""
    m = MEASUREMENTS.get(key)
    return m.units if m else None
