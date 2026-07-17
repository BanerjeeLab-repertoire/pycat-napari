"""**Intensity → concentration → free energy, with the validity gate that keeps it honest.**

PyCAT measures intensity *ratios* — partition coefficient, client enrichment — which are unitless and
say "the dense phase is 30× brighter". They cannot say "the dense phase is 40 µM", and they cannot give
the transfer free energy ΔG = −RT ln(K_p) that a manuscript actually wants. That step needs a
**calibration curve**: intensity mapped to apparent molar concentration against a purified standard.

This is the shared authority for that mapping. It is deliberately a standalone module, not buried in
the partition code, because the same curve serves concentration mapping, FCS/N&B brightness, and
ratiometric standards — one calibration, many consumers.

── The one property that matters most ───────────────────────────────────────────────────────

**A calibration curve is only valid under the acquisition it was measured with.** Change the exposure,
the gain, or the laser power and the intensity scale changes, so the curve's slope no longer converts
anything — it just produces a number of the right magnitude that is wrong. That is the exact
"plausible lie" this codebase's contracts forbid (the pixel-size gate, the z-step NaN, the CNR fix).

So every consumer must pass the curve and the image's acquisition metadata through
``check_calibration_validity`` first, and it **fails loud**: a mismatched exposure/gain/laser or a
missing critical field is a HARD BLOCK, not a warning. A concentration computed under an unverifiable
acquisition is never returned as if it were fine — its ``Parameter`` carries the verdict.

Results are ``measurement.Parameter`` (value + units + 1σ + provenance), never bare floats, so a
concentration's uncertainty and trustworthiness travel with it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from typing import Optional

import numpy as np

from pycat.utils.measurement import Parameter, ParameterSource
from pycat.utils.notify import show_warning as _warn


# Gas constant in the units ΔG is reported in. kcal/mol is the manuscript convention (the roadmap's
# `dG_transfer_kcal_mol`); kJ/mol is offered for SI.
_R = {
    'kcal/mol': 1.987204259e-3,   # kcal / (mol·K)
    'kJ/mol': 8.314462618e-3,     # kJ / (mol·K)
}

# Water is liquid from ~273 to 373 K. A "temperature" below this is almost certainly Celsius passed
# where Kelvin was asked for (24 °C → 24 K is physically absurd), and silently using it would poison
# every ΔG. So it is refused loudly rather than absorbed.
_MIN_PLAUSIBLE_TEMPERATURE_K = 150.0


# ── What a curve was measured under, and must match to be reused ─────────────────────────────

@dataclass(frozen=True)
class AcquisitionFingerprint:
    """The acquisition settings that determine the intensity scale.

    Absent fields are ``None`` and stay ``None`` — never a guessed default. The validity gate treats a
    ``None`` on a *critical* field (exposure, channel) as "cannot verify", which fails loud; a ``None``
    on a soft field (gain, laser, pixel size) is a WARN it cannot check, not a silent pass.
    """
    exposure_s: Optional[float] = None
    camera_name: Optional[str] = None
    gain: Optional[float] = None
    channel: Optional[str] = None            # a stable channel/fluorophore label
    laser_power: Optional[float] = None
    pixel_size_um: Optional[float] = None

    @staticmethod
    def from_metadata(meta: dict) -> "AcquisitionFingerprint":
        """Build a fingerprint from a ``metadata_extract`` dict, reusing what it reliably provides.

        ``gain`` and ``laser_power`` are not curated fields there, so they come through absent unless
        the raw block carried them — which is honest, not a gap to paper over.
        """
        meta = meta or {}
        raw = meta.get('raw', {}) if isinstance(meta.get('raw'), dict) else {}

        def _num(*keys):
            for source in (meta, raw):
                for k in keys:
                    v = source.get(k)
                    if v is not None:
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
            return None

        # A channel label the curve can be keyed on: prefer emission wavelength, then excitation.
        channel = None
        for k in ('emission_nm', 'excitation_nm'):
            if meta.get(k) is not None:
                channel = f"{k.split('_')[0]}:{meta[k]}"
                break

        return AcquisitionFingerprint(
            exposure_s=_num('exposure_s'),
            camera_name=meta.get('camera_name'),
            gain=_num('gain', 'Gain', 'camera_gain'),
            channel=channel,
            laser_power=_num('laser_power', 'LaserPower', 'laser_power_mW'),
            pixel_size_um=_num('pixel_size_um'),
        )


# ── The curve ────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CalibrationCurve:
    """A linear intensity→concentration map, with everything needed to use it HONESTLY.

    The fit stats (``n``, ``intensity_mean``, ``intensity_sxx``, ``residual_std``) are kept so the
    concentration's uncertainty can be computed at any intensity — the confidence band of the fitted
    line, not a bare point estimate. The intensity range is kept so extrapolation is flagged rather
    than silently trusted.
    """
    channel: str
    fluorophore: str
    slope: float                      # concentration per intensity unit
    intercept: float
    conc_units: str
    r_squared: float
    acquisition: AcquisitionFingerprint
    created: str                      # ISO-8601 timestamp — the input to drift
    standard_id: str                  # which purified standard / dye
    n: int = 0
    intensity_mean: float = 0.0
    intensity_sxx: float = 0.0        # Σ(I − Ī)²
    residual_std: float = 0.0         # residual standard error of the fit
    intensity_min: float = float('-inf')
    intensity_max: float = float('inf')

    def concentration_se(self, intensity: float) -> float:
        """1σ on the concentration this curve assigns to ``intensity``.

        The confidence band of the fitted line: ``s·√(1/n + (I−Ī)²/Sxx)``. It widens away from the
        centre of the calibration, which is the honest behaviour — the curve is least certain where it
        was least constrained. It does NOT include shot noise on the new measurement itself; that is a
        separate term a caller with a per-pixel error can add.
        """
        if self.n < 3 or self.intensity_sxx <= 0 or self.residual_std <= 0:
            return float('nan')
        return float(self.residual_std * np.sqrt(1.0 / self.n
                     + (intensity - self.intensity_mean) ** 2 / self.intensity_sxx))


# ── The validity gate — the safety property ────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidityVerdict:
    """Whether a curve may be used on an image, and why. ``valid`` False is a HARD BLOCK."""
    valid: bool
    level: str                        # 'ok' | 'warn' | 'invalid'
    reason: str

    def __bool__(self) -> bool:
        return self.valid


# Fractional tolerances: within these two acquisitions are "the same" for calibration purposes.
_EXPOSURE_TOL = 0.02      # 2% — exposure sets the intensity scale directly
_GAIN_TOL = 0.02
_LASER_TOL = 0.05
_PIXEL_TOL = 0.05         # pixel size does not change intensity scale; a mismatch only WARNs


def _relclose(a, b, tol):
    if a is None or b is None:
        return None                   # cannot compare
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom <= tol


def check_calibration_validity(curve: CalibrationCurve, image_metadata: dict) -> ValidityVerdict:
    """May ``curve`` be used on an image with this acquisition metadata? **Fails loud.**

    HARD BLOCK (``valid=False``) on anything that changes the intensity scale or means the wrong curve:
    a different exposure/gain/laser, a different channel/fluorophore, or a *missing* critical field
    (exposure or channel) — because a curve you cannot verify is a curve you cannot trust. A pixel-size
    mismatch only WARNs (it does not change the intensity scale). This is the pixel-size-gate
    philosophy applied to calibration: refuse rather than return a plausible lie.
    """
    fp = curve.acquisition
    img = AcquisitionFingerprint.from_metadata(image_metadata)

    # Channel/fluorophore: the wrong curve entirely.
    if fp.channel is None or img.channel is None:
        return ValidityVerdict(False, 'invalid',
                               "channel not recorded on the curve or the image — cannot confirm this "
                               "is the right curve for this fluorophore")
    if str(fp.channel) != str(img.channel):
        return ValidityVerdict(False, 'invalid',
                               f"channel mismatch: curve was measured on {fp.channel!r}, image is "
                               f"{img.channel!r} — this is a different fluorophore/channel")

    # Exposure: sets the intensity scale directly. Missing => cannot verify => refuse.
    if fp.exposure_s is None or img.exposure_s is None:
        return ValidityVerdict(False, 'invalid',
                               "exposure not recorded on the curve or the image — the intensity scale "
                               "cannot be confirmed to match")
    if not _relclose(fp.exposure_s, img.exposure_s, _EXPOSURE_TOL):
        return ValidityVerdict(False, 'invalid',
                               f"exposure mismatch: curve {fp.exposure_s}s vs image {img.exposure_s}s "
                               f"— intensity is not comparable across exposures")

    # Gain / laser: also scale intensity. Present-and-different => block; absent => warn (can't check).
    for name, cv, iv, tol in (('gain', fp.gain, img.gain, _GAIN_TOL),
                              ('laser power', fp.laser_power, img.laser_power, _LASER_TOL)):
        near = _relclose(cv, iv, tol)
        if near is False:
            return ValidityVerdict(False, 'invalid',
                                   f"{name} mismatch: curve {cv} vs image {iv} — intensity scale differs")

    soft = []
    for name, cv, iv, tol in (('gain', fp.gain, img.gain, _GAIN_TOL),
                              ('laser power', fp.laser_power, img.laser_power, _LASER_TOL),
                              ('pixel size', fp.pixel_size_um, img.pixel_size_um, _PIXEL_TOL)):
        near = _relclose(cv, iv, tol)
        if near is None:
            soft.append(f"{name} not recorded on both — not verified")
        elif near is False:      # only pixel size reaches here (gain/laser blocked above)
            soft.append(f"{name} differs ({cv} vs {iv})")

    if soft:
        return ValidityVerdict(True, 'warn',
                               "usable, but check: " + "; ".join(soft))
    return ValidityVerdict(True, 'ok', "acquisition matches the calibration")


# ── Building and using a curve ─────────────────────────────────────────────────────────────

def build_calibration(intensities, concentrations, acquisition: AcquisitionFingerprint, *,
                      channel: str, fluorophore: str, conc_units: str, standard_id: str,
                      created: str) -> CalibrationCurve:
    """Fit concentration = slope·intensity + intercept against a standard dilution series.

    ``created`` is passed in (an ISO timestamp) rather than read from the clock, so the function is
    pure and testable — and so a curve records when its STANDARD was imaged, which is what drift is
    measured against, not when this code happened to run.
    """
    I = np.asarray(intensities, dtype=float).ravel()
    C = np.asarray(concentrations, dtype=float).ravel()
    if I.size != C.size or I.size < 3:
        raise ValueError("a calibration needs at least 3 paired (intensity, concentration) points")

    slope, intercept = np.polyfit(I, C, 1)
    fit = slope * I + intercept
    ss_res = float(np.sum((C - fit) ** 2))
    ss_tot = float(np.sum((C - C.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float('nan')
    residual_std = float(np.sqrt(ss_res / (I.size - 2))) if I.size > 2 else 0.0

    return CalibrationCurve(
        channel=channel, fluorophore=fluorophore,
        slope=float(slope), intercept=float(intercept), conc_units=conc_units,
        r_squared=r_squared, acquisition=acquisition, created=created, standard_id=standard_id,
        n=int(I.size), intensity_mean=float(I.mean()), intensity_sxx=float(np.sum((I - I.mean()) ** 2)),
        residual_std=residual_std, intensity_min=float(I.min()), intensity_max=float(I.max()))


def intensity_to_concentration(intensity: float, curve: CalibrationCurve, *,
                              name: str = 'concentration') -> Parameter:
    """Convert an intensity to a concentration ``Parameter`` (value + units + 1σ + provenance).

    The provenance is ``CALIBRATED`` — the strongest source ``measurement`` recognises — *unless* the
    intensity is outside the curve's calibrated range, in which case the curve is extrapolating: the
    value is still returned (it may be legitimately just past the last standard) but downgraded to
    ``FITTED`` with a note, so ``is_trustworthy()`` flags it rather than presenting an extrapolation as
    a measurement.
    """
    value = curve.slope * float(intensity) + curve.intercept
    se = curve.concentration_se(float(intensity))

    extrapolating = intensity < curve.intensity_min or intensity > curve.intensity_max
    source = ParameterSource.FITTED if extrapolating else ParameterSource.CALIBRATED
    note = ("intensity is outside the calibrated range "
            f"[{curve.intensity_min:.4g}, {curve.intensity_max:.4g}] — extrapolated"
            if extrapolating else "")

    return Parameter(name=name, value=float(value), units=curve.conc_units, source=source,
                     uncertainty=(None if se != se else float(se)), note=note)


def delta_g_transfer(c_dense, c_dilute, temperature_K: float, *,
                    units: str = 'kcal/mol') -> Parameter:
    """Transfer free energy ΔG = −RT ln(K_p), K_p = C_dense / C_dilute. **Refuses the impossible.**

    ``c_dense`` / ``c_dilute`` may be plain floats or concentration ``Parameter``s; when they are
    ``Parameter``s their 1σ propagates:

        σ_ΔG = RT · √[(σ_dense/C_dense)² + (σ_dilute/C_dilute)²]

    Refuses (raises ``ValueError``) on a non-positive concentration — ``ln`` of a ratio that includes
    zero or negative is undefined, and a saturated dense phase that read as ≤ 0 after background
    subtraction is exactly the case a plausible number would mislead on — and on a temperature below
    ~150 K, which is almost certainly Celsius passed as Kelvin.
    """
    if units not in _R:
        raise ValueError(f"units must be one of {sorted(_R)}, got {units!r}")
    if temperature_K < _MIN_PLAUSIBLE_TEMPERATURE_K:
        raise ValueError(f"temperature_K={temperature_K} is below {_MIN_PLAUSIBLE_TEMPERATURE_K} K — "
                         f"this looks like Celsius passed as Kelvin; aqueous biology is ~273-373 K")

    def _val_sigma(x):
        if isinstance(x, Parameter):
            return float(x.value), (None if x.uncertainty is None else float(x.uncertainty))
        return float(x), None

    cd, sd = _val_sigma(c_dense)
    cl, sl = _val_sigma(c_dilute)
    if cd <= 0 or cl <= 0:
        raise ValueError(f"non-positive concentration (dense={cd}, dilute={cl}) — ΔG is undefined; "
                         f"a saturated or over-subtracted phase cannot be turned into a free energy")

    RT = _R[units] * float(temperature_K)
    kp = cd / cl
    dg = -RT * np.log(kp)

    sigma = None
    if sd is not None and sl is not None:
        sigma = float(RT * np.sqrt((sd / cd) ** 2 + (sl / cl) ** 2))

    return Parameter(name='delta_g_transfer', value=float(dg), units=units,
                     source=ParameterSource.CALIBRATED, uncertainty=sigma,
                     note=f"K_p = {kp:.4g} (C_dense/C_dilute), T = {temperature_K} K")


# ── Persistence — curves outlive a session ─────────────────────────────────────────────────

def save_curve(curve: CalibrationCurve, path) -> None:
    """Write a curve as versioned JSON. Human-readable on purpose — a calibration is a lab record."""
    blob = {'schema': 'pycat.calibration/1', **asdict(curve)}
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(blob, fh, indent=2)


def load_curve(path) -> CalibrationCurve:
    """Read a curve saved by ``save_curve``. Rejects an unknown schema rather than guessing."""
    with open(path, 'r', encoding='utf-8') as fh:
        blob = json.load(fh)
    schema = blob.pop('schema', None)
    if schema != 'pycat.calibration/1':
        raise ValueError(f"unrecognised calibration schema {schema!r} — cannot load safely")
    acq = blob.pop('acquisition', {}) or {}
    return CalibrationCurve(acquisition=AcquisitionFingerprint(**acq), **blob)


def curve_age_days(curve: CalibrationCurve, now_iso: str) -> float:
    """Days between when the curve's standard was imaged and ``now_iso``.

    ``now`` is passed in, not read from the clock — same purity reason as ``build_calibration``. A
    consumer compares this to a staleness window and warns; drift is a first-class concern because a
    calibration decays (lamp aging, detector drift) and a stale curve quietly mis-scales everything.
    """
    from datetime import datetime

    def _parse(s):
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))

    try:
        return (_parse(now_iso) - _parse(curve.created)).total_seconds() / 86400.0
    except Exception:
        return float('nan')
