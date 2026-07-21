"""**Two typed result envelopes — so a result crossing a module boundary is a TYPE, not a bare dict.**

PyCAT increasingly depends on structured semantic state (identity, provenance, ontology, calibration,
operation context), but it travels between modules as free-form dictionaries with convention-based keys. A
renamed key or a missing field is then a runtime surprise, not a type error, and each consumer (brushing,
batch replay, plotting, publication export) drifts a slightly different set of assumed keys. These two
models formalize the two results worth pinning first.

**They COMPOSE existing types — they invent nothing.** ``AnalysisResult`` carries the measurements table plus
the provenance (`feature_provenance.FeatureProvenance`), calibration (`calibration.CalibrationCurve`) and
operation context PyCAT already produces; ``BatchStepResult`` carries a status, outputs, warnings and a
typed ``PyCATError`` (not a string). Both are **frozen** and **validate at construction**, so an impossible
result (a batch step marked ``error`` with no error, an unknown status) cannot be built at all.

Adoption is incremental and non-breaking: ``to_dict`` / ``from_dict`` bridge the boundary to the dict form
existing code speaks, so a producer can emit the typed object and a not-yet-migrated consumer still reads a
dict. The rich composed fields (provenance, calibration) cross the serialization boundary in their **dict**
form; the in-memory model holds the real typed objects.
"""
from __future__ import annotations

import dataclasses

from pycat.utils.errors import PyCATError, ScientificAssumptionError

#: The allowed batch-step outcomes. `error` ⇔ a `PyCATError` is attached (enforced below).
STATUSES = ('ok', 'warning', 'error', 'skipped')


def _is_frame(x):
    return x is not None and hasattr(x, 'columns') and hasattr(x, 'to_dict')


def _as_plain(x):
    """A dataclass → its dict; anything else (already a dict, or None) unchanged."""
    return dataclasses.asdict(x) if dataclasses.is_dataclass(x) and not isinstance(x, type) else x


@dataclasses.dataclass(frozen=True)
class AnalysisResult:
    """The typed envelope for one analysis operation's output. ``measurements`` is the object table (a
    DataFrame) or ``None``; ``provenance`` is a ``{column: FeatureProvenance}`` map (or its dict form across
    a boundary); ``calibration`` is a ``CalibrationCurve`` (or its dict form) or ``None``."""
    operation_id: str
    entity_type: str
    source_layer_ids: tuple = ()
    measurements: object = None
    artifacts: tuple = ()
    provenance: object = None
    calibration: object = None

    def __post_init__(self):
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ScientificAssumptionError("AnalysisResult needs a non-empty operation_id.")
        if not isinstance(self.entity_type, str) or not self.entity_type.strip():
            raise ScientificAssumptionError("AnalysisResult needs a non-empty entity_type.")
        if not (self.measurements is None or _is_frame(self.measurements)):
            raise ScientificAssumptionError(
                "AnalysisResult.measurements must be a DataFrame or None — not a bare dict; the point of "
                "the envelope is to stop free-form dicts crossing module boundaries.")
        if not (self.provenance is None or isinstance(self.provenance, dict)):
            raise ScientificAssumptionError(
                "AnalysisResult.provenance must be a {column: FeatureProvenance} map or None.")
        object.__setattr__(self, 'source_layer_ids', tuple(self.source_layer_ids or ()))
        object.__setattr__(self, 'artifacts', tuple(self.artifacts or ()))

    def to_dict(self) -> dict:
        return {
            'operation_id': self.operation_id,
            'entity_type': self.entity_type,
            'source_layer_ids': list(self.source_layer_ids),
            'measurements': (self.measurements.to_dict('list') if _is_frame(self.measurements) else None),
            'artifacts': list(self.artifacts),
            'provenance': ({k: _as_plain(v) for k, v in self.provenance.items()}
                           if self.provenance else None),
            'calibration': (_as_plain(self.calibration) if self.calibration is not None else None),
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'AnalysisResult':
        """Rebuild from the boundary dict form. ``measurements`` returns to a DataFrame; ``provenance`` /
        ``calibration`` stay in their dict form (the serialization boundary carries dicts, not the frozen
        `FeatureProvenance` / `CalibrationCurve` objects)."""
        import pandas as pd
        meas = d.get('measurements')
        return cls(
            operation_id=d['operation_id'],
            entity_type=d['entity_type'],
            source_layer_ids=tuple(d.get('source_layer_ids') or ()),
            measurements=(pd.DataFrame(meas) if meas is not None else None),
            artifacts=tuple(d.get('artifacts') or ()),
            provenance=d.get('provenance'),
            calibration=d.get('calibration'))


@dataclasses.dataclass(frozen=True)
class BatchStepResult:
    """The typed envelope for one batch/replay step. ``status`` is one of :data:`STATUSES`; ``error`` is a
    typed ``PyCATError`` (never a string) or ``None``. An ``error`` status and an attached error must agree
    — you cannot construct a step that failed with no error, or carries an error but claims success."""
    status: str
    outputs: tuple = ()
    warnings: tuple = ()
    error: object = None

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ScientificAssumptionError(
                f"BatchStepResult.status must be one of {STATUSES}, got {self.status!r}.")
        if self.error is not None and not isinstance(self.error, PyCATError):
            raise ScientificAssumptionError(
                "BatchStepResult.error must be a typed PyCATError or None — not a string; a stringly-typed "
                "error is exactly the drift this envelope exists to stop.")
        if (self.status == 'error') != (self.error is not None):
            raise ScientificAssumptionError(
                "BatchStepResult status and error must agree: status 'error' iff a PyCATError is attached "
                f"(got status={self.status!r}, error={'present' if self.error is not None else 'None'}).")
        object.__setattr__(self, 'outputs', tuple(self.outputs or ()))
        object.__setattr__(self, 'warnings', tuple(self.warnings or ()))

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'outputs': list(self.outputs),
            'warnings': list(self.warnings),
            'error': (None if self.error is None
                      else {'type': type(self.error).__name__, 'message': str(self.error)}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'BatchStepResult':
        """Rebuild from the boundary dict form. A serialized error returns as a ``PyCATError`` carrying its
        message (the concrete subclass is not preserved across serialization — only the base contract is)."""
        err = d.get('error')
        error = None
        if err is not None:
            error = PyCATError(err.get('message', '') if isinstance(err, dict) else str(err))
        return cls(
            status=d['status'],
            outputs=tuple(d.get('outputs') or ()),
            warnings=tuple(d.get('warnings') or ()),
            error=error)
