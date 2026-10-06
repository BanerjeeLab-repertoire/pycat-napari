# Spec — Optical ring rejection, a user-controlled contrast floor, and scale reconciliation

**Baseline:** repo at **1.6.469**, 2D cellular fluorescence workflow.

**Decisions already made — do not revisit:**
- **No checkbox for a separate large-condensate protocol.** One segmentation path.
- Out of scope: the punctate gate, the Felzenszwalb merge (1.6.465/466 are bit-identical and are where
  the speed came from), the user-mask boundary calibration.

**Correction to my earlier diagnosis, recorded so it isn't repeated.** I previously attributed the
annular objects to a *detection duplicate* — the primary pass band-passing a large condensate's
interior away and detecting only its rim, while the coarse pass detected the filled disc. Gable's
account is that these are **real optical features in the image**: Airy rings and interface effects
arising from the object's geometry and how light behaves at a refractive-index boundary. If that is
right, **scale reconciliation will not remove them**, because every scale will legitimately detect a
genuinely bright annulus. The two mechanisms are not mutually exclusive and Phase A settles which is
operating. The fix for the optical case (Phase B) is different in kind and is the priority.

---

## Phase A — Settle the mechanism (decisive, and it gates Phase B)

For each reference ring from Meet's crops, in the **raw, pre-enhancement** image:

1. Take the parent condensate's centroid and compute a **radial intensity profile** outward, averaged
   over angle (and separately per-angular-sector, so partial arcs are visible).
2. Classify:
   - **A genuine local maximum beyond the object's edge in the RAW profile → optical ring.** Real
     photons; Phase B applies.
   - **No such feature in raw, ring present only in the enhanced/band-passed image → detection
     artifact.** Phase C's reconciliation applies.
   - Both can occur on different objects. Report the split.
3. Record, for each confirmed optical ring: its radius relative to the parent's equivalent radius, its
   peak intensity as a **fraction of the parent's peak**, and its radial width. These three numbers
   parameterise the Phase B rejector — do not guess them, measure them.

**Also check in this phase:** whether `fz._bridge_fragmented_rims` is converting halo arcs into filled
objects. Its purpose is to reconnect a condensate's *own* fragmented rim and fill it — but its fourth
condition (bridge brightness relative to the pieces' own raw footprint) would **pass** for a genuine
continuous Airy ring, since such a ring is continuous and bright in the raw image. If it is bridging
and filling halo arcs, it is manufacturing an inflated object that covers the condensate *and* its
halo. Report whether this is happening on the reference crops; it may be a significant part of the
severity Gable describes.

**Report before Phase B.**

---

## Phase B — Reject optical rings (the priority)

**Requirement, stated precisely:** a ring or arc caused by the parent object's optics must be **removed
entirely** — not kept as its own object, and **not merged into the parent**. Merging it in would
inflate the parent's measured area and dilute its intensity, corrupting size distributions and
partition measurements. The parent's boundary stays at its true edge.

Fragmented rings are the severe case: one halo becomes several arc-shaped false "condensates", each
inflating the count. The rejector must therefore work on **arcs**, not only complete annuli.

### Detection rule

For each detected object `O`, find the nearest object `B` with a higher peak intensity. `O` is a halo
fragment when all of the following hold:

1. **It follows B's contour.** Compute the distance from each boundary pixel of `O` to `B`'s boundary.
   A halo sits at a roughly constant standoff, so the distribution is tight — gate on a low coefficient
   of variation. This is the discriminator that works identically for a full ring and a short arc, and
   it is what a real neighbouring condensate will fail.
2. **It is thin.** Its radial width is small relative to its arc length, and small relative to `B`'s
   radius. A condensate is filled; a halo fragment is a sliver.
3. **It is dim relative to its parent.** `O`'s peak is below the measured fraction of `B`'s peak from
   Phase A. (An ideal Airy first side lobe is ~1.7% of peak; interface and defocus halos are brighter.
   Use the measured value, with the physics as a sanity check, not as the threshold.)
4. **Its standoff matches.** The median distance from `B`'s boundary is consistent with the Phase A
   radius measurement, scaled to `B`'s size.

All four required. Each alone has a plausible false positive — two genuinely adjacent condensates can
satisfy (1) by chance, a small punctum satisfies (2) and (3) — the conjunction is what makes it safe.

### Behaviour

- Rejected ring objects are **flagged, not silently dropped** — a `ring_rejected` column in the results
  table and a distinct colour in the overlay, so a count discrepancy is always explainable and a user
  can see what was removed and disagree.
- Provide a toggle, **default ON**, so the rejection can be disabled for inspection.
- **Do not alter the parent's mask.** Removing the ring must not change `B`'s boundary at all.

---

## Phase C — Contrast floor with a live slider

Gable's call: the default is **raised**, because Meet judges these objects should not be counted, with
the control exposed so the decision is visible and reversible rather than baked in.

### The control

- A **checkbox** — "Apply minimum contrast floor" — **default ON**.
- A **slider** beside it, in units of the object's local contrast-to-noise ratio.
- **Default value: calibrated, not invented.** Determine the value that removes the objects Meet circled
  on his reference cells while retaining the ones both he and Gable agree are condensates. State that
  value and the cells it was fitted on in the CHANGELOG. If no single value separates them cleanly, say
  so and report the overlap — that is itself the finding.

### Live overlay update (the part that makes it usable)

This is feasible cheaply because **`local_cnr` is already computed per object** in
`puncta_refinement.py` — contrast above background in units of background noise. The slider must not
re-run segmentation.

1. **Segment once.** Retain, per object, its `local_cnr` alongside the label image.
2. **On slider move, re-filter only.** Build a lookup table `label → keep | drop` from the stored
   per-object values and apply it with vectorised indexing (`lut[labels]`). On a 512² field this is
   sub-millisecond, so the overlay tracks the slider in real time.
3. **Make the change intelligible**, which is the actual requirement — the user must see *which* shapes
   are being recovered or lost, not just a count. Use **two label layers** updated by the same LUT:
   - objects above the floor in normal label colours;
   - objects below the floor in a single muted colour (e.g. dim grey/red), still visible.
   Sliding then visibly moves objects between the two, so the population at the margin is directly
   inspectable. Dropping them to invisible would hide exactly the information being judged.
4. **Debounce** the slider signal (the same pattern used for the MSD curve picking) so a fast drag
   coalesces rather than queueing redraws.
5. Show a **live count** of kept / below-floor objects next to the slider.

### Behaviour

- Below-floor objects are **flagged in the results table, not deleted**, with their `local_cnr`, so a
  reviewer can see what the floor excluded and at what value.
- Record the floor value for batch replay alongside the other refinement parameters.
- The floor filters on **contrast only** — no shape or size term.

---

## Phase D — Scale reconciliation (still needed; fixes the split and the merge)

Independent of the ring question. The multiscale union at `subcellular.py:320-322` is a plain boolean OR
with no reconciliation, which produces two further defects Meet reported:

- **One condensate split in two** — two non-touching primary sub-peaks become two markers in
  `ndi.label(seeds)`; the watershed divides the object and `keep_objects_apart` makes the seam visible.
- **Two nearby small condensates merged into one** — at the coarse scale they blur into a single blob;
  OR-ing that with the two resolved primary seeds yields one oversized object. This is a **size and
  count error** that propagates into size distributions and any downstream density or partition
  measurement, independent of the sensitivity debate.

**Rule.** For each coarse-scale detection `L`, let `P` be the primary-scale detections it overlaps:

| `|P|` | Action | Why |
|---|---|---|
| **0** | Keep `L` | A genuinely new large object the primary band-pass could not see — preserves the multiscale benefit (missed 102 → 80). |
| **1** | `L` replaces that primary detection | Same object, better resolved at its own scale — removes the split. |
| **≥2** | Discard `L`, keep the primary detections | The fine scale resolved them; the coarse scale would fuse them — removes the merge. |

Applied to both `puncta_mask_crop` and `refined_puncta_mask_crop`, before `refit_regional_boundaries`,
folding one scale at a time. Object count can only stay flat or fall by duplicate views; nothing is
dropped for being dim. `keep_objects_apart` stays — it fixed a real 11% fusion rate and was never the
bug.

---

## Tests

**Ring rejection**
1. Synthetic condensate with an added Airy-like annulus at the Phase A measured radius and brightness →
   the annulus is rejected, the condensate's mask is **unchanged**.
2. The same annulus **fragmented into 5 arcs** → all five rejected (the severe case).
3. **Negative control:** a genuine small condensate adjacent to a large one → **not** rejected. This is
   the test that matters most; run it at several standoffs and record the closest separation at which
   the rejector stays correct.
4. Meet's reference crops pinned as fixtures.

**Contrast floor**
5. Slider at the minimum reproduces pre-floor object counts exactly.
6. Overlay update measured under 16 ms per slider step on a 512² field.
7. Below-floor objects appear in the results table with their `local_cnr`.

**Reconciliation**
8. Rim + disc of one object → one object. Double-seeded object → one object, no seam. Two puncta the
   coarse scale fuses → stays two.
9. A large object the primary pass misses entirely is still detected.

**Global**
10. Missed objects on the annotated large fields no worse than the current 80 of 757; fusion rate no
    worse than 11%.
11. In-vitro, time-series, two-channel coloc and z-stack byte-identical; route equivalence green.
12. Timings within noise of 1.6.469.

---

## Constraints

- Phases are separate commits. **A → B → C → D**; A gates B, the rest are independent.
- Version bump + CHANGELOG per the standing ritual. The Phase C entry must state the calibrated default
  and the cells it was fitted on.
- Zero functions over 120 lines, zero new broad exception handlers — the complexity ratchet is at its
  ceiling.
- If Phase A shows the rings are **not** optical, stop and report rather than building Phase B — the
  rejector would then be solving a problem that doesn't exist, and Phase D alone would be the fix.
