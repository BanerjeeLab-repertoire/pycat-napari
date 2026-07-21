# Claude Code spec — QC: scan-acquisition aberrations (confocal & spinning disk)

> **✅ STATUS — DONE, shipped in 1.6.157** (stamped 2026-07-20 from a CHANGELOG cross-reference). `scan_qc_tools.py` (shear/bidirectional/disk/crosstalk), `run_scan_qc`, biological-QC composition, tests.

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Adds a QC
category the module does not cover: artifacts produced by the **acquisition geometry itself** rather
than by the optics or the sample. The motivating case is Gable's: on a laser-scanning confocal, a
*mobile* condensate is torn or sheared because it moves during the raster, while a *stable* condensate
in the same frame is clean — so the same image contains both trustworthy and untrustworthy objects.

## Why this is a new category
Verified: `data_qc_tools.py` covers saturation, focus, SNR, vignetting, ghosting, photobleaching,
drift, vibration, spherical aberration, Nyquist, time sampling, chromatic shift. Every one asks about
**the image as a whole** or about **the optics**. None asks *"was this object distorted by the way the
pixels were collected?"*

That question is distinct because:
- A laser-scanning confocal builds a frame **one line at a time**. Two vertically adjacent pixels are
  acquired one line-time apart; the top and bottom of a 40-px object may be separated by tens of
  milliseconds. Anything moving on that timescale is recorded sheared, torn, or smeared — the object's
  shape is a *motion artifact*, not morphology.
- A spinning disk exposes the whole field through a rotating pinhole array. It does not shear, but it
  can imprint **periodic disk structure** if camera exposure is not an integer multiple of the disk
  period, and it suffers **pinhole crosstalk** in bright/dense fields.
- Crucially, **the artifact is per-object, not per-frame.** A stable condensate and a diffusing one in
  the same field have completely different trustworthiness. Every existing QC check would pass this
  image.

## Part 1 — Scan-shear / motion-tearing (laser-scanning confocal)

### The physics to encode
For a raster scan, the acquisition time of a pixel depends on its row: `t(y) ≈ y × line_time`. An
object moving at velocity `v` is displaced by `v × line_time` between successive lines, producing a
**shear along the fast-scan axis proportional to displacement per line**. The signature is therefore:
- a **systematic, monotonic offset of the object's centroid as a function of row** within the object,
- with the shear direction set by the motion direction and the slow-scan axis,
- **absent** in immobile objects **in the same frame** — which is the discriminating fact.

### The check — per object, not per frame
```python
def qc_scan_shear(labels, image, *, line_time_s=None, slow_axis=0) -> dict
```
For each object: fit the per-row centroid of the intensity profile against row index. A stable object
gives a flat fit (slope ≈ 0 within noise); a sheared object gives a **significant, consistent slope**.
Report:
- per-object shear slope (px of lateral offset per row), and, when `line_time_s` is known, the implied
  **velocity in µm/s** — the physically meaningful quantity;
- the **fraction of objects showing significant shear**, which is the frame-level verdict;
- a per-object flag so downstream analysis can exclude sheared objects (composing with the biological
  QC flag mechanism — **flag, do not filter**).

**The in-frame control is what makes this rigorous.** Compare each object's shear slope against the
distribution of slopes from objects in the same frame. If *all* objects shear identically, that is
stage drift or sample flow, not per-object motion — a different diagnosis, and the check should say
so rather than flagging every object as mobile.

### Honest limits (state them in the output)
- Needs enough rows per object to fit a slope — very small objects cannot be assessed; return `na` for
  them rather than a noisy verdict.
- Elongated objects genuinely tilted in the field will show a slope. **Distinguish shear from
  orientation** by checking whether the slope is consistent with the object's own principal axis; if
  the object is simply elongated along a diagonal, that is morphology. Report `ambiguous` rather than
  claiming motion when the two cannot be separated.
- Without `line_time_s` the shear is reported in px/row only — do **not** convert to a velocity using
  an assumed line time. (Follow the pixel-size gate precedent: an unknown calibration yields an
  honest unitless number, never a plausible-looking physical one.)

### Bidirectional-scan mismatch (a second confocal artifact, cheap to add)
Bidirectional scanning acquires alternate lines in opposite directions; a phase mismatch produces a
**comb/interlace artifact** — odd and even rows offset laterally. Detect by cross-correlating the
odd-row and even-row sub-images and reporting the lateral offset. A non-zero systematic offset is a
scanner calibration problem, and it corrupts every measurement in the frame.

## Part 2 — Spinning-disk artifacts

### Disk-pattern residual
If exposure is not an integer multiple of the disk rotation period, the pinhole array leaves a
**periodic striping/honeycomb** in the background. Detect in the frequency domain: a sharp peak at a
spatial frequency corresponding to the pinhole pitch, above the local spectral background. Report the
modulation depth as a percentage of mean background.

**Reuse, don't reinvent:** `qc_vibration` already does spectral peak detection with a hard-won lesson
recorded in its source — a steady drift appeared as a perfect periodic component until it was
detrended. The same trap applies here: **vignetting is a low-frequency gradient that must be removed
before looking for a periodic peak**, or a smoothly-shaded field will read as disk striping. Detrend
first, exactly as `qc_vibration` does.

### Pinhole crosstalk
In dense or bright fields, light from one pinhole leaks through neighbours, raising the apparent
background near bright objects and inflating measured intensity. Detect as an **elevated local
background in the immediate neighbourhood of bright objects relative to distant background**, scaled
by object brightness. Report as a warning that partition coefficients and enrichment ratios will be
biased — the measurements this most directly corrupts.

## Part 3 — Gating: only run what applies
These checks are meaningless on the wrong modality — scan shear on a widefield camera image is noise,
and disk striping on a point-scanner is nonsense.

- Attempt to read modality/`line_time`/`dwell` from metadata. **Verified: `metadata_extract.py`
  currently extracts none of these**, so add them where the format exposes them (CZI, IMS, and OME
  commonly carry scan mode, dwell/line time, and pinhole size).
- When modality is unknown, **do not guess from pixel data.** Use the existing `_not_applicable(name,
  why)` helper to report *"not assessed — acquisition mode unknown"*, and let the user select the
  modality explicitly in the QC UI. An unrun check that says why is honest; a check run on the wrong
  modality produces a confident wrong answer.

## Part 4 — Integration
- Register the new checks in `run_full_qc` with the existing result contract
  (`name/tier/status/value/unit/headline/how/good/diag`) so they appear in `plot_qc_report`
  automatically.
- Follow the module's Image → Assessment → Interpretation → **Recommendation** shape. The
  recommendations here are concrete and worth stating: *"reduce line time or use resonant scanning to
  freeze motion"*, *"acquire this sample on the spinning disk instead"*, *"set exposure to an integer
  multiple of the disk period"*, *"re-calibrate bidirectional scan phase"*.
- Per-object shear flags flow into the biological-QC flag columns and the consolidated table, so a
  condition comparison can be recomputed excluding motion-corrupted objects.

## Tests (`core`, synthetic — no microscope needed)
- **Scan shear:** synthesize a frame by compositing an object rendered at progressively displaced
  positions per row (this *is* the artifact, constructed exactly as the physics describes). Assert the
  measured slope recovers the injected displacement-per-line; assert an immobile object in the same
  frame reports ≈ 0.
- **The discriminating test:** one stable and one sheared object in a single frame — the check must
  flag exactly one. This is the motivating case and the most important assertion.
- **Uniform shear:** all objects sheared identically → reported as drift/flow, not per-object motion.
- **Orientation vs shear:** an elongated, tilted, *immobile* object is not flagged as mobile (or is
  reported `ambiguous`), never confidently called motion.
- **Bidirectional:** an injected odd/even row offset is recovered; an aligned frame reports ≈ 0.
- **Disk pattern:** an injected periodic modulation is detected at the right frequency; a smooth
  vignetted field with no periodicity is **not** flagged (the detrending test).
- **Crosstalk:** an injected halo around bright objects raises the metric; a clean field does not.
- **Gating:** with unknown modality, checks return `na` with a stated reason rather than a verdict.

## Steps
1. Extend `metadata_extract` to capture acquisition mode, line/dwell time, and pinhole size where
   available.
2. `qc_scan_shear` (per-object, with in-frame control) + `qc_bidirectional_phase`.
3. `qc_disk_pattern` (detrended spectral peak) + `qc_pinhole_crosstalk`.
4. Modality gating via `_not_applicable`; explicit user override in the QC UI.
5. Register in `run_full_qc`; recommendations in the existing report shape.
6. Per-object shear flags into the biological-QC flag columns.
7. The synthetic test suite above.
8. Full `pytest -m core` green.
9. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Scan shear is measured **per object**, with an in-frame stable-object control distinguishing motion
  from drift and from mere elongation.
- Bidirectional phase, disk-pattern residual, and pinhole crosstalk are detected with justified
  metrics.
- Checks are gated by modality and report `na` with a reason when it is unknown — never guessed.
- Velocities are only reported when line time is known; otherwise px/row.
- Per-object flags compose with biological QC; nothing is silently excluded.
- Full `pytest -m core` green.

## Cautions
- **The in-frame control is the whole method.** Absolute shear thresholds vary with sample, scan
  speed, and zoom; a per-object slope compared against its neighbours in the same frame is robust.
  Do not use a fixed global threshold.
- **Detrend before spectral tests** — `qc_vibration`'s recorded lesson (a steady drift read as a
  perfect periodic component and sent the user hunting for a pump) applies directly to disk-pattern
  detection versus vignetting.
- **Do not guess modality from pixels.** A confident wrong verdict is worse than "not assessed".
- Do not convert shear to a velocity without a real line time (the pixel-size-gate principle).
- Small objects cannot support a slope fit — return `na`, not a noisy number.
- Flag, never filter — sheared objects are reported so the user can decide.
