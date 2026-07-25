# Claude Code spec — Decompose the colocalization science files

> **◐ STATUS — step 1 (metrics) DONE, shipped 1.6.341 (consistency verified: the module was still 2,029 lines,
> 37 functions before this). Remaining: thresholding, nulls, temporal, analysis, object_based.**
>
> **Step 1 — metrics.py — DONE, 1.6.341.** New `toolbox/coloc/` package; `coloc/metrics.py` holds the raw
> pairwise measures moved VERBATIM — `pearsons_correlation`, `manders_overlap`, `manders_k1/k2_calculation`,
> and the rank correlations (`spearman_r/kendall_tau/weighted_tau_calculation`). Each is self-contained (none
> calls another coloc function). Pinned identical by the coloc test net (test_coloc_metrics / test_pixel_coloc
> / test_group_f_coloc, all pass through the re-export). `pixel_wise_corr_analysis_tools.py` re-exports the
> seven names and drops 2,029 → 1,738 lines. Recorded in `_DELIBERATE`. The remaining domains (thresholding =
> Costes + sensitivity; nulls = spatial_null + randomisation; temporal = coloc_time_trace; analysis =
> pixel_wise_correlation_analysis + run_pwcca; object_based) follow as separate steps — note the coupled Li-ICA
> set (`li_intensity_correlation` + `li_ica_histogram/plot`) and the plots were deliberately NOT moved here, to
> keep step 1 an unambiguous split.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. With the six big
science files now shims, `pixel_wise_corr_analysis_tools.py` (**2,029 lines**) is the largest remaining
science module. It is well covered and splits cleanly by domain.

## Verified structure
```
pixel_wise_corr_analysis_tools.py   2,029 lines, 40 functions, 2 over 120
   267  pixel_wise_correlation_analysis
   152  run_pwcca
   104  perform_costes_test
   102  manders_threshold_sensitivity
    84  spatial_null_test
    84  coloc_time_trace
```
**17 test files** reference the colocalization surface — a strong characterization net, including the
spatial-null work (the structure-preserving randomisation that stops blurred-but-independent channels
reading as colocalized).

Related and worth doing in the same arc: `obj_based_coloc_analysis_tools.py` (**1,216 lines**) and
`two_channel_coloc_tools.py` (whose `_add_run_two_channel_coloc` is a 354-line builder — covered by the
UI-builder spec, not this one).

## Target — a `coloc/` package by domain
```
toolbox/coloc/
    metrics.py       # Pearson, Manders M1/M2, overlap coefficients — the raw measures
    thresholding.py  # Costes threshold determination, manders_threshold_sensitivity
    nulls.py         # spatial_null_test and the randomisation machinery
    temporal.py      # coloc_time_trace
    analysis.py      # pixel_wise_correlation_analysis, run_pwcca (orchestration)
    object_based.py  # obj_based_coloc_analysis_tools contents
```
`pixel_wise_corr_analysis_tools.py` and `obj_based_coloc_analysis_tools.py` become thin re-export shims.

## Method — coverage-gated, science untouched
1. **Pin before moving.** For each function, confirm an existing test asserts its *number* (not just its
   shape) on a known input; add a characterization test at `rtol=1e-9` where coverage is only structural.
   **No test, no move.**
2. **The null machinery is the most sensitive piece.** `spatial_null_test` preserves each image's own
   spatial structure — that property is what makes the colocalization claim defensible. Its test must
   pass unmodified, and the randomisation must not be "tidied" during the move (a reseeded or reordered
   shuffle changes results).
3. **Costes thresholding is iterative** — its convergence path is order-dependent. Move it whole; do not
   restructure the loop.
4. **Move, don't rewrite.** Cut, paste, fix imports. No reassociated arithmetic — the tolerances will
   catch it, which is the point.
5. One domain per commit; `pytest -m core` + the coloc tests green between each.
6. Re-export shims for every previously-public name; grep callers first (the coloc UIs, batch steps, and
   the comparative path all import these).

## Why now
- Largest remaining science file, and the only big one not yet decomposed.
- 17 test files — the split is *safe*, which is rare for a 2,000-line science module.
- The pattern is proven six times over on this codebase.
- A focused `metrics.py` / `nulls.py` is far easier to cite in a Methods section than a 2,000-line file —
  and colocalization is manuscript-facing.

## Tests
- Every moved function is byte-identical on its characterization input.
- The spatial-null tests pass **unmodified** (including the independent-but-blurred negative control).
- Costes thresholding produces the same threshold on the same input.
- Manders sensitivity output unchanged.
- All 17 referencing test files pass unmodified.
- Both shims resolve every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` and the per-file line ratchets.

## Steps
1. Create `toolbox/coloc/`; move `metrics.py`; run coloc tests + core.
2. Move `thresholding.py` (Costes + sensitivity); run.
3. Move `nulls.py` (spatial null + randomisation); run — **the null tests are the gate**.
4. Move `temporal.py`; run.
5. Move `analysis.py` (orchestration: `pixel_wise_correlation_analysis`, `run_pwcca`); run.
6. Move `object_based.py`; run.
7. Both source files → re-export shims; lower ratchets.
8. Full `pytest -m core` green after each step.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG with before/after.

## Definition of done
- `pixel_wise_corr_analysis_tools.py` and `obj_based_coloc_analysis_tools.py` are thin shims; the domains
  live in `toolbox/coloc/`.
- Every moved function is proven behaviour-preserving by a characterization test written first.
- The spatial-null property and Costes convergence are unchanged.
- Ratchets lowered; all pre-existing tests pass unmodified; no numerical output changes.

## Cautions
- **The spatial null is the defensibility of the whole module.** If its tests move at all after a
  "structural" split, behaviour changed — revert, don't adjust the test.
- **Costes is iterative** — moving the loop is fine, restructuring it is not.
- **No test, no move.** Verify per function that existing coverage pins the *number*, not just the shape.
- Re-export shims mandatory; coloc is imported by UIs, batch, and the comparative path.
- One domain per commit.
