# Ring rejection — Phase A: optical ring or detection artifact?

**Spec:** `PyCAT_Ring_Rejection_and_Contrast_Floor_Spec.md` · **Baseline:** 1.6.469 · **Date:** 2026-10-06

## Verdict

**No optical rings in these images.** Around the condensates in all 18 large-puncta and irregular-puncta
fields, the raw image shows no ring of light beyond the object's edge at a consistent radius beyond what
empty nucleoplasm produces by chance. The ring and arc shapes Meet circled are segmentation outputs, not
real photons. Per the spec's constraint, **Phase B (the optical-ring rejector) is not built.** The arcs come
from segmentation, so Phase D (scale reconciliation) plus how the two mask layers are shown in the GUI is
where the fix lies.

## Reference crops

Meet's screenshots (`Downloads/image*.png`, 2 and 6 October) were located in the source fields by
inverting the viridis display and template-matching against all 27 annotated fields (normalised
cross-correlation 0.80–0.99 on the raw panels):

| Screenshot | Field | Meet's note |
|---|---|---|
| `image (3)` top | large puncta, In Cell 9 | "ring formation in large condensate case" |
| `image (3)` bottom | large puncta, In Cell 2 | same |
| `image (2)` | irregular puncta, In Cell 1 | C-shaped arc around one object, bar-shaped slivers around another |
| `image` | large puncta, In Cell 9 | one condensate split in two |
| `image (1)`, `image (4)` | small puncta, In Cells 2, 5, 7 | diffuse cells, objects that should not be counted (Phase C) |

## What was measured

1. **Meet's arrowed points, raw radial profiles from the nearest condensate's centre.** There is no
   secondary maximum beyond the edge at any of the four points.
   - The irregular C-arc (In Cell 1, 1024-px (961, 261)) is reproduced exactly by 1.6.469. It is a
     refined object wrapping 56% of a brighter neighbour at a 3.6 px standoff. Its raw profile falls
     monotonically from the bright core outward, so the arc is the **dim body of one irregular
     condensate split into a bright core and a C-shaped shell**. That is a segmentation split, not a halo.
   - The two large-puncta rings are **not reproduced** by 1.6.469 at the automatic object size, nor at
     1.5×, 2× or 2.5× it (0–1 arc per field, never at Meet's points). Meet's run differed: its objects are
     visibly larger. His screenshots show a thin dark rim on several objects, which is what the GUI draws
     when the unrefined "Total Puncta Mask" layer sits beneath a smaller object on the "Total Refined Puncta
     Mask" layer. Either way, the raw image at those points has no ring.
2. **Every ring- or arc-shaped object in all 18 fields.** The criteria were thin (half-width ≤ 3.5 px),
   covering at least a quarter of a refined parent's circumference, at a near-constant standoff
   (CV ≤ 0.35). There are 19 such objects, 9 refined and 10 unrefined.
   - The unrefined ones are chains of tiny diamond-shaped detections that refinement already discards.
   - Where a candidate's sectors held a raw maximum, it was a real neighbouring object, 12–237% as bright
     as the parent. That is far brighter than any halo (an Airy first lobe is about 1.7%).
3. **Segmentation-independent test on every condensate with r ≥ 5 px (906 objects).** The test looked for
   a dim secondary maximum beyond the edge, at a consistent radius (SD ≤ 2 px), in at least half of the
   24 sectors free of other objects.
   - It fires on **5.2%** of condensates, against **2.6%** for the same test at random object-free
     nucleoplasm positions with the same radii.
   - In every flagged case the angle-averaged profile **decreases monotonically**. The "ring" is
     nucleoplasm texture in a few sectors, not an annulus.
4. **`fz._bridge_fragmented_rims`.** It was called 15, 15 and 4 times (once per cell) on the three
   reference fields and **added no pixels in any call**. It is not converting halo arcs into filled
   objects here.

The Phase B parameters (ring radius, peak fraction, width) cannot be measured, because no optical ring
exists to measure.

## Implications

- **Phase B:** not built, per the spec's stop condition.
- **Phase D:** addresses the irregular C-arc (core and shell of one object) and the split in `image`. Both
  are one object divided by segmentation.
- **Large-field "rings":** Meet's settings are needed to reproduce them (version, and the object-size
  line he drew). The likeliest explanation is the two stacked label layers in the GUI: an
  unrefined-mask object showing as a rim around a smaller refined one. If so, it is a display question
  (which layer is shown, and how) and not a segmentation defect.
- **Phase C** (contrast floor) is independent of this result.

Scripts (scratch, not in the repo): `ringA_run.py`, `crop_match*.py`, `phaseA*.py`, `radial.py`,
`bridge_probe.py`.
