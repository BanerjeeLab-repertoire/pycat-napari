## [1.6.97] - 2026-07-17
### Added — **Comparative phenotyping increment 3: comparative figures with honest, replicate-aware stats.**
Cross-condition comparison over the consolidated table (increment 2). Two modules: the statistics
(`utils/comparative_stats.py`) and the figures (`utils/comparative_figures.py`).

**The statistics are the part that has to be right.** The easiest false result in imaging biology is
pseudoreplication — treat 5 000 puncta from 3 cells as 5 000 independent observations and any trivial
difference becomes p < 10⁻⁹. PyCAT's own doctrine (`pixel_wise_corr_analysis_tools`) is that the
inferential unit is the biological replicate, not the object. So `compare_conditions` **aggregates each
condition×replicate to one value first**, making the replicate the unit, then tests (Mann-Whitney /
Kruskal-Wallis, or t / ANOVA parametric). It reports the test used and n at **both** levels, and
**refuses to infer** when a condition has < 2 replicates — descriptive only, never a pixel-level
p-value dressed as a biological one. Measured on identical null data: the pseudoreplicated test gives
p = 2.6×10⁻⁸⁷, the replicate-aware one gives p = 0.70. It recovers real replicate-level effects and
does not cry significance on a pseudoreplicated null — the roadmap's three deliverables, each a test.

**The figures make the replicate structure visible.** `condition_comparison_figure` draws every
condition twice — the object cloud (light, many) and the **replicate means on top** (dark, few) —
because the honest test runs on those few points and the picture should show it. The annotation
carries the test, the p-value, and n at both levels, straight from the stats; a pseudoreplicated null
is labelled `n.s.`, a too-few-replicates case `NO TEST`, never a fabricated star.
`dose_response_figure` gives mean ± SEM **over replicates**, same rule.
### Notes
- Static matplotlib (Agg, renders headlessly). **Interactive brushing and a PyQtGraph render are
  deferred** — they need a viewer, and the roadmap scopes them as "later"; this ships the part
  verifiable without one. The static figures are usable today and are the substrate a brushing layer
  would sit over.
- All `core` — the statistics are pure and their correctness is exactly what must not be trusted to a
  figure someone glances at. Verified: the object-cloud + replicate-means design renders correctly on
  a 3-condition graded effect.
- Increment 4 (publication figure refinement — themes, labels, export polish) is the remaining
  comparative-phenotyping increment.

