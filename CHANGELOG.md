## [1.6.94] - 2026-07-17
### Added — **Calibration → concentration → ΔG_transfer: intensity ratios become biophysical parameters.**
The flagship differentiator: PyCAT could say "the dense phase is 30× brighter" but not "the dense
phase is 40 µM" or give the transfer free energy a manuscript wants. New `utils/calibration.py` is the
shared authority for intensity → apparent molar concentration → ΔG = −RT ln(K_p). It is a standalone
module, not buried in the partition code, because one calibration serves many consumers (concentration
mapping, and later FCS/N&B/ratiometric).

**The validity gate is the point, not the arithmetic.** A calibration curve is only valid under the
acquisition it was measured with — change the exposure, gain, or laser and the intensity scale changes,
so the curve's slope converts nothing; it just produces a wrong number of the right magnitude. That is
the exact "plausible lie" this codebase forbids (the pixel-size gate, the z-step NaN, the CNR fix). So
`check_calibration_validity` **fails loud**: a mismatched exposure/gain/laser/channel, or a *missing*
critical field, is a HARD BLOCK — the concentration is not computed. A minor pixel-size difference (it
does not change the intensity scale) only WARNs.

Included in this first slice:
- `CalibrationCurve` + `AcquisitionFingerprint` (built from `metadata_extract`, absent fields honestly
  `None`), `build_calibration` (linear fit + fit statistics), JSON `save_curve`/`load_curve` (versioned
  schema, rejects unknown), `curve_age_days` (drift is first-class — a stale curve mis-scales silently).
- `intensity_to_concentration` → `measurement.Parameter` (µM + 1σ + `CALIBRATED` provenance). The
  uncertainty is the confidence band of the fitted line — it widens away from the calibration's centre,
  the honest behaviour. An intensity outside the calibrated range downgrades to `FITTED` with a note, so
  extrapolation is never presented as a measurement.
- `delta_g_transfer(c_dense, c_dilute, temperature_K)` → `Parameter`, with error propagation
  `σ_ΔG = RT·√[(σ_d/C_d)² + (σ_l/C_l)²]`. **Refuses** a non-positive concentration (ln undefined — a
  saturated/over-subtracted phase must not become a free energy) and a temperature below ~150 K
  (Celsius passed as Kelvin — 24 °C → 24 K is absurd for aqueous biology).
- **One consumer, additively:** `partition_enrichment_tools.client_enrichment` gains optional
  `calibration_curve` / `image_metadata` / `temperature_K`. With a valid curve it reports
  `dense_concentration`, `dilute_concentration`, `Kp_calibrated`, `delta_g_transfer` alongside the
  intensity ratio; a mismatch reports the verdict and computes **no** concentration; with no curve the
  return dict is byte-identical to before.
### Notes
- Every calibrated result is a `measurement.Parameter` (units + uncertainty + provenance), never a bare
  float, so trustworthiness travels with the number.
- All tests are `core` (pure, headless) — the reason calibration is a standalone module. Verified
  end-to-end: a real image yields real-unit K_p and ΔG under a matching acquisition and refuses under a
  mismatched one, leaving no plausible concentration behind.
- **Later increments** (not built here, per the spec): broader consumers (FCS, N&B, ratiometric), the
  consolidated condensate-thermodynamics export table (comparative-phenotyping increment 2), and a
  calibration-curve manager UI.

