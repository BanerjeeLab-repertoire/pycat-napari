"""VPT **viscosity** from diffusion — the Stokes-Einstein physics, split out of vpt_tools (1.6.235).

viscosity_measurement + viscosity_from_diffusion + viscosity_interval_from_diffusion: convert a bead
diffusion coefficient D (with its CI) into a medium viscosity via eta = kT / (6 pi r D). Moved VERBATIM -
no number changed; pinned by the golden-master viscosity chain (viscosity to 3.2%). The tools module
re-exports all three. (Distinct from condensate_physics' general MSD path - kept separate per the spec.)
"""
from __future__ import annotations

import numpy as np
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info

#: Boltzmann constant (J/K) — the k in the Stokes-Einstein relation eta = kT/(6 pi r D).
_K_BOLTZMANN = 1.38064852e-23


# ---------------------------------------------------------------------------
# 6. Stokes-Einstein viscosity
# ---------------------------------------------------------------------------

def viscosity_measurement(
    D_um2_per_s,
    bead_radius_um,
    temperature_C=24.0,
    radius_source="manufacturer",
    radius_uncertainty_um=None,
    D_ci=None,
    alpha=None,
    n_tracks=None,
    bulk_sampling_checked=False,
    bulk_sampling_holds=None,
    bulk_sampling_detail="",
):
    """Stokes-Einstein viscosity, returned as a Measurement that accounts for itself.

    ``viscosity_from_diffusion`` returns a bare float. That float cannot tell you the
    two things most likely to make it wrong:

    * **Where the bead radius came from.** eta = kT / (6 pi R D), so eta is INVERSELY
      proportional to R. A radius that is 30 % wrong makes the viscosity 30 % wrong,
      silently. In particular, a radius "measured" from the imaged blob is NOT the
      physical radius: the blob is broadened by the PSF, so a fitted optical radius is
      systematically too LARGE and the viscosity correspondingly too SMALL. Only a
      manufacturer specification or a bead-batch calibration should enter
      Stokes-Einstein.

    * **Whether the probes sampled bulk material.** Stokes-Einstein assumes a bead in a
      homogeneous continuum, far from any interface. Excluding beads near the host
      boundary helps, but it does not PROVE bulk sampling: beads can stick, sit in a
      heterogeneous region, or be confined. If that assumption fails, the number is not
      a bulk viscosity, whatever the arithmetic says.

    It also carries the diffusion interval through to a viscosity interval (eta ~ 1/D,
    so the bounds invert), and flags a fitted ``alpha`` far from 1 -- because in the
    viscous-dominated media PyCAT normally measures, the true alpha IS 1, so a fitted
    value far from it usually indicates linking artefacts or D-alpha-sigma covariance
    rather than genuine anomalous diffusion. Stokes-Einstein does not apply if the
    motion is not Brownian.
    """
    from pycat.utils.measurement import (
        Measurement, Parameter, Assumption, ParameterSource, ValidationLevel,
        Interpretability)

    eta = viscosity_from_diffusion(D_um2_per_s, bead_radius_um, temperature_C)

    try:
        src = ParameterSource(str(radius_source).lower())
    except Exception:
        src = ParameterSource.UNKNOWN

    params = [
        Parameter("bead_radius", float(bead_radius_um), "um", src,
                  uncertainty=radius_uncertainty_um,
                  note=("A radius FITTED from the image is the PSF-broadened blob, not "
                        "the bead. It biases the viscosity LOW."
                        if src is ParameterSource.FITTED else "")),
        Parameter("D", float(D_um2_per_s), "um^2/s", ParameterSource.FITTED,
                  note="from the MSD fit", expected_fitted=True),
        Parameter("temperature", float(temperature_C), "C", ParameterSource.ASSUMED),
    ]

    assumptions = [
        Assumption(
            "brownian_motion",
            "the probes undergo simple Brownian diffusion (alpha = 1)",
            checked=alpha is not None,
            holds=(None if alpha is None else bool(0.85 <= float(alpha) <= 1.15)),
            detail=("" if alpha is None else f"fitted alpha = {float(alpha):.2f}"),
        ),
        Assumption(
            "bulk_sampling",
            "the probes sample homogeneous BULK material, away from interfaces",
            checked=bool(bulk_sampling_checked),
            holds=bulk_sampling_holds,
            detail=bulk_sampling_detail,
        ),
        Assumption(
            "physical_probe_radius",
            "the bead radius is a PHYSICAL radius (a specification or a calibration), "
            "not one derived from the imaged blob",
            checked=True,
            # A radius derived from the image is the bead CONVOLVED WITH THE PSF. For a
            # 200 nm bead at ~1.2 NA the PSF is comparable to the bead itself, so the
            # apparent size is dominated by the optics, not the object: you would be
            # measuring the microscope. Using the apparent size as a SANITY CHECK
            # ("does this look like the beads I bought?") is good practice and catches
            # a wrong vial or aggregates; using it as the INPUT to Stokes-Einstein is
            # not. Hence: flagged, but not treated as a fatal violation.
            holds=(src is not ParameterSource.FITTED),
            detail=("the radius was derived from the image. The imaged blob is the bead "
                    "convolved with the PSF, so it is systematically too LARGE and the "
                    "viscosity correspondingly too SMALL. Use the manufacturer's "
                    "specification; compare the apparent size to it as a check, but do "
                    "not feed the apparent size in."
                    if src is ParameterSource.FITTED
                    else f"radius source: {src.value}"),
        ),
    ]

    ci = None
    if D_ci is not None:
        try:
            d_lo, d_hi = float(D_ci[0]), float(D_ci[1])
            if d_lo > 0 and d_hi > 0:
                # eta ~ 1/D: the interval inverts.
                ci = (viscosity_from_diffusion(d_hi, bead_radius_um, temperature_C),
                      viscosity_from_diffusion(d_lo, bead_radius_um, temperature_C))
        except Exception:
            ci = None

    notes = []
    if src in (ParameterSource.ASSUMED, ParameterSource.UNKNOWN):
        notes.append("The bead radius was not independently established (no "
                     "specification or calibration recorded). Since eta is "
                     "proportional to 1/R, the viscosity inherits that uncertainty "
                     "directly.")
    if n_tracks is not None:
        notes.append(f"{int(n_tracks)} tracks contributed.")

    m = Measurement(
        name="viscosity", value=float(eta), units="Pa.s", ci=ci,
        parameters=params, assumptions=assumptions,
        validation=ValidationLevel.EXPERIMENTALLY_VALIDATED,
        notes=notes,
    )
    m.notes.append(
        "Method validation: the VPT chain has been checked against a hand analysis on "
        "real bead data (~8.3 Pa.s). A glycerol/water standard of KNOWN viscosity is "
        "the correct experimental control and is the recommended calibration.")
    return m


def viscosity_interval_from_diffusion(
    D_um2_per_s: float,
    D_ci: tuple,
    bead_radius_um: float,
    temperature_C: float = 24.0,
) -> dict:
    """The viscosity, WITH the interval that the MSD fit actually supports.

    Why this exists
    ---------------
    ``fit_anomalous_diffusion`` now reports a confidence interval on D (1.5.447), and
    ``viscosity_from_diffusion`` then collapsed it back to a single number — **throwing away
    the one quantity that says how much to trust the answer.**

    Stokes-Einstein is ``η = kT / (6πRD)``, so the interval propagates **exactly**, and it
    **inverts**: a LOW D gives a HIGH viscosity. The resulting interval is therefore *not*
    symmetric about the point estimate.

    On the measured MSD intervals (bead radius 0.1 µm, 24 °C):

    ==========  ==========================  ==================================
    lag window  D (95 % CI)                 viscosity (95 % CI)
    ==========  ==========================  ==================================
    30 lags     0.0473 [0.0353, 0.0594]     0.046 Pa·s [0.037, 0.062]  (1.7×)
    4 lags      0.0510 [0.0349, 0.0671]     0.043 Pa·s [0.032, 0.062]  (**1.9×**)
    ==========  ==========================  ==================================

    **A factor of 1.9 between the ends of the interval** — on the number that goes into the
    paper.

    The caveat from 1.5.447 travels with it: the CI on D is honest at long lag windows and
    **over-confident at short ones** (it claims 95 % coverage and delivers 78 % at four lags),
    so the viscosity interval is a *lower bound* on the true uncertainty, not an upper one.

    Parameters
    ----------
    D_um2_per_s : the point estimate of D.
    D_ci : ``(low, high)`` — the 95 % CI on D from ``fit_anomalous_diffusion``'s
        ``identifiability['D_um2_per_s']['ci']``.
    """
    eta = viscosity_from_diffusion(D_um2_per_s, bead_radius_um, temperature_C)

    lo_D, hi_D = (float(D_ci[0]), float(D_ci[1])) if D_ci is not None else (np.nan, np.nan)

    # A non-positive D has no viscosity — the interval is open on that side, which is itself
    # the finding: the data does not exclude an arbitrarily large viscosity.
    unbounded_above = not (np.isfinite(lo_D) and lo_D > 0)

    eta_hi = (viscosity_from_diffusion(lo_D, bead_radius_um, temperature_C)
              if not unbounded_above else float('inf'))
    eta_lo = (viscosity_from_diffusion(hi_D, bead_radius_um, temperature_C)
              if np.isfinite(hi_D) and hi_D > 0 else float('nan'))

    fold = (eta_hi / eta_lo) if (np.isfinite(eta_hi) and np.isfinite(eta_lo)
                                 and eta_lo > 0) else float('inf')

    if unbounded_above:
        napari_show_warning(
            "Viscosity: the confidence interval on D includes zero (or a negative value), so "
            "the viscosity interval is UNBOUNDED ABOVE — the data does not exclude an "
            "arbitrarily large viscosity. Stokes-Einstein is eta = kT/(6*pi*R*D), and eta "
            "diverges as D goes to zero. Report the interval, not the point estimate.")
    elif np.isfinite(fold) and fold > 1.5:
        napari_show_warning(
            f"Viscosity = {eta:.3g} Pa\u00b7s, 95% CI [{eta_lo:.3g}, {eta_hi:.3g}] \u2014 a "
            f"factor of {fold:.1f} between the ends.\n\n"
            f"This is the interval on D from the MSD fit, propagated through "
            f"eta = kT/(6*pi*R*D). It INVERTS: a low D gives a high viscosity, so the "
            f"interval is not symmetric about the point estimate.\n\n"
            f"Note that the CI on D is honest at long lag windows and OVER-CONFIDENT at "
            f"short ones (it claims 95% coverage and delivers 78% at four lags), so this "
            f"interval is a LOWER bound on the true uncertainty.")

    return dict(
        viscosity_Pa_s=float(eta),
        viscosity_ci=(float(eta_lo), float(eta_hi)),
        fold_uncertainty=float(fold),
        unbounded_above=bool(unbounded_above),
        D_um2_per_s=float(D_um2_per_s),
        D_ci=(lo_D, hi_D),
    )


def viscosity_from_diffusion(
    D_um2_per_s: float,
    bead_radius_um: float,
    temperature_C: float = 24.0,
) -> float:
    """
    Stokes-Einstein viscosity: η = kT / (6πRD).

    Parameters
    ----------
    D_um2_per_s : diffusion coefficient (µm²/s) from the MSD fit.
    bead_radius_um : probe bead radius in µm.
    temperature_C : temperature in Celsius.

    Returns
    -------
    eta : viscosity in Pa·s. Returns NaN if D or R is non-positive.

    Notes
    -----
    Unit handling: D is converted µm²/s → m²/s (×1e-12) and R µm → m
    (×1e-6). η = kT / (6πRD) then comes out in Pa·s directly. Equivalently,
    combining the constants gives the 1e18 prefactor seen in the manual
    workflow (1e-12 in D and 1e-6 in R together invert to 1e18 when the
    conversions are folded into a single constant on µm-based inputs).
    """
    if D_um2_per_s <= 0 or bead_radius_um <= 0:
        return float('nan')
    T = temperature_C + 273.15
    D_m2 = D_um2_per_s * 1e-12
    R_m  = bead_radius_um * 1e-6
    eta = _K_BOLTZMANN * T / (6.0 * np.pi * R_m * D_m2)
    return float(eta)
