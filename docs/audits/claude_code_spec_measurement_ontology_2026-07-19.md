# Claude Code spec — Measurement ontology: definition, equation, units, reference

> **✅ STATUS — DONE, shipped in 1.6.154** (stamped 2026-07-20 from a CHANGELOG cross-reference). `measurement_ontology.py` (12 entries), units-agreement test, comparative-figures consumer.

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Builds the
structured measurement registry the roadmap describes: *"Today these live in scattered docstrings. A
structured ontology makes Methods-section and figure-legend generation nearly automatic — directly
serving the reproducibility story."* Additive; no behaviour change to any computation.

## Why this, and why it composes with what exists
PyCAT already has the *value* side of a measurement well modelled — `utils/measurement.py` defines
`Parameter` with `units`, `uncertainty` (1-sigma), `ParameterSource`, and `ValidationLevel`. What is
missing is the *definitional* side: what the measurement **means**, the equation behind it, and where
it comes from in the literature. Verified: no ontology/registry module exists.

Today a partition coefficient is defined in a docstring (`partition_enrichment_tools.py`: *"~1 means
no preference; <1 means exclusion"*) — good prose, but not machine-readable, not attached to the
emitted column, and not available to a figure legend or a Methods section.

This is the missing half of a philosophy the codebase already commits to: measurements carry their
units and uncertainty; they should also carry their *definition and provenance in the literature*.

## The model — `utils/measurement_ontology.py`
```python
@dataclass(frozen=True)
class MeasurementDef:
    key: str                  # the column name as emitted, e.g. 'partition_coefficient'
    display_name: str         # 'Partition coefficient'
    definition: str           # one sentence, plain language
    equation: str             # 'K_p = I_dense / I_dilute'  (plain text or LaTeX)
    units: str                # 'dimensionless', 'µm²', 'Pa·s' — must agree with what is emitted
    reference: str | None     # 'Brangwynne et al. 2009, Science 324:1729'
    doi: str | None
    interpretation: str | None  # '>1 enrichment, ~1 no preference, <1 exclusion'
    caveats: tuple[str, ...]    # e.g. '2D projection proxy — not a true volume fraction'

MEASUREMENTS: dict[str, MeasurementDef]

def describe(key) -> MeasurementDef | None
def units_for(key) -> str | None
```

## Scope — seed it, don't boil the ocean
**Do not attempt to define every emitted column.** Seed with the measurements that are (a) scientific
claims rather than raw geometry, and (b) already documented somewhere in the codebase so the
definition is *transcribed, not invented*:
- `partition_coefficient`, `client_enrichment` (definitions exist in `partition_enrichment_tools`)
- `delta_g_transfer`, and the calibrated concentration outputs (definitions exist in `calibration.py`)
- `viscosity`, `diffusion_coefficient`, `alpha` (anomalous exponent) — from the VPT chain
- `mobile_fraction`, `t_half` — FRAP
- Pearson / Manders / overlap coefficients — colocalization
- `volume_fraction` — **with its caveat recorded**: `invitro_fluor_ui` already warns this is a 2D
  projection proxy, and that caveat belongs in the ontology, not only in a UI string.

Plain `regionprops` geometry (`area`, `eccentricity`, …) can be seeded later or delegated to
scikit-image's own documentation — note the decision rather than silently omitting them.

**Every entry must be transcribed from an existing docstring, paper, or the code itself.** If a
definition cannot be sourced, leave the measurement out rather than inventing an equation — a wrong
equation in a Methods section is worse than an absent one.

## Part B — the consistency test (what makes it real)
An ontology that drifts from what the code emits is worse than none. So:
1. **Units agreement.** For every measurement in the ontology that is also emitted as a `Parameter`,
   assert the ontology's `units` matches the `Parameter.units` the code produces. A mismatch is a bug
   in one of them and the test names which.
2. **Key existence.** Every ontology key must appear as a column emitted by at least one analysis (or
   be explicitly marked `emitted=False` for derived/reported-only values). This prevents the registry
   filling with aspirational entries.
3. **No orphan claims.** Every entry with a `reference` must have a non-empty `equation` — a citation
   without the formula it supports is decoration.

## Part C — one consumer, to prove it
Wire the ontology into **one** place so it is not a write-only registry. The cheapest high-value
consumer: the comparative-figures summary frame (increment 3 returns `(Figure, summary_df)`) gains
measurement metadata — display name, units, and caveats available for axis labels and legends. A
figure whose y-axis says *"Partition coefficient (dimensionless)"* with the caveat retrievable is the
concrete payoff.

**Methods-section generation is explicitly out of scope for this increment** — it is the eventual
prize, but it needs the registry populated and proven first.

## Steps
1. `utils/measurement_ontology.py` — the dataclass + the seeded `MEASUREMENTS` dict.
2. Transcribe definitions from existing docstrings/`calibration.py`/VPT/FRAP/coloc sources.
3. The three consistency tests (`core`, pure).
4. Wire display name + units + caveats into the comparative-figures summary frame.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (measurement ontology seeded
   and consistency-guarded; comparative figures now carry measurement metadata).

## Definition of done
- A structured registry defines the seeded measurements with definition, equation, units, reference,
  interpretation, and caveats.
- Units in the ontology are asserted to match units the code emits.
- No entry is aspirational (every key is emitted or explicitly flagged).
- One consumer uses it, proving it is not write-only.
- Full `pytest -m core` green.

## Cautions
- **Transcribe, never invent.** An unsourced equation in a registry destined for Methods sections is a
  correctness hazard. Omit rather than guess.
- The units test is the load-bearing part — without it the ontology drifts from reality within a
  release or two.
- Record caveats as data (e.g. the 2D-projection-proxy warning), not as prose buried in a UI.
- Seed, don't complete. A partial registry that is *correct and guarded* beats a full one that is
  half-invented.
- Do not build Methods-section generation or the Measurement Reliability Index here; both depend on
  this and both are larger.
