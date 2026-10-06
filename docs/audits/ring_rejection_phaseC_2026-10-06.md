# Contrast floor — Phase C calibration and a per-cell check

**Spec:** `PyCAT_Ring_Rejection_and_Contrast_Floor_Spec.md` · **Version:** 1.6.472 · **Date:** 2026-10-06

## Measure

Each final object's **local CNR** uses the refinement gate's definition (`puncta_refinement._snr_conditions`):

- **Numerator:** the mean of the RAW image over the object (dilated by its gap), minus the robust median of a
  background ring scaled to the object, with other objects excluded from the ring.
- **Denominator:** the ring's robust (MAD) spread.

It is measured once per object after segmentation (`segmentation/contrast_floor.py`), so moving the floor is a
lookup.

## Calibration data

Both sets come from the 1.6.471 pipeline run end to end on all 27 annotated fields:

- **Rejected set.** Meet's two whole "diffuse" cells (`image (1)`: small-puncta field 5, cell 11, and field 7,
  cell 8; 37 detections). His own masks trace 13 of those 37, so the calibration uses the **24 he did not
  trace**.
- **Kept set.** Detections that both Meet's and Gable's masks cover (≥ 20% of the detection) in annotated
  cells: **1,371**.

## Result: no single value separates them

| Floor (local CNR) | Meet's untraced objects removed | Agreed condensates lost | Small-class agreed lost |
|---|---|---|---|
| 1.00 | 2/24 | 1.4% | 0.6% |
| 1.25 | 6/24 | 3.8% | 2.5% |
| 1.50 | 11/24 | 7.1% | 6.9% |
| **1.75** | **19/24** | **10.6%** | **9.5%** |
| 2.00 | 20/24 | 15.2% | 12.6% |

- **The populations overlap.** Meet's untraced objects have CNR 0.93–2.80. The agreed condensates have a median
  of 3.44, but their 5th percentile is 1.35 and their 10th percentile 1.71.
- **The objects Meet did trace** in those cells reach CNR 0.79–8.0.
- **The trade-off.** The best balance (fraction of rejected objects removed minus fraction of agreed objects
  lost) is at 1.95 (83% / 13.9%).
- **The default.** The knee is at 1.75, which Gable chose: **default 1.75, on**.

## Per-cell check (is a cell-level test worth pursuing?)

Each annotated cell with three or more detections (59 cells) was summarised by its detections' local CNR
(median, 90th percentile, maximum). Cells whose detections the annotators largely traced (≥ 70%, 52 cells)
were compared against cells they largely did not.

- **There are no other "diffuse" cells.** Apart from Meet's two, no annotated cell has ≤ 30% of its
  detections traced by anyone.
- **Meet's two cells:**

  | Cell | Median CNR | 90th-percentile CNR | Detections Meet traced himself |
  |---|---|---|---|
  | Field 5, cell 11 | 1.58 | 2.79 | 45% |
  | Field 7, cell 8 | 2.40 | 4.55 | 65% |

  The agreed cells' median CNR has a 10th percentile of 1.97 and a minimum of 1.43, so field 5 cell 11 falls
  below 90% of them. Field 7 cell 8 sits inside their range.
- **The best single cut** reaches a balanced accuracy of about 0.81 (on median or 90th-percentile CNR), from
  only two negative cells.

**Verdict:** a per-cell test is not supported by these data; one of the two cells Meet called diffuse looks
like an ordinary cell by every contrast statistic. It would need a set of cells labelled diffuse or not before
it is worth building. Within the two cells, the per-object floor at 1.75 removes 57% of the detections.

## Behaviour

- **Control:** "Apply minimum contrast floor" (default on), with a slider in local-CNR units (0.05 steps; the
  minimum means no floor) and a live kept / below-floor count. The slider is debounced at 30 ms.
- **Live update:** each step is a label → keep lookup: about 1.9 ms on a 512² field with 233 objects, with no
  re-segmentation.
- **Display:** objects above the floor stay in "Total Refined Puncta Mask"; objects below it move to a muted
  red "Below Contrast Floor" layer.
- **Table:** every object carries `local_cnr`. Below-floor objects are listed with `below_contrast_floor` set
  to True, and are never in the per-cell summaries.
- **Batch:** the floor value and its on/off state are recorded with the condensate segmentation step, and kept
  in step with the slider. Batch replay applies the floor when it is recorded; a recording made before 1.6.472
  replays unchanged.
