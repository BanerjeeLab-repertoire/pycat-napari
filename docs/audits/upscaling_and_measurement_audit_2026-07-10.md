# Upscaling audit — what's exposed, what's fixed, what's left

> **Read this first.** The headline conclusion of this audit changed once the
> analysis was pushed further. Partial-volume weighting **fixes the statistics** (no
> more pseudoreplication) but it does **not** rescue a comparison between groups of
> different object size — because the dominant size-dependent bias is **optical, not
> computational**. See *"The correction that matters"* at the end. The measurement
> fix and the **bias advisory** are both needed, and the advisory is the one that
> protects the science.

## The finding

PyCAT's UI **actively steered users into measuring intensities on upscaled images.**
Three dropdowns pre-selected `Upscaled Fluorescence`:

| Location | Purpose | Verdict |
|---|---|---|
| `ui_analysis_mixin.py:40` — *"Select Image for Cell Analysis"* | **measurement** | ❌ **wrong** — feeds `run_cell_analysis_func` (per-cell intensities) |
| `ui_analysis_mixin.py:79` — puncta/condensate analysis image | **measurement** | ❌ **wrong** |
| `ui_segmentation_mixin.py:323` — *"Fluorescence Image to Process"* | **processing** | ✅ fine — processing legitimately wants the upscale |

## What's wrong with measuring on the upscale (all verified numerically)

1. **No information is added.** Tested: upscaling never split two objects that
   native-resolution segmentation merged, at any separation. The **PSF**, not the
   pixel grid, is the resolution limit. Upscaling's only legitimate purpose is to
   satisfy a segmenter's learned object-scale prior — a property of the *algorithm*.

2. **Pseudoreplication.** 4× upscale = 16× "samples", zero new photons. Reported
   SEM came out **1.5× smaller** than the true standard error across noise
   realisations. Falsely confident error bars and p-values.

3. **Size-dependent low bias.** Interpolation blurs background into boundary
   pixels: **−14%** for a 9-px object, **−2%** for a 517-px one. This can
   **manufacture a spurious intensity-vs-size correlation** — a serious hazard for
   condensate work, where intensity-vs-size is a standard plot.

## Why not just downscale the mask?

**Measured: it's *worse* than the status quo for small objects** (bias −16.4 vs
−14.1 at R=2.5 px). Most of a small object's pixels are boundary pixels, and a hard
0/1 call throws away real information.

A native edge pixel at intensity 60, between background 20 and object 100, genuinely
encodes *"I'm about 50% covered."* **Binarising destroys that.** So:

> Upscaling adds no information — but **binarisation destroys** information.
> Partial-volume weighting avoids the destruction without ever reading an
> interpolated pixel.

Verified: PV weights recovered true sub-pixel coverage better than a binary native
mask in **31 of 36 conditions** (object size × PSF width × noise × threshold offset).

## The fix (shipped in 1.5.372)

```
upscale (only to satisfy the segmenter's scale prior)
  → segment
  → map mask to native grid as FRACTIONAL COVERAGE weights
  → measure on the ORIGINAL image, weighted, with effective N
```

New `partial_volume_tools` module:
- `partial_volume_weights(hi_mask, factor)` → per-native-pixel coverage in [0,1]
- `weighted_intensity_stats(image, weights)` → mean / integrated / std / **SEM**
- `effective_n(w)` = (Σw)²/Σw² — Kish effective sample size
- `measure_objects_pv(...)` → regionprops-equivalent that's PV-correct
- New Toolbox tool: **Cell and Object Analyses ▸ Partial-Volume Measurement**
- Cell Analyzer now **warns** when the intensity image is an upscaled layer

### A subtle bug caught during validation

My first SEM was **2.8× too conservative**. Cause: I estimated σ from the *weighted
standard deviation of intensities inside the object* — but that's **2.7× the true
noise**, because it also captures the object's genuine internal structure and the
real intensity gradient across its edge. **That's signal, not measurement error.**

- `std` = how much intensity **varies** inside the object (descriptive; real)
- `σ` = how uncertain each pixel **measurement** is (what an SEM is built from)

Fixed by estimating σ from local pixel-to-pixel differences (Immerkaer-style MAD).
**Result: SEM now calibrated at ratio 1.12–1.19 to the true SE** across every object
size and noise level (slightly conservative — the safe direction), vs the old path's
**1.5× overconfidence**.

## Honest limits

**Small objects are biased low no matter what.** Even a native mask on native data
reads −2.4 at R=2.5 px — the **detector itself** integrates a mix of object and
background photons across an edge pixel. PV weighting minimises the *software-added*
bias; **it cannot undo the optics.** Unbiased absolute intensity on ~2-px objects is
a deconvolution / PSF-modelling problem, not a masking problem. Nobody should claim
otherwise on the strength of this tool.

## Still to wire (deliberately staged)

9 measurement call sites use `regionprops(..., intensity_image=...)`. They can't
express "mask at one scale, image at another" — that's *why* the flawed pattern
exists. Rewiring all of them at once is the kind of sweep that has broken the build
before, so 1.5.372 ships the **validated engine + the standalone tool + the warning**;
the call sites get converted incrementally:

| File | Line(s) | What it measures |
|---|---|---|
| `feature_analysis_tools.py` | 430, 624 | **cell analysis, puncta analysis** ← highest value |
| `segmentation_tools.py` | 1254, 1408 | segmentation-time properties |
| `brightfield_tools.py` | 569 | brightfield OD metrics |
| `invitro_tools.py` | 369 | in-vitro objects |
| `timeseries_invitro_tools.py` | 151 | per-frame object props |
| `zstack_segmentation_tools.py` | 363 | 3-D condensate metrics |
| `label_and_mask_tools.py` | 663 | generic region props |

## Methods-section language

> Segmentation was performed on interpolated images to match the network's
> object-scale prior. Interpolation adds no information and was not used for
> measurement: segmentation boundaries were mapped to the native pixel grid as
> fractional-coverage weights, and all intensity statistics were computed on the
> original detector pixels using weighted estimators, with effective sample sizes
> accounting for fractional pixel coverage.

---

# The correction that matters (added after further testing)

The audit above recommends partial-volume weighting as *the fix*. Further testing
shows that framing is **incomplete, and in one respect wrong.**

## PV weighting does not rescue a comparison

The question that actually matters for comparative biology is not *"how accurate is
my intensity?"* but *"can I trust a difference between two conditions?"* Tested
directly:

> Two groups. **Identical true intensity (100).** They differ only in **size**
> (radius 3 px vs 8 px) — a very common situation, e.g. a treatment that changes
> condensate size but not composition.

| Measurement method | apparent intensity change | Cohen's *d* | *p* |
|---|---|---|---|
| Measured on the upscale (the old default) | **+12.5 %** | 22.3 | 1e-83 |
| Partial-volume, measured on native pixels | **+11.7 %** | 19.2 | 1e-78 |

**Both produce the same false positive.** The truth is *zero difference*.

PV weighting barely helped because **the residual bias is the detector's, not the
software's**: an edge pixel physically integrates a mix of object and background
photons. Better masking cannot undo optics.

## The principle

> **A shared bias LEVEL cancels in a comparison. The bias GRADIENT does not.**

A uniform −14 % divides out of an A/B comparison — this is the standard and correct
intuition, and it is why "we only care about comparisons" is usually a sound
defence. But this bias is **not uniform**: it is −52 % at r=3 px and −11 % at
r=15 px. So any difference in the *size distribution* between two conditions
converts directly into an apparent *intensity* difference.

## Therefore: quantify, don't chase

Since the bias **cannot be removed**, the correct engineering response is to make it
**visible and predictable** rather than to pursue a measurement that cannot be won.

The bias is predictable from quantities measurable in the user's own data:

```
bias ≈ −tanh(0.75 · σ_PSF / R)          (fraction of object-background contrast)
```

Fitted to numerically imaged discs; max error ~5 % of contrast over R = 2–20 px and
σ_PSF = 0.5–2.0 px, saturating (rather than extrapolating to nonsense) where the
object approaches the resolution limit.

Shipped in `partial_volume_tools`:

| Function | Purpose |
|---|---|
| `estimate_psf_sigma(image)` | measure the PSF from the user's own data |
| `intensity_bias_for_size(r, psf)` | predict the dilution for an object of size *r* |
| `is_sub_resolution(r, psf)` | flag objects whose absolute intensity is untrustworthy by **any** method |
| `size_confound_warning(a, b, psf)` | **the important one** — can a size difference between these groups fabricate an intensity difference? |

Validated: `size_confound_warning` correctly flags r=3 vs r=8 as **SEVERE** and
stays quiet for r=6.0 vs r=6.5.

## The demonstration that justifies the whole exercise

Three objects with **identical true intensity (100)**:

| radius | measured | `predicted_bias_pct` | `sub_resolution` |
|---|---|---|---|
| 3.0 px | **72.9** | −52 % | ⚠ **yes** |
| 9.0 px | **91.4** | −19 % | no |
| 15.0 px | **94.9** | −11 % | no |

A textbook intensity-versus-size correlation that **does not exist**. Without the
bias column, a user plots it, believes it, and reports it.

## What is still true from the original audit

* Measuring on upscaled pixels **is** pseudoreplication, and the SEM fix **is** real
  (reported SEM went from **1.5× overconfident** to **calibrated at 1.12–1.19×**,
  i.e. slightly conservative). That matters for any statistical claim.
* Upscaling **does** add no information, and it should never touch a measurement.
* The UI **was** defaulting users into the flawed path, and that is fixed.

The PV machinery is correct and worth keeping. It simply is not sufficient, and it
was a mistake to present it as the whole answer.
