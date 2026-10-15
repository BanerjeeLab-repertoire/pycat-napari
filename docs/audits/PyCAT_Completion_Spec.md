# PyCAT — Completion Spec

**Baseline:** v1.7.003 (sandbox copy of `pycat-napari_3.zip`). 368 source files, ~126k lines, 360 test files.
**Scope:** the eight things asked for — science, pipelines, architecture, deprecations, batch-on-all, linking-on-all, data QC MVP — organised under the gate you set: *nothing proceeds without sample data to test on.*

**Evidence discipline.** Every finding below is marked **[executed]** (a function was run on controlled input and the number reported is what it returned), **[verified]** (grep/AST/import confirmed in this session), or **[read]** (static reading only, not executed). Claims that could not be settled are labelled as such rather than inferred. Scripts producing the **[executed]** numbers are in the session scratchpad and are named in each item; **they should be promoted to tests, not left in scratch** — that is a work item, not a footnote.

---

## 0. The headline, before anything else

Three things are true at once and they shape the whole plan:

1. **The Data QC dashboard has never run.** `src/pycat/toolbox/data_qc_ui.py:157` imports `QC_MAX_FRAMES` from `pycat.toolbox.data_qc_tools`. **That name does not exist anywhere in `src/`** — it was lost when `data_qc_tools.py` became a re-export shim in the 1.6.350 decomposition. **[verified]**

   ```
   $ python3 -c "from pycat.toolbox.data_qc_tools import run_full_qc, plot_qc_report, QC_MAX_FRAMES"
   ImportError: cannot import name 'QC_MAX_FRAMES' from 'pycat.toolbox.data_qc_tools'
   ```

   The line sits in `_on_run()` — the `run_btn.clicked` slot — *above* the `try:` at `:178`. Every click, every image, dies there. Three menu entries route to it (`menu_manager.py:866`, `analysis_methods_ui.py:1466`, `feature_cards.py:86`). **38 QC tests pass** because every QC-UI test is a `pathlib.read_text()` + regex/AST source grep (`tests/test_qc_ui_contract.py:31`); nothing imports or executes the handler. `tests/test_no_undefined_names.py` can't see it either — the name *is* bound by the import statement.

2. **The linking machinery is built, tested, correct — and connected to 2 of 14 pipelines.** `SelectionService`, the `_pycat_entity_id` key, sort-safe row mapping, echo suppression, two interleaved object tiers, offline batch crop resolution: all of it exists and is covered by real round-trip tests. Ten pipelines route their results through `show_dataframes_dialog` (`src/pycat/ui/ui_utils.py:284`), a 2019-era dialog that calls `create_table_view(df)` and stops. **[verified]** The premise "linking is missing" is right in effect and wrong in cause. This is a wiring job, not a design job.

3. **A recurring structural pattern, now seen four times: the correct code exists and the production call path does not reach it.** The truncated size-distribution MLE (`xmin`) is implemented, tested, and unreachable because the public wrapper has no `xmin` parameter. `partition_coefficient_local` is correct and the time-series path calls `_field` instead. `OperationRunner.execute` is the designed identity chokepoint and has three callers, none of them a pipeline. The navigator's op catalog advertises `data_qc_tools.qc_snr` and nothing executes it. **This pattern deserves a standing CI check of its own** (§C4).

None of these is a science problem. All three are reachability problems, and all three are the kind a reviewer finds in ten minutes.

---

# PHASE 0 — The sample-data gate (blocking)

> *"None of that should proceed without sample data to test on."*

Nothing in Phases A–G may be merged before Phase 0 is green. The reason is not process hygiene: §A below contains measured parameter biases of 50–350%, and **you cannot tell a bias fix from a bias introduction without data that has a known answer.** Phase 0 is what makes every later phase falsifiable.

Phase 0 has three legs. They are different kinds of truth and all three are needed.

## 0.1 — Leg 1: the real-data corpus (external, versioned, hash-pinned)

**Source:** `C:\Users\gmwadsworth\Box\[0] PyCAT manuscript\Test` and `…\[3] Experimental data\Biological data comparison`.

**Requirement:** exactly **one real acquisition per pipeline — all 14** (`PIPELINE_DEFS`, `src/pycat/ui/workflow_checklist.py:190-205`): condensate, timeseries, coloc, cellular_bf, invitro_fluor, timeseries_invitro_fluor, invitro_bf, fibril, zstack, vpt, frap, fusion, temperature, fd_curve.

Selection rule per file: the **smallest** acquisition that still exercises the pipeline's full step list. Crop, decimate frames, drop channels — a 4-frame 512² time series tests the same code path as a 600-frame 2048² one. Target ≤ 20 MB per file, ≤ 300 MB total.

**Distribution.** Do not commit these to git. Publish one versioned archive as a GitHub release asset, fetched by a `pooch`-style registry with SHA256 per file:

```
tests/data/registry.txt      # filename  sha256  size  pipeline  provenance
tests/data/fetch.py          # downloads on demand, caches, verifies, skips offline
```

Every entry carries provenance: instrument, objective, NA, emission wavelength, pixel size in µm, bit depth, and **who acquired it**. That block is not bookkeeping — §A4 and §G2 both need real NA/λ/pixel-size to test the honesty gate, and §0.3 needs the real pixel size to be *absent* from some files on purpose.

**Mandatory negative fixtures.** The corpus must include, because the audits found live defects behind each:
- one file with **no pixel-size metadata at all** (exercises `pixel_size_um() → NaN`, §A4)
- one file **already background-subtracted** (float dtype, hard zero floor — exercises §G5, §G9)
- one **z-stack** (exercises the `is_zstack` misclassification, §G6)
- one **(C, Y, X) multichannel** file (exercises the same, as channels)
- one **saturated** acquisition
- one **12-bit-in-16-bit-container** acquisition (ceiling ≠ dtype max)

## 0.2 — Leg 2: the synthetic ground-truth corpus (in-repo, generated)

Real data has no known answer. Every measurement bias in §A was found with synthetic data and can only be *fixed* against synthetic data.

Build `tests/synthetic/` as deterministic generators (fixed seed, parameters in the call, no files committed):

| Generator | Known truth | Gates |
|---|---|---|
| `spida_field(N, eps, read_sigma, pedestal)` | N, ε | A1, A1′, A1″ |
| `partition_field(K, dilute_cv, pedestal, dtype)` | K | A2a, A2b, A2c |
| `textured_object(shape, texture, area)` | identical texture, varying eccentricity | A3′, A3″ |
| `lognormal_sizes(mu, sigma, d_min)` | μ, σ, truncation point | A4 |
| `bleaching_stack(tau, sparsity, pedestal)` | τ, fraction remaining | G2 |
| `noise_stack(sigma, n_frames, axis)` | per-frame σ | G3, G7 |
| `drifting_stack(dx_per_frame)` vs `zstack(no drift)` | 0 vs known drift | G6 |

**Rule, and it is the load-bearing one:** every generator takes a **noise parameter and a pedestal parameter**, and every golden-master test built on it runs a **sweep**, not a single point. Two of the four science biases below survived precisely because the existing golden masters (`tests/test_partition.py::_ivf_field`, `tests/test_group_a_moments.py`) generate **noise-free, pedestal-free** data on which the biased branch is mathematically unreachable. A noise sweep over those two existing generators, by itself, would have caught items A1′ and A2b.

## 0.3 — Leg 3: the harness

`tests/test_pipelines_on_real_data.py`, marked `@pytest.mark.realdata`, skipped when the corpus is absent, run in CI on a schedule rather than per-commit.

For each of the 14 pipelines × each of the 4 routes (`ROUTE_ORDER = ('headless', 'batch', 'session', 'kernel')`):

1. **Runs to completion** — no exception, no silent-empty result.
2. **Produces its results table** with the expected columns.
3. **Route equivalence** — all four routes produce byte-identical tables for the same input and parameters. This is the existing route-equivalence discipline, applied to real data for the first time.
4. **Golden master** — tables hashed and pinned. Any later phase that changes a number must change a pin, deliberately, with a CHANGELOG line saying why.
5. **Timing recorded**, not asserted — a regression log, so the speed Meet valued is visible if it moves.

**The 14×4 matrix is the deliverable.** Publish it as a table in the repo (`docs/pipeline_route_matrix.md`), regenerated by the harness. Today's cells are mostly unknown; that is the point. Every subsequent phase is scored by cells turning green in this matrix.

**Acceptance for Phase 0:** the matrix exists and is generated, every cell has a verdict (pass / fail / not-applicable / not-implemented — **never blank**), and the synthetic generators reproduce every **[executed]** number in §A below. The phase is green when the matrix is *complete and honest*, not when it is all green.

## 0.4 — Ground-truth annotations: a standing caveat

Your own statement governs any comparison against the Box masks: *the boundaries were drawn to measure objects; the count should be read as loosely accurate; the boundary is what is at question.*

Encode that in the harness rather than in a reviewer's memory:

- **Count** metrics (recall, precision, F1 at IoU ≥ 0.3) → asserted with tolerance.
- **Boundary** metrics (IoU, Dice, Hausdorff) → **recorded and plotted, never asserted.** A boundary regression is reported for a human to look at.
- The registry's provenance block carries this per-file.

This protects you twice: it stops a boundary-metric CI failure from blocking a correct change, and it stops anyone later quoting a Dice score in the manuscript as though the annotation were a gold standard.

---

# PHASE A — Science scrutiny

Four previously-unverified MEDIUM items from the original scientific-method audit were settled empirically this session. **Two are fixed. Two are live, and worse than the audit guessed.** Four additional defects were found in the same code while settling them. All numbers below are **[executed]** against the shipped functions.

### Settled clean — no action

| Item | Verdict |
|---|---|
| **SpIDA low-intensity tail truncation** | **Fixed.** The k=0 population is retained; recovery within 4% for N = 1…300 at ε = 25. The 99.9th-percentile upper cut discards 0.05–0.10% of pixels and changes the fit by <0.1%. |
| **GLCM over bbox rather than mask** | **Fixed.** `_masked_graycomatrix` (`feature_analysis_tools.py:50-82`) counts only pairs with both pixels inside the mask (`:74`). Circle vs 10:1 ellipse of equal area and identical texture: contrast differs 6.85%, and **identically whether the background is real or zeroed** — proving the residual is pure geometry (a thin object has fewer long-range in-object pairs), not background contamination. Pinned by `tests/test_feature_analysis.py:145,154`. |

### A1 — SpIDA: detector read-noise variance is never subtracted · **HIGH**

`src/pycat/toolbox/spida_tools.py:388-389`. `white_noise` subtracts an **offset from the mean** and never touches the second moment, so the moment seeds `div = var/mean`, `x0 = mean²/var` absorb read-noise variance directly.

Analytically `ε̂ = ε + s²/(εN)` and `N̂ = (εN)²/(ε²N + s²)`. Measured at N = 8, ε = 25 **[executed]** (`item1b.py`):

| read σ | fit N | err N | fit ε | err ε | warning |
|---|---|---|---|---|---|
| 0 | 7.81 | −2.4% | 25.7 | +2.7% | none |
| 40 | 6.22 | −22.3% | 32.8 | +31.2% | none |
| 80 | 3.98 | **−50.2%** | 53.2 | **+112.7%** | none |
| 160 | 2.12 | **−73.5%** | 111.7 | **+346.9%** | none |

A 4× under-count of molecules and a 4.5× over-estimate of quantal brightness, silently. The `low_density_regime` flag does fire at σ ≥ 80 but attributes it to sampling variance (`:528-532`) — it **actively misdirects** away from the cause. Because oligomeric state is ε/ε₀, the bias cancels only if the monomer control was acquired at identical signal level; it does not cancel for a dimmer control. No test exercises read noise (`tests/test_group_a_moments.py:67` generates `poisson(poisson(N)·ε)` — shot noise only).

**Fix:** add `read_noise_var` to `fit_spida_histogram`; use `div = (var − s²)/mean`, `x0 = mean²/(var − s²)`; **refuse** (NaN + stated reason) when `var ≤ s²`. Rewrite the `low_density_regime` message so it stops asserting a cause it has not tested.

### A1″ — SpIDA pedestal guard false-fires for N ≳ 10 · **MEDIUM**

`spida_tools.py:346`, `_lo > 0.17 * _spread`. On **pedestal-free** data `floor/spread` grows as √N **[executed]** (`item1c.py`): N=8 → 0.143 (no warning); N=10 → 0.188 (**false**); N=20 → 0.375 (**false**); N=100 → 1.468 (**false**). The gate was calibrated at N = 8 only; its own comment at `:330-345` admits "the margin is thin." Every ROI with N ≳ 10 gets a confident pedestal accusation it does not deserve.

**Fix:** re-derive against `mean − 3·std` rather than the spread, so the statistic does not scale with N. Pin with an N-sweep.

### A2 — Partition coefficient: three distinct defects · **HIGH**

`src/pycat/toolbox/invitro/partition.py`. The sound path is sound: `partition_coefficient_local` with a correct dark reference recovers K = 100 / 1000 / 10000 at **0.00% error**, at pedestal 0 and 500 **[executed]**. The defects are all in `partition_coefficient_field` and its estimator.

**A2a — the `1e-6` denominator floor is silent and non-monotonic.** `:766`. Float image, dilute phase below 1e-6 (what a background-subtracted image looks like) **[executed]** (`item2_kp.py`):

| true K | `c_sat_proxy` reported | denominator actually used | K returned |
|---|---|---|---|
| 100 | 1.000e-07 | 1.77e-06 | **5.66** |
| 1000 | 1.000e-07 | 1.69e-05 | **5.92** |
| 10000 | 1.000e-07 | 1.68e-04 | **5.94** |

Three true K spanning two decades all return ≈ 5.9. **The output is not monotonic in K.** The returned dict carries `c_sat_proxy` and `partition_coeff` such that `partition_coeff ≠ c_dense_proxy / c_sat_proxy`, and **no key says why**.

**A2b — the 10th-percentile dilute estimator biases K upward under any realistic noise.** `:755-762`. Uniform dilute phase + Gaussian relative noise; result independent of true K **[executed]** (`item2b.py`):

| dilute CV | K returned (true 100) | error |
|---|---|---|
| 0.05 | 106.8 | +6.8% |
| 0.10 | 114.7 | +14.7% |
| 0.20 | 134.4 | +34.4% |
| 0.40 | 204.9 | **+104.9%** |
| 0.78 | 99.8 | −0.2% ← guard trips |

Note the **discontinuity**: at CV ≈ 0.78 the `0.05·mean` guard engages and the error snaps from +100% to zero. A 100-point jump at a threshold invisible to the user. The golden master (`tests/test_partition.py:171-184`) cannot see this — `_ivf_field` builds a noise-free uniform background, so `percentile == mean` by construction and **neither branch can engage**. Its sibling at `:188-198` pins the clipped fallback as `0 < kp < 100` for a true Kp of 6 — the clipping is pinned *as acceptable*, with no accuracy requirement.

**A2c — float dtype → every image reads as saturated.** `:790-791`: `sat_level = 1.0 if mx <= 1.0+1e-6 else mx`. The ceiling becomes the image's own brightest pixel, so the brightest droplet is saturated by definition **[executed]**: uint16 → K = 100 ✓; float64 → **NaN**; float[0,1] → **NaN**. Fail-loud rather than silent-wrong, but it makes the function unusable on float input.

**Also:** `partition_coefficient_field` performs **no background subtraction**. Pedestal 500: true K = 10000 → **100** (−99.0%) **[executed]**. The docstrings warn about this at length; the function computes and returns the number anyway.

**Live call sites:** `timeseries_invitro_tools.py:513` (per-frame Kp(t) — A2b would manufacture spurious kinetics) and `invitro_tools.py:79` (public API). The main UI button (`invitro_fluor_ui.py:789`) and batch (`batch/steps/invitro_steps.py:101`) correctly use `_local`.

**Fix:** return `dilute_denominator` and `dilute_fallback_used`, and set `partition_coeff = NaN` when the floor engages; switch `:755` from the 10th percentile to the background **median**, or flag when `bulk_pct/bulk_mean < 0.9`; at `:790` treat the image max as a ceiling **only** when `saturation_level` was supplied explicitly; route `timeseries_invitro_tools.py:513` to `_local`. Regenerate `_ivf_field` with a noise sweep.

### A3 — Texture features: entropy over bbox, and two LBP columns that are not texture · **HIGH**

`src/pycat/toolbox/feature_analysis_tools.py`. GLCM is masked (above). Entropy is not.

**A3′ — `shannon_entropy` over the whole bbox crop.** `:184-185` and `:187-188` take the first return of `crop_bounding_box` (background **zeroed**) and run entropy over the rectangle. Control holding the within-mask intensity multiset **byte-identical** across shapes **[executed]** (`item3_tex.py`):

| shape | bbox fill | `8_bit_entropy` (shipped) | masked control |
|---|---|---|---|
| circle | 0.792 | **5.968** | 4.505 |
| ellipse 4:1 | 0.363 | **3.339** | 4.479 |
| ellipse 10:1 | 0.154 | **1.634** | 4.446 |
| ellipse 16:1 | 0.097 | **1.102** | 4.422 |

**An 81.5% swing driven purely by bbox fill fraction, with zero change in texture.** The masked control moves 1.8%. Background pixels are set to 0, so the collapse is spurious mass in bin 0 — not a real background. `32_bit_entropy` behaves identically (−75.9%). These columns are live: `calculate_image_features` (`:381-387`) is called per cell at `:512` and concatenated into the user-facing table at `:530-532`. Cell solidity varies widely, so this is **an eccentricity proxy sitting in the results table labelled as texture.**

**A3″ — `lbp_mean` and `lbp_std` are area statistics.** `:325-331` restricts the histogram to the mask correctly, then takes statistics over the **256 bin counts** rather than the LBP values. Verified to floating-point equality **[executed]**: circle `lbp_mean = 15.597656 == 3993/256`; 16:1 ellipse `15.644531 == 4005/256`. **Unchanged when the texture is replaced by a flat constant.** `lbp_std` scales linearly with area. Only `lbp_entropy` responds to texture, and it is an unnormalised-histogram entropy that is itself partly an area statistic.

**No test touches either** — grep for `8_bit_entropy` / `lbp_mean` across `tests/` returns nothing.

**Fix:** at `:185`/`:188` use the **third** return value of `crop_bounding_box`, which is already computed and currently discarded as `_`: `shannon_entropy(cropped_img[cropped_mask])`. At `:329-330` compute from `lbp[cropped_mask]` values, not `lbp_hist`; normalise `lbp_hist` before `:331`. **This changes published numbers** — version-bump, CHANGELOG, and a golden-master re-pin with the shape-invariance test attached.

### A4 — Size distribution: the truncated fit exists and production cannot reach it · **HIGH**

`src/pycat/toolbox/invitro/size_distribution.py`. A correct left-truncated MLE is implemented (`_trunc_loglik:25`, `_fit_trunc_lognormal:35`, `_fit_trunc_scipy:52`). The public wrapper `fit_size_distribution(radii_um, n_bins=30)` **has no `xmin` parameter** — passing one raises `TypeError` **[executed]** — and calls `fit_size_distribution_mle(r)` bare at `:498`. All three production call sites go through the wrapper: `invitro_fluor_ui.py:861`, `invitro_bf_ui.py:418`, `batch/steps/invitro_steps.py:132`. **The truncated path never runs in production.**

The detection limit is a *known quantity that is never computed*: `min_area` (default 6 px², `invitro/segmentation.py:27`, UI `invitro_fluor_ui.py:463`, recorded in the step at `:539`) and radii built as `sqrt(area·mpx²/π)` ⇒ `xmin = sqrt(min_area·mpx²/π)`.

Measured, true lognormal μ = ln 0.5, σ = 0.6, n = 20000 **[executed]** (`item4_size.py`):

| d_min | % lost | err μ (shipped) | err σ (shipped) | err μ (`xmin` honoured) | err σ |
|---|---|---|---|---|---|
| 0.20 | 6.0 | +11.5% | −11.9% | +0.1% | −0.3% |
| 0.30 | 19.6 | +29.9% | −23.6% | +0.4% | −0.4% |
| 0.40 | 35.4 | **+50.1%** | **−33.1%** | +2.5% | −1.5% |

Also biased with no caveat at 35% loss: `mean_radius_um` +29%, `median_radius_um` +34%, `polydispersity_index` −24%. **Model identification is robust** — lognormal is still selected at every truncation level up to 60%, and the power-law tail test correctly rejects power-law throughout. The damage is confined to parameters, not model choice; worth stating plainly, since the original audit anticipated an exponent bias and none was observed.

`res['mle']['detection_limit_xmin']` is **always `None`** on the shipped path, and the exported CSV records neither the truncation nor its radius. The existing test `tests/test_size_distribution_mle_characterization.py:96` pins the bias *as the intended default* — `"CHARACTERISATION (survives the fix — the DEFAULT stays untruncated)"` — and the fix test at `:108` calls the MLE directly, as no production code does.

**Fix:** add `xmin: float | None = None` to `fit_size_distribution:398`, forward at `:498`; at the three call sites compute `xmin` from the already-recorded `min_area` and pixel size; surface `detection_limit_xmin` in the exported row; **retire the characterisation test's premise** — the default should be the truncated fit when the limit is known.

### A5 — Pixel-size honesty gate: 15 bypasses remain

`pixel_size_um()` returns NaN rather than guessing; the loaders write `microns_per_pixel_sq = 1` as the explicit *"we don't know"* sentinel (`pixel_size.py:200`, `file_io/stack_load.py:98`, `data/data_modules.py:382,388,404,414`). **15 call sites still read the repository key directly with a `1.0` default**, including `two_channel_coloc_tools.py:548` and `:649`. Gate adoption: `pixel_size_um` 3 sites, `pixel_size_um_or_default` 17, `has_real_pixel_size` 6.

The worst of them is in Data QC and is covered at §G4 — it pre-fills the QC spinbox with the sentinel so an uncalibrated image gets a fabricated Nyquist verdict.

**Fix:** convert all 15 to the gate. The structural test `tests/test_pixel_size_single_accessor.py` only scans functions literally named `_mpx` (`:28`) — widen it to any read of `microns_per_pixel_sq` outside `utils/pixel_size.py`.

**Phase A acceptance:** every **[executed]** table above is reproduced as a test over the Phase 0.2 generators, with a noise/pedestal sweep; `_ivf_field` and `_spida_pixels` regenerated with noise parameters; every number that changes gets a CHANGELOG line naming the bias it removes.

---

# PHASE B — Per-pipeline assessment

The 14 pipelines (`PIPELINE_DEFS`, `workflow_checklist.py:190-205`), 102 steps total. **8 steps have empty step keys — 7 of them in fibril.** An empty step key means the step cannot be recorded, cannot be replayed in batch, and cannot be bound by the navigator. Filling them is a prerequisite for Phase E, not an aside.

| Pipeline | Steps | Batch replay | Linking | Notes |
|---|---|---|---|---|
| condensate | 13 | real | **full, 2 tiers** | the reference implementation |
| timeseries | 10 | partial | none | key collides with `_PARENT_COLUMNS` (§F3) |
| coloc | 10 | partial | partial | borrows condensate steps 7/9; own tables unlinked |
| cellular_bf | 10 | partial | none | bbox present, so Path B works; no label column |
| invitro_fluor | 7 | real | **full** | second reference implementation |
| timeseries_invitro_fluor | 6 | partial | none | Kp(t) also carries the A2b bias |
| invitro_bf | 8 | partial | none | |
| fibril | 13 | partial | **none, but stamped** | 7 empty step keys; clearest "identity exists, no view" case |
| zstack | 5 | partial | none | label cols `cell_label`/`condensate_label` |
| vpt | 5 | partial | own key | self-linked, position-indexed, divergent id (§F4) |
| frap | 3 | partial | n/a | rows are time points, not objects |
| fusion | 3 | partial | n/a | rows are a fit + time points |
| temperature | 5 | partial | none | |
| fd_curve | 4 | partial | none | crosses `OperationRunner` but has no `EntitySpec` |

**frap, fusion, fd_curve and temperature are not linking failures** — their rows are time points or fit parameters, not objects. Mark them `not-applicable` in the matrix explicitly. "Linking on all methods" means *all methods that have objects*; saying so in the matrix prevents a permanent four-cell red that nobody can fix.

**Work:** fill the 8 empty step keys (fibril first); run each pipeline's step list against the Phase 0 corpus; record per-pipeline which steps are recorded, replayable and bound. The output is the §0.3 matrix, populated.

---

# PHASE C — Architecture scrutiny and the refactoring decision

## C1 — Two subsystems at zero production adoption: adopt or delete

Both grep-verified at **zero production call sites**, tests only:

- **`src/pycat/kernel/operation_service.py`** — 27 registered kernels, 0 production callers.
- **`src/pycat/utils/object_graph.py`** — 0 production consumers; 1 relation type (cell→puncta).

**Recommendation: delete `object_graph.py`.** It is not on the linking path and should not be. Its own docstring scopes it to read-only aggregation ("linked navigation is later still", `:22`) and it explicitly reuses `_pycat_entity_id` rather than defining a key. Routing linking through it would add a second abstraction over an identity that already works. Keeping an unused parallel abstraction next to the real one is how the next person wires the wrong thing.

**`operation_service.py` is a harder call and the spec should not pretend otherwise.** 27 kernels at zero adoption is either dead weight or the intended route-unification layer not yet connected. Decide it explicitly and record the decision in the CHANGELOG: if the four-route equivalence (§0.3) is meant to be enforced *through* it, Phase 0 is the moment to wire it, because that is when the four routes first get compared on real data. If not, delete it. **Do not leave it at zero for another release** — it is the single largest unexplained surface in the codebase.

## C2 — Is more refactoring needed? Mostly no

The complexity ratchet is working. `_ABSOLUTE_LONGEST = 676` with an actual max of 423 — **253 lines of slack** — and three file ceilings are stale. The decomposition guard (`tests/test_complexity_budget.py`) already exists and does its job.

**Do not open a refactoring workstream.** Do two small things instead:
1. **Tighten the ratchet to actual + small margin** (e.g. 450) and refresh the three stale file ceilings, so the guard resumes having teeth.
2. The `pyproject.toml` policy against mass `ruff --fix` stands and should not be revisited — a global autofix over a 7,000-line `file_io.py` is how a working codebase breaks.

The one *structural* change worth making is §F1 (one line in `show_dataframes_dialog`), and it is a wiring change, not a refactor.

## C3 — Navigator

- `generated_method_ui.py:40` — `self._review = dict(review or {})` receives a `ParamReview` object from `navigator_dock.py:364 → :270`. Reproduced: `TypeError: 'ParamReview' object is not iterable`, swallowed by the broad handler at `navigator_dock.py:235`. **"Build method panel" silently does nothing.** Fix: flatten `params_by_step()` (`{step: {param: value}}`) to match `_seed_reviewed_parameters`'s flat `repo[key] = value` at `:52`. `ParamReview` (`navigator/parameters.py:218`) has no `__iter__`/`keys()` — add neither; flatten at the call site.
- `section_bindings.json` — 22 of 133 `_add_*` builders bound.
- `operation_guidance.json` ships `"guidance": {}` and the coverage floor is **0** (`tests/navigator/test_guidance_coverage.py:31`). A floor of zero is not a floor. Raise it to the current count on every release so guidance can only grow.

## C4 — The standing check this codebase has earned

Four times now (`xmin`, `partition_coefficient_local`, `OperationRunner.execute`, `qc_snr` in the op catalog) the correct implementation exists and nothing production calls it. Add a CI check:

> Every public function in `toolbox/` and `utils/` that has a test but **zero non-test callers** is listed in a declared-gap file with a reason. New entries require an explicit addition.

This is the same declared-gap discipline already used elsewhere, pointed at reachability instead of complexity. It would have caught all four.

---

# PHASE D — Deprecations

Dependency state was settled by a real resolution (`uv pip compile`, 194 packages, Python 3.12) and a forward-stack test run: numpy 2.5.3 / scipy 1.18.1 / scikit-image 0.26.0 / pandas 3.0.5 / matplotlib 3.11.2 / opencv 5.0.0 / zarr 3.4.1 on Python 3.13 → **2325 passed, 7 failed**, all 7 traceable to deps absent from the sandbox (qtpy, napari, bioio, h5py). **Zero genuine compatibility failures.** 306/367 modules import with **zero** deprecation warnings.

**Correction to carry forward: napari is at 0.9.2, not 0.8.0.** The PyQt5 question is settled and favourable; the live items are in 0.9.0.

## Must fix before ship

| # | Item | Evidence | Fix | Effort |
|---|---|---|---|---|
| **D1** | `config/requirements-base.txt` has drifted badly: **9 deps missing** (`av`, `bioio-imageio`, `hdf5plugin`, `imaris-ims-file-reader`, `largestinteriorrectangle`, `networkx`, `numba`, `openpyxl`, `zarr>=2.12`) and **5 constraints dropped** (`bioio`, `cellpose`, `numpy`, `qtpy`, `PySide6-Essentials` all bare). The bare `cellpose` installs **4.2.1.1**, which removed the cyto2 CNN — exactly what the pyproject pin exists to prevent; `segmentation/cellpose.py:62-66` would silently take its Cellpose-4 branch and segment with a different model. | computed diff | generate from pyproject, or delete the file | trivial |
| **D2** | `scipy.spatial.distance_matrix` — SciPy 1.18 emits *"deprecated… will be removed in SciPy 1.20.0"* (message captured from the installed scipy, not from docs). **One site: `src/pycat/toolbox/spatial_metrology_tools.py:692`** — **[verified]**, and the only PyCAT-source deprecation that fires in the entire 2325-test run. | executed | `from scipy.spatial.distance import cdist; cdist(coords, coords)` | trivial |
| **D3** | `src/pycat/fix_tifffile.py` — **importing it executes the script and calls `sys.exit()`**; on an unpatched tifffile it rewrites `site-packages`. Content is now false: the numpy<2.0 claim contradicts pyproject, aicsimageio was removed in 1.6.0, and the bug it patches **no longer exists** (tifffile 2026.9.15 ships the fix; running the script prints "may already have this fixed upstream"). Still advertised at `file_io/file_io.py:407,413` and `README.md:862`; two live `"newbyteorder" in str(e)` catches remain at `file_io/readers/image_reader_2d.py:70-77` and `batch/steps/_common.py:144`. | executed (it killed an import harness) | delete, or move under `if __name__ == "__main__":` and out of the package; drop the two help strings, the README section, and the two dead catches | small |
| **D4** | Declared floors describe nothing that was tested. `bioio>=1.0` resolves to **bioio 3.6.0 + bioio-base 3.5.0**, while the plugins resolve to **1.x** (`bioio-ome-tiff 1.4.0`, `bioio-tifffile 1.3.1`, `bioio-imageio 1.3.0`) and `bioio-czi` to **3.0.1**. The 1.x plugins declare only `bioio-base>=1.4.0`, so pip pairs 1.x plugins against a 3.x base purely because nobody capped it. bioio 2.0.0 dropped zarr/tifffile/fsspec/imageio/ome-zarr/scikit-image from its dependency set, so core and readers moved apart. **This pairing could not be verified** — bioio is not installed here and the CHANGELOG fetch was proxy-blocked. | metadata; **unverified pairing** | pin tested floors *and* ceilings for `bioio`, the four plugins, and `napari` | small to write, moderate to validate |
| **D5** | The `cellpose<4 → numpy<2.1 → tifffile≤2026.4.11 → zarr-monkeypatch` chain. Traced end to end: cellpose 3.1.1.3 declares `numpy<2.1` → numpy pinned to 2.0.2 → tifffile ≥2026.5.2 requires `numpy>=2.1`, so tifffile caps at 2026.4.11 → which imports `zarr.core.chunk_grids.RegularChunkGrid`, restructured away in zarr 3.2 → so `file_io/tifffile_zarr_shim.py` **monkeypatches a private zarr internal unconditionally on every import of `pycat.file_io`** (`file_io/__init__.py:36`). I ran the shim against the zarr that actually resolves (3.4.1): it returns `True` — **not currently broken**, but load-bearing code on a private path that has already moved once. The fix is already upstream: tifffile 2026.9.15 drops the import entirely, so the shim becomes dead code the moment the cellpose pin relaxes. `segmentation/cellpose.py:100-154` is **already version-aware** (branches `model_type=` vs `pretrained_model=`). The code is ready; only the pin is not. | executed | track as a dependency, not a sprint task — the unblock is upstream | blast-radius |

**Verify first, before anything else in this phase: the bioio 1.x-plugins-against-3.x-base pairing (D4).** It is the only item with no evidence, and a fresh `pip install` exercises it on every file open.

## Watch — no deadline, do them while you're in the file

- **napari 0.9.0 viewer attributes, 17 sites.** Verified by reading the `napari-0.9.2` wheel (`components/viewer_model.py:300-395`): six attributes wrapped in `typing_extensions.deprecated`. `viewer.camera → viewer.scene.camera` (10 sites: `ui/viewer_actions.py:37,58`; `ui/batch_navigator.py:133`; `toolbox/vpt/napari_adapter.py:286,296`; `file_io/napari_adapter.py:400,405,406,413`; `utils/object_ref.py:451`); `viewer.grid → viewer.canvas.grid` (3: `ui/grid_view.py:84,124`; `batch_processor.py:1246`); `viewer.scale_bar → viewer.canvas.overlays.scale_bar` (2: `file_io/napari_adapter.py:267,335`); `viewer.text_overlay → viewer.canvas.overlays.text` (2: `toolbox/temperature_ui.py:323,345`); `axes`/`floating_axes` — not exposed. Every one says *"no planned date for removal."* A mechanical 17-line rename with a known target. **One precision note:** the suppression at `napari_adapter.py:292-293,338-344` filters `FutureWarning` only, which will **not** silence the new `viewer.scale_bar` access — that is a `DeprecationWarning` and will surface in pytest.
- **`scale_bar.unit` — the one item with a date.** FutureWarning, already a no-op, *"removed in 0.10.0."* Set at `file_io/napari_adapter.py:294,340,345`, all inside `try/except`, and the code already documents the no-op and pushes `Layer.units` as the real mechanism. Dead code; delete the three assignments once the napari floor is ≥0.9.
- **`window.qt_viewer`** — FutureWarning, *"no earlier than v0.10.0."* PyCAT already tries private `_qt_viewer` first at four sites, so nothing warns today — but napari's own comment calls `_qt_viewer` "vestigial." One exception: `toolbox/vpt/napari_adapter.py:292` uses the public attribute with **no fallback** and will warn. Both paths are 0.10 liabilities.
- **Warning filters.** No systemic suppression — **no `filterwarnings` in `[tool.pytest.ini_options]` and none in `tests/conftest.py`**, which is how D2 was found. But `toolbox/image_processing/background.py:775` calls bare `warnings.filterwarnings("ignore", …)` **inside a function with no `catch_warnings()` context** — a permanent global filter mutation on first call (same pattern at `toolbox/timeseries/execution.py:63` and `analysis.py:157`). Six `simplefilter("ignore")` with no category (`spatial_acf_tools.py:385,446,478`; `timeseries/analysis.py:69,183`; `gpu_utils.py:268`) would hide a future skimage deprecation. Narrow them to a category.
- **`.exec_()` ×29** — works on both bindings (PySide6 6.12 declares `exec_` natively; qtpy 2.4.3 shims it for PyQt6). Qt lists the rename with **no removal timeline**. Mechanical, no urgency.

## Verified not exposed — dropped from the action list

numpy 2.x removals (36 patterns, **zero hits** — the pyproject claim is accurate); `np.array(copy=False)`; `np.ptp` on masked arrays (no `np.ma.` anywhere); scipy `trapz`/`simps`/`cumtrapz`/`misc`/`interp2d`/ndimage legacy names (zero each); **pandas 2.x/3.x removals** — `DataFrame.append`, `applymap`, `fillna(method=)` are all gone in pandas 3.0.5 and PyCAT uses none, 2325 tests pass on it; **zarr 3** — all 23 `zarr.open` calls use only `mode/shape/chunks/dtype`, executed clean on 3.4.1; **OpenCV 5.0.0.93** — all 11 functions and 4 constants present, `findContours` returns 2 values matching PyCAT's unpack; matplotlib `get_cmap` already wrapped version-agnostically (`video_export_tools.py:95-104`); **scikit-image regionprops renames already migrated** — the 42-name list at `masks/measurement.py:354-355` uses modern names throughout, and the three attribute accesses are defensive shims that try the modern name first; Python stdlib 3.12 removals (zero `datetime.utcnow()`, `pkg_resources`, `distutils`, `imp`, `get_event_loop`); napari PyQt5 removal — PyCAT imports Qt exclusively through qtpy (97 files, **zero** direct `PyQt5`/`PySide6` imports) and `run_pycat.py:_prefer_pyside6()` forces `QT_API` before any Qt import.

**Python floor.** `requires-python = ">=3.12,<3.13"`. The ceiling reasoning is accurate and was verified: numpy ships **no cp313 wheel below 2.1.0**, so with `cellpose<4`'s `numpy<2.1` a py3.13 resolve picks 2.0.2 and must build from source. Conversely the **floor is higher than PyCAT's own code needs** — zero `match`, zero PEP 695, zero `itertools.batched`, 303 files with `from __future__ import annotations`. It is set by the dependency stack. **State this in the spec** so nobody "fixes" the floor downward and then discovers zarr blocks it.

**Vendored/patching code — complete inventory: two.** `fix_tifffile.py` (D3) and `tifffile_zarr_shim.py` (D5). No `sitecustomize`, no other `sys.modules` injection.

---

# PHASE E — Batch on all methods

**Current state:** `src/pycat/batch_step_registry.py:196-327`, 72 keys — **37 real, 35 skip-stubs** (`lambda s,p,pa,o: print(...)`). Plus **18 keys recorded by the GUI but unregistered**. Replay is **49% real**.

The navigator is in better shape than batch: `navigator/executor.py:229` has 14 `ExecAdapter`s and all 18 resolved routes land on real `replay_*` handlers — **zero on skip-stubs** — and `run_plan:377` genuinely executes.

**Work, in order:**

1. **Make the skip-stubs loud.** A stub that `print()`s is indistinguishable from a step that ran. Convert all 35 to raise a typed `StepNotImplemented` carrying the step key, and have the batch runner collect them into a per-run "steps skipped" block in the report. Today a batch run over a pipeline with 8 stubbed steps produces a results table that looks complete. **This is a correctness fix, not a cosmetic one, and it comes first** — before Phase 0 can score batch cells honestly.
2. **Register the 18 orphan keys**, or remove them from GUI recording. A recorded key with no registry entry is a silent no-op on replay.
3. **Fill the 8 empty step keys** (§B) — unrecordable steps can never be replayed.
4. **Implement the 35 stubs**, pipeline by pipeline, scored against the §0.3 matrix. Order by the matrix: pipelines whose headless route already works are cheapest, because the computation exists and only the replay binding is missing.
5. **Route equivalence as the acceptance test**, per step: `headless == batch == session == kernel` on the Phase 0 corpus.

**Acceptance:** 72/72 real, 0 stubs, the 18 orphans resolved, and the batch column of the matrix green for all 14 pipelines.

---

# PHASE F — Linking on all methods

The machinery exists and is correct. `SelectionService` (`utils/selection_service.py:165`) is instantiated once at `central_manager.py:82-83`; the canonical key is `_pycat_entity_id` = `f"{dataset_id}/{operation_id}/{entity_type}/{frame}/{label}"` (`utils/entity_ref.py:89,164-185`); `BrushableTable` maps through a `QSortFilterProxyModel` so **selection survives a sort** (asserted, `tests/test_brushable_table.py:108,129`); echo suppression, delayed busy release, weak subscribers, a debounced expensive lane, two interleaved object tiers with a finest-first viewer dispatcher, and offline batch crop resolution via `source_path` + bbox are all implemented and tested.

**Only 2 of 14 pipelines are connected** (condensate, invitro_fluor), plus coloc partially (it borrows condensate steps 7/9) and vpt on a divergent key.

## F1 — The one-function fix that unlocks all of them

`show_dataframes_dialog` (`src/pycat/ui/ui_utils.py:284`) is how essentially every pipeline displays results — **109 references across 30+ modules** **[verified]**. At `:319` it calls `create_table_view(df)` and stops. It never calls `make_brushable`, never takes a service, never looks at `_pycat_entity_id`. **Every pipeline that uses it gets a dead table even when the DataFrame is correctly stamped** — which is exactly fibril's situation (stamped at `masks/measurement.py:224-226`, displayed dead at `:304-305,380-381`).

Change `:319` to mirror what `BrushableWorkspace.add_table` already does at `brushable_workspace.py:494-504`:

```python
table_view = create_table_view(without_identity(df))
make_brushable(table_view, df, service, f'{window_title}.{title}')
```

Add one optional `service=` (or `central_manager=`) parameter; pass `central_manager.selection`. **`make_brushable` (`brushable_table.py:165`) already never raises and already returns `None` when it cannot link**, and `linkability_of` (`entity_ref.py:510`) already supplies the honest *"Linked by row position (sort/filter unsafe)"* label for unstamped tables. The 109 call sites adopt incrementally — not passing the service means today's behaviour.

## F2 — Make identity grow by declaration, as designed

`entity_ref.py:343-349` describes coverage growing by *declaration*, with `OperationRunner.execute` (`operation_runner.py:131-136`) as the chokepoint. **`OperationRunner` has three call sites in the entire codebase** — `fd_curve_ui.py:410`, `contrast_cascade_ui.py:174`, `navigator/executor.py:468` — and **none of the 14 pipelines runs its object-table analysis through it.** Identity still comes from four hand-written `finalize_entity_table` calls. The chokepoint built to end scattered stamping has not replaced scattered stamping.

Also: **two of the six registered specs are dead letters.** `_register_default_entity_specs` (`entity_ref.py:430-435`) declares six; `vpt_tracks` and `object_colocalization` have **no call site at all**. And `object_colocalization`'s `label_column='label'` would not match the real columns, which are `'Mask 1 Labels'` / `'Mask 2 Labels'` (`coloc/object_based.py:602`).

Register the missing specs with the **real** column names and wire the producers:

- `timeseries_condensate_analysis` → `label_column='cell_label'`, `frame_column='frame'`
- `zstack_3d_metrics` → two specs, `'cell_label'` / `'condensate_label'`, `frame_column='frame'`
- fix `object_colocalization` → `label_column='Mask 1 Labels'`
- `invitro_bf` / `cellular_bf` per-object tables → specs on their real label columns

## F3 — Resolve the `_PARENT_COLUMNS` collision first

**`'cell_label'` is in `_PARENT_COLUMNS` (`entity_ref.py:281`)**, so `stamp_entity_ids` auto-detects it as the *parent*, not the label (`:313-318`). Naively stamping the timeseries and zstack tables would produce ids with an empty or wrong label segment. **This is a real trap and it blocks F2** — fix it first: an explicit `label_column` on the spec must win over an auto-detected parent.

## F4 — Reconcile VPT onto the one key

Two concrete defects:

- **Position-keyed table.** `vpt/table_adapter.py:81-86` does `row_for_id.get(int(track_id))` → `table.selectRow(r)`. **Sort the table and every highlight lands on the wrong track, silently** — the precise bug `brushable_table.py:1-14` was written to kill. No test covers it.
- **Divergent id.** `vpt_ui.py:143` emits `entity_id_column(source, 'vpt', 'track', None, tid)` → `"{raw_path}/vpt/track/-/{tid}"`, while the registered spec (`entity_ref.py:434`) produces `"{uuid}/vpt_tracks/bead/{frame}/{track_id}"`. **Three of four segments differ.** And VPT passes the **raw path** where every other producer passes `dataset_id_for(source_path)` (a durable UUID) — so `SelectionService.invalidate_dataset`, which matches on `f"{dataset_id}/"` (`selection_service.py:388-389`), **will not clear VPT selections when the dataset closes.**

Fix: `entity_id_column(dataset_id_for(source), 'vpt_tracks', 'bead', frame, tid)`, and replace the raw `QTableWidget` + `row_for_id` with `BrushableTable`.

## F5 — Retire the parallel, position-keyed path

There are **two** selection mechanisms. `data_viz_tools.PlottingWidget._wire_brushing:509` calls `make_pickable` at `:564-566` **without `hub=`**, so it never joins `SelectionService` and keys on **row position** (`brushing.py:179-182`). This is the only "linking" most pipelines get, and it is both one-way and sort-unsafe. Pass `central_manager.selection` and prefer `_pycat_entity_id` over positional lookup when the column is present. One argument converts the generic plotting widget from a second system into a `SelectionView` on the bus.

## F6 — Close the lifecycle hole

**`grep '\.detach()' src/` returns nothing.** `feature_analysis_tools.py:1178` and `invitro_fluor_ui.py:656` store `(dock, ws)` with comments saying "detach on close"; no close handler is connected. The workspace is held strongly by `central_manager`, so weak subscriptions are never pruned, and **each re-mount appends another `_pick` closure to `viewer.mouse_drag_callbacks` (`brushable_workspace.py:534-536`) that is never removed.** `test_selection_lifecycle.py` exercises the bare service, not the workspace, so the suite cannot see it.

## F7 — Note on export

`without_identity` (`entity_ref.py:478-490`) deliberately strips `_pycat_*` on display and export, asserted at `tests/test_brushable_table.py:227`. An exported CSV therefore **can never be re-linked.** That is a defensible trade, but for a methods paper it is worth adding an opt-in `--keep-identity` export so a reviewer can round-trip a table back into the viewer.

**Acceptance:** a contract test — *every pipeline's results table is either brushable or declared not-applicable with a reason* — plus a sort-safety test for VPT, and `detach()` with a production caller.

---

# PHASE G — Data QC: minimum viable to ship

**Assessment first, because it is not what one would expect: there are no stubs.** No `pass`, no `NotImplementedError`, no constant-returning placeholder in any of the 12 modules. Several methods are recognised and correctly implemented: Abbe `λ/(2·NA)` and Nyquist `λ/(4·NA)` with a correct FWHM→σ conversion, Guizar-Sicairos phase cross-correlation (used with a z-scoring guard that stops photobleaching masquerading as motion), a textbook cepstrum for echo detection, and — the strongest thing in the module — a 400-permutation null for the vibration statistic that removes a genuine frame-count dependence. In-code failure documentation is unusually honest; `focus.py:62-65` states outright that a sub-1 ratio "is physically impossible, and a symptom of comparing two different quantities."

**The weakness is integration, not science.** The one GUI entry point is dead; the one live consumer is a bare `run_full_qc(img)`.

| Path | Site | Status |
|---|---|---|
| GUI dashboard | `data_qc_ui.py:179` | **dead** — `ImportError` (§0.1) |
| Batch | `batch_processor.py:452-453` | live but degraded — `run_full_qc(img)` bare: no optics, no `is_zstack`, no channels, no `n_source_frames`; only runs when `records_have_scored_family(records)`, so most batches never QC; exceptions swallowed at `:455` |
| Manuscript Fig-1 | `manuscript/panels.py:124-127` | never fires — reads `context["qc_image"]`, supplied **only by tests** |
| Headless / kernel | — | no entry point; zero `data_qc` references |
| Navigator | `op_catalog.py:172-178` | metadata only — advertises `data_qc_tools.qc_snr`, executes nothing |
| QC gallery | `qc_gallery.py:334` | live, teaching only |

The only programmatic consumer is `reliability._score_qc` (`utils/reliability.py:59-67`), which reduces the whole report to an **unweighted mean** of `{good:1.0, warn:0.6, bad:0.2}` — one `bad` among eight `good` moves the score by 0.1. `data_qc_ui.py:208` writes `data_repository['data_qc_results']` and **nothing ever reads that key.**

## The MVP set — ordered by what embarrasses you fastest

A reviewer will open the dashboard, point it at a z-stack with no pixel-size metadata, and read the verdicts. That sequence currently produces an `ImportError`, then — once fixed — a 101-px drift report on zero drift and a NaN-valued red "POOR". Items G1, G4, G6 are that sequence.

### G1 — Fix `QC_MAX_FRAMES` and make the QC-UI tests execute · **BLOCKER**
Define it (64, per `CHANGELOG.md:7500`) in `data_qc/runner.py` and re-export it from the shim. **Then add one test that actually imports `data_qc_ui` and invokes `_on_run` against a stub `ui_instance`.** Every static source-grep test in `test_qc_ui_contract.py` should be backed by one executing test. The static tests are not wrong; they are insufficient, and the 38-green suite is why this shipped.

### G2 — Photobleaching must track the signal, not the background · **BLOCKER**
`stability.py:45` — `per_frame = median(frame)`. On a sparse fluorescence field — **PyCAT's entire target domain** — the per-frame median *is* the background, which does not bleach. **[executed]**, 30 frames, objects decaying with τ = 8 on a constant pedestal:

```
true object signal remaining:  2.7%
qc_photobleaching reported:    good | 96% of the initial signal remains
(per-frame MEAN would have given 86.8%)
```

A confident false-negative on the defect the module's own docstring calls "one of the most common and most destructive defects there is." Fix: a high percentile or an object-mask-restricted mean, median as fallback; **refuse** when the trace is flat because the field is sparse.

### G3 — SNR must be per-frame · **BLOCKER**
`noise.py:16` → `_base.py:106-109`: `_mean_frame` collapses a 3-D stack with `a.mean(axis=0)`, so noise falls as √N before the estimator runs. **[executed]**, identical per-frame data:

| N frames | SNR | verdict |
|---|---|---|
| 1 | 5.99 | warn |
| 16 | 20.25 | good |
| 200 | 67.31 | good (reported σ 2.23; true per-frame 25) |

**The verdict flips warn→good purely on frame count**, and the `how` string never mentions the averaging. Fix: measure on a single representative frame, or report the median of per-frame SNRs, and say which in `how`. The same `_mean_frame` collapse affects `qc_vignetting:51`, `qc_ghosting:150`, `qc_chromatic:196` — audit each for the same artifact.

### G4 — NaN pixel size must refuse, not produce a red verdict · **BLOCKER**
Both guards use Python truthiness, and **`float('nan')` is truthy**: `sampling.py:13` (`if not (pixel_um and na and wavelength_nm)`) and `focus.py:77`, plus `focus.py:165` (`if limit:`). **[executed]** with `pixel_um = nan`, NA 1.4, λ 520:

```
qc_nyquist -> status 'bad', value nan   "pixel nan µm vs Nyquist 0.093 µm — undersampled"
qc_focus   -> status 'bad', value nan   "sharpest edge ≈ 0.2 px vs a diffraction limit of nan px"
```

`pixel_size_um()` returns NaN **precisely so callers refuse**; two checks turn it into a red POOR with a NaN in the value column. Reachable today via `manuscript/panels.py:126`, whose filter is `is not None` — NaN passes.

**And the sentinel leak:** `data_qc_ui.py:103-107` reads `data_repository.get('microns_per_pixel_sq')` directly, bypassing both `pixel_size_um()` and `pixel_size_is_placeholder()`. The loaders write **exactly 1** as the "don't know" sentinel, so an uncalibrated image silently pre-fills the spinbox with **1.0000 µm**, the user clicks Run, and a fabricated Nyquist ratio and absolute focus verdict come out. This is §A5's worst instance.

Fix: finite-and-positive checks at all three sites; route the pre-fill through the gate.

### G5 — Saturation must not depend on dtype · **BLOCKER**
`_base.py:95-103`. The well-reasoned spike/pile-up logic (`:59-94`) is applied **only to integer dtypes**; a float image gets its ceiling rounded *up* to the next camera bit depth. **[executed]**, identical pixel values:

```
float32 clipped at 300 -> _dtype_max 4095.0 -> good | 0.00% at ceiling   (truth: 2.48% clipped)
uint16  clipped at 300 -> bad  | 2.48% at ceiling
```

`_to_float` and PyCAT's preprocessing produce float arrays everywhere, so QC on a deconvolved/registered/background-corrected image reports "no clipping" **by construction**. `tests/test_data_qc.py:92-130` parametrises over uint16 only — which is why this survived.

### G6 — Refuse an axis whose meaning is unknown · **BLOCKER**
`runner.py:68-69`: `is_zstack` is caller-supplied and never inferred; `:39` treats any 3-D array with `shape[0] > 1` as a time series absent the flag. `runner.py:91-98` contains an **excellent** explicit guard that even states "on a z-stack with ZERO drift it reports 89 px" — and the flag is set **only by the GUI checkbox**, which is in the dead handler, while `batch_processor.py:453` calls `run_full_qc(img)` bare. **So in every reachable non-GUI path the guard never fires.** **[executed]**, 21-plane z-stack, zero lateral drift:

```
without is_zstack:  Drift bad — max drift 101.3 px = 101.3x the feature size (63.3% of FOV)
with is_zstack:     Drift/Focus/Vibration na (correct); Spherical aberration good
```

A `(C, Y, X)` 3-channel image gives `Drift: bad, 45.1 px` the same way; there is no "these are channels" input at all.

Fix: `run_full_qc` **refuses** a 3-D array whose axis semantics are unknown rather than defaulting to time series; add a channel case; `batch_processor.py:453` must pass the semantics it already knows.

### G7 — Two-axis noise estimate · **SHIP-BLOCKING for sCMOS users**
`_base.py:26` differences along **x only**, so row-correlated noise — sCMOS row noise, line-scan artifacts, resonant-scanner banding — reads as zero. **[executed]**, same σ = 25 amplitude: row-structured → σ 0.00, SNR 7,026,023, **good**; column-structured → σ 25.36, SNR 6.08, **warn**. *Rotating the camera 90° changes the verdict.* Fix: min or mean of the x- and y-difference estimates.

### G8 — Refuse the physically impossible
`focus.py:236` refuses when `ratio > 3.0` but not at the other end. **[executed]**, pure Gaussian noise `N(1000, 10)`, no specimen: `qc_focus → good | "sharpest edge ≈ 0.7 px vs a diffraction limit of 1.2 px — 0.6x"`. **A blank noisy field scores better than diffraction-limited**, because the noise gradient *is* the sharpest edge — exactly what the code's own docstring says is impossible. (A perfectly *flat* field is handled correctly as `na`; a noisy empty one is not. `qc_snr` on a flat field returns `inf`, status `good`.) Fix: refuse below ~0.8, or when the 99.9th-percentile gradient is not separable from the noise floor.

### G9 — Decline rather than decide on a background-subtracted floor
`exposure.py:33-42`. **[executed]**, a subtracted image with **46.6% of pixels at exactly 0** reports `good | 0.00% at floor`, because the spike test compares against a 40.95-count-wide window that captures most of a subtracted background. The docstring's argument — a processed image's floor is not a sensor property — is defensible. Reporting `0.00% at floor` is not; it should report `na`.

### G10 — Version and schema the output contract
`run_full_qc` returns `list[dict]` with an **implicit, unversioned, non-uniform** contract: `status`/`tier` are bare repeated string literals, not enums; `qc_drift` adds three top-level keys nobody reads and `qc_vibration` adds a fourth; `diag` holds **raw numpy arrays — not JSON-serialisable**; `report.py` indexes `r['status']`/`r['name']`/`r['headline']` unconditionally and raises `KeyError` rather than degrading. `report.py` emits **a matplotlib `Figure` only** and serialises nothing; the only machine-readable output is the UI CSV writer (`data_qc_ui.py:271-280`), which drops `diag` and is unreachable.

**For a methods paper this is the item that matters most after the blockers:** add `schema_version`, freeze the key set, make `status`/`tier` enums, and emit a JSON-serialisable form so the QC table can ship as a supplementary file.

### G11 — Cite the methods, and mark the heuristics as heuristics
`grep` for `doi`/`et al` inside `toolbox/data_qc/` returns **nothing**. Citations exist only in `qc_gallery.py`, for 7 of 13 checks, and they are general best-practice papers (Waters 2009, North 2006, Jonkman 2020, Jost & Waters 2019), not method citations for these estimators.

Cite in-code: Abbe, Nyquist, Guizar-Sicairos phase correlation, the MAD-of-adjacent-differences noise estimator, the cepstrum. And mark explicitly — **in both the docstring and the rendered `how` string** — that `edge_width_px`, the DoG(1,2) sharpness and the autocorrelation feature scale are **PyCAT-defined heuristics with no literature anchor.** `edge_width_px`'s own docstring already concedes the conversion constant is 2.51 for a step and 1.65 for a blob, i.e. the absolute ratio is uncertain by ~1.5×, and the `good` text already says "this is a SCREEN, not a measurement." Say that where the reviewer reads it, not only where the maintainer does.

### G12 — Thresholds
Every threshold in the module is a hard-coded magic number. Roughly half carry a measured table or a prose argument in a comment (the ceiling spike test, the vignetting kernel sizing, the drift feature-scale argument, the vibration p-value, the chromatic 2-px floor — these are genuinely well-reasoned); the rest carry nothing (`snr >= 10/4`; saturation `0.001/0.01`; vignetting `0.9/0.7`; ghosting `0.005/0.012`; Nyquist `1.05/2.0/0.4`; the biological MAD `k=3.5`; focus `lo.mean() < 0.15`).

**For MVP, do not re-derive them.** Do one cheaper thing: move all thresholds into a single `thresholds.py` with a one-line provenance comment each — *measured*, *argued*, or **`# PROVENANCE: none — chosen by inspection`**. An honest "chosen by inspection" is defensible in a methods paper; a bare `0.005` buried at `illumination.py:168` is not.

### Explicitly out of MVP scope
Re-deriving unprovenanced thresholds against real data; a citable replacement for the DoG sharpness metric; `qc_biological_objects` coverage (currently **0 direct tests**); 4-D input (today a 4-D array raises `ValueError: too many values to unpack` at `illumination.py:52`, swallowed by `batch_processor.py:455` — at minimum, refuse it with a message).

### Also fix while in the file
`report.py:91` claims *"the mechanical overlap test (`test_the_report_has_no_overlapping_text`) is what proved it."* **That test does not exist** — grep across the whole repo returns only the comment. Either write it or delete the claim.

**Phase G acceptance — the MVP bar:**
1. The dashboard runs, end to end, on every file in the Phase 0 corpus, with one executing test per static contract test.
2. No check returns a verdict it cannot support: NaN pixel size → refuse; unknown axis semantics → refuse; float dtype → the same saturation logic as integer; blank field → refuse.
3. Photobleaching and SNR measure what their labels claim.
4. The output has a `schema_version` and a JSON form.
5. Every heuristic is labelled a heuristic where the user reads it, and every threshold has one line of provenance — including "none."

---

# Sequencing

```
PHASE 0  ─ sample data gate ─────────────────────────────  BLOCKS EVERYTHING
            0.1 real corpus (14 pipelines + 6 negatives)
            0.2 synthetic generators (noise + pedestal sweeps)
            0.3 harness → the 14×4 matrix
            0.4 annotation caveat encoded
                        │
     ┌──────────────────┼──────────────────┬─────────────────┐
     │                  │                  │                 │
  PHASE D1-D3        PHASE G1           PHASE E1          PHASE C3
  (independent of    (ImportError —     (make stubs       (ParamReview
   sample data —     needs the corpus   loud — needed     TypeError —
   do in parallel)   to verify)         to score batch)   one-line fix)
     │                  │                  │                 │
     └──────────────────┴────────┬─────────┴─────────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
      PHASE A              PHASE F3 → F1 → F2        PHASE E2-E5
   (science fixes —     (collision, then the one   (49% → 100%,
    each needs a         function, then the specs)  scored by matrix)
    known-truth
    generator)                   │                      │
          │                      │                      │
          └──────────────────────┴──────────────────────┘
                                 │
                          PHASE G2-G12        PHASE B (matrix populated)
                                 │
                          PHASE C1 (adopt-or-delete), C4, D4, D5
```

**Do first, today, independent of everything:** D1 (requirements drift — one file, highest value per line), D2 (`cdist`, one line), D3 (`fix_tifffile.py` — it writes site-packages on import), C3 (`ParamReview` — one line, unblocks the navigator). None needs sample data; all four are self-contained.

**Do first inside Phase 0:** G1, because until the QC dashboard runs you cannot evaluate QC against the corpus you are building, and E1, because until the stubs are loud the batch column of the matrix cannot be scored honestly.

---

# What this does not cover, deliberately

- **Re-deriving QC thresholds against real microscopy data.** That is a paper-sized calibration study. MVP is labelling provenance honestly, not manufacturing it.
- **The annotation boundary question.** Your own caveat stands: boundaries are not a gold standard. §0.4 encodes that; it does not resolve it.
- **The cellpose pin (D5).** Gated on an upstream decision PyCAT does not control. Track it; do not schedule it.
- **A refactoring workstream.** The ratchet has 253 lines of slack and the decomposition guard works. Tighten the ratchet, fix the three stale ceilings, and leave it.
- **The bioio plugin pairing (D4).** Flagged as the one unverified item in the dependency audit. It needs a real install, not more reading.
