# Claude Code spec — Analysis-aware kymographs

> **✅ STATUS — core DONE, shipped in 1.6.186.** `toolbox/kymograph_tools.py` — `kymograph` (materialize-
> safe base, real-unit axis labels only when calibrated else px/frame, recorded averaging-band width),
> `colocalization_kymograph` (two channels + per-time-slice Pearson from the existing metric), and
> `object_property_kymograph` (a tracked object's property vs time from the per-object table).
> `tests/test_kymograph.py` — band-velocity recovered from the slope, the lazy-stack-collapse guard (a
> lazy stack yields a full kymograph, not frame 0), calibrated vs px/frame axis labels, per-slice Pearson
> matches an independent computation, the shrinking-diameter trend, and wide-band noise reduction without
> slope shift. Follow-on (thin UI + FRAP/phase-boundary variants): the draw-line widget and the two
> higher-effort variants; the base + the two cheap analysis-aware variants are delivered.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. A roadmap
capability with no implementation (`grep def.*kymograph` → 0). The angle that makes it worth building
is *analysis-aware* kymographs — not just a line-scan-over-time image, but one paired with the
quantities PyCAT already measures. Builds on an existing intensity-profile primitive.

## What exists to build on (verified)
`intensity_profile_tools.py` and `analysis_plots.py` provide line-profile primitives. A classic
kymograph is "sample intensity along a line, stack those lines over time" — the profile half already
exists; the time-stacking and the analysis pairing are the new parts.

## Design — the classic kymograph, then the analysis-aware layer
### Part A — the base kymograph
```python
def kymograph(stack, line, *, axis='time', width_px=1, reduce='mean') -> Kymograph
```
- `stack`: a T×Y×X (or Z×Y×X) layer — **materialize via `materialize_stack`**, not `np.asarray`, so a
  lazy time-series does not collapse to frame 0 (the known landmine).
- `line`: endpoints, or a napari Shapes line layer.
- `axis`: `'time'` (T on one axis) or `'depth'` (Z) — the two the roadmap named.
- `width_px`: average a band, not a single pixel row, to reduce noise (the multi-line/averaging case).
- Output: a 2D array (position × time/depth) plus the coordinate metadata to label axes in real units
  (µm and seconds) when calibration is present — respect the pixel-size and frame-interval gates; label
  in px/frame when they are absent, never assume.

### Part B — the analysis-aware layer (the actual value)
A kymograph paired with the measurements PyCAT already computes, chosen from the roadmap's list by what
is cheap and on-thesis:
1. **Colocalization kymograph** — two channels' intensity over time along the line, with Pearson/overlap
   computed per time-slice and plotted alongside. Reuses the existing coloc metrics.
2. **Object-property kymograph** — for a tracked condensate, plot a property (diameter, intensity,
   circularity, partition coefficient) vs time. Reuses tracking identity + the per-object measurements.
3. **FRAP kymograph** — recovery across the bleached region over time, not just the mean-intensity
   curve; pairs with the just-decomposed `fit_frap_recovery`.
4. **Phase-boundary kymograph** (the distinctive, material-properties one) — track a condensate
   interface along the line and plot interface position / local intensity gradient over time. Useful
   for fusion/dissolution/maturation. Higher effort — specify it but gate it behind the simpler three.

Start with (1) and (2); they reuse the most existing machinery. (3) and (4) are follow-ons noted here
so the design accommodates them.

### The traps
- **Lazy-stack collapse** — always `materialize_stack`; add the guard test.
- **Units** — a kymograph with unlabelled axes invites misreading a drift rate; label µm/s when
  calibrated, px/frame otherwise, and say which.
- **Line width vs resolution** — a wide averaging band blurs sub-line structure; report the band width
  so the user knows the spatial averaging applied.
- **Registration/drift** — if the sample drifts, a fixed line samples different material over time. Note
  this; where drift correction exists, offer to apply it first.

## Output
- The kymograph as an image layer (tagged), axes labelled in real units when available.
- For analysis-aware variants, the paired metric as a synchronized plot (the per-slice coloc value, the
  per-time object property) — ideally with the cohort/selection hook so clicking a time-slice
  highlights that frame.
- A small table of the per-slice values for export.

## Tests (`core`, synthetic)
- A synthetic moving-bright-band stack produces a kymograph with the band's slope matching the known
  velocity (the correctness test).
- **Lazy-stack test:** a lazy time-series produces a full kymograph, not a single collapsed frame.
- Units: a calibrated stack labels axes in µm/s; an uncalibrated one in px/frame, never assumed.
- Colocalization kymograph: per-slice Pearson matches an independent per-frame computation.
- Object-property kymograph: for a synthetic tracked object with known shrinking diameter, the
  kymograph recovers the trend.
- Width averaging: a wider band reduces noise without shifting the recovered slope.

## Steps
1. `toolbox/kymograph_tools.py` — `kymograph` base (materialize-safe, calibrated labels).
2. Colocalization kymograph (reuse coloc metrics).
3. Object-property kymograph (reuse tracking identity + per-object measurements).
4. Output layers + synchronized metric plot + per-slice table.
5. A UI entry point (draw line, pick mode/channels/property).
6. Tests above, including the lazy-stack guard.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG. FRAP + phase-boundary variants
   noted as follow-ons.

## Definition of done
- A materialize-safe base kymograph over time or depth, with real-unit axis labels when calibrated.
- Colocalization and object-property analysis-aware variants, each reusing existing measurements.
- Outputs are tagged layers + a synchronized metric plot + an exportable per-slice table.
- Correctness proven against synthetic stacks with known dynamics.
- Full `pytest -m core` green.

## Cautions
- **`materialize_stack`, never `np.asarray`** on the stack — the frame-0 collapse landmine applies
  directly here, and a kymograph is a time-axis tool, so it is the worst place to hit it.
- **Label axes in real units or say px/frame** — an unlabelled kymograph invites wrong drift/velocity
  readings.
- Report the averaging band width; wide bands blur real structure.
- Drift makes a fixed line sample different material over time — warn, and offer drift correction where
  available.
- Start with the two cheap analysis-aware variants; FRAP and phase-boundary are follow-ons, not this
  spec's scope.
