# Scale reconciliation — Phase D

**Spec:** `PyCAT_Ring_Rejection_and_Contrast_Floor_Spec.md` · **Version:** 1.6.473 · **Date:** 2026-10-06

## As specified

`segmentation/reconcile.py` folds each coarse detection pass into the primary pass object by object, instead
of a boolean OR. For each coarse detection L, the action depends on how many primary detections it overlaps:

| Primary detections L overlaps | Action |
|---|---|
| 0 | Keep L |
| 1 | L replaces that detection |
| 2 or more | Discard L, keep the primary detections |

It is applied to both the raw and the refined masks, before the boundary refit, one scale at a time.

**One addition, to meet both halves of spec test 8.** The spec's "double-seeded object becomes one object" and
"two puncta the coarse scale fuses stay two" are the same geometry (one coarse blob over two primary pieces).
The 2-or-more case is therefore refined by the raw image. If there is no dip between the pieces inside L (the
saddle falls less than 20% of the dimmer piece's height above background), they are one condensate and L
replaces them. Otherwise L is discarded.

**Effect on the 27 annotated fields: none.** IoU, missed objects and fusion are identical to 1.6.472. At the
measured object sizes the coarse pass rarely proposes anything, as the earlier multiscale ablation already
showed on large field 9 and irregular field 1.

## Meet's split ("mask split into two", large field 9) is not a scale problem

The split survives with the multiscale pass switched off. The primary detection finds two touching pieces of
one elongated condensate, and the regional watershed keeps them apart. The raw dip between the pieces is 2% of
the dimmer piece's height. The level boundary draws one object.

The fix is `refit_regional_boundaries(join_split_objects=True)`, which runs with scale reconciliation. Two
touching objects are joined only when both of these hold:

- **No real dip:** less than 20%, the same test as above.
- **A compact union:** solidity of at least 0.9, the bar a regional boundary itself must meet.

A dim condensate pressed against a bright one also shows little dip, but two touching discs are not compact,
so they stay two (tested at dim fractions 0.3 and 0.6).

**Effect on the 27 fields (consensus, annotated cells):**

- **Joins:** 35 detections joined (16 large, 7 small, 12 irregular).
- **IoU:** 0.71 / 0.44 / 0.39 (large / small / irregular; irregular was 0.38).
- **Missed objects:** unchanged at 80/757, 85/446 and 177/603.
- **Fusion** (annotated objects inside one predicted object), against the spec's limit of 11%:

  | Class | Before | After |
  |---|---|---|
  | Large | 0.3% | 0.3% |
  | Small | 3.6% | 4.7% (+4 annotated objects) |
  | Irregular | 6.1% | 6.6% (+2 annotated objects) |

  About 3 of the 35 joins merge objects the annotators drew separately.
- **Runtime:** 615 s against 622 s for the 27 fields, within noise.

## Scope

`scale_reconciliation` defaults to False in `segment_subcellular_objects`, so in-vitro, time-series,
colocalisation and z-stack segmentation stay byte-identical to 1.6.460 (verified on the synthetic scope
probe). The 2D cellular GUI handler and batch replay turn it on.
