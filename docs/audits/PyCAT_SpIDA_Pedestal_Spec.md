# Spec — SpIDA pedestal: measure it, estimate it, or bound it

**Status: ROADMAP.** Not scheduled. **Blocked on real SpIDA data**: no acquisition is available yet, and every
step below needs it before it can be trusted (see *Validation*).
**Baseline:** 1.7.005 + [Unreleased] (OperationService batch D).
**Evidence:** synthetic only — `validation/spida_pedestal_sim.py` (private harness) reproduces every number here.

---

## The problem

SpIDA reads molecular density N and brightness ε from the shape of the intensity histogram. A camera pedestal P
shifts the histogram and the fit reads the shift as more molecules (`spida_tools.check_assumptions`, measured at
true N = 8: P = 200 fits N = 31, +287 %; P = 800 fits N = 196, +2347 %).

The current gate (A1'', Completion Spec) compares the density implied by the first two moments, mean²/var, with
the density implied by the skewness, ~1/skew². A pedestal inflates the first by ((P + Nε)/(Nε))² and leaves the
second alone. It flags when the ratio exceeds 1 + max(25 %, 3 bootstrap SE), and when the threshold itself
exceeds 1.5 it says a pedestal **cannot be ruled out**.

**At high N that is most of the time.** The skewness falls as 1/√N while its standard error stays ~√(6/n), so
the gate loses power exactly where SpIDA is used on bright cells. Because the ratio and the moment estimate of N
are inflated by the same factor, the threshold minus 1 is the N inflation the gate cannot exclude:

| True N | Largest N inflation the gate cannot exclude (single ROI, 20 000 px, P = 0, ε = 25, read σ = 20; median of 20 fields) |
|---|---|
| 8 | +33 % |
| 20 | +48 % |
| 50 | +79 % |
| 100 | +98 % |
| 300 | +169 % |

No single-histogram statistic fixes this. A pure offset moves the floor, the mean and every percentile together
and barely changes the shape of a near-Gaussian histogram. The information has to come from **outside the one
histogram**: a dark reference, or several regions of the same image.

---

## Proposal — three tiers, used in order

### Tier 1 — Measure the dark level (preferred)

A cell-free background ROI, or a dark frame (shutter closed, same exposure and gain), gives the pedestal as its
mean and the read noise as its standard deviation. Those are the two inputs SpIDA already takes: `white_noise`
(the subtracted offset) and `read_noise_sd`.

- Panel: an optional **"Dark / background ROI"** selector next to the ROI dropdown. When set, it fills
  `white_noise` and `read_noise_sd` and shows where they came from. A typed value still overrides it.
- Metadata: read the camera offset where the file records it. Which formats do (OME, ND2, CZI, vendor TIFF tags)
  is to be surveyed; missing metadata is "not available", never 0.
- A cell-free background contains autofluorescence and out-of-focus signal as well as the pedestal. A dark frame
  isolates the camera. The panel should say which one it was given; the tooltip already says the same about
  `white_noise`.

### Tier 2 — Photon-transfer estimate across sub-regions (no reference available)

For a compound-Poisson emitter with pixel intensity εk + P + read noise, k ~ Poisson(N):

    var − s² = ε · (mean − P)

Every region of the same image (same ε, same P) lies on **one line in (mean, variance)**. Its slope is ε and its
x-intercept is P. This is the photon-transfer method that detector calibration uses. It needs only the first two
moments, so unlike the skewness it does not weaken at high N.

Procedure:
1. Tile the analysis ROI (or take every ROI and cell in the image) into sub-regions of ≥ 5000 px.
2. Per tile, take the mean and the read-noise-corrected variance. Fit a line, weighted by each tile's variance
   standard error.
3. P̂ = x-intercept, with a bootstrap CI over tiles. Subtract P̂, then fit SpIDA as now.
4. Report P̂ ± CI, the density range the tiles span, and the line's residual (curvature means the model failed;
   see below).

Simulated (8 tiles × 5000 px, ε = 25, read σ = 20, 200 trials per row):

| Tile density range | True P | SD of P̂ | N error after subtracting P̂ | N error uncorrected (moments) |
|---|---|---|---|---|
| 20–60 | 0 / 200 / 800 | ±21–23 | 0.0 to +0.9 % (±5 %) | 0 / +51 / +275 % |
| 50–150 | 0 / 200 / 800 | ±49–50 | +0.1 to +0.3 % (±5 %) | 0 / +19 / +88 % |
| 100–300 | 0 / 200 / 800 | ±105–110 | 0.0 to +0.5 % (±5 %) | 0 / +9 / +40 % |
| **100–130 (narrow)** | 0 / 200 / 800 | **±227–258** | +0.2 to +3.4 % (**±16–19 %**) | 0 / +15 / +64 % |

The "uncorrected" column is the moment estimate. The histogram fit is worse (+287 % at N = 8, P = 200).

**It needs a lever arm.** When tile densities span < ~2×, the intercept is poorly constrained and the method
must say so: report the CI and fall through to Tier 3 rather than subtract a P̂ it cannot support.

### Tier 3 — Report a bound, not "cannot be ruled out"

When neither tier applies, replace the current wording with the number the gate already computes:
*"No dark reference; the histogram shape cannot exclude a pedestal that inflates N by up to +X %."*
X = threshold − 1. Every result, whichever tier it came from, carries a `pedestal_source` (`dark_frame`,
`background_roi`, `metadata`, `photon_transfer`, `typed`, `none`) and a `pedestal_n_bound`.

---

## Where Tier 2 can be wrong (the reason it needs real data)

1. **Brightness varying with density.** The method assumes one ε across tiles. If oligomeric state rises with
   expression level, which is what SpIDA is often used to find, ε increases with mean. The line then curves or
   tilts, and P̂ absorbs the change. The fit residual and a quadratic term are the diagnostic. A material
   curvature must refuse Tier 2, not report a P̂.
2. **Spatial structure inside a tile.** Uneven density (membrane edges, puncta, gradients) adds variance that is
   not shot noise. Tiles must exclude puncta (the condensate mask exists) and edges. Tile size trades this
   against moment noise.
3. **Uniform illumination and detector gain.** A falloff varies the effective ε across the field
   (`data_qc.illumination` measures this). EMCCD excess noise doubles the slope but keeps the intercept;
   sCMOS per-pixel offset and gain maps break the single-line assumption.
4. **Read noise.** An error in s² shifts the intercept by Δs²/ε. A dark frame (Tier 1) fixes this too.
5. **Too few tiles or too narrow a range** gives a wide CI (table above). The CI is the honest output.

---

## Implementation sketch (when it is scheduled)

- `spida_tools`: `estimate_pedestal_photon_transfer(pixels_by_tile, read_noise_sd) -> {P, ci, slope, residual,
  range_ratio, usable}`, and `pedestal_from_reference(pixels) -> {P, read_noise_sd}`. `check_assumptions` reports the
  Tier 3 bound in place of the current low-power warning.
- **Catalog:** SpIDA is on the `_NOT_YET_IN_CATALOG` ratchet (`tests/test_kernel_contract.py`). This work should
  move it into the catalog first: `spida.calibrate`, `spida.analyze`, with `spida.pedestal_estimate` as a `part_of`
  component. The panel and any batch step then reach it through `OperationService`, and the ratchet shrinks.
- Panel: the dark/background ROI selector, the pedestal line in the results (value, source, CI or bound), and the
  photon-transfer plot (mean vs variance with the fit) as an optional diagnostic.
- No change to the fit itself. The pedestal is subtracted before `build_intensity_histogram`, as `white_noise`
  is today.

## Validation (the blocker)

Needed before any of this ships:
1. **A monomer control** (e.g. monomeric GFP) acquired with a dark frame at the same settings. The dark frame gives
   true P and s. Tier 2's P̂ on the cell must agree with it within its CI.
2. **Several cells across a range of expression levels** in one field or session, for the lever arm.
3. **A known dimer or oligomer control**, to check failure mode 1: Tier 2 must not erase a real brightness
   difference, and its curvature check must fire when ε varies.
4. At least one confocal (photon-counting or analog PMT) and one camera (TIRF) dataset; the pedestal behaves
   differently on each.

Acceptance: on (1) the Tier 2 P̂ is within its CI of the dark-frame P, and N after correction is within the fit's
own uncertainty of N computed with the dark-frame P. On (3) the oligomeric state ratio is unchanged by Tier 2. The
existing skewness gate stays as a cross-check and must not raise more false flags than it does today.

## Open questions

- Tile the ROI, or use cells as the "tiles"? Cells give a larger lever arm but mix cell-to-cell differences in ε.
- Should Tier 2 subtract automatically, or propose a P̂ for the user to accept?
- Is a dark frame a realistic ask for the intended users, or should Tier 2 be the default?
