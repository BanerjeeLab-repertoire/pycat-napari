# Claude Code spec — Background mode selector (surface an existing capability, with its guardrail)

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. A UI-exposure
gap, not a missing capability — and one where the *reasoning* already written in the code is more
valuable than the control itself. Small, contained, high scientific value.

## The gap (verified)
`partition_enrichment_tools.client_enrichment` already supports three background treatments:
- `background: float = 0.0` — a scalar instrument offset,
- `background_mask` — a signal-free region whose mean is used as the offset (overrides the scalar),
- `dilute_dilation_px` — a local dilute shell around each condensate instead of the whole cell.

Its docstring contains an unusually careful piece of scientific reasoning:

> *"The only legitimate background to subtract is the **instrument / camera offset**… an additive
> offset b makes (C_dense+b)/(C_dilute+b) ≠ K and biases the ratio toward 1. **The dilute phase is NOT
> background.** … Subtracting 'the region outside the condensate' as background would be subtracting
> the dilute phase from itself and destroy the measurement."*

**Verified: no UI exposes any of this.** Grepping the analysis UIs for `background_mask` or
`background=` returns nothing. So every partition coefficient computed through the GUI uses
`background=0.0` — which is the *safe* default, but means:
1. users with a genuine dark reference cannot use it, and their K_p stays biased toward 1;
2. users who *think* they should subtract "the area outside the condensate" have no guidance telling
   them that would destroy the measurement.

The second point is the important one. This is a mistake a well-intentioned user makes naturally, and
the code already knows why it is wrong.

## Design — the selector, and the guardrail
### Part A — an explicit mode picker
```
Background offset:
  ( ) None — report raw means            [default]
  ( ) Scalar value: [____] counts
  ( ) From a signal-free region: [layer dropdown]
  ( ) From a dark/blank frame: [layer dropdown]
```
Plus, separately (it is a *different* concept, not a background mode):
```
Dilute reference:  ( ) whole cell   ( ) local shell of [__] px around each condensate
```
Keep these visually distinct. Conflating "what is the instrument offset" with "what is the dilute
reference" is precisely the confusion the docstring warns about.

### Part B — the guardrail (the actual value of this spec)
When the user selects a signal-free region, **validate that it plausibly is one**:
- compare the candidate region's mean against the dilute-phase mean;
- if they are comparable (the region is not meaningfully darker), **warn loudly**: *"the selected
  region has intensity similar to the dilute phase — if this is inside the cell, subtracting it will
  destroy the partition measurement. A background region should be outside the cell or a dark frame."*
- Do not block — the user may have a legitimate reason — but the warning must be unmissable and must
  state the consequence, not just the fact.

This turns a docstring nobody reads into a check at the moment the mistake would be made. Reuse the
existing warning machinery; do not invent a new notification path.

### Part C — the choice travels with the result
Record `background_mode`, the resulting offset value, and its source in the output table (and hence in
the consolidated long table). A partition coefficient computed with a dark-frame offset and one
computed raw are **different measurements**, and a reader must be able to tell them apart. Today the
distinction would be invisible.

Register the reasoning in the **measurement ontology** as a caveat on `partition_coefficient` — the
`caveats` field exists for exactly this, and it makes the warning available to figure footnotes.

## Tests (`core`, synthetic)
- Each mode produces the expected offset (scalar; mask-mean; none = 0).
- **The guardrail test:** a "background" region drawn inside the dilute phase triggers the warning; a
  genuinely dark region does not.
- Offset subtraction moves K_p in the correct direction: with a known pedestal added, the corrected
  K_p recovers the no-pedestal value (this is the `test_imaging_realism` pedestal-invariance contract,
  applied through the new UI path).
- `background_mode` and the offset appear in the emitted table.
- Default remains `none` — existing behaviour unchanged when the user does nothing.

## Steps
1. Background-mode selector + separate dilute-reference control in the partition/enrichment UIs.
2. The signal-free-region guardrail with a consequence-stating warning.
3. `background_mode` + offset + source into the output table and consolidated table.
4. Ontology caveat on `partition_coefficient`.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- All three background modes and the dilute-reference choice are selectable in the UI.
- A plausibly-invalid background region triggers a warning that states the consequence.
- The mode, offset, and source travel with every emitted measurement.
- Default is unchanged (`none`), so existing results are not silently altered.
- Full `pytest -m core` green.

## Cautions
- **Keep "instrument offset" and "dilute reference" visually and conceptually separate.** Merging them
  in the UI would institutionalize the exact error the docstring warns about.
- Warn, do not block — the user may have a valid unusual case; but the warning must state the
  *consequence* ("this will destroy the measurement"), not merely observe intensity similarity.
- Default stays `none`. Changing the default would silently alter every existing workflow's numbers.
- Do not compute a background from "outside the condensate" as a convenience option. It is the wrong
  answer and should not be offered.
