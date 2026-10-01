# Addendum (2026-10-01) — missed objects and runtime (1.6.464–1.6.465)

## Why traced objects were missed — three different mechanisms

Consensus objects in annotated cells, 1.6.463:

| class | missed | where lost | signature |
|---|---|---|---|
| Irregular | 61% | **7/15 annotated cells skipped whole by the punctate gate** (278/603 objects) | missed as bright as found (SNR 3.5 vs 4.3), 132 radii from any detection |
| Small | 20% | **67/84 proposed, then removed by refinement** — global SNR 50, local SNR 21, gradient 11, local intensity 9 | lower SNR than found (3.5 vs 5.7) |
| Large | 14% | never proposed | dim relative to the cell's brightest objects (0.28 vs 0.75) |

## Fixed: the punctate gate (1.6.464)

The gate's floor is `base + 5 × sigma_cell`; in transfected cells dense with dim puncta `sigma_cell` is texture
(~5× pixel noise). Contact sheet: every annotated cell it rejected is a bright transfected nucleus full of puncta;
the cells a *lower* n_sigma would admit are untransfected nuclei. New second route — baseline ≥ 10 background σ
above the image background **and** peak ≥ 10 × pixel noise — passes 59/59 annotated cells (52 before), admits 6
unannotated cells (all transfected, none dark), stable for baseline 5–20 / peak 8–10. Irregular consensus IoU
**0.19 → 0.38**, recall 0.26 → 0.61, missed 365 → 180 of 603.

## Not changed: the global-SNR refinement check (finding, per spec 3.2)

Sweep 1.0 / 0.5 / 0.0 (traced objects found; untraced detections in annotated cells): Small 362/389/396 of 446,
untraced 79/161/181; Irregular 423/506/513 of 603, untraced 68/176/217; large unchanged. The contact sheet shows the
recovered traced objects are faint real puncta, but the added untraced detections are mostly featureless nucleoplasm
texture and outnumber them. **The check cannot be relaxed without mostly spurious detections; default kept at 1.0.**

## Not solved: large-puncta objects shadowed by bright neighbours

Never proposed by the Felzenszwalb stage; small (median 17 px) and dim relative to the cell's brightest condensates.
Two proposal passes (local maxima ≥ z σ; robust-background threshold on the flattened image) added more untraced
detections than they recovered. Their share of the large-puncta union is small (IoU 0.71, level with CellProfiler,
which misses a similar share).

## Runtime (1.6.465)

98% of segmentation was `skimage.graph.merge_hierarchical` (networkx, 35.7 M weight updates on one field).
`fz.merge_mean_color_fast` reproduces it bit for bit on plain containers: Irregular 8 segmentation 306 → 96 s,
Small 6 132 → 40 s, large 7 45 → 23 s. Remaining cost is the algorithm itself (6 M heap operations as one background
region absorbs its neighbours); a sorted-neighbour structure for that node could cut it further but must keep the
exact tie-breaking.

## Runtime, continued (1.6.466)

`fz.merge_mean_color_fast` rewritten with per-node lazy minima — same merges, same order, ties included (identical
on the captured real calls and 400 synthetic cases): 109.5 s → 0.84 s on the largest call. Segmentation per field
(1.6.464 → 1.6.466): Irregular 8 306 → 10 s, Small 6 132 → 6 s, large 7 45 → 20 s; whole pipeline 20–39 s, now
dominated by background removal, CLAHE, contrast stretching and Cellpose.

## Large-puncta objects shadowed by bright neighbours — mechanism found

The Felzenszwalb region merge folds regions whose means differ by < `merge_tol` × the crop's dynamic range (0.05).
One bright condensate sets that range, so a dim punctum beside it is merged into the background. Sweep 0.05 / 0.02 /
0.01 (traced found; untraced in annotated cells): large 655/677/**687** of 757, untraced 21/25/29 (IoU 0.708 → 0.712);
Irregular 423/449/450, untraced 68/88/101 (IoU flat); Small 362/361/358, untraced 79/102/101 (IoU 0.438 → 0.425).
A lower tolerance is a clear win where bright condensates shadow dim puncta and a loss for sparse puncta, so the
default stays 0.05 pending a per-cell rule.
