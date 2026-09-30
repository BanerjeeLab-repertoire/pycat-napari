# Phases 2–3 report — regional boundaries, object identity, missed objects (1.6.461)

Spec: `PyCAT_2D_Cellular_Region_Selection_Spec.md`. Target set by the author: pixel IoU and the
confusion metrics **at or above CellProfiler on the large-puncta fields**, without moving Small or
Irregular in the wrong direction, **with no dependence on the human annotations** (this is a
control figure compared against them) and **without fusing distinct objects** to flatter the
comparison.

Code: `pycat/toolbox/segmentation/boundary_refit.py` (`refit_regional_boundaries`,
`keep_objects_apart`), `subcellular.py` (`boundary_mode`), benchmark
`benchmarks/incell_pipeline_eval.py`.

## Ground rules for every number here

- **Scored on the annotated cells only.** The annotators traced 30 of the 86 nuclei Cellpose finds
  in the large fields; these are transient transfections and a nucleus without meaningful GFP was not
  annotated. Choosing cells is a data-split question, not the segmentation's; every method,
  CellProfiler included, is scored on whole objects touching a traced nucleus.
- **No annotation enters PyCAT's run.** The measure-line object size comes from
  `estimate_object_size_px` on the GFP image (the batch auto-estimator), not from the masks; the
  cell diameter from DAPI.
- **Settings chosen label-blind.** Candidates were scored pooled over all 27 fields as one
  population; tuned values were chosen by field-held-out cross-validation (20 × 3-fold). The class is
  used only to report per-class regressions.

## The method

Every detection is offered two boundaries and keeps the regional one only if it behaves like a
condensate:

1. **Regional:** flatten the cell with a white top-hat at 2 × `ball_radius`; foreground = per-cell
   Otsu × 0.8 of the flattened image; grow each detection into it by watershed.
2. **Half-max:** the existing `refit_object_boundaries` contour (the 1.6.460 behaviour).
3. Keep (1) if it is **compact** (solidity ≥ 0.9) **and** at most **2.5×** (2); otherwise keep (2).
4. **Keep objects apart:** pixels where two different objects touch are dropped, so the pipeline's
   boolean OR + relabel cannot fuse them.

Why: half-max under-draws large condensates (seeds cover ~45% of the object; the level ring sits in
the skirt), while a regional threshold — what CellProfiler's pipeline effectively does — is right for
condensates and over-draws sparse puncta 9–15×. The per-object guards decide which regime an object
is in from the image alone; a field mixing condensates and puncta is handled object by object.

## Results (pooled consensus IoU, annotated cells, image-estimated sizes)

| | Pooled | Large | Small | Irregular |
|---|---|---|---|---|
| CellProfiler (hand-tuned per class) | 0.554 | 0.71 | 0.13 | 0.17 |
| PyCAT 1.6.460 (half-max) | 0.468 | 0.55 | 0.40 | 0.20 |
| **PyCAT 1.6.461 (regional)** | **0.580** (CV held-out) | **0.71** | **0.44** | **0.19** |

Paired bootstrap over fields (95% CI of ΔIoU): large vs CellProfiler +0.005 [−0.030, +0.067] —
level; large vs 1.6.460 +0.162 [+0.137, +0.193]; Small vs 1.6.460 +0.035 [−0.030, +0.066];
Irregular vs 1.6.460 −0.010 [−0.035, +0.000] — not significant, accepted by the author. Large vs
CellProfiler per annotator: Meet +0.049 [+0.015, +0.109], Gable +0.003 [−0.029, +0.059],
Shamli −0.053 [−0.081, −0.009].

**Robust to the measure line:** pooled 0.577 / 0.572 / 0.561 with the object size at 0.7× / 1× /
1.5× the estimate (settings 3.0 / 0.9 in that run), always above CellProfiler (0.554) and 1.6.460
(≤ 0.469).

**Confusion matrix (recall row):** large 0.78 vs CellProfiler 0.87. CellProfiler's extra recall comes
with more false-positive area (19% of the union vs PyCAT's 9%) and object fusion (below), neither of
which the row-normalised matrix shows.

## Object identity — nobody gets credit for fusing condensates

Share of matched consensus objects that end up inside the same predicted object as another one
(final binary mask, relabelled as the pipeline does):

| | Large | Small | Irregular |
|---|---|---|---|
| CellProfiler | **28%** | 4% | 20% |
| PyCAT 1.6.460 | 1% | 8% | 8% |
| regional, objects allowed to touch | 11% | 12% | 12% |
| **1.6.461 (objects kept apart)** | **0%** | **4%** | **7%** |

Keeping objects apart costs nothing measurable (IoU identical to two decimals in every class).
Object counts are never changed by the boundary step: one output object per detection.

## Phase 3 (missed objects): not a gate problem

Of the 141 consensus large-puncta objects 1.6.460 misses, **132 were never proposed** by the
Felzenszwalb segmentation, 8 were removed by the refinement gates, none were in a cell the punctate
gate skipped. They are small (median 20 px) and dim (median peak 456 vs ~870). Loosening gates cannot
recover them, so no gate default changed. Two proposal passes were tested — local maxima ≥ z σ over a
ring, and a CellProfiler-style robust-background threshold on the flattened image — and both added
more untraced detections than they recovered; not shipped. The boundary step cannot add objects,
so never-proposed objects remain the main shortfall that is left (CellProfiler misses a similar share).

## What was tried and dropped, and why

- **A(r) size rule (Phase 1):** circular; worse than half-max at runtime. Withdrawn.
- **Learned per-object level:** reached 0.71 on large, but only when trained on large-puncta
  annotations (0.61 with the class held out) — fitted to the figure's own ground truth. Not shipped.
- **Noise-threshold (k σ) and half-max with ring fixes:** ≤ 0.57 on large.
- **Local (per-object) Otsu:** 0.65 on large, neutral elsewhere — kept in mind as the fallback.
- **Per-cell Otsu without guards:** right for condensates, 9–15× over-drawn for sparse puncta.
- **Fusion-capped stepped relaxation (spec Phase 2):** superseded — the regional watershed never
  assigns one pixel to two seeds, and `keep_objects_apart` removes the only fusion path left.

## Caveats

- One confocal, 0.0977 µm/px, one construct family; the constants are scale-relative
  (`ball_radius`) but were chosen on this data.
- CellProfiler's pipeline was hand-tuned per class by a person looking at these images; PyCAT runs
  one set of defaults for all three.
- Pixel IoU against human traces rewards generous masks on big objects; the fusion table is the
  check that PyCAT's gain is not bought that way.
