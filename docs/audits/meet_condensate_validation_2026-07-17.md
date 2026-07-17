# Validation ask — Meet's condensates against 1.6.87 (segmentation refinement)

**Date:** 2026-07-17 · **Tree:** `main` @ 1.6.87 · **For:** Meet Raval
**Status:** awaiting real-data validation. **Not on PyPI** — test from `git pull` on `main`.

Your report — *large condensates are being wrongly rejected by the refinement filter* — drove three
releases. **Your diagnosis was right.** The remedy changed, and the result needs your data to confirm,
because everything below was measured against synthetic Gaussian blobs on flat noise. Your condensates
have real internal texture and real boundary gradients; mine do not.

---

## TL;DR — what to look for

1. **Does the rejection you reported stop?** That is the headline. Large condensates should now pass
   **on their merits**, with nothing exempted.
2. **Do your puncta results move?** They should not — not by one pixel.
3. **Do your total counts drop?** Expected, and it should be noise leaving.
4. **If it is only a marginal improvement, say so.** That is the most useful thing you could report:
   it would mean your data has something the synthetic does not.

---

## What changed, and why it affects you

### 1.6.84 — your plumbing fix, shipped under your name
`segment_subcellular_objects` accepted five refinement thresholds and **dropped them on the floor**
before calling `puncta_refinement_func`, which fell back to its own defaults. Those defaults are
*identical* (`-3.0, 1.0, 1.0, 1.17, 0.25`), which is why nobody saw it — at defaults it changes
nothing. **It only bit when you changed a threshold**, at which point your control silently did
nothing.

This was inside your commit, unmentioned by its title. It is independent of condensate size, so it
shipped on its own. Authorship preserved.

> **If you ever tuned a threshold and it seemed to do nothing — that was this.** Worth re-running any
> parameter sweep you did before 1.6.84.

### 1.6.86 — the CNR gate now runs in the default path
`puncta_refinement_filtering_func_fast` is documented as bit-for-bit identical to the slow filter, and
a test asserts it. **It was not true.** The 1.5.416 CNR fix went into the slow filter and never into
the fast one — and `_PYCAT_REFINE_FAST = True`, so the fast one is what everyone runs:

```
slow:  local_cnr = (dilated_mean - loc_med) / loc_sd      # background-subtracted, robust
fast:  dilated_mean / (img_local_bg_std + eps)            # pedestal in the numerator only
```

The bare ratio **cannot reject anything** with a positive camera pedestal (`object_mean/bg_std` ≈ 24
against a threshold of 1.0), so every noise blob surviving the other checks was kept and counted.

Ground truth (`synthetic_puncta_image`, 3 amplitudes × 3 seeds): CNR removed **0 real puncta and 128
spurious**. On the bundled `Image 1`, the default now keeps **6** where it kept 11.

> **Expect your counts to drop.** The refinement now reports how many it rejected and why — read that
> message rather than trusting the count.

### 1.6.87 — the local-background ring scales with the object *(this is your fix)*
`local_intensity_condition` and `gradient_condition` compared an object's interior (eroded 1px)
against a band 1-4px outside it, **regardless of the object's size**. Right for a punctum; wrong for a
condensate:

- eroding 1px off a 30px-wide object removes almost nothing → the "interior" is basically the whole
  object, boundary included;
- a 1-4px ring hugging a large object's edge sits **inside that object's own halo** (PSF tail +
  boundary gradient, both of which scale with the object) → the "background" is contaminated upward →
  contrast underestimated → **real objects rejected.**

That is your argument, and it is why this exists. The geometry now scales per object:

```
r_eq = sqrt(area / pi)
erode = gap = 0.5 * r_eq        band = 1.0 * r_eq
```

**A punctum comes out at 1/1/2 — byte-identical to the old fixed geometry.** So punctate results must
not move. The ceiling is physical, from Gable: in cellulo the cell bounds the condensate and all but
extreme ones are ≤ ~25% of the cell **diameter**, so the standoff caps at 25% of the cell's equivalent
radius; in vitro condensates exceed a cell, and `cell_area` is then the field, so the cap scales with
it.

Measured against the fixed ring, ground truth:

| | real kept (before → after) | spurious kept (before → after) |
|---|---|---|
| amp=30 ×3 seeds | 3→7, 3→8, 11→14 | 8→9, 13→15, 8→8 |
| amp=60 ×3 seeds | 6→10, 7→9, 8→11 | 5→5, 6→6, 2→3 |
| amp=120 ×3 seeds | 18→21, 25→27, 27→28 | 1→1, 6→6, 3→3 |

**+27 real puncta recovered across 9 fields, for +4 spurious.** The fixed rim was rejecting genuine
objects — exactly as you said.

---

## Why your branch was closed rather than merged

Your exemption (skip the two checks for objects ≥ 150px) is on `Meet-Raval-exemption`, reworked per
review (parameterised, reported, tested) and retained for the record. It is not merged, for two
reasons — **neither of which is that the diagnosis was wrong**:

**It stopped working once CNR went live.** It worked because the fast path's SNR gate was *dead*. CNR
reads the **same fixed rim**, so exempting `local_intensity` merely handed the object to `local_snr`:

```
exemption ON : dropped (900px): local_snr                  -> 0 px kept
exemption OFF: dropped (900px): local_intensity, local_snr -> 0 px kept
```

For a large object the eroded/dilated/plain means converge, so `local_intensity` (contrast < 1.17σ)
and CNR (contrast ≤ 1.0σ) are nearly the same test. Exempting one hands the object to the other.

**A fixed pixel bar could not have been right.** In vitro condensates exceed a cell, so "large" has no
fixed value. And ground truth put real detections at a **median area of 157px** — a 150px bar sits at
the *median*, not "comfortably above any single punctum" as the comment claimed; it would have
exempted 77% of real detections from the local checks.

Fixing what the checks *measure* means nothing needs exempting and one rule holds at every size.

---

## How to test

```bash
git pull origin main          # -> 1.6.87
pytest -m core                # 832 passed, 2 skipped
```

Run the same condensate images that prompted your branch, and compare against a pre-1.6.83 checkout.

**Read the refinement message.** It is always on now, in both paths, and it names the reason:

```
Puncta refinement: N of M detections rejected. Reasons: local_intensity (k), local_snr (j), ...
```

If your condensates are *still* rejected, **that message is the answer** — it says which check did it.
Send it verbatim.

For per-object detail:

```bash
PYCAT_REFINE_DEBUG=1 python -m pycat        # prints each dropped label + its reasons
```

To compare geometry directly:

```python
from pycat.toolbox.segmentation_tools import _local_ring_radii
_local_ring_radii(area_px, cell_area_px)     # -> (erode, gap, band); a punctum gives (1, 1, 2)
```

---

## What to report back

| Question | Why it matters |
|---|---|
| Do your large condensates pass now? | The whole point. If not, paste the refinement message. |
| Did your **punctate** counts move at all? | They must not. If they did, the 1/1/2 compatibility claim is wrong. |
| How many detections did you lose, and were they noise? | 1.6.86's removals should be spurious. Ground truth says so; your data decides. |
| Is the improvement large or marginal? | Your branch's premise was that the checks misfire on **essentially every** real large condensate. If it is marginal on your data, your data has something the synthetic lacks — and that is the next thing to chase. |
| At your magnification: single punctum area (px)? condensate area (px)? | Everything here is calibrated on synthetic sizes. Real numbers would let the ring fractions be checked against a real distribution rather than a generated one. |

---

## Caveats — read before trusting any number above

- **All of it is synthetic.** `synthetic_puncta_image` paints Gaussian blobs on flat Gaussian noise.
  Real condensates have internal texture and real boundary gradients. Every ground-truth claim here is
  "on beads that are not yours".
- **The detection rate in those runs was poor** (3-28 of 40 real puncta found) — `ball_radius=6` and
  my normalisation are not tuned for that fixture. The *absolute* counts are weak; the comparisons are
  paired (identical inputs, one condition changed), so the direction is sound and the magnitudes are
  not.
- **The ring fractions (0.5 / 0.5 / 1.0 × r_eq) are a design choice**, anchored only by the constraint
  that a punctum reproduce the old geometry exactly. They are not measured against real halos. If your
  condensates need a wider standoff, this is the knob.
- **Nothing here has been driven in a real napari session by eye** — it is verified by tests and
  measurement.
