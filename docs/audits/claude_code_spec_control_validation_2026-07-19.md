# Claude Code spec — Positive/negative control validation workflow

> **✅ STATUS — DONE, shipped in 1.6.156** (stamped 2026-07-20 from a CHANGELOG cross-reference). `control_validation.py` (`validate_against_controls`, `recommend_parameters` refusal case), UI entry, tests.

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Extends the
existing benchmarking harness with the control-comparison workflow the roadmap describes. This is the
"does my segmentation actually work on my data?" question, answered with the user's own controls
rather than synthetic fixtures — a rigor capability with direct manuscript value.

## What exists, and the gap
`toolbox/benchmark_tools.py` already provides real machinery: `run_candidate`, `pixel_overlap`,
`matched_detection`, `basic_metrics`, and `run_benchmark(image, candidates, ground_truth_name=…)`
which scores every candidate against a nominated ground-truth candidate. Verified.

What it cannot do: compare a method's behaviour **across two different images with known opposite
expectations**. That is the control experiment every microscopist actually runs:
- a **positive control** (a sample known to contain the objects) → the method should detect them;
- a **negative control** (untransfected, no-primary-antibody, diffuse, or a dye-only field) → the
  method should detect **nothing**.

A segmentation that scores well on ground truth can still fire on empty fields. The false-positive
rate on a negative control is the number that tells a reviewer the detections are real — and PyCAT
currently cannot produce it.

## Design — `toolbox/control_validation.py`
```python
@dataclass(frozen=True)
class ControlResult:
    method: str
    params: dict
    n_positive: int          # objects found in the positive control
    n_negative: int          # objects found in the negative control  ← should be ~0
    false_positive_rate: float
    positive_density: float  # objects per unit area, for scale-free comparison
    separation: float        # how cleanly the two are distinguished
    verdict: str             # 'usable' | 'marginal' | 'unusable' — with a stated reason

def validate_against_controls(positive_image, negative_image, method, param_grid) -> pd.DataFrame
def recommend_parameters(results) -> ControlResult | None
```
- Sweep the parameter grid on **both** controls with identical settings.
- `recommend_parameters` returns the setting that maximizes positive detection **subject to** the
  negative control staying near zero — not the setting that maximizes detections outright. State the
  rule in the docstring; it is the whole scientific point.
- If **no** setting achieves separation, return `None` with a stated reason. *"No parameter set
  distinguishes your positive from your negative control"* is an extremely valuable finding — it means
  the assay, not the software, needs work. **Do not return a least-bad setting as if it were usable.**

## Honest handling of the hard cases
- **Different exposure/illumination between controls invalidates the comparison.** Check acquisition
  metadata (the calibration module's `AcquisitionFingerprint` already models this) and **warn loudly**
  when the two controls were not acquired comparably. An intensity-threshold method compared across
  mismatched exposures produces a meaningless verdict.
- **Area normalization**: report density (objects per µm²), not raw counts, so controls of different
  field size are comparable. Requires a real pixel size — respect the existing pixel-size gate and
  refuse rather than assuming 1.0.
- **Negative controls are not always empty.** Some legitimately contain autofluorescence or a low
  baseline. Let the user declare an expected negative count (default 0) rather than assuming zero.

## Part B — the report
Produce a small validation report (a DataFrame plus a figure): detections vs parameter value for both
controls on one axis, with the recommended operating point marked and the separation stated. This is
the artifact that goes into a supplementary figure — *"segmentation parameters were chosen to maximize
detection in positive controls while yielding <1% detections in matched negative controls."*

## Tests (`core`, synthetic)
- A synthetic positive (known N objects) and negative (empty) pair: the recommended parameters recover
  ~N with ~0 false positives.
- **The refusal case:** when positive and negative are made statistically indistinguishable, the
  function returns `None` with a reason — never a fabricated recommendation. This is the most
  important test.
- Mismatched acquisition metadata triggers the warning.
- Density normalization: two positives of different field size yield comparable densities.
- A non-empty negative with a declared expected count is handled without flagging everything.

## Steps
1. `toolbox/control_validation.py` — `ControlResult`, `validate_against_controls`,
   `recommend_parameters`.
2. Acquisition-comparability check + loud warning.
3. Density normalization through the existing pixel-size accessor.
4. The report (DataFrame + figure).
5. Tests above, including the refusal case.
6. A UI entry point (pick positive image, negative image, method, grid).
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- A method can be swept across matched positive/negative controls and scored.
- Recommendation maximizes true detection subject to a near-zero negative control — and refuses,
  with a reason, when no setting separates them.
- Mismatched acquisition between controls warns loudly.
- Counts are density-normalized with a real pixel size.
- A report artifact suitable for a supplementary figure is produced.
- Full `pytest -m core` green.

## Cautions
- **Refusing is a valid, valuable answer.** Returning a least-bad parameter set when the controls do
  not separate would launder an assay problem into a software recommendation.
- Do not compare controls acquired under different exposure/gain without warning — the comparison is
  invalid and the verdict meaningless.
- Respect the pixel-size gate; a density in "objects per pixel²" is not a scientific quantity.
- Reuse `benchmark_tools`' existing metrics (`matched_detection`, `basic_metrics`) rather than writing
  parallel scoring — a second scoring implementation would drift.
- The negative control's expected count is user-declared, not assumed zero.
