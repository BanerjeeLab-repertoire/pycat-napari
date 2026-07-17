## [1.6.89] - 2026-07-17
### Added — **Filter-sensitivity increment 2: two new validated cases, and one deliberate rejection.**
The increment was *"needs prioritisation, not machinery"*. Prioritising meant **checking** the three
candidates the spec named rather than adding all three — and one of them does not belong.

**`segmentation.local_snr_threshold` / `global_snr_threshold` — ADDED, as OFFSET sensitivity.** Not
the `r2_min` selection-bias shape the spec guessed. The old gate was `object_mean / bg_std`: the
pedestal sits in the numerator and *not* the denominator, so the score scaled with the camera — the
same punctum reported "SNR" 115 at a 500-count pedestal and 416 at 2000. Against a threshold of 1.0
it could never reject anything, so a zero-contrast noise blob was kept at any real pedestal and
counted. Same family as the registered `ts_cellpose` case, found independently, and it survived far
longer because the 1.5.416 fix reached only the slow filter while the **default** path kept the broken
form until 1.6.86.

**`segmentation.local_ring_geometry` — ADDED, and it is the FIRST validated `scale_invariance` case.**
The harness shipped that check type with machinery and no case: *"the check type exists so the
increment that finds one does not also have to invent the harness."* 1.6.87 found one. A fixed 1-4px
rim is a probe in pixels: the same physical condensate at a finer pixel size spans more pixels, so the
rim sits proportionally closer to its boundary and samples the object's own halo instead of
background. Same specimen, different objective, different verdict — and nothing in the output would
say the population had been excluded. The negative control pins `_local_ring_radii` to its old `(1,1,2)`
rather than keeping a second copy of the filter that could drift from the real one.

**`condensate.bleach_r2_min` — NOT ADDED, and it is not an oversight.** The spec expected the `r2_min`
shape, but `bleach_r2` gates nothing: `has_bleaching` only picks the reported `dominant_cause` label,
so there is no population statistic to bias. Getting it wrong mislabels a diagnosis; it does not
invert a number. Nor is it offset-sensitive — `fit_photobleaching` fits `I(t) = I0·exp(−t/τ) + I_inf`,
and **I_inf absorbs a pedestal**: measured, `r_squared = 0.9989` at pedestals 0, 100, 500 and 2000. A
sensitivity test on it would assert an invariant that cannot break, which is coverage, not a warning.
Both reasons are pinned as tests rather than comments, because they are claims about code and code
changes: if `has_bleaching` ever gates a population, the test fails and the case should be added.
(A different reason from `defocus_r2_max`, which is excluded for being dead — this one runs, it just
is not a filter.)
### Notes
- ~37-113 other defaults remain. The audit's view stands: **they are not equal**, so the next
  increment is another prioritisation call, not a sweep.
- Each new case keeps the established shape: a **mechanism** test proving the fixture exhibits the
  effect (otherwise the negative control proves nothing), a **positive** control on current
  production, and a **negative** control reconstructing the old behaviour *locally* — never back into
  production.

