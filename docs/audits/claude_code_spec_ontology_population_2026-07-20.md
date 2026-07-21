# Claude Code spec — Populate the measurement ontology (structure is done; content is the gap)

> **✅ STATUS — DONE, shipped in 1.6.185.** Premise was partly stale: Tier 1 (partition_coefficient,
> client_enrichment, delta_g_transfer, viscosity, D, alpha, mobile_fraction, t_half, Pearson/Manders,
> projected_area_fraction) was ALREADY present (populated 1.6.154+). This shipped **Tier 2** — `area`,
> `equivalent_diameter`, `eccentricity`, `solidity`, `intensity_mean`, `intensity_total` — each transcribed
> from scikit-image / the code with a real equation and units, size units flagged calibration-dependent,
> intensity units `a.u.` with the offset/gain caveat. The well-formed guard (definition+equation+units), the
> no-orphan guard (every emitted key appears in src), and the units-agreement guard all pass
> (`test_measurement_ontology.py`). Transcribe-never-invent honoured; no reference without its equation.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The ontology's
*machinery* shipped and is well-designed — but it holds **one entry**. Everything built on it (Feature
Explorer cards, publication-figure labels, reliability captioning, condensate-mode caveats) is
therefore running nearly empty. Populating it is the highest-leverage small task available: no new
code paths, just careful transcription that lights up features already built.

## Verified state
`utils/measurement_ontology.py` defines `MeasurementDef` (display_name, definition, equation, units,
reference, interpretation, caveats) with the exactly-right discipline in its own docstring:

> *"Transcribed, never invented. Every entry's definition, equation, and units are transcribed from an
> existing PyCAT docstring or the code itself… A reference is set only where the citation is certain —
> a wrong equation or DOI in a registry destined for a Methods section is worse than none."*

But `grep -c MeasurementDef(` returns **1**. The registry is a well-built shelf with one book on it.
Consumers that read it (`figure_spec`, `feature_explorer` when it lands, `reliability` captions) get a
definition for one measurement and `None` for everything else.

## The task — transcribe the measurements PyCAT actually emits
This is deliberately **not** a code-architecture task. It is disciplined transcription, and the
discipline is the point: every field sourced from an existing docstring, paper, or the code, or left
absent.

### Tier 1 — the manuscript-facing scientific claims (do all of these)
Each already has a definition somewhere in the codebase to transcribe from:
- `partition_coefficient` — `partition_enrichment_tools` (K_p = I_dense/I_dilute; Brangwynne 2009 if
  certain, else no reference)
- `client_enrichment` — same module
- `delta_g_transfer`, and the calibrated concentration outputs — `calibration.py`
- `viscosity`, `diffusion_coefficient`, `alpha` (anomalous exponent) — the VPT chain
- `mobile_fraction`, `t_half` — FRAP (`fit_frap_recovery`, just decomposed)
- Pearson / Manders M1 M2 / overlap coefficient — colocalization
- `volume_fraction` / `projected_area_fraction` — **with the 2D-projection caveat** the condensate-mode
  work already established; this is the highest-value caveat to encode

### Tier 2 — common geometry/intensity (transcribe units + one-line definitions)
`area`, `equivalent_diameter`, `eccentricity`, `solidity`, `intensity_mean`, `intensity_total`. For
`regionprops`-derived ones, the definition can point to scikit-image; the important field is **units**
(µm² vs px² depending on calibration), because the units-agreement test and the figure labels depend
on it.

## The units-agreement test is the load-bearing part
The ontology is only trustworthy if it matches what the code emits. So alongside population:
- For every ontology entry that is also emitted as a `Parameter`, assert the ontology `units` equals
  the `Parameter.units` the code produces. A mismatch fails the test and names both sides.
- Every ontology key must correspond to a column some analysis actually emits (or be explicitly flagged
  `emitted=False`), so the registry cannot fill with aspirational entries.

This test already exists in concept from the original ontology spec; with only one entry it has had
nothing to check. Population makes it meaningful — and it is what keeps the ontology from drifting.

## Handling uncertainty honestly
- **No certain citation → no `reference`.** An absent DOI is fine; a wrong one is a correctness hazard
  in a Methods section. The docstring already says this; honour it.
- **Definition genuinely unclear → leave the entry out**, don't guess an equation. A smaller correct
  registry beats a larger half-invented one.
- Where a measurement's units depend on calibration (µm² only if pixel size is set), state that in the
  entry rather than asserting one.

## Tests (`core`)
- Every populated entry has non-empty `display_name`, `definition`, `units`.
- Units-agreement: ontology units match emitted `Parameter` units for every overlapping key.
- No orphan keys: every entry is emitted or flagged `emitted=False`.
- Every entry with a `reference` also has a non-empty `equation` (no decorative citations).
- The `volume_fraction`/`projected_area_fraction` entry carries the 2D-projection caveat.

## Steps
1. Transcribe Tier 1 entries from their existing sources into `MEASUREMENTS`.
2. Transcribe Tier 2 units + short definitions.
3. Ensure the units-agreement + no-orphan tests cover the new entries.
4. Full `pytest -m core` green — a units mismatch is a **finding** (fix whichever side is wrong,
   don't loosen the test).
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (ontology populated: N entries;
   note any units mismatch found and corrected).

## Definition of done
- Tier 1 scientific measurements and Tier 2 common geometry/intensity are in the registry, each
  transcribed from a real source.
- Units in the ontology provably match units the code emits.
- No orphan or aspirational entries; no decorative references.
- The 2D-projection caveat is encoded where it belongs.
- Full `pytest -m core` green.

## Cautions
- **Transcribe, never invent.** This is the entire discipline; an invented equation destined for a
  Methods section is the one thing this registry must not contain.
- A units mismatch surfaced by the test is a real bug in the ontology or the code — fix the wrong side,
  never the test.
- Absent beats wrong for references and for unclear definitions.
- This is content, not architecture — resist the urge to refactor the ontology module while filling it.
- Do not build Methods-section generation yet; it needs the registry populated first, and this spec is
  that prerequisite.
