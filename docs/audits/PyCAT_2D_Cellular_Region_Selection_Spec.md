# Spec — Calibrate and improve region selection in the 2D cellular fluorescence workflow

**Target:** the 2D cellular fluorescence (in-cell condensate) path only. Do not change the in-vitro,
brightfield, VPT, or time-series paths. Any shared helper you touch must leave those paths
byte-identical in behaviour — assert that with the existing route-equivalence tests.

**Baseline:** v1.6.460 working tree (`object_scale.py`, `boundary_refit.py`, the `intensity.py`
`robust_cell_background` change, `benchmarks/condensate_scale.py`).

**Author's note on scope of my knowledge:** I have not seen the 1.6.460 source, the annotation files,
or the Box test data. Every file path, function name, and data-format assumption below that is not
verbatim from the repo must be **discovered and reported**, not assumed. Where I name something I
haven't verified, it is marked `[verify]`. Do not invent file names to satisfy this spec.

---

## Data

- **Test images:** `C:\Users\gmwadsworth\Box\[0] PyCAT manuscript\Test`
- **User-defined masks (calibration ground truth):**
  `C:\Users\gmwadsworth\Box\[0] PyCAT manuscript\[3] Experimental data\Biological data comparison`
- **Reference figures:** two PNGs in the repo `docs/audits/` folder `[verify exact folder]` — the
  "Large / In-Cell / PyCAT" comparison, and the V 1.6.460 four-cell field with the zoomed crop.

---

## The problem, as observed

From Gable's direct observation of his own data and masks — these are the requirements, not hypotheses
to re-litigate:

1. **User masks do not track FWHM.** They sit further out, and the gap widens with object size. The
   manuscript deck's pred/GT area ratios are ~0.79 for small puncta and ~0.37 for large ones `[verify]`
   — PyCAT already under-covers before the refit, and `refit_level = 0.5` was observed to *shrink*
   masks further on the real test images, i.e. it currently moves in the wrong direction.
2. **Two plausible causes**, both worth testing: (a) perceived-brightness non-linearity of the display
   colormap, so the apparent edge is not the half-intensity point; (b) humans trace the *outer visible
   extent* — where the object becomes indistinguishable from background — which is far more generous
   than half-max. A starting estimate of **5–10% over background** is the working hypothesis.
3. **Dimmer objects are being missed.** In the four-cell field (V 1.6.460 image), the **3rd cell from
   the left** contains clearly perceptible objects absent from the final mask. These must be recovered.
4. **Larger irregular shapes** are detectable but usually unwanted. They need a filter toggle that
   **defaults to ON** (i.e. irregular objects excluded by default).
5. **Relaxing sensitivity fuses neighbouring objects.** Use a consecutive/stepped threshold scheme so
   objects grow without merging.

---

## Phase 0 — Inventory and pairing *(gate: do not proceed until this is reported)*

1. Enumerate both Box folders. Report: file count, formats, naming conventions, image dimensions, bit
   depth, channel layout, pixel size if present in metadata.
2. Determine the **annotation format** — label TIFF, binary mask, ImageJ `.roi`/`RoiSet.zip`, NPY,
   something else — and how one annotation corresponds to one image and one cell. Report it; do not
   guess.
3. Build an explicit **image ↔ annotation pairing table** (CSV under `benchmarks/`). Any image without
   an annotation, or annotation without an image, is listed as unpaired rather than silently dropped.
4. Report **how many annotated objects exist in total**, split by cell and by approximate size. The
   entire calibration's statistical power depends on this number — if it's ~20 objects, say so and
   temper every conclusion accordingly.
5. Report whether annotations record **who traced them** and whether multiple annotators traced the
   same object. If there is any overlap, compute inter-annotator IoU — that is the **ceiling** on any
   automated method, and every later result must be read against it.

**Stop and report before Phase 1.** If the annotation format is ambiguous, ask rather than assume.

---

## Phase 1 — Measure the level human masks actually correspond to

This is the core measurement and the highest-value deliverable. **Do not change any default until this
is done.**

### 1.1 Per-object level fit

For each annotated object, using the **raw (pre-enhancement) image** in the same units the refit
operates on:

- Estimate local background `bg` and noise `σ` in an annulus around the object, excluding all
  annotated objects and any detected object (use the sigma-clipped estimator from
  `robust_cell_background` `[verify name]`, so the calibration and the runtime agree).
- Take `I_peak` as a robust maximum (e.g. 99th percentile inside the annotation, not the single max).
- Sweep an intensity level `t` across the full range `bg → I_peak`. At each `t`, take the connected
  iso-level region containing the object's centroid, and compute IoU against the human mask.
- Record `t*` = the level maximising IoU, along with `IoU_max`, object area, equivalent radius,
  amplitude `(I_peak − bg)`, local `σ`, and the cell it belongs to.

### 1.2 Express `t*` in three competing parameterizations

The choice between these is the central design decision, and it must be **decided by the data**:

| Parameterization | Formula | Interpretation |
|---|---|---|
| **A — fraction of amplitude** | `f = (t* − bg) / (I_peak − bg)` | FWHM is `f = 0.5` |
| **B — relative over background** | `r = t* / bg − 1` | Gable's "5–10% over background" |
| **C — noise units** | `z = (t* − bg) / σ` | display-independent, noise-aware |

**Decision rule: adopt the parameterization with the lowest coefficient of variation across the object
population.** That is the one that generalizes. Report all three distributions (median, IQR, CV) and
state the winner explicitly.

This matters because A and B diverge sharply for dim objects: 5% of amplitude is tiny, while 5% over
background may sit above a dim object's peak entirely. Whichever wins, **B and C must be floored** —
a level below ~2–3σ over background will leak into noise regardless of how well it fits the median
object.

### 1.3 Test the two causal hypotheses

- **Size dependence.** Regress `t*` (in each parameterization) against object radius. The 0.79-vs-0.37
  area ratio strongly predicts that **`t*` falls as objects get larger** — plausibly because large
  condensates have flatter-topped profiles with a broad shoulder, so half-max cuts well inside the
  visible edge. If the trend is real and monotonic, the level must be **size-dependent**, not a
  constant, and the spec's later phases should implement it as a function of measured object scale
  (which `object_scale.py` already estimates).
- **Display dependence.** Regress `t*` against per-image dynamic range and against the contrast limits
  that would be produced by a standard auto-scale (min–max and 1–99th percentile). A correlation is
  evidence for the colormap/display hypothesis, and implies a level defined in **display-invariant**
  terms (A or C) rather than absolute intensity.
- **Annotator dependence.** If Phase 0 found multiple annotators, test whether `t*` differs between
  them. A significant difference caps achievable accuracy and belongs in the manuscript's limitations.

### 1.4 Deliverable

`benchmarks/mask_level_calibration.py` plus a written report containing: the three distributions, the
CV comparison and chosen parameterization, the size- and display-dependence regressions with plots, the
recommended default level (and its functional form if size-dependent), and the inter-annotator ceiling.

**Report before Phase 2.** The recommended default comes out of this measurement — it is not
5–10% by assumption, though that is the prior to beat.

---

## Phase 2 — Fusion-safe stepped relaxation

Implements requirement 5. Relaxing a global threshold merges neighbours; the fix is to let each object
relax **only as far as it can without touching another**.

### 2.1 Algorithm

1. **Seeds.** Take the current detection output (post `object_scale.py` two-pass) as seeds. Each seed
   carries a stable identity for the whole procedure — seeds are never created or destroyed here, so
   object **counts are unchanged by this phase**; only boundaries move.
2. **Ladder.** Build a descending threshold ladder from a conservative start down to the calibrated
   level from Phase 1, in fixed steps (`n_steps`, default ~20 `[tune]`), expressed in the
   parameterization Phase 1 chose.
3. **Per-object stopping.** Descend the ladder. At each level, label the thresholded mask. For each
   component, count the seeds it contains:
   - **1 seed** → accept the growth for that object; continue descending.
   - **≥2 seeds** → a fusion event. **Freeze every seed in that component at the previous level** and
     stop descending for them. Isolated objects keep relaxing further; crowded ones stop earlier.
4. **Result:** a per-object adaptive level, capped by the fusion constraint. Record the stop level and
   the stop reason (`reached_target` vs `fusion_capped`) per object — that per-object provenance is
   what lets a user see *why* a crowded object is drawn tighter than an isolated one.

### 2.2 Notes

- Prefer this pure topological rule over a seeded watershed as the primary mechanism: watershed would
  always split a merged component and could place a boundary where no intensity minimum exists.
  Freezing at the last non-fused level never invents an edge. Watershed may be offered as a secondary
  option if the calibration shows it matches human masks better in crowded fields — decide from data.
- The ladder must operate on the **raw/pre-enhancement image**, consistent with `boundary_refit.py`'s
  existing design `[verify]`.
- Guard cost: the ladder is O(n_steps × labelling). Cap `n_steps` and short-circuit once every object
  has stopped. The existing performance note (~11 s → ~15 s per 512² field) is the budget to respect —
  report the new timing.

### 2.3 Deliverable

Extend or replace `boundary_refit.py`'s level logic `[verify structure]`. Fusion behaviour gets a
dedicated test: two Gaussians at a known separation must remain **two** objects through the full
ladder, with the boundary between them placed at the last pre-fusion level.

---

## Phase 3 — Recover the missed dim objects

Implements requirement 3. The 3rd-from-left cell in the V 1.6.460 field is the reference case.

### 3.0 The asymmetry rule — read this before scoring anything

The author has confirmed the annotations were **drawn to measure objects**, so:

- **For boundaries, the masks are authoritative.** Phase 1 and Phase 2 may optimise against them
  directly, at full weight. The pred/GT area ratios (~0.79 small, ~0.37 large) are therefore a **hard
  finding**, not a soft one — large objects really are being drawn at roughly a third of their true
  area, which is why boundary level is the priority.
- **For counts, the masks are only loosely accurate.** They are a **lower bound on truth**, not a
  complete inventory. An annotator measuring objects may reasonably not have traced every faint one.

This creates a hard rule for this phase: **an object PyCAT detects that is absent from the mask is NOT
automatically a false positive.** Treat it as *unadjudicated*. Concretely:

- `detected ∧ in-mask` → true positive.
- `in-mask ∧ not-detected` → **definite miss**. This is the quantity to minimise, and it is the only
  count metric with authoritative ground truth.
- `detected ∧ not-in-mask` → **needs review**. Render these as a contact sheet (crop + intensity
  profile per object) for visual adjudication. Report them as a separate count; never fold them into
  an FP rate.

**Why this matters:** the reference case is objects Gable can *see* that PyCAT misses. If the annotator
also skipped some of those, then sweeping gate thresholds to minimise "FP against the masks" would
actively tune the detector toward under-detection — optimising against the exact blind spot being
fixed. The sweep in 3.2 must therefore plot *definite misses recovered* against *unadjudicated
detections added*, and the operating point is chosen after eyeballing the contact sheet, not by
minimising a number.

### 3.1 Diagnose before changing anything

Instrument the candidate path so that **every rejected candidate is logged with the specific gate that
rejected it and the margin by which it failed** — `cell_has_punctate_signal`, `local_snr`,
`local_intensity`, `global_snr`, size/area limits, the scale pass `[verify exact gate names]`.

Then, using the annotated masks as ground truth for "should have been found", produce a table:
**how many annotated objects are missed, and which gate is responsible for each.** This preserves the
distinction that already proved important — *not detected* versus *detected then gated out* — and
prevents loosening the wrong knob.

### 3.2 Then calibrate, don't loosen blindly

- Sweep each implicated gate's threshold and plot **definite misses recovered vs. unadjudicated
  detections added** (per the 3.0 asymmetry rule — not "false positives"). Choose the operating point
  from that curve *after* reviewing the contact sheet of unadjudicated detections; state explicitly how
  many were added and what fraction of those reviewed looked real.
- The prior history here is a real constraint: gates were tightened because of spurious puncta, and
  the earlier "In Cell 1" investigation deliberately declined to loosen per-object gates. **Do not
  relax a gate without showing its FP curve.** If a gate cannot be relaxed without unacceptable FP,
  report that as the finding rather than shipping a looser default.
- Prefer a **locally adaptive** gate over a globally looser one where the sweep shows the failure is
  contrast-dependent (a textured nucleoplasm raising the local floor) — that was the suspected
  mechanism in the earlier In Cell 1 case, distinct from scale.

### 3.3 Deliverable

The gate-attribution table, the sweep curves, a recommended operating point with its FP cost, and a
regression test pinning recovery on the 3rd-cell reference image.

---

## Phase 4 — Irregular-shape filter (default ON)

Implements requirement 4.

1. **Metric.** Compute per-object circularity (`4πA/P²`), solidity (`A/A_convex`), and eccentricity.
   Report which best separates the two populations in the real data.
2. **Empirical threshold — and first, an empirical question:** do the **user masks themselves include
   irregular objects?** Measure the shape-metric distribution of annotated objects. If annotators
   consistently excluded irregular shapes, that distribution sets the threshold directly. If they
   included them, say so — it would mean the default-ON filter disagrees with the ground truth, which
   is Gable's call to make, but he should make it knowing that.
3. **Control.** A checkbox in the 2D cellular fluorescence panel, **default checked** (= filter active,
   irregular objects excluded), with a tooltip stating the metric, the threshold, and that unchecking
   admits aggregates/nucleoli-like objects.
4. **Never silently delete.** Filtered objects must remain in the results table with a flag column
   (e.g. `shape_filtered = True`) rather than being dropped, so a count discrepancy is always
   explainable. This follows the anti-black-box rule that applies everywhere else in PyCAT.

---

## Phase 5 — Wire into the 2D cellular fluorescence workflow

1. **Controls**, in the existing "Show refinement parameters" group alongside the 1.6.460 checkboxes
   `[verify group name]`:
   - `Boundary level` — the Phase 1 calibrated default, in the Phase 1 parameterization, with the
     units named in the label (not a bare number).
   - `Fusion-safe relaxation` — checkbox, default **ON**.
   - `Filter irregular objects` — checkbox, default **ON**.
   - Tooltips state what each does *and* its failure mode, per house style.
2. **Defaults changed = a documented decision.** The CHANGELOG entry must record the old default, the
   new one, the number of annotated objects it was calibrated on, and the resulting IoU — so a future
   reader can see the evidence rather than a bare number.
3. **Scope guard.** Run the route-equivalence suite; in-vitro, brightfield, VPT and time-series results
   must be unchanged. If a shared helper had to change, add an assertion proving those paths still call
   the old behaviour.

---

## Acceptance criteria

1. **Calibration report exists** with the three parameterizations, the CV comparison, the chosen level,
   and the size-/display-dependence findings — the empirical answer to "what level do humans trace at."
2. **Agreement with user masks improves**, reported as **pred/GT area ratio (primary)** and mean IoU
   (secondary), both split by object size. The area ratio is the number that exposes the 0.79/0.37
   problem; IoU alone can hide it. Target: area ratio → 1.0 across *both* size classes, since the
   current spread between them is itself the defect.
3. **No fusion:** the two-Gaussian test keeps two objects across the full ladder.
4. **The 3rd-from-left cell** recovers its visible objects. Report definite misses recovered and
   unadjudicated detections added (never a bare "FP rate"), with the contact sheet attached.
5. **Irregular filter** defaults ON, is calibrated against the annotation shape distribution, and flags
   rather than deletes.
6. **Per-object provenance:** every object records its stop level and stop reason.
7. **Other workflows unchanged** (route-equivalence green).
8. **Runtime** within budget of the current ~15 s per 512² field; report the measured figure.

---

## Constraints and known-red tests

- **Three tests were already failing before this work**, on files unrelated to it: the complexity
  ratchet (117 long functions vs a ceiling of 113; `image_processing_tools.py` at 222 lines vs 221),
  an uncategorised broad-ok in `size_estimation.py:152`, and `test_ui_structure.py` still looking for
  workflow layouts in `ui_modules.py` after they moved. **Do not fix these as part of this work**, and
  **do not make them worse** — the ratchet is already over its ceiling, so this work must add **zero**
  functions over 120 lines and zero new broad exception handlers. If a phase would breach that,
  decompose it rather than raising the ceiling.
- Version bump + CHANGELOG per the standing ritual. Phases 1, 2, 3, and 4 are separately shippable —
  prefer four commits over one.
- **Report at the Phase 0 and Phase 1 gates before continuing.** The whole point is that the level is
  measured, not assumed; proceeding past Phase 1 without the measurement defeats the exercise.

---

## What I could not determine, and why it matters

- **The annotation count.** If there are only a handful of traced objects, every regression in Phase
  1.3 is underpowered and the "size-dependent level" conclusion may not be supportable. Report `n`
  prominently and scale the claims to it.
- ~~Whether the annotations are boundary-accurate or indicative.~~ **Resolved by the author:** the
  boundaries were **drawn to measure objects**, so they are authoritative ground truth for boundary
  calibration. Counts are "loosely accurate" — see the asymmetry rule in Phase 3.
- **The exact 1.6.460 structure** of the refit/scale modules and the gate names. Discover and report
  rather than assuming the names in this spec are right.
