"""
Measurement results that carry their own assumptions, provenance, and validity.

The problem this solves
-----------------------
PyCAT returns numbers. A viscosity is a ``float``. A partition coefficient is a
``float``. Nothing attached to that float records:

* **what it rests on** — was the bead radius a manufacturer specification, or a value
  somebody typed in? Stokes-Einstein is ``eta = kT / (6 pi R D)``, so ``eta`` is
  inversely proportional to ``R``: a radius that is 30 % wrong makes the viscosity 30 %
  wrong, silently, and no downstream consumer can tell.
* **whether its assumptions were met** — a viscosity is only a *bulk* viscosity if the
  probes sampled bulk material. A partition coefficient is only meaningful if neither
  phase is saturated.
* **how uncertain it is** — a point estimate with no interval invites over-reading.
* **what stage of validation it has reached** — "this code runs" is not the same claim
  as "this has been checked against a glycerol standard".

A number that cannot answer those questions is not a measurement; it is an output.

The staged model
----------------
Every quantitative result should be able to say where it sits:

    can compute  ->  assumptions checked  ->  uncertainty quantified  ->  interpretable

and separately, what evidence backs the *method itself*:

    IMPLEMENTED  ->  ANALYTICALLY_VALIDATED  ->  SIMULATION_VALIDATED  ->  EXPERIMENTALLY_VALIDATED

These are different axes. A method can be experimentally validated (the glycerol
calibration works) and still produce an *uninterpretable* result on a particular
dataset (because the probes were stuck to the coverslip). Both must be reported.

Provenance of inputs
--------------------
An input's *source* is part of the measurement. ``ParameterSource`` distinguishes:

* ``MANUFACTURER``  — a specification (a bead datasheet). Trustworthy, still has a
  tolerance.
* ``CALIBRATED``    — measured against a standard on this instrument. The strongest.
* ``METADATA``      — read from the acquisition file. Usually reliable, occasionally a
  default the microscope wrote without knowing.
* ``FITTED``        — estimated from the data. **Beware**: a bead radius "fitted" from
  the imaged blob is NOT the physical radius — the blob is broadened by the PSF, so the
  fitted value is systematically too large, and the viscosity correspondingly too small.
* ``ASSUMED``       — a default or a user guess. Usable, but the result inherits it.
* ``UNKNOWN``       — nobody recorded it. The result should say so rather than imply
  confidence it does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pycat.utils.reliability import ReliabilityScore


class ParameterSource(str, Enum):
    """Where an input value came from. Part of the measurement, not metadata."""
    CALIBRATED = "calibrated"        # measured against a standard on this instrument
    MANUFACTURER = "manufacturer"    # a specification sheet
    METADATA = "metadata"            # read from the acquisition file
    FITTED = "fitted"                # estimated from the data (see the warning above)
    ASSUMED = "assumed"              # a default or a guess
    UNKNOWN = "unknown"              # not recorded


class ValidationLevel(str, Enum):
    """What evidence backs the METHOD (not this particular result)."""
    IMPLEMENTED = "implemented"                      # the code runs
    ANALYTICALLY_VALIDATED = "analytically_validated"    # recovers a known closed form
    SIMULATION_VALIDATED = "simulation_validated"        # recovers truth under realistic
                                                         # noise, blur, drift, bleaching
    EXPERIMENTALLY_VALIDATED = "experimentally_validated" # recovers a physical standard


class Interpretability(str, Enum):
    """How far THIS result got along: compute -> assumptions -> uncertainty -> meaning."""
    COMPUTED = "computed"                  # a number exists
    ASSUMPTIONS_CHECKED = "assumptions_checked"
    UNCERTAINTY_QUANTIFIED = "uncertainty_quantified"
    INTERPRETABLE = "interpretable"        # all of the above, and the assumptions HELD
    NOT_INTERPRETABLE = "not_interpretable"  # an assumption failed — the number exists
                                             # but should not be read as the physical
                                             # quantity it is named after


@dataclass
class Parameter:
    """An input value together with where it came from."""
    name: str
    value: float
    units: str = ""
    source: ParameterSource = ParameterSource.UNKNOWN
    uncertainty: Optional[float] = None      # 1-sigma, in `units`
    note: str = ""

    # Some quantities are SUPPOSED to be fitted -- a diffusion coefficient IS the
    # output of an MSD fit, and flagging it as "not independently established" is
    # pedantic noise. Noise is how warnings get ignored. `expected_fitted` marks a
    # parameter whose provenance is legitimately FITTED, so only the parameters that
    # should have come from a specification or a calibration are flagged when they
    # did not.
    expected_fitted: bool = False

    def is_trustworthy(self) -> bool:
        if self.source is ParameterSource.FITTED:
            return bool(self.expected_fitted)
        return self.source in (ParameterSource.CALIBRATED,
                               ParameterSource.MANUFACTURER,
                               ParameterSource.METADATA)


@dataclass
class Assumption:
    """A condition the measurement requires, and whether it was actually checked."""
    name: str
    description: str
    checked: bool = False
    holds: Optional[bool] = None     # None = not checked
    detail: str = ""

    @property
    def status(self) -> str:
        if not self.checked:
            return "UNCHECKED"
        return "HOLDS" if self.holds else "VIOLATED"


@dataclass
class Measurement:
    """A quantitative result that can account for itself.

    Carries the value, its uncertainty, the parameters it depended on (each with a
    provenance), the assumptions it rests on (each with a status), and the validation
    level of the method that produced it.
    """
    name: str
    value: float
    units: str = ""
    ci: Optional[tuple] = None                       # (lo, hi)
    uncertainty: Optional[float] = None              # 1-sigma
    parameters: list = field(default_factory=list)   # list[Parameter]
    assumptions: list = field(default_factory=list)  # list[Assumption]
    validation: ValidationLevel = ValidationLevel.IMPLEMENTED
    notes: list = field(default_factory=list)

    # The Measurement Reliability Index for this number, when it was scored (utils.reliability). REPORTED,
    # never a silent filter: it appears in the summary and the dict, and the user decides. None = not scored.
    reliability: Optional["ReliabilityScore"] = None

    # ---- status -----------------------------------------------------------

    @property
    def violated_assumptions(self):
        return [a for a in self.assumptions if a.checked and a.holds is False]

    @property
    def unchecked_assumptions(self):
        return [a for a in self.assumptions if not a.checked]

    @property
    def untrustworthy_parameters(self):
        return [p for p in self.parameters if not p.is_trustworthy()]

    @property
    def interpretability(self) -> Interpretability:
        if self.violated_assumptions:
            return Interpretability.NOT_INTERPRETABLE
        if self.unchecked_assumptions:
            return Interpretability.COMPUTED
        if self.ci is None and self.uncertainty is None:
            return Interpretability.ASSUMPTIONS_CHECKED
        return Interpretability.INTERPRETABLE

    # ---- reporting --------------------------------------------------------

    def summary(self) -> str:
        """A plain-English account of the number and what it rests on."""
        lines = []
        v = f"{self.name} = {self.value:.4g} {self.units}".rstrip()
        if self.ci and all(x == x for x in self.ci):
            v += f"  [{self.ci[0]:.4g}, {self.ci[1]:.4g}]"
        elif self.uncertainty is not None:
            v += f" ± {self.uncertainty:.3g}"
        else:
            v += "   (no uncertainty reported)"
        if self.reliability is not None:
            v += f"   (reliability: {self.reliability.grade})"
        lines.append(v)

        state = self.interpretability
        lines.append(f"  status: {state.value.replace('_', ' ')}"
                     f"   |  method: {self.validation.value.replace('_', ' ')}")

        # The reliability grade is decomposable: show the worst-first reasons and any factors that could
        # not be assessed, so the number never hides WHY its reliability is what it is.
        if self.reliability is not None:
            for r in self.reliability.reasons:
                lines.append(f"    reliability: {r}")

        for a in self.assumptions:
            mark = {"HOLDS": "ok  ", "VIOLATED": "FAIL", "UNCHECKED": "?   "}[a.status]
            lines.append(f"    [{mark}] {a.name}: {a.description}"
                         + (f" — {a.detail}" if a.detail else ""))

        for p in self.parameters:
            flag = "" if p.is_trustworthy() else "   <-- not independently established"
            u = f" ± {p.uncertainty:.3g}" if p.uncertainty is not None else ""
            lines.append(f"    input: {p.name} = {p.value:.4g}{u} {p.units} "
                         f"({p.source.value}){flag}")

        if state is Interpretability.NOT_INTERPRETABLE:
            names = ", ".join(a.name for a in self.violated_assumptions)
            lines.append(f"  >> An assumption FAILED ({names}). The number exists, but "
                         f"it should not be reported as {self.name}.")
        for n in self.notes:
            lines.append(f"  note: {n}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return dict(
            name=self.name, value=self.value, units=self.units,
            ci=self.ci, uncertainty=self.uncertainty,
            interpretability=self.interpretability.value,
            validation=self.validation.value,
            parameters=[dict(name=p.name, value=p.value, units=p.units,
                             source=p.source.value, uncertainty=p.uncertainty,
                             note=p.note) for p in self.parameters],
            assumptions=[dict(name=a.name, description=a.description,
                              status=a.status, detail=a.detail)
                         for a in self.assumptions],
            notes=list(self.notes),
            reliability=(None if self.reliability is None else dict(
                grade=self.reliability.grade, value=self.reliability.value,
                contributions=dict(self.reliability.contributions),
                reasons=list(self.reliability.reasons),
                missing=list(self.reliability.missing))),
        )
