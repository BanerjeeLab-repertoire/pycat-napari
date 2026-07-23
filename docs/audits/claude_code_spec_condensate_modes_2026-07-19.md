# Claude Code spec — Explicit 2D / 3D / time-series condensate modes

> **✅ STATUS — DONE (done-but-unstamped; verified 2026-07-22). `toolbox/condensate_modes.py` models the
> approximation the code previously only admitted in a transient napari string: `CondensateMode`
> (FIELD_2D / ZSTACK_3D / TIMESERIES), `resolve_condensate_mode(data, declared=, axis_kind=)` (declared
> wins, else derived from dimensionality, ambiguous z-vs-t routed to the existing disambiguation — never
> silently assumed), and a per-mode output-validity table so a projected_area_fraction is labelled a 2-D
> projection proxy (not a volume fraction) wherever it travels. field_summary carries the caveats on its
> result dict. `test_condensate_modes` green (9). DoD met.**

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Closes an
approximation the codebase already admits to in a UI string but does not model. Scientific
correctness, not a feature.

## The admitted approximation (verified)
`invitro_fluor_ui.py:786` reports:
```
f"area fraction={summ['projected_area_fraction']:.3f} (2D projection, not a volume fraction)"
```
The code is honest — but the honesty lives in a **transient napari info message**, while the number
travels onward into tables, the consolidated long table, and comparative figures with no such
qualifier attached.

Two distinct problems follow:
1. **A projected area fraction is not a volume fraction**, and the relationship between them depends on
   object size, shape, and how many objects overlap along the optical axis. A reader who sees
   "fraction = 0.12" in a results table has no way to know it is a projection proxy.
2. **The same workflow is applied to fundamentally different data shapes.** A 2D field, a z-stack, and
   a time series each have different valid measurements — and some measurements that are valid in one
   are meaningless in another (a "volume fraction" from a single 2D plane; a per-frame size
   distribution treated as independent samples when it is one drifting population).

## Design — declare the mode, gate the outputs
### Part A — an explicit mode
```python
class CondensateMode(str, Enum):
    FIELD_2D    = '2d'          # one plane; projected quantities only
    ZSTACK_3D   = '3d'          # true volumes available
    TIMESERIES  = 'timeseries'  # one population through time; not independent samples
```
The mode is **declared by the user or derived from the data's dimensionality**, never silently
assumed. Where dimensionality is ambiguous (a 3D array could be z or t), use the existing
disambiguation path rather than guessing — the loader already asks this question for TIFFs.

### Part B — outputs differ by mode, and say so
| quantity | 2D | 3D | time series |
|---|---|---|---|
| `projected_area_fraction` | ✔ primary | available, but volume is better | ✔ per frame |
| `volume_fraction` | **refused** — not measurable | ✔ true value | refused unless z present |
| size distribution | ✔ (projected radii) | ✔ (true radii) | ✔ but flagged non-independent |
| per-frame statistics | n/a | n/a | ✔ with a drift/independence caveat |

- **Refuse, don't approximate.** In 2D mode, a request for volume fraction returns NaN with a stated
  reason — consistent with the pixel-size and calibration gates. Do not emit a converted estimate;
  the conversion needs assumptions (mono-disperse spheres, no axial overlap) the data cannot support.
- Each emitted quantity carries its mode in the output table (`condensate_mode` column), so the
  qualifier travels with the number instead of evaporating with the info message.

### Part C — the caveat becomes data
Register the projection caveat in the **measurement ontology** (1.6.154 already models `caveats`), so:
- the consolidated long table can surface it,
- the publication-figure footnote can render it,
- and it is queryable rather than being prose in a UI string.

This is precisely what the ontology's `caveats` field exists for.

### Part D — time-series independence
In `TIMESERIES` mode, per-frame measurements of the same droplets are **not independent samples**. Flag
this in the output so downstream statistics (the comparative-figures replicate aggregation) treat a
time series as one unit rather than N. Reuse the existing pseudoreplication machinery — comparative
figures already aggregates to a declared biological unit; a time series should declare itself as one.

## Tests (`core`, synthetic)
- 2D mode: requesting volume fraction returns NaN **with a reason string**, never a number.
- 3D mode: on a synthetic z-stack of known spheres, the true volume fraction is recovered within
  tolerance — and it **differs materially from the projected area fraction**, demonstrating why the
  distinction matters.
- The `condensate_mode` column is present on every emitted table.
- The projection caveat is retrievable from the ontology for the projected quantity.
- Time-series mode flags non-independence, and comparative aggregation treats the series as one unit.
- Mode is never silently inferred for an ambiguous 3D array — the disambiguation path is used.

## Steps
1. `CondensateMode` + mode resolution (declared or unambiguous-from-data).
2. Gate the output set per mode; refuse-with-reason where a quantity is not measurable.
3. `condensate_mode` column on emitted tables.
4. Register the projection caveat in the ontology.
5. Time-series independence flag wired to the comparative aggregation.
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- The three modes are explicit; the mode travels on every emitted table.
- Volume fraction is refused in 2D with a stated reason, and correct in 3D.
- The projection caveat lives in the ontology, not only in a napari message.
- Time series are flagged non-independent and aggregate as one unit.
- Full `pytest -m core` green.

## Cautions
- **Refuse rather than convert.** A projected-to-volume conversion requires assumptions the data
  cannot support; emitting an estimate would be the exact "plausible lie" the codebase's contracts
  forbid.
- Do not infer z-vs-t silently for a 3D array — use the existing disambiguation.
- The caveat must become data. Leaving it as a UI string is the current bug.
- Do not change the 2D numbers themselves; they are correct *as projected quantities*. The fix is
  labelling and gating, not recomputation.
