# Claude Code spec — Calibration module + ΔG_transfer (the flagship differentiator)

**Date:** 2026-07-17 · **Target tree:** 1.6.90 · Verified against the 1.6.90 tree. Builds the
calibration→concentration→ΔG_transfer chain that turns PyCAT from "intensity ratios" into "calibrated
biophysical parameters" — the flagship manuscript differentiator. Per Gable's design: calibration is a
**SHARED module callable by other functions** (not ΔG-only), with **drift tests** (calibration decays
over time) and **validity gates** (the microscope metadata must adequately match the calibration
images before proceeding). Large — likely 2–3 increments; this spec defines the architecture + the
first buildable slice. Touches a new `utils/calibration.py`, `partition_enrichment_tools.py`,
`metadata_extract.py` (read-only use). Not `file_io.py`.

## Why, and the gap (verified)
Zero `delta_g` / `standard_curve` / `intensity_to_concentration` code exists. PyCAT has intensity
RATIOS (partition coefficient, client enrichment in `partition_enrichment_tools.py` +
`invitro_tools.py`) but cannot convert intensity → apparent molar concentration → ΔG_transfer. That
conversion is the differentiator: `K_p = C_dense / C_dilute` in real units, `ΔG_transfer = −RT ln(K_p)`.

Two pieces of existing infra this MUST build on (don't reinvent):
- **`utils/measurement.py`** — `Parameter` (value + `units` + `uncertainty` + provenance),
  `ParameterSource`, `ValidationLevel`, `is_trustworthy`. Calibration results are `Parameter`s, not
  raw floats — so a concentration carries its units, its 1σ, and whether it's trustworthy.
- **`file_io/metadata_extract.py`** — captures `exposure_s`, `camera_name`, gain, channel, pixel size.
  These are the inputs to the validity gate (calibration is only valid under matching acquisition).

## The architecture (Gable's three requirements)

### 1. A SHARED calibration module — `utils/calibration.py`
Calibration is useful beyond ΔG (concentration mapping, FCS molecular brightness, N&B calibration,
ratiometric standards). So it lives as a standalone, importable module with a clean API any function
can call — NOT buried in the partition code:
```python
@dataclass(frozen=True)
class CalibrationCurve:
    channel: str
    fluorophore: str
    # intensity -> concentration model (linear or piecewise); store coefficients + fit quality
    slope: float                    # concentration per intensity unit
    intercept: float
    r_squared: float
    conc_units: str                 # 'uM' etc
    # the acquisition fingerprint the curve was measured under (for the validity gate):
    acquisition: AcquisitionFingerprint
    created: str                    # ISO timestamp — for drift
    standard_id: str                # which purified standard / dye

class AcquisitionFingerprint:   # the metadata that MUST match to reuse a curve
    exposure_s: float; camera_name: str; gain: float | None
    channel: str; laser_power: float | None; pixel_size_um: float
    # + whatever metadata_extract reliably provides

def build_calibration(intensities, concentrations, acquisition, **meta) -> CalibrationCurve: ...
def intensity_to_concentration(intensity, curve) -> Parameter:   # returns measurement.Parameter (uM + uncertainty + provenance)
def load_curve(path) / save_curve(curve, path)                   # curves persist as JSON, versioned
```
Design it so `partition_enrichment`, a future FCS module, N&B, etc. all call
`intensity_to_concentration(...)` — one calibration authority, many consumers.

### 2. VALIDITY GATES (Gable: "metadata must match the calibration images adequately to proceed")
A calibration curve is only valid under the acquisition it was measured with. Before ANY consumer uses
a curve, gate it:
```python
def check_calibration_validity(curve, image_metadata) -> ValidityVerdict:
    # compare curve.acquisition against the image's metadata_extract fingerprint:
    #   exposure, gain, camera, channel, laser power, pixel size
    # exposure/gain/laser mismatch => intensity scale differs => curve INVALID (hard block)
    # channel/fluorophore mismatch => wrong curve => INVALID
    # pixel-size mismatch within tolerance => WARN; large => INVALID
    # missing metadata => cannot verify => refuse (fail toward the loud side, per the no-silent-gate contract)
```
- **Hard blocks** (refuse to compute concentration): different exposure/gain/laser (intensity isn't
  comparable), wrong channel/fluorophore, missing critical metadata.
- **Warn** (proceed with a flagged `ValidationLevel`): small pixel-size or minor differences.
- The verdict rides on the returned `Parameter` (`ValidationLevel` + a human reason). A concentration
  computed under a mismatched acquisition must be marked untrustworthy, never silently returned as if
  fine. This is the pixel-size-gate philosophy applied to calibration.

### 3. DRIFT TESTS (Gable: "calibration can drift over time")
Calibration decays — lamp aging, detector drift, alignment. So:
- `CalibrationCurve.created` timestamps every curve; consumers can warn when a curve is older than a
  configurable staleness window.
- A recommended re-calibration cadence + a "calibration age" field on results.
- **Golden-master drift tests** (model on `test_imaging_realism.py` / `test_msd_drift.py`): assert the
  intensity→concentration recovery on a synthetic standard is exact; assert that a curve applied to
  data acquired under a DIFFERENT (drifted) intensity scale is caught by the validity gate, not
  silently used; assert a stale curve raises the age warning. These tests are the guard that
  calibration correctness doesn't regress and that drift is detected, not absorbed.

## First buildable slice (this increment)
1. `utils/calibration.py`: `CalibrationCurve`, `AcquisitionFingerprint`, `build_calibration`,
   `intensity_to_concentration` (→ `measurement.Parameter`), `save/load_curve` (JSON), and
   `check_calibration_validity`.
2. `AcquisitionFingerprint` populated from `metadata_extract` — reuse what's captured
   (`exposure_s`, `camera_name`, pixel size); mark absent fields honestly.
3. ΔG on top: `delta_g_transfer(c_dense, c_dilute, T) -> Parameter` = `−RT ln(C_dense/C_dilute)`,
   propagating uncertainty from the two concentrations; refuse on non-positive/saturated inputs
   (mirror the partition refusal already in `test_imaging_realism`).
4. Wire ONE consumer as proof: `partition_enrichment_tools` gains an OPTIONAL calibrated path — given a
   valid curve, report `K_p` and `ΔG_transfer` in real units alongside the existing intensity ratio;
   without a curve, behave exactly as today (additive).
5. Tests (`core`, pure): intensity→concentration recovers a known standard; the validity gate BLOCKS a
   mismatched-exposure curve and WARNS on a minor mismatch and REFUSES on missing metadata; a stale
   curve warns; ΔG recovers a known value and refuses saturated/non-positive inputs; the calibrated
   partition path matches the intensity ratio when the curve is identity.

## Steps
1. `utils/calibration.py` (the shared module + validity gate).
2. `AcquisitionFingerprint` from `metadata_extract`.
3. `delta_g_transfer` (+ uncertainty propagation + refusal).
4. Optional calibrated path in `partition_enrichment_tools` (additive proof consumer).
5. Drift + validity + ΔG tests (golden-master style).
6. Full `pytest -m core` green (complexity budget).
7. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (calibration module +
   ΔG_transfer, shared/callable, validity-gated, drift-tested).

## Definition of done
- `utils/calibration.py` is a standalone module any function can call for intensity→concentration.
- Every calibrated result is a `measurement.Parameter` (units + uncertainty + provenance + validity).
- The validity gate BLOCKS concentration computation under mismatched acquisition and REFUSES on
  missing metadata (fails loud); minor mismatches WARN with a flagged `ValidationLevel`.
- Drift is timestamped and stale curves warn; golden-master tests prove recovery + drift detection.
- ΔG_transfer computes in real units with uncertainty; refuses saturated/non-positive inputs.
- The partition path gains an additive calibrated output; uncalibrated behaviour unchanged.
- Full `pytest -m core` green.

## Cautions
- Calibration is a SHARED authority — design the API for many consumers (FCS/N&B/ratiometric later),
  do NOT bury it in partition code.
- Results are `measurement.Parameter`, never bare floats — units/uncertainty/validity must travel.
- The validity gate FAILS LOUD (refuse on missing/mismatched metadata) — a concentration under an
  unverifiable acquisition is the exact "plausible lie" the codebase's contracts forbid. This is the
  most important safety property in the spec.
- Additive — the calibrated partition path is optional; no-curve behaviour is byte-unchanged.
- Drift is a first-class concern: timestamp curves, test that drift is DETECTED not absorbed.
- This increment builds the module + ΔG + one consumer. Broader consumers (FCS, N&B, ratiometric) and
  a calibration-curve MANAGER UI are later increments — don't build them here.
