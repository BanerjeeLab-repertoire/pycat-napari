# PyCAT — Implementation Specs for Outstanding Work

*Written against the live tree at **1.6.340**. Every file:line, function name, and current-code
snippet below was read out of the uploaded source, not recalled. Specs are ordered by the audit's
"shortest outstanding-work list": four wrong estimators, the two half-done quantification fixes,
lineage recording, resolver activation, navigator adapter growth, and the small vocab/UI items.*

**Ground rules that apply to every spec below**

- Each **code** change is its own version bump + PyPI push + commit (per your version-bump rule).
  Docs fold forward into the next code change. Group the four estimator fixes as you like, but each
  distinct code change gets a fresh version — do not fold a new fix into a not-yet-pushed number.
- Every scientific fix ships a **golden-master / A-B test** proving the number moved in the right
  direction on a controlled input. The audit's whole thesis is "protect the scientist from a wrong
  belief" — a fix without a test that would have caught the bug is not done.
- The medium/consistency items (Manders naming, etc.) are **not** in scope here; this covers exactly
  the items the audit flagged as outstanding.

---

## PART A — The four wrong estimators (high severity, still live)

### A1. `calculate_autocorrelation` — `Re(FFT)²` → true `|FFT|²` ACF

> **✅ DONE — 1.6.351 (2026-07-25).** Body replaced with `Re(ifft(F·conj(F)))` on the mean-subtracted image
> (signature/shape unchanged; both callers still work). Golden-master `tests/test_autocorrelation_fix.py`:
> Gaussian σ√2-width recovery, translation invariance (old `Re(F)²` was phase-sensitive), cross-check vs
> `autocorrelation_length`. Verified still-live against the 1.6.350 tree before fixing.

**File:** `toolbox/correlation_func_analysis_tools.py:1068`
**Blast radius:** `autocorrelation_analysis` (:1322) → `run_autocorrelation_analysis` (:1442), and the
**entire Spatial-ACF module** via `spatial_acf_tools.sacf_single_roi` (:293, imports
`calculate_autocorrelation` at :88). Every ACF-derived object diameter is currently wrong.

**Current (buggy) core, lines 1095–1108:**
```python
fourier_transform = np.real(np.fft.fft2(image))      # BUG: discards phase
power_spectrum = fourier_transform**2                # BUG: not |F|²; DC never removed
normalized_power_spectrum = power_spectrum / np.max(power_spectrum)
autocorrelation_function = np.fft.ifft2(normalized_power_spectrum)
acf_min, acf_max = np.min(autocorrelation_function), np.max(autocorrelation_function)
normalized_autocorrelation_function = (autocorrelation_function - acf_min) / (acf_max - acf_min)
shifted_autocorrelation = np.real(np.fft.fftshift(normalized_autocorrelation_function))
```

Two defects: (1) `np.real(fft2)` throws away the imaginary part, so this is not the power spectrum;
(2) the mean (DC) is never subtracted, so the flat background dominates the transform.

**The correct form already exists in-repo** — copy its structure from
`spatial_randomness_tools.autocorrelation_length` (:313):
```python
img = img - img.mean()                       # remove DC
F   = np.fft.fft2(img)
acf = np.fft.fftshift(np.real(np.fft.ifft2(F * np.conj(F))))   # |F|² via Wiener–Khinchin
```

**Spec.**

1. Replace the body of `calculate_autocorrelation(image)` with:
   ```python
   img = np.asarray(image, dtype=float)
   img = img - img.mean()                                  # subtract DC / mean
   F   = np.fft.fft2(img)
   acf = np.real(np.fft.ifft2(F * np.conj(F)))             # true autocorrelation
   acf = np.fft.fftshift(acf)                              # zero-lag to centre
   # Normalise to [0,1] for the downstream Gaussian fit + display, preserving peak-at-centre.
   acf_min, acf_max = float(acf.min()), float(acf.max())
   if acf_max - acf_min <= 0:
       return np.zeros_like(acf)
   return (acf - acf_min) / (acf_max - acf_min)
   ```
2. **Do not change the signature or return shape** — `autocorrelation_analysis` slices the returned
   2-D array (`limited_acf_values`, central slice at :1394), and `sacf_single_roi` unpacks
   `sx, sy, _`; both still work because the output is still a normalised, centre-peaked 2-D ACF of the
   same shape.
3. The peak now sits exactly at the centre (`acf.shape // 2`) for both even and odd sizes because
   `fftshift` centres zero-lag; verify `calculate_indices_and_plot_limits` (used at :1391) still
   brackets the centre — it keys off `acf_range` shape, so it is unaffected.
4. **Mean-subtraction interaction with ROI:** `autocorrelation_analysis` crops to the mask bbox
   (`crop_bounding_box`, :1381) and passes the crop. Subtracting the crop mean is correct for a
   bounded ROI. Leave the ROI handling in the caller as-is.

**Test (`tests/test_autocorrelation_fix.py`, new):**
- **Known-Gaussian blob:** synth image = single 2-D Gaussian of known σ on zero background. Fit the
  returned central slice; assert recovered σ within 5% of truth. The old code fails this (phase loss
  distorts the width).
- **Phase sensitivity:** two images identical in magnitude spectrum but different in phase (e.g. a
  blob at centre vs. shifted) must produce **different** ACFs; assert the old-vs-new divergence.
- **Cross-check:** on a clustered binary field, assert the 1/e width from the new
  `calculate_autocorrelation` matches `spatial_randomness_tools.autocorrelation_length` within
  tolerance (the two ACF paths should now agree — closes consistency defect #2).

---

### A2. `orientation_order_parameter` — nematic S measures x-axis alignment, not the director

> **✅ DONE — 1.6.352 (2026-07-25).** `S = |⟨exp(2iθ)⟩|` (the resultant magnitude already computed as
> `mean_resultant`); `circular_variance = 1 − S`; docstring fixed; bbox/brushing contract untouched.
> Golden-master `tests/test_nematic_order.py`: 45° bundle → S>0.95 (old ~0), crossed → S<0.2, random → S<0.25.
> Verified still-live against the 1.6.351 tree before fixing.

**File:** `toolbox/morphological_complexity_tools.py:313`, ensemble block at :363–378.

**Current (buggy), lines 364–378:**
```python
angles = df['orientation_rad'].values
S = float(np.mean(np.cos(2 * angles)))          # BUG: |<cos2θ>| only — axis-referenced
mean_resultant = np.mean(np.exp(2j * angles))   # the CORRECT quantity, computed but unused for S
circ_var = 1 - abs(mean_resultant)
preferred_angle = float(np.angle(mean_resultant) / 2 * 180 / np.pi)
return dict(..., S=abs(S), ...)
```

The nematic order parameter is `S = |⟨e^{2iθ}⟩| = √(⟨cos2θ⟩² + ⟨sin2θ⟩²)`. Fibrils all at 45° give
`cos(90°)=0` → the current code reports **S=0 (isotropic)** for a perfectly aligned bundle. The
correct value is literally the magnitude of `mean_resultant`, already on line 367.

**Spec.**

1. Replace line 365 / the return so `S` is the resultant magnitude:
   ```python
   mean_resultant = np.mean(np.exp(2j * angles))
   S              = float(abs(mean_resultant))       # nematic order parameter, director-invariant
   circ_var       = 1.0 - S
   preferred_angle = float(np.degrees(np.angle(mean_resultant)) / 2.0)
   ...
   return dict(per_object_df=df, S=S, circular_variance=circ_var,
               preferred_angle_deg=preferred_angle, ...)
   ```
   (`circular_variance` is now exactly `1 − S`, which is the correct relationship — keep both since
   downstream/UI may read either.)
2. Fix the **docstring** at :325: replace `S = <cos²θ − 1/2> × 2` with
   `S = |⟨exp(2iθ)⟩| = √(⟨cos2θ⟩² + ⟨sin2θ⟩²)`, and keep the `S=0 isotropic … S=1 aligned` note (now
   true).
3. Do **not** touch the per-object bbox columns (the brushing contract at :338–347) or the empty-df
   guard.

**Test (`tests/test_nematic_order.py`, new):**
- Build a labels mask of elongated ellipses all oriented at 45° → assert `S ≈ 1.0` (old code gives
  ~0). At 0°/90° mix (crossed) → assert `S` low. Uniform random orientations (n large) → `S → 0`.
- Assert `circular_variance == 1 - S` exactly.

---

### A3. Costes thresholded M1/M2 — never reference the opposite channel

> **✅ DONE — 1.6.353 (2026-07-25), shipped with A4.** M1/M2 now cross-reference the channels (ROI-masked,
> nan-guarded) in `coloc/analysis.py`. Note: code moved to the `coloc` package during the coloc
> decomposition; the fix was applied at the new location. Golden-master `tests/test_costes_manders.py`
> (drives the real dispatch): colocalized → ~1, disjoint → not ~1, partial → graded (0,1).

**File:** `toolbox/pixel_wise_corr_analysis_tools.py:1495–1496` (inside the coloc dispatch).

**Current (buggy):**
```python
thresh1, thresh2 = method_functions['Costes Automatic Thresholded M1 & M2'](image1, image2, roi_mask)
costes_m1 = np.round(np.sum(image1[image1 > thresh1]) / np.sum(image1), 4)   # BUG
costes_m2 = np.round(np.sum(image2[image2 > thresh2]) / np.sum(image2), 4)   # BUG
```

M1 must be the fraction of channel-1 intensity **in pixels where channel 2 is above its threshold**,
not where channel 1 is above its own. As written these are self-referential and identically ~1 for
any threshold at the noise floor — not colocalization coefficients.

**Correct definitions (Manders, thresholded):**
```
M1 = Σ_i image1[i]  over pixels where image2[i] > thresh2   /  Σ image1
M2 = Σ_i image2[i]  over pixels where image1[i] > thresh1   /  Σ image2
```
restricted to the ROI.

**Spec.**

1. Replace lines 1495–1496 with an ROI-masked, cross-referenced computation:
   ```python
   if roi_mask is not None:
       roi = roi_mask > 0
   else:
       roi = np.ones(image1.shape, dtype=bool)

   above2 = roi & (image2 > thresh2)          # channel-2 positive pixels
   above1 = roi & (image1 > thresh1)          # channel-1 positive pixels

   denom1 = np.sum(image1[roi]); denom2 = np.sum(image2[roi])
   costes_m1 = np.round(np.sum(image1[above2]) / denom1, 4) if denom1 > 0 else np.nan
   costes_m2 = np.round(np.sum(image2[above1]) / denom2, 4) if denom2 > 0 else np.nan
   ```
2. Ship A3 **with A4** (the threshold search) — M1/M2 are only meaningful once `thresh1/thresh2` come
   from a correct Costes search. Deliver them in the same version.
3. This also lands consistency-defect #1 partially: the "Costes M1" column now means the same class of
   thing as the intensity M1 thresholded-on-both path (:328). Add a one-line CHANGELOG note that
   "Costes M1/M2" now cross-references channels.

**Test (`tests/test_costes_manders.py`, new):**
- Two fully colocalized channels (identical support) with a clean threshold → assert M1,M2 ≈ 1.
- Two disjoint channels (no overlap above thresholds) → assert M1,M2 ≈ 0. Old code returns ~1 for
  both cases (it never looks at the other channel), so this test fails pre-fix.

---

### A4. `costes_thresholding` — OLS instead of orthogonal regression, capped 0.5-unit descent, wrong stop

> **✅ DONE — 1.6.353 (2026-07-25), shipped with A3.** TLS line via PCA (`_costes_tls_line`), full-range
> 256-level descent, `r_below ≤ 0` stop, `(nan,nan)` on degenerate inputs. Code lives in
> `coloc/thresholding.py` (moved during the coloc decomposition). Golden-master
> `tests/test_costes_threshold.py`: threshold not pinned at max on uint16, TLS slope within 10% under
> errors-in-variables noise (OLS under-estimates), degenerate → nan.

**File:** `toolbox/pixel_wise_corr_analysis_tools.py:1020` (search loop :1064–1086).

**Three compounding defects (current code):**
```python
params, _ = scipy.optimize.curve_fit(costes_linear_model, red_flat, green_flat)  # (a) OLS, not TLS
...
threshold = max_intensity
while threshold > min_nonzero_intensity and np.abs(r) > 0.1 and iterations < 50:  # (b),(c)
    mask = (red_flat > threshold) & (green_flat > a*threshold + b)
    if np.any(mask):
        r, _ = scipy.stats.pearsonr(red_flat[~mask], green_flat[~mask])
    threshold -= 0.01                                                             # (b) 50×0.01 = 0.5
    iterations += 1
```
- **(a)** OLS regression of green-on-red is not the orthogonal/total-least-squares line Costes
  requires; it biases the slope `a` and therefore both thresholds.
- **(b)** `threshold -= 0.01`, capped at 50 iterations, descends at most 0.5 intensity units from the
  max. On any uint16 image the threshold never leaves the maximum → returns ≈max.
- **(c)** Stop condition `|r| > 0.1` instead of the Costes criterion "descend until the Pearson r of
  the *below-threshold* population reaches 0 (or ≤0)".

**Spec.** Rewrite `costes_thresholding(red_channel, green_channel, roi_mask)`:

1. **Orthogonal (total least squares) regression** for the intensity line. Implement via PCA on the
   mean-centred (red, green) point cloud — the first principal component is the TLS line:
   ```python
   R = red_flat.astype(float); G = green_flat.astype(float)
   Rm, Gm = R.mean(), G.mean()
   cov = np.cov(np.vstack([R - Rm, G - Gm]))
   evals, evecs = np.linalg.eigh(cov)
   vx, vy = evecs[:, np.argmax(evals)]         # principal axis
   a = vy / vx if abs(vx) > 1e-12 else np.inf  # slope
   b = Gm - a * Rm                             # intercept through the centroid
   ```
2. **Descend across the full intensity range**, not fixed 0.01 steps. Use N steps spanning
   `[min_nonzero, max]` (e.g. 256 levels, or unit steps for integer images):
   ```python
   t_hi = float(min(R.max(), (G.max() - b) / a) if a not in (0, np.inf) else R.max())
   t_lo = float(max(R[R > 0].min(), 0.0))
   for T in np.linspace(t_hi, t_lo, 256):
       below = (R <= T) | (G <= a * T + b)     # sub-threshold population
       if below.sum() < 8:
           continue
       r_below, _ = scipy.stats.pearsonr(R[below], G[below])
       if r_below <= 0:                         # (c) correct Costes stop
           break
   threshold_red   = float(T)
   threshold_green = float(a * T + b)
   return threshold_red, threshold_green
   ```
3. **Guard the degenerate cases** the old code silently mishandled: `a == 0`/`inf`, all-zero channel,
   ROI with < 8 pixels → return `(nan, nan)` and let the caller (A3) emit `nan` M1/M2 rather than a
   fabricated ≈max threshold. The dispatch already handles "no pixels above threshold" with a warning
   at :1524.
4. Keep `costes_linear_model` (:993) for any external caller, but stop using `curve_fit` for the
   threshold line.

**Test (`tests/test_costes_threshold.py`, new):**
- **uint16 regression (the smoking gun):** two channels scaled to 0–60000 with a known colocalized
  core plus independent noise floors. Assert the returned threshold sits **near the noise/signal
  boundary**, not at ≈max. The old code returns ≈max on this input; the new one must not.
- **Slope recovery:** synthetic `green = 1.5*red + noise` → assert TLS slope within 10% of 1.5 (OLS
  would under-estimate under symmetric noise).
- Feed the recovered thresholds into A3 and assert M1,M2 land in [0,1] and are ~1 for the colocalized
  core.

---

## PART B — The two half-done quantification fixes

### B1. Kaplan–Meier: finish left-censoring + censored-aware mean

> **✅ DONE — 1.6.354 (2026-07-25).** Left-censoring (frame-0 tracks folded into censored), RMST mean
> (`mean_lifetime_is_rmst` flag + `n_left/right_censored` counts). **Also found & fixed a residual risk-set
> bug the spec assumed closed:** the decrement only removed censored tracks at exact event times, so
> between-event censoring was never removed (n_at_risk read 3 vs correct 2); replaced with the canonical
> `n_at_risk = sum(durations >= t)`. Golden-master `tests/test_kaplan_meier.py`. Verified still-live against
> the 1.6.353 tree before fixing.

**File:** `toolbox/condensate_physics/survival.py` (moved verbatim from condensate_physics_tools at
1.6.219). The **main** bug (risk set) is fixed:
`n_at_risk -= (n_events + n_censored)` runs. Two documented behaviours remain unimplemented.

**Defect 1 — left-censoring claimed, not implemented.** Docstring (:24) says "Condensates present at
frame 0 are left-censored (unknown birth)", but the loop only computes `censored = (t_end >= total_frames - 1)`
(right only). A track that starts at frame 0 has an unknown true birth, so its observed `duration`
underestimates its lifetime — it must not be treated as a clean uncensored death.

**Defect 2 — mean averages censored lifetimes as complete.** Line (near end):
`df.attrs['mean_lifetime_frames'] = float(durations.mean())` averages right-censored (incomplete)
durations as if the condensate died at the last observed frame → biases the mean low.

**Spec.**

1. **Classify each track into one of three cases** in the collection loop:
   ```python
   t_start = int(grp['frame'].min()); t_end = int(grp['frame'].max())
   duration = t_end - t_start + 1
   born_before_start = (t_start <= 0)                 # left-censored birth
   alive_at_end      = (t_end >= total_frames - 1)    # right-censored death
   # 'uncensored' == birth AND death both observed inside the movie
   right_censored = alive_at_end
   left_censored  = born_before_start and not alive_at_end
   lifetimes.append((duration, right_censored, left_censored))
   ```
2. **Left-censored handling (pragmatic, documented).** True left-censored KM needs a reversed-time
   estimator; the honest, low-risk choice here is to **treat frame-0-born tracks as right-censored
   observations of a *minimum* lifetime** (we know they lived at least `duration`, birth unknown) and
   **exclude them from the event set** — i.e. fold `left_censored` into the censored mask so they
   never count as deaths. Update the docstring to say exactly this ("frame-0 tracks are treated as
   censored: they contribute to the risk set but are never counted as an observed death, because their
   true birth — and thus true lifetime — is unknown"). Do **not** silently keep claiming full
   left-censoring you don't compute.
   ```python
   is_censored = np.array([r or l for (_d, r, l) in lifetimes])   # right OR left → censored
   ```
3. **Censored-aware mean via RMST** (restricted mean survival time = area under the KM curve), which
   is the statistically correct summary when data are censored:
   ```python
   # After building df with time_frames (t) and survival_probability (S):
   t = df['time_frames'].to_numpy(dtype=float)
   S = df['survival_probability'].to_numpy(dtype=float)
   rmst = float(np.trapz(S, t))                       # area under KM curve
   df.attrs['mean_lifetime_frames'] = rmst            # replaces durations.mean()
   df.attrs['mean_lifetime_is_rmst'] = True           # honesty flag for the UI/label
   ```
   Keep `median_lifetime_frames` as-is (first time S ≤ 0.5).
4. Add an `attrs['n_left_censored']` / `attrs['n_right_censored']` count so the UI can show how much
   of the population was censored (anti-black-box: the user sees why the mean is an RMST).

**Test (`tests/test_kaplan_meier.py`, extend if present):**
- **Right-censor risk set (regression guard):** a population where a censored track does not coincide
  with a death time must still be removed from `n_at_risk` at its own time. Assert `n_at_risk`
  monotonic-decreasing and equal to `n_total − cumulative(events+censored)` at each row.
- **RMST vs naive mean:** a population with heavy right-censoring → assert
  `mean_lifetime_frames (RMST) > durations.mean()` (naive under-estimates).
- **Left-censor exclusion:** inject frame-0-born tracks and assert they do not appear as events
  (survival curve does not step down at their duration).

---

### B2. Molecular-counting UI reinstates the selection-effect gate

> **✅ DONE — 1.6.355 (2026-07-25).** Panel R² default 0.999 → 0.0 (step 0.05) + selection-effect tooltip;
> `_on_run` warns when a raised gate drops >10% of traces. Test `tests/test_molecular_counting_ui_default.py`
> (integration). Verified still-live (setValue(0.999)) against the 1.6.354 tree before fixing.

**File:** `toolbox/molecular_counting_tools.py`. The library default is correct
(`count_molecules_pooled(..., r2_min=0.0)`, :313, with a `.. danger::` block documenting that 0.999
inflated the mean 44→77). But the widget builder overrides it back to 0.999:
- `_build_molecular_counting_panel` :491 → `r2_spin.setValue(0.999)`
- `_add_molecular_counting._on_run` passes `r2_min=r2_spin.value()` into `count_molecules_pooled`.

So through the GUI the selection-effect bug is live.

**Spec.**

1. Change the default in `_build_molecular_counting_panel`:
   ```python
   r2_spin.setValue(0.0); r2_spin.setSingleStep(0.05)
   r2_spin.setToolTip(
       "Minimum bleaching-fit R² for a trace to contribute. DEFAULT 0.0 (keep all traces).\n"
       "A high R² gate selects for BRIGHT traces (better SNR fit better), which biases the "
       "pooled population toward high copy number — it inflated a true mean of 44 to 77 in the "
       "module's own test. Raise this only if you understand you are filtering on brightness.")
   ```
2. Leave the spinbox **present and adjustable** — the fix is the default and the tooltip, not removing
   user control (an expert may want to gate on a specific dataset). The anti-black-box move is that the
   tooltip now states the selection effect explicitly.
3. **Optional but recommended (fold into same version):** when `r2_min > 0` and the run drops a
   nontrivial fraction of traces, surface a one-line warning in the result path
   (`_on_run`) — "N of M traces excluded by the R² gate; excluded traces skew low-copy-number" — so a
   raised gate is never silent.

**Test (`tests/test_molecular_counting_ui_default.py`, new):**
- Build the panel headless (the panel builder is Qt-smoke-testable like the others) and assert
  `r2_spin.value() == 0.0`.
- Optional: a route-level test that a mixed low/high-N population run through the UI default recovers a
  mean within tolerance of truth (mirrors the library's danger-block numbers).

*(Note on C5 pooled camera corrections: `count_molecules_pooled` still omits the pedestal / read-noise
handling that `count_molecules_single` performs — `initial_intensity=y[fast]` keeps the pedestal. That
is a deeper estimator change than the UI default and is **not** in this spec set; flag it as a
separate follow-up if you want the pooled path to match the single-trace corrections.)*

---

### B3. Contact angle capped at 90°

> **✅ DONE — 1.6.356 (2026-07-25).** Fixed more deeply than the spec sketch: `arcsin(a/R)` → full-angle
> `θ = arccos((cy − base_row)/R)` (0–180°), **and** the base line changed from the widest row (the equator
> for θ>90, which capped the angle at 90 regardless of the sign fix) to the bottom-most row (the real contact
> line). **The spec's sign convention was inverted** — verified empirically against rasterized known-angle
> droplets: θ<90 has cy>base_row, θ>90 has cy<base_row. Golden-master `tests/test_contact_angle.py` recovers
> 40–135° within 4°. Verified still-live (arcsin cap) against the 1.6.355 tree before fixing.

**File:** `toolbox/invitro/analysis.py:292–294` (also re-exported via `invitro_tools.py`,
`invitro_bf_ui.py`).

**Current:** `theta = degrees(arcsin(min(1, a/R)))` — `arcsin` returns ≤90°, so a hydrophobic droplet
(θ>90°, explicitly claimed reachable in the docstring at :228) is reported as its acute supplement.
The circle fit already yields the centre `cy` and the contact line `base_row`; the sign of
`(cy − base_row)` disambiguates.

**Spec.**

1. After the circle fit (`cx, cy, R = res.x`), compute the base half-width angle and correct the
   quadrant using centre-vs-baseline:
   ```python
   a = base_width / 2.0
   sin_theta = min(1.0, a / max(R, 1e-6))
   theta = float(np.degrees(np.arcsin(sin_theta)))     # acute solution, 0–90°
   # If the circle centre sits ABOVE the contact line (smaller row index in image coords),
   # the cap is less than a hemisphere → acute θ is correct. If the centre sits BELOW the
   # baseline, the droplet bulges past a hemisphere → the true angle is the obtuse supplement.
   if cy > base_row:            # centre below baseline (image rows increase downward)
       theta = 180.0 - theta
   ```
   Confirm the row-direction sign against how `y_b`/`base_row` are defined here (image coords: rows
   increase downward, and `upper = y_b < base_row` selects the arc above the base — so a centre with
   `cy > base_row` lies on the droplet side, indicating θ>90°). Add a comment stating the convention.
2. Update the docstring example to note θ can now exceed 90° and how the sign test resolves it.

**Test (`tests/test_contact_angle.py`, new):**
- Rasterize a circular cap with a **known** contact angle > 90° (centre below the baseline) → assert
  recovered θ within a few degrees of truth (old code returns 180−θ). Repeat for θ<90° (unchanged).

---

## PART C — Record lineage (the highest-leverage substrate fix)

**Problem.** `tag_from_operation` (`utils/tag_registry.py`) is the only path that stamps `op` **and**
records the lineage edge (`mark_derived(layer, source_layer, via=op)`), but it has **zero real call
sites**. So derived/superseded edges are essentially never written, and `tag_resolver`'s
`prefer='head_of_lineage'` and "the labels derived *from this image*" queries fall back to weak
`provenance='raw'` guesses. This is the dependency that gates the resolver (Part D) and the navigator
(Part E) being reliable.

**Two viable mechanisms — spec the additive one first.**

### C1. Stamp lineage at the UI add-sites for the highest-value operations (additive, low-risk)

> **◐ IN PROGRESS — increment 3 DONE, 1.6.403 (2026-07-26); increment 2 DONE, 1.6.399; increment 1 DONE, 1.6.357.**
> **Increment 3** unblocked the in-vitro-fluorescence droplet mask: its producer was an inline `_task` closure
> in `invitro_fluor_ui` (otsu/multiotsu/sauvola/rf/advanced-spot dispatch) with no decorated function to tag.
> Extracted it VERBATIM into `toolbox/invitro/segmentation.py::segment_ivf_droplets` — a named
> `@tags_layer('ivf_droplet_segment', role='labels', target='condensate')` op — and wired
> `tag_from_operation`←pre-processed image at the add-site. Behaviour is pinned by
> `tests/test_ivf_droplet_segmentation.py` (exact per-method output on a fixed scene); the catalog was
> regenerated (+1 op). The TIME-SERIES per-frame segmenter was also done (1.6.405): `segment_stack_per_frame`
> is now `@tags_layer('ts_droplet_segment')` and its panel records lineage — and the feared planner ripple did
> NOT materialise (the full gate stays green with the new op). **VPT bead trajectories done too (1.6.406):**
> `run_vpt_analysis` is now `@tags_layer('bead_track', role=overlay, target=bead)` and the napari adapter tags
> the "Bead Trajectories" layer with a `derived_from` edge to the bead image. **With that, the layer-lineage
> story is complete** — every major output (masks, labels, preprocessed images, droplet masks, trajectories)
> carries op + source. The time-series TRACKED-droplet relabelling was also completed (1.6.407):
> `relabel_stack_by_track` is now `@tags_layer('ts_track_relabel')` and the "TSIVF Tracked Droplets" layer
> carries a `derived_from` edge to the per-frame stack — on reflection it is a distinct analytical output, not
> merely a recolouring. **Every layer-producing operation in the toolbox now records lineage.**
>
> Increment 1 wired `tag_from_operation` at: cellpose→cell labels, subcellular→puncta/refined masks,
> background removal→preprocessed (replacing a raw-string `mark_derived`), preprocess→preprocessed
> (enabling fix: `add_image_with_default_colormap` now RETURNS the created layer). **Increment 2** wired the
> five add-sites whose producer is a REGISTERED op and whose source layer is re-resolvable from its dropdown:
> z-stack 3D background removal (`bg_removal_3d`←vol), 3D cell segmentation (`cellpose_segmentation_3d`←vol),
> 3D condensate segmentation (`segment_subcellular_objects_3d`←pre-processed vol) in `zstack_segmentation_ui`,
> and brightfield/in-vitro condensate masks (`segment_bf_condensates`←enhanced) in `brightfield_ui` /
> `invitro_bf_ui`. Each re-resolves the Layer via `viewer.layers[dropdown.currentText()]` (the input vars held
> only `.data`) and captures the `add_*` return; lineage recording is wrapped best-effort so it can never break
> the output layer. Tests in `tests/test_lineage_recording.py`: a `core` wiring guard + a parametrized mechanism
> test that each producer records op + a `derived_from` edge. **Deferred (NEEDS-DECORATOR / AMBIGUOUS):** VPT
> tracks (only detection is decorated, not linking), in-vitro-fluor & time-series droplet masks (inline
> `_task` producers with no single decorated function).

The layer hook (`layer_tag_hook.install`) already auto-tags `role`/`provenance` on every `add_*`. What
it *cannot* know is the **source layer** — only the UI call site knows "these labels came from *that*
image". `tag_from_operation` exists precisely to record that edge.

**Spec.**

1. Identify the ~10–15 operations whose lineage the resolver most needs — the ones whose outputs feed
   later steps: background removal → preprocessed image; cellpose → cell labels; puncta segmentation →
   puncta mask; VPT detect → bead points/tracks; condensate segmentation → condensate labels. These
   map 1:1 onto the `layer_bindings.json` entries (Part D) and the `_STEP_MAP` batch routes.
2. At each such UI add-site, after building the layer, add one line:
   ```python
   from pycat.utils.tag_registry import tag_from_operation
   new_layer = self.viewer.add_labels(labels, name=...)
   tag_from_operation(new_layer, run_local_thresholding, source_layer=input_layer)
   #                              ^ the DECORATED function that produced it (already @tags_layer)
   #                                                     ^ the layer it was computed from
   ```
   `tag_from_operation` raises `KeyError` if the op isn't registered — so this also *verifies* the
   producing function carries `@tags_layer`. For the ~63 decorated toolbox functions this is already
   true.
3. **Do not** try to convert all 116 add-sites. The hook covers `role`/`provenance` everywhere; only
   the lineage-bearing outputs need the explicit call. Adding it incrementally is safe (each call is
   independent) and matches your "build multi-part changes incrementally, compile after each step"
   rule.
4. Because these outputs now carry `source='pipeline'` (0.95 confidence, fixed in the applied patch
   0001) **and** a `derived_from` edge, the resolver's `head_of_lineage` queries start returning the
   right layer with `certain`/`likely` confidence instead of guessing.

### C2. (Alternative / complement) capture the op from arguments in the hook

If retrofitting call sites proves too spread out, the hook can capture the decorated op from the
**call arguments** rather than the return stack (the current stack-walk misses it because the decorated
transform has already returned by the time `viewer.add_image(result)` runs). This is a larger,
centralized change to `layer_tag_hook.py` and should only be taken if C1's incremental path stalls —
spec it as a follow-up, not first.

**Test (`tests/test_lineage_recording.py`, new):**
- Run a real segmentation UI path headless against a fixture image; assert the output labels layer has
  `op` set with `source='pipeline'`, and that `mark_derived` wrote a `derived_from` edge pointing at
  the input image layer.
- Assert `tag_resolver.resolve(viewer, {'role':'labels','target':'cell'}, prefer='head_of_lineage')`
  returns that layer with `certain`/`likely` (not `ambiguous`) once the edge exists.

---

## PART D — Light up the resolver on the dropdowns (Stage 2 activation)

> **◐ IN PROGRESS — increment 6 DONE, 1.6.408 (2026-07-26); increment 5 DONE, 1.6.404; increment 4 DONE, 1.6.402; increment 3 DONE, 1.6.400; increment 2 DONE, 1.6.398; increment 1 DONE, 1.6.359.**
> **Increment 6:** the FRAP / droplet-fusion / temperature primary stack inputs bind to `common.raw_image`
> (single-movie workflows; provenance-discriminated, degrades to empty when several raw images match). **Remaining:**
> only secondary/ambiguous fields (coloc-as-image channels, FRAP bleach ROI, dark-reference/scribble/control
> dropdowns) that would each need a new key or lack a defensible auto-selection — left for the user.
> **Increment 5** (unblocked by C1 inc 3 / 1.6.403): added `invitro_fluor.droplet_mask` (role=labels,
> target=condensate) and wired the three in-vitro-fluorescence "Droplet mask:" consumers to it. **Remaining:**
> the time-series droplet masks (still blocked on decorating their higher-order producers) and the ambiguous
> coloc-as-image / FRAP / fusion / temperature fields (need NEW keys).
> **Increment 4** (unblocked by the 1.6.401 role fix) wired the mask/labels consumers with target-discriminated
> keys: fixed `puncta_analysis.puncta_mask` (`role: mask`→`labels` — puncta layers ARE labels, so the old key
> matched nothing), added `brightfield.condensate_mask` (`role=labels, target=condensate`), and wired the
> brightfield/in-vitro condensate & droplet mask consumers to it and the brightfield cell-mask consumers to
> `cell_segmentation.cell_labels`. Behavioural test proves a condensate slot and a cell slot never cross-pick when
> both masks coexist. **Remaining:** the fluor/time-series droplet masks (blocked on C1 inc 3 decorating their
> inline producers) and the ambiguous coloc-as-image / FRAP / fusion / temperature fields (need NEW keys).

> **Foundational fix (1.6.401):** wiring the mask/labels bindings surfaced a bug in `layer_tags.mark_derived` —
> it decided a derived layer's role with `if via in ('segment','segmentation')`, but on the `tag_from_operation`
> path `via` is the OP NAME (`bf_segment`, `cellpose`, …), so every segmentation output inherited its source
> image's `role='image'`. That silently broke ALL role-based resolution — the increment-2 target bindings
> (`cell_segmentation.cell_labels`, `puncta_analysis.puncta_mask`) could not match their own layers. Fixed by
> passing the op's declared `produces` role through; segmentation outputs now keep their own `mask`/`labels`
> role. This ACTIVATES the discriminated bindings and unblocks the deferred `common.mask`/`common.labels` work.

> **Increment 3** wired the clearly-labeled raw / preprocessed IMAGE dropdowns across the brightfield, in-vitro
> (BF + fluor), and z-stack panels → `common.raw_image` / `common.preprocessed_image`. These are safe ahead of
> the mask/labels ones: `raw` is provenance-discriminated (`prefer=head_of_lineage`) and BOTH degrade to an empty
> dropdown when several images match, so the resolver never makes a silent wrong pick. Guard
> `test_increment_3_raw_and_preprocessed_image_dropdowns_are_bound`. **Remaining:** the `common.mask`/`common.labels`
> droplet/condensate/cell-mask fields (need per-panel judgment — several masks can coexist so "newest" can mis-pick),
> and the ambiguous coloc-as-image and FRAP/fusion/temperature fields that would need NEW binding keys.
>
> Increment 1 wired the object-based coloc mask dropdowns (`ui_analysis_mixin.py`) →
> `colocalization.channel_a`/`channel_b` (deliberately ambiguous → select nothing, name candidates).
> **Increment 2** wired the highest-value TAG-DISCRIMINATED dropdowns — the ones that match by `target`/`modality`
> (not merely newest), so the mis-selection risk is low and, where several match, the resolver still picks none:
> cell-analysis cell mask → `cell_segmentation.cell_labels` (target=cell, Part-C-backed); puncta measurement
> mask → `puncta_analysis.puncta_mask` (target=punctum, Part-C-backed); cell-segmentation input image →
> `cell_segmentation.input_image`; brightfield inputs (`brightfield_ui`, `invitro_bf_ui`) → `brightfield.input_image`
> (modality=brightfield); VPT bead channel (`vpt/panels.py`) → `vpt.bead_stack`; second invitro-fluor input →
> `invitro_fluor.input_image`. `_layer_row` grew a `binding=` param (forwards to `create_layer_dropdown`); the
> `test_resolver_wired` sweep now covers both entry points. Regression guard extended
> (`test_increment_2_domain_dropdowns_carry_their_bindings`). (Increment 3 continued with the `common.*` raw/
> preprocessed image fields — see the header above.)

**Problem.** The resolver + `layer_bindings.json` (16 entries) + `autopopulate` are complete but
**dormant**: `create_layer_dropdown(..., binding='')` defaults to empty, and **0 of the ~180 call
sites pass `binding=`**. The mechanism is fully built (`ui_modules.py:262`, the `binding` docstring
there describes exactly the tag-query behaviour); it just has no consumers.

**Existing binding keys** (`utils/layer_bindings.json`): `common.{raw_image, preprocessed_image, mask,
labels}`, `cell_segmentation.{input_image, cell_labels}`, `puncta_analysis.{input_image, cell_labels,
puncta_mask}`, `vpt.{bead_stack, host_mask}`, `brightfield.input_image`, `invitro_fluor.input_image`,
`colocalization.{channel_a, channel_b}`.

**Spec.**

1. **Wire the covered dropdowns first.** For every `create_layer_dropdown` call whose field maps to an
   existing binding key, pass `binding='<key>'`. Examples:
   - Cell-analysis input-image dropdown → `binding='cell_segmentation.input_image'`
   - Cell-mask/labels dropdown → `binding='cell_segmentation.cell_labels'`
   - Puncta mask dropdown → `binding='puncta_analysis.puncta_mask'`
   - VPT bead-stack dropdown (`vpt_ui.py:158`) → `binding='vpt.bead_stack'`
   - Coloc channel dropdowns → `binding='colocalization.channel_a'` / `channel_b`
   Note some toolbox UIs have their **own** `create_layer_dropdown` (e.g. `vpt_ui.py:158`,
   `invitro_fluor_ui.py:136` already has a `binding=` param, `frap_ui.py:52`, etc.). For those, thread
   `binding=` through to the shared resolver call the same way `ui_modules` does (autopopulate at the
   `_pycat_binding` gate). Where a toolbox dropdown does **not** yet accept `binding`, add the param
   (mirroring `invitro_fluor_ui.py`, which already has it) and forward it.
2. **Confidence-gated behaviour is already correct** — when several layers match and none is clearly
   right, the resolver selects nothing and reports which matched (the anti-black-box rule). No new
   logic; you are only supplying the `binding=` argument.
3. **Order of value:** the resolver's `head_of_lineage` / derived-layer queries become reliable only
   after Part C records edges. So sequence: land Part C for an operation, then bind the dropdowns that
   consume its output. For raw-image / preprocessed / simple-role bindings (`common.*`), no lineage is
   needed and they can be wired immediately.
4. **Add missing binding keys** for high-traffic fields not yet in the table (e.g. a
   `dynamic_spatial.tracks` or `frap.bleach_roi` entry) as small additive JSON edits — each is a tag
   query + `prefer` + a `why` string, following the existing entries' shape.

**Test (`tests/test_resolver_wired.py`, new):**
- A contract test that asserts a curated set of high-value dropdowns carry a non-empty
  `_pycat_binding` after construction (fails today: 0 bound). This is the "0/180 → N bound" regression
  guard.
- A behaviour test: build a viewer with a tagged cell-labels layer, construct the cell-analysis panel,
  assert the labels dropdown auto-selects it; add a second ambiguous labels layer, assert the dropdown
  selects nothing and the reason string names both (anti-black-box).

---

## PART E — Grow navigator execution adapters + finish the 13-pipeline oracle

**Problem.** The navigator is integrated (`navigator_dock.py`, `home_dock.py`, `executor.run_plan`,
parameter review, templates), but **only 3 execution adapters exist** in `navigator/executor.py`
`_ADAPTERS`:
```python
_ADAPTERS = {
  "image_processing_tools": ExecAdapter("image_processing_tools", "background_removal", _background_removal_params),
  "segmentation_tools":     ExecAdapter("segmentation_tools", _cellpose_step_if_cell, _cellpose_params),
  "feature_analysis_tools": ExecAdapter("feature_analysis_tools", _feature_analysis_step_if_cell, _cell_analysis_params),
}
```
Every other step returns `needs_panel` → "run this from its method panel" (navigator_dock.py:181:
"Auto-running from the guided panel is coming"). So only the cell-analysis pipeline runs end-to-end.

**The batch layer already provides the routes.** `batch_step_registry._STEP_MAP` (:213) has ~40
registered handlers the adapters can bind to, including: `preprocessing`, `calibration_correction`,
`condensate_segmentation`, `condensate_analysis`, `sacf_analysis`, `spatial_metrology`,
`dynamic_spatial`, `organizational_metrics`, `timeseries_condensate_analysis`,
`two_channel_condensate_coloc`, `msd_analysis`, the `bf_*` brightfield steps, the `ivf_*` in-vitro
fluorescence steps, and `zstack_*`. An adapter is the bridge from a navigator plan step to one of these
keys plus a `params_from(intent, ctx, state, reviewed)` function.

**Spec.**

### E1. Add adapters module-by-module, each behind its own test

For each navigator module that maps to a real `_STEP_MAP` route, register an `ExecAdapter`:
```python
ExecAdapter(
    plan_step="<navigator module name as execution_order reports it>",
    batch_step="<_STEP_MAP key>"  OR  lambda intent: "<key>" if <target matches> else None,
    params_from=<fn(intent, ctx, state, reviewed) -> params dict for that handler>,
)
```
Priority order (highest scientific value / most-requested pipelines first):

1. **Condensate pipeline** — `condensate_segmentation` + `condensate_analysis` (mirrors the
   cell-analysis pair already done). Target-gated: the segmenter route depends on
   condensate-vs-cell (the coarse-module callable pattern `_cellpose_step_if_cell` already
   demonstrates this).
2. **Preprocessing / calibration** — `preprocessing`, `calibration_correction` (shared front of most
   pipelines; low-risk, no target branching).
3. **VPT / MSD** — `msd_analysis` (+ the VPT detection/linking route). This is where target-aware
   terminal selection (bead vs condensate) already exists in the planner, so the adapter just needs to
   thread the bead-specific params.
4. **Spatial statistics** — `spatial_metrology`, `dynamic_spatial`, `organizational_metrics`,
   `sacf_analysis` (now that A1 fixed the ACF, `sacf_analysis` produces correct numbers).
5. **Coloc / timeseries / brightfield / in-vitro / z-stack** — the `two_channel_condensate_coloc`,
   `timeseries_condensate_analysis`, `bf_*`, `ivf_*`, `zstack_*` families, as bandwidth allows.

**Each adapter ships with:** a `params_from` unit test (given canned answers/reviewed values, assert
the produced `params` dict matches what the handler reads) and a route-equivalence assertion that the
adapter's `batch_step` resolves to a real `_STEP_MAP` key (there's already `test_batch_step_map.py` and
a `route_equivalence.py` harness to extend).

### E2. Turn on auto-run in the dock once ≥1 full pipeline has adapters end-to-end

`navigator_dock.py:181` currently hard-codes the "coming soon" message. Once a pipeline's every step
has an adapter (cell-analysis already does; condensate next), gate the auto-run path on
`all(has_adapter(s.name) for s in plan.steps)`:
- If every step has an adapter → enable the "Run" action (calls `executor.run_plan`), reporting
  per-step `StepOutcome` (`ran`/`ran_with_caveat`/`blocked`/`needs_panel`/`error`).
- If any step lacks one → keep the "run these from their method panels, in order" guidance for the
  gap, but **run the steps that can run** and clearly mark the manual ones (partial execution is more
  useful than all-or-nothing).

### E3. Finish the exact 13-pipeline oracle

The standalone oracle flags **6 of 13** pipelines `needs_codebase`. To make them exact:
1. Map each `workflow_checklist.py` step key to its handler in `ui/ui_modules.py` /
   `batch_step_registry._STEP_MAP` (the step→function wiring). Many now line up 1:1 with the
   `_STEP_MAP` keys listed above.
2. Add **modality/dimensionality-aware provider selection** to the planner terminals: brightfield vs
   fluorescence (the `bf_*` vs standard routes), 2D vs 3D vs 2D+t (z-stack vs timeseries routes). The
   `modality`/`dimensionality` tags (Part F) are the inputs for this selection.
3. Add **controlled-observable** handling for the instrument workflows (temperature turbidity,
   force-distance rips) so the VPT/temperature/FD pipelines resolve to their specialized terminals.
4. Flip each pipeline from `needs_codebase` to a passing `CanonicalCase` as its adapters land — the
   oracle becomes the regression guard that the navigator reproduces the 13 hardcoded checklists.

**Test:** extend `tests/navigator/` — one passing `CanonicalCase` per pipeline as it gains adapters;
assert `run_plan` on the generated plan produces the same ordered step set as the corresponding
`workflow_checklist` literal (spine-level first, then exact step keys).

---

## PART F — Small vocabulary + UI items

### F1. Tag vocabulary: add `3d+t` dimensionality and `phase`/`DIC` modalities

> **✅ DONE — 1.6.358 (2026-07-25).** Added `3d+t` + `phase`/`DIC`/`trace` to CORE_VALUES; fixed the
> `_tag_layout` classifier (checked `n_t>1` before `n_z>1`, so TZYX → `2d+t`, dropping z) to emit `3d+t`.
> Test `tests/test_dimensionality_3dt.py`. Verified still-live against the 1.6.357 tree.

**File:** `utils/layer_tags.py` (`CORE_VALUES`, :125 and :132).

**Current:**
```python
'dimensionality': {'2d', '2d+t', 'z-stack', 'multi-position'},   # missing 3d+t
'axis_order':     {'YX', 'TYX', 'ZYX', 'TZYX'},                  # already has TZYX
'modality':       {'fluorescence', 'brightfield'},              # missing phase/DIC/trace
```

**Spec (purely additive — the validator accepts new controlled values in one place):**
```python
'dimensionality': {'2d', '2d+t', 'z-stack', 'multi-position', '3d+t'},
'modality':       {'fluorescence', 'brightfield', 'phase', 'DIC', 'trace'},
```
- `3d+t` pairs with the existing `TZYX` axis order (a true volumetric time-lapse). Confirm the loader's
  dimensionality classifier can emit `3d+t` when it sees a 4-D TZYX stack; if it currently downgrades
  such a stack to `z-stack` or `2d+t`, extend the classifier accordingly (small, in `metadata_extract`
  / the load path).
- `phase`/`DIC` distinguish transmitted-light contrast methods from `brightfield`; `trace` covers
  1-D intensity-vs-time data (FCS/molecular-counting traces) that aren't images. These feed the
  navigator's modality-aware provider selection (Part E3).

**Test:** extend the existing `tests/navigator/test_workbook.py` (or the tag-vocab test) to assert the
new values validate and that a TZYX fixture tags as `3d+t`.

### F2. Surface `frame_interval_inconsistent` in the metadata panel

> **✅ DONE — 1.6.358 (2026-07-25).** `detect_contradictions` emits a critical `frame_interval_inconsistent`
> row; `_engine_input` threads the reconciled frame-interval fields from `common` so the panel raises it.
> Cry-wolf preserved. Test `tests/test_frame_interval_contradiction.py`. Verified still-live (flag set at
> metadata_extract.py:1088 but unread by the contradiction engine) against the 1.6.357 tree.

**Background.** The loader patch already sets `common['frame_interval_inconsistent']` (and
`frame_interval_nominal_s`) via `reconcile_frame_interval` (`file_io/metadata_extract.py:834`), but
**no UI reads the flag** — the user never sees that the nominal cadence disagreed with the per-frame
timestamps. There is already a **contradiction framework** built for exactly this class of
"internally inconsistent metadata" surfacing:
- `utils/metadata_contradictions.py` — `detect_contradictions(metadata)` returns `Contradiction`
  records `(pattern, severity, message, fields)`, sorted critical-first, with a per-pattern
  anti-numbing "mark expected" store.
- `ui/metadata_contradiction_panel.py` — `build_contradiction_panel(file_metadata, ...)` renders them
  at the top of the metadata dialog.

**Spec (extend the existing framework, don't build new UI):**

1. In `detect_contradictions` (`metadata_contradictions.py:69`), add a check that reads the reconciled
   flag off the metadata:
   ```python
   if md.get('frame_interval_inconsistent'):
       nominal = md.get('frame_interval_nominal_s')
       actual  = md.get('frame_interval_s')
       out.append(Contradiction(
           pattern='frame_interval_inconsistent', severity='critical',
           message=(f"Declared frame interval {nominal} s disagrees with the interval derived from "
                    f"per-frame timestamps ({actual} s). The timestamp-derived value is used. A wrong "
                    f"interval corrupts every time-derived quantity (D, viscosity, MSD, moduli axis)."),
           fields=('frame_interval_s', 'frame_interval_nominal_s')))
   ```
   `critical` is correct here — unlike the info-level modality-vs-pixels check, a wrong frame interval
   silently corrupts physics (the audit's consistency-defect #8 class). It raises the red button
   indicator via the existing `has_critical` path.
2. No panel change needed — `build_contradiction_panel` already lists whatever `contradiction_rows`
   returns and offers the reversible "expected for this instrument" mark, which is appropriate (some
   instruments legitimately report a nominal interval that differs from measured).
3. Confirm the metadata dict passed to `detect_contradictions` includes the reconciled `common` fields
   (the flag lives in `common['frame_interval_inconsistent']`); if the panel is fed `raw` only, thread
   `common` through.

**Test (`tests/test_frame_interval_contradiction.py`, new):**
- A metadata dict with `frame_interval_inconsistent=True`, nominal 0.1, actual 0.5 → assert
  `detect_contradictions` returns a `critical` `frame_interval_inconsistent` row and `has_critical` is
  True. A consistent file → assert no such row (cry-wolf contract).

### F3. Loader nice-to-haves (low priority, fold into the next relevant code change)

> **DEFERRED (as of 1.6.358).** Both items (promote gain/binning/temperature from `raw['acquisition']` into
> `common`; parse per-frame times for the non-MicroManager TIFF path) are a lower-value display convenience
> needing deeper per-reader loader work; not shipped with F1/F2. Fold into a future loader change.

From `PyCAT_Loader_Assessment.md`, still open and explicitly low-priority:
- **Promote `gain` / `binning` / `temperature`** from `raw['acquisition']` into `common` (they're
  parsed by `parse_description_blob` but not surfaced in the normalized `common` block). Additive to
  `metadata_extract`.
- **Parse per-frame times for the non-MicroManager TIFF path** so `reconcile_frame_interval` can flag
  inconsistency on non-MM files too (today the per-frame derivation only runs on the MM path).

These have no tests-of-record requirement beyond a parse-shape assertion; ship them alongside F2 (same
metadata subsystem) rather than as a standalone docs/no-code change.

---

## Delivery sequencing (each line = one code change = one version + ritual)

1. **A1** ACF fix (isolated, high value, correct form already in-repo).
2. **A2** nematic S (one-line correctness, correct value already computed).
3. **A3+A4** Costes M1/M2 + threshold search (ship together — M1/M2 need a correct threshold).
4. **B1** Kaplan–Meier left-censor + RMST mean.
5. **B2** molecular-counting UI default (+ optional exclusion warning).
6. **B3** contact angle >90°.
7. **C1** lineage recording at the top ~10 UI add-sites (incremental; can be split across versions).
8. **D** resolver activation — bind the covered dropdowns (sequenced after C for lineage-dependent
   ones; `common.*` bindings can go first).
9. **F1** tag vocab (`3d+t`, `phase`/`DIC`/`trace`) + **F2** frame-interval contradiction + **F3**
   loader promotions (metadata subsystem, group them).
10. **E1→E3** navigator adapters, one module family per version, oracle case flipped as each lands;
    **E2** auto-run gate flipped once condensate (2nd full pipeline) has end-to-end adapters.

Each ships changed-files-only as `pycat_<VERSION>_changed.zip` with the 4-line handoff (commit → clean
→ build/check/upload → uninstall/install/run), version bumped in `pyproject.toml`, and a CHANGELOG
entry. The scientific fixes (A1–B3) each carry the golden-master/A-B test that would have caught the
bug — that test is the deliverable as much as the fix.
