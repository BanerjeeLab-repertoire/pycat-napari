# Claude Code spec — Ratiometric / two-channel intensity-ratio analysis

> **✅ STATUS — core DONE, shipped in 1.6.182.** `toolbox/ratiometric_tools.py` — `ratio_image` +
> `object_ratios` (+ `RatioResult`): background-subtracted-first, denominator-thresholded-to-NaN with the
> excluded fraction reported, both summary modes (`ratio_of_means` default + `mean_of_ratio`) labelled,
> optional bleed-through coefficient (`D − c·N`, no auto-unmixing) with an uncorrected flag. `ratio`
> registered in the measurement ontology with its caveats. `tests/test_ratiometric.py` (known-ratio
> recovery, the pedestal test, low-denominator→NaN, mean-of-ratio vs ratio-of-means, bleed-through
> bias+correction, ontology). Follow-on (thin UI): the tagged ratio LAYER output and the channel-picker
> widget — the computation they consume is delivered.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. A genuine
capability gap on hardware the lab already has: multichannel confocals are present but there is no
ratiometric module. Pure downstream analysis, composes with existing segmentation, and it is the
intensity-based cousin of FLIM — much cheaper to build and buildable now.

## Why this, now
Ratiometric imaging (per-pixel or per-object channel ratios) reports environment-sensitive quantities:
FRET-by-ratio, and polarity/viscosity/pH from environment-sensitive dyes. The lab's confocals produce
the two-channel data; PyCAT can already segment the objects; what is missing is the ratio computation
done *correctly* — which is where the value is, because a naive `A/B` is riddled with traps.

Verified: no ratiometric/`ratio_image`/FRET-by-ratio module exists. The channel-math helpers scattered
across `image_processing_tools`, `partition_enrichment_tools`, and `general_image_tools` are the
building blocks, not the analysis.

## The design — and the traps that ARE the science
A ratio image is trivial to compute and easy to get wrong. The module's worth is in handling the
traps, not the division.

```python
def ratio_image(numerator, denominator, *, background_num=0.0, background_den=0.0,
                threshold=None, mask=None) -> RatioResult
def object_ratios(labels, numerator, denominator, *, background=..., mode='mean_of_ratio'
                  ) -> pd.DataFrame
```

### The traps, each handled explicitly
1. **Background before ratio, always.** `(N-b_N)/(D-b_D)`, not `N/D`. An offset in either channel bends
   the ratio — exactly the partition-coefficient reasoning already written in
   `partition_enrichment_tools`. Reuse that background machinery; do not reinvent it. Background
   subtraction is mandatory input, not optional polish.
2. **The low-signal denominator problem.** Where `D ≈ 0`, the ratio explodes into meaningless spikes.
   Require a **denominator threshold**: pixels/objects below it are `NaN`, not a huge number. Report
   how many were excluded — a ratio map that is 60% thresholded is telling you the measurement barely
   holds.
3. **mean-of-ratio vs ratio-of-means.** For per-object summaries these differ and answer different
   questions. `mean(N_i/D_i)` weights every pixel equally (noisy where D is small);
   `mean(N)/mean(D)` is the aggregate ratio (robust, but hides heterogeneity). **Offer both, default to
   ratio-of-means, and label which is which** — silently picking one is a subtle bias.
4. **Bleed-through / spectral crosstalk.** If channel A leaks into channel B, the ratio is corrupted.
   Provide an optional linear unmixing coefficient (user-supplied, from a single-label control) and
   **warn** that uncorrected bleed-through biases the ratio toward 1. Do not attempt automatic unmixing.
5. **Registration.** A ratio assumes the two channels are pixel-aligned. If a registration offset is
   known/detectable, warn — a half-pixel shift at an object edge produces spurious ratio rings (the
   same edge-artifact logic as the scan-shear QC).

### Output
- A **ratio layer** (with `NaN` where thresholded), added with proper tags so it flows through the
  platform.
- A **per-object table**: `ratio_mean_of_ratio`, `ratio_of_means`, fraction thresholded, background
  used — so the choice and its caveats travel with the number.
- Register `ratio` in the **measurement ontology** with its definition, the mean-of-ratio caveat, and
  the bleed-through caveat.

## Tests (`core`, synthetic)
- A known ratio field (construct N and D with a fixed ratio) is recovered within tolerance after
  correct background subtraction.
- **The pedestal test:** adding an offset to one channel and NOT subtracting it bends the recovered
  ratio; subtracting it recovers the truth. (This is the whole point — proves background-first matters.)
- Low-denominator pixels become `NaN`, not spikes; the excluded fraction is reported.
- mean-of-ratio and ratio-of-means differ on a heterogeneous field and agree on a uniform one — and are
  labelled correctly.
- A bleed-through-corrupted field, uncorrected, biases toward 1; the warning fires.
- The ratio layer carries `NaN` in thresholded regions and correct tags.

## Steps
1. `toolbox/ratiometric_tools.py` — `ratio_image` + `object_ratios`, reusing the partition background
   machinery.
2. Denominator thresholding with reported excluded fraction.
3. Both summary modes, labelled; default ratio-of-means.
4. Optional bleed-through coefficient + warning; registration warning.
5. Ratio layer with tags; per-object table; ontology entry.
6. A UI entry point (pick numerator/denominator channels, background, threshold).
7. Tests above.
8. Full `pytest -m core` green.
9. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Per-pixel and per-object ratios computed with mandatory background-first subtraction.
- Low-denominator regions are `NaN` with the excluded fraction reported.
- Both summary modes offered and labelled; bleed-through and registration caveats warned.
- Ratio layer tagged; per-object table records the mode and background; ontology entry added.
- Full `pytest -m core` green.

## Cautions
- **Background before ratio is mandatory**, not optional — an un-subtracted offset silently bends every
  ratio toward 1. Reuse the partition background reasoning.
- **Never emit a raw ratio where the denominator is near zero** — threshold to `NaN` and report the
  fraction, or the map is dominated by meaningless spikes.
- mean-of-ratio vs ratio-of-means is a real scientific choice; label it, don't hide it.
- Do not attempt automatic spectral unmixing — take a user coefficient and warn otherwise.
- This is downstream analysis; do not reimplement acquisition or channel loading — consume existing
  layers.
