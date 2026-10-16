"""``OperationService.execute(op_id, inputs, params) -> AnalysisResult`` — the one execution kernel (Spec 6).

One task, one place. A manual panel, a batch replay, the Navigator and headless code that perform the same
operation all call ``OperationService.execute`` with the same op id, so the operation cannot compute differently
depending on how it was launched (1.7.005 found three cases where it did: the local-threshold k, the brightfield
measurements, the fibril chain).

**Vocabulary.** An op id is the operation-catalog id (``@tags_layer`` / the UI-registered ops / the curated
measure ops) — the same vocabulary the Navigator plans over. A batch *step* is a workflow stage that invokes
operations (``batch_step_registry._STEP_OPERATIONS``); steps and ops are not the same thing and are not renamed.

**Each kernel declares the toolbox function it wraps** (``wraps='module:function'``) and, by default, passes the
caller's ``params`` straight through to it by keyword. A kernel adds no defaults of its own: the function's
signature is the parameter vocabulary, so the kernel and a direct call cannot disagree on a default.
``tests/test_kernel_contract.py`` checks every kernel against the catalog (the wrapped function IS the catalog's
function for that op) and that launchers do not call a kernel's function directly.

Kernels live in :mod:`pycat.kernel.ops`; an op with no kernel raises a clear error rather than being rerouted.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from pycat.utils.errors import ScientificAssumptionError
from pycat.utils.result_models import AnalysisResult


@dataclass(frozen=True)
class KernelSpec:
    op_id: str
    fn: Callable          # (inputs: dict, params: dict) -> AnalysisResult
    wraps: str            # 'module:function' -- the toolbox function that performs the science
    entity: str
    part_of: Optional[str] = None   # a COMPONENT of this plannable catalog op (see register_kernel)


#: op-id -> KernelSpec.
_KERNELS: Dict[str, KernelSpec] = {}


def register_kernel(op_id: str, fn: Callable, *, wraps: str, entity: str = 'image',
                    part_of: Optional[str] = None) -> None:
    """Register the science for one operation. ``fn(inputs, params)`` must return an :class:`AnalysisResult`.

    ``part_of`` names the plannable catalog op this one is a COMPONENT of: a stage or a variant the Navigator
    plans as one operation (the Manders sub-coefficients of colocalization, the per-frame properties that
    trajectory linking reads). The component's own id is still the one every launcher calls, so the panel and
    the batch replay run the same code; the planner sees the whole."""
    if ':' not in wraps:
        raise ValueError(f"wraps must be 'module:function', got {wraps!r}")
    _KERNELS[str(op_id)] = KernelSpec(str(op_id), fn, wraps, entity, part_of)


def resolve(wraps: str) -> Callable:
    """The toolbox function a ``'module:function'`` reference names (imported lazily: the kernel stays light)."""
    module, name = wraps.split(':', 1)
    return getattr(importlib.import_module(module), name)


def _result(op_id: str, entity: str, out) -> AnalysisResult:
    import pandas as pd
    if isinstance(out, pd.DataFrame):
        return AnalysisResult(operation_id=op_id, entity_type=entity, measurements=out, artifacts=())
    if isinstance(out, dict):   # a one-record measure: the table, and the record itself (exact types) as the artifact
        try:
            table = pd.DataFrame([out])
        except (ValueError, TypeError):   # a record pandas cannot lay out as one row stays the artifact only
            table = None
        return AnalysisResult(operation_id=op_id, entity_type=entity, measurements=table, artifacts=(out,))
    return AnalysisResult(operation_id=op_id, entity_type=entity, measurements=None, artifacts=(out,))


def passthrough(op_id: str, wraps: str, *, roles: tuple = ('image',), optional: Optional[dict] = None,
                entity: str = 'image', first: bool = False, part_of: Optional[str] = None) -> None:
    """Register the common kernel: ``fn(*inputs[roles], **optional_inputs, **params)``.

    ``roles`` are the required inputs, in the wrapped function's positional order. ``optional`` maps an optional
    input role to the function's keyword (``{'cell_mask': 'cell_mask'}``). ``first`` keeps element 0 of a tuple
    result (functions that also return diagnostics). A DataFrame or dict result is the measurements table; any
    other result is the artifact.
    """
    optional = dict(optional or {})

    def run(inputs: dict, params: dict) -> AnalysisResult:
        missing = [r for r in roles if r not in inputs]
        if missing:
            raise ScientificAssumptionError(f"{op_id}: missing input(s) {missing}; needs {list(roles)}.")
        kwargs = {kw: inputs[role] for role, kw in optional.items() if inputs.get(role) is not None}
        out = resolve(wraps)(*(inputs[r] for r in roles), **kwargs, **params)
        if first and isinstance(out, (tuple, list)):
            out = out[0]
        return _result(op_id, entity, out)

    register_kernel(op_id, run, wraps=wraps, entity=entity, part_of=part_of)


def named(op_id: str, wraps: str, *, entity: str = 'image', part_of: Optional[str] = None) -> None:
    """Register a kernel that passes inputs AND params to the wrapped function by its own parameter names:
    ``fn(**inputs, **params)``. Inputs are the data (images, masks, tables), params the settings; the split is
    for the reader, the function's signature is the vocabulary."""
    def run(inputs: dict, params: dict) -> AnalysisResult:
        return _result(op_id, entity, resolve(wraps)(**inputs, **params))

    register_kernel(op_id, run, wraps=wraps, entity=entity, part_of=part_of)


class OperationService:
    """The single scientific-execution kernel — see the module docstring."""

    @staticmethod
    def execute(op_id: str, inputs: dict, params: dict = None) -> AnalysisResult:
        """Run operation ``op_id`` on ``inputs`` (arrays / masks / tables, keyed by role) with ``params`` (the
        wrapped function's keywords), returning a typed :class:`AnalysisResult`. Raises
        ``ScientificAssumptionError`` for an op with no kernel — it is reported, never silently rerouted."""
        spec = _KERNELS.get(op_id)
        if spec is None:
            raise ScientificAssumptionError(
                f"No execution kernel registered for operation {op_id!r}. Kernels are migrated one family at a "
                f"time; until this op has one it runs through its own panel/batch code.")
        return spec.fn(dict(inputs or {}), dict(params or {}))

    @staticmethod
    def produce(op_id: str, inputs: dict, params: dict = None):
        """Exactly what the wrapped function returned: the array, the record (dict), the scalar, or the table."""
        result = OperationService.execute(op_id, inputs, params)
        return result.artifacts[0] if result.artifacts else result.measurements

    @staticmethod
    def has_kernel(op_id: str) -> bool:
        return op_id in _KERNELS

    @staticmethod
    def migrated_ops() -> frozenset:
        """Every op with a kernel."""
        return frozenset(_KERNELS)

    @staticmethod
    def spec(op_id: str) -> KernelSpec:
        return _KERNELS[op_id]

    @staticmethod
    def specs() -> Dict[str, KernelSpec]:
        return dict(_KERNELS)


from pycat.kernel import ops  # noqa: E402,F401  -- registers every kernel
