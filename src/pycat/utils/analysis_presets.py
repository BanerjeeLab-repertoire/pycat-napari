"""**Analysis presets — turn "reasonable starting parameters" from tribal knowledge into a declared object.**

Sensible defaults are scattered across function signatures, docstrings, and the maintainer's head; a new
user opening a condensate workflow faces a dozen parameters with no indication of which values suit *their*
data. The knowledge exists but is not offered at the moment of use. This makes a preset a **declared,
inspectable, versioned bundle** — and, crucially, one that cannot smuggle an unaudited parameter set past a
user.

**Two invariants keep a preset honest, and neither is optional:**

- ``provenance`` is mandatory and non-empty. A preset with unstated provenance is just a hidden default
  with a friendly name — and hidden defaults are exactly what the filter-sensitivity programme exists to
  expose. Every preset must say WHERE its numbers came from.
- ``validated=True`` means *"this exact parameter set passed the sensitivity harness"*, not *"someone liked
  it"* — so it REQUIRES a linked ``validation_ref`` (checked at import). An unvalidated preset says so.

**Seed sparsely and honestly.** Only instrument/sample combinations actually run are seeded; an invented
preset for hardware never used carries false authority and is worse than none. Applying a preset
**populates but never locks** the controls, and once the user edits a value the application reports
"modified from <preset>" — a result produced from a modified preset is not the preset's result. Requirement
gating reuses ``operation_spec.runnability`` (the single requirements vocabulary), never a second gate.
"""
from __future__ import annotations

import dataclasses
import inspect


@dataclasses.dataclass(frozen=True)
class AnalysisPreset:
    key: str                          # 'invitro_condensate_confocal_63x'
    display_name: str
    applies_to: str                   # a workflow id in _WORKFLOW_PARAMS (the drift-guard anchor)
    description: str                  # what data this suits, one sentence
    parameters: dict                  # the actual values
    provenance: str                   # MANDATORY: where these numbers came from
    validated: bool = False           # did this exact set pass the sensitivity harness?
    validation_ref: tuple = ()        # the VALIDATED_CASES id(s) backing validated=True
    requirements: tuple = ()          # OperationSpec requirement names (z_stack, pixel_size, time_axis…)
    caveats: tuple = ()


# ── The drift guard's anchor: a workflow's REAL parameter names, from the live function signatures ──
# A preset that claims a workflow must only set parameters that workflow actually has — otherwise the
# preset silently rots when the function changes. These sets are read from the real signatures at import,
# so they cannot drift from the functions themselves.
def _sig_params(fn) -> frozenset:
    return frozenset(inspect.signature(fn).parameters)


def _workflow_params() -> dict:
    from pycat.toolbox.segmentation_tools import puncta_refinement_filtering_func
    from pycat.toolbox.condensate_physics_tools import compute_msd
    return {
        'condensate_puncta_refinement': _sig_params(puncta_refinement_filtering_func),
        'vpt_msd': _sig_params(compute_msd),
    }


ANALYSIS_PRESETS: dict = {p.key: p for p in (
    AnalysisPreset(
        key='condensate_snr_gate_validated',
        display_name='Condensate puncta SNR gate (validated defaults)',
        applies_to='condensate_puncta_refinement',
        description='The shipped signal-to-noise gate for condensate puncta refinement — a safe starting '
                    'point for confocal fluorescence condensate data.',
        parameters={'local_snr_threshold': 1.0, 'global_snr_threshold': 1.0},
        provenance='The shipped SNR-gate defaults, validated by the filter-sensitivity harness for offset, '
                   'scale, and selection-bias invariance (increments 2 and 4) — these thresholds do not '
                   'select for brightness across a plausible sweep.',
        validated=True,
        validation_ref=('segmentation.local_snr_threshold', 'segmentation.global_snr_threshold')),

    AnalysisPreset(
        key='vpt_bead_tracking',
        display_name='VPT bead microrheology (200-frame tracks)',
        applies_to='vpt_msd',
        description='Video particle tracking of diffusing beads for microrheology, requiring long tracks '
                    'so the MSD fit is well-constrained.',
        parameters={'min_track_length': 200},
        provenance='MIN_TRACK_LENGTH_FRAMES=200 is the grounded default for VPT bead tracking (a shorter '
                   'track under-constrains the MSD power-law fit). Starting point for other bead sizes — '
                   'NOT run through the sensitivity harness as a bundle.',
        validated=False,
        requirements=('time_axis',),
        caveats=('A shorter track length trades statistical power for more tracks — verify the MSD fit '
                 'residuals if you lower it.',)),

    AnalysisPreset(
        key='invitro_condensate_confocal_63x',
        display_name='In-vitro condensate, 63×/1.4 confocal (starting point)',
        applies_to='condensate_puncta_refinement',
        description='A starting parameter set for in-vitro reconstituted condensate puncta imaged on a '
                    '63×/1.4 confocal.',
        parameters={'kurtosis_threshold': -3.0, 'local_snr_threshold': 1.0, 'global_snr_threshold': 1.0,
                    'intensity_hwhm_scale': 1.17, 'min_spot_radius': 2},
        provenance='Starting point only — assembled from the shipped defaults, NOT validated as a bundle. '
                   'Adjust for your objective, pixel size, and fluorophore.',
        validated=False,
        requirements=('pixel_size',),
        caveats=('kurtosis_threshold=-3.0 is inert at its default (it can never reject) — see the '
                 'filter-sensitivity findings. min_spot_radius is a raw-pixel gate; check it against your '
                 'pixel size.',)),
)}


def orphan_parameter_keys(preset) -> tuple:
    """The preset's parameter keys that are NOT real parameters of the workflow it claims to apply to — the
    drift guard. An orphan key means the preset has rotted out of sync with the function it configures."""
    valid = _workflow_params().get(preset.applies_to)
    if valid is None:
        return ()                     # unknown workflow id — caught separately by the registry test
    return tuple(k for k in preset.parameters if k not in valid)


def _validate_registry():
    """Enforce the honesty invariants at import: unique non-empty provenance, and validated⇒has a ref."""
    for key, p in ANALYSIS_PRESETS.items():
        if not (p.provenance or '').strip():
            raise ValueError(f"preset {key!r} has empty provenance — a preset must say where its numbers "
                             "came from, or it is just a hidden default with a friendly name")
        if p.validated and not p.validation_ref:
            raise ValueError(f"preset {key!r} is validated=True but carries no validation_ref — "
                             "'validated' must mean the set passed the sensitivity harness, not that it "
                             "looked reasonable")


_validate_registry()


def presets_for(workflow_id) -> list:
    """The presets that apply to a workflow, for a preset picker."""
    return [p for p in ANALYSIS_PRESETS.values() if p.applies_to == workflow_id]


def preset_availability(preset, available) -> tuple:
    """``(can_run, reason)`` for a preset given the session facts, REUSING ``operation_spec.runnability``
    (the single requirements vocabulary and reason phrasing) — never a second gate. ``available`` is a set
    of requirement names the session provides (e.g. ``{'pixel_size', 'time_axis'}``)."""
    from pycat.navigator.operation_spec import runnability

    class _Shim:
        requirements = tuple(preset.requirements)
    return runnability(_Shim(), available)


class PresetApplication:
    """The populate-but-never-lock contract: a preset seeds the values, the user may change any, and the
    application tracks the deviation. A result from a modified preset is NOT the preset's result, so the
    modification state travels into the recorded workflow."""

    def __init__(self, preset):
        self.preset = preset
        self.values = dict(preset.parameters)
        self.modified = set()

    def set(self, key, value):
        """Set a value; mark the key modified if it differs from the preset (or is new)."""
        if key not in self.preset.parameters or self.preset.parameters[key] != value:
            self.modified.add(key)
        else:
            self.modified.discard(key)     # set back to the preset value → no longer a modification
        self.values[key] = value
        return self

    @property
    def is_modified(self) -> bool:
        return bool(self.modified)

    def state_label(self) -> str:
        """What the UI shows: the preset key, or 'modified from <preset>' once the user edits."""
        return f"modified from {self.preset.key}" if self.is_modified else self.preset.key

    def record(self) -> dict:
        """The dict recorded into the workflow (via ``batch_processor.record``) so a session states which
        preset it started from and whether it was modified."""
        return {'preset_key': self.preset.key, 'modified_parameters': sorted(self.modified),
                'is_modified': self.is_modified, 'parameters': dict(self.values)}
