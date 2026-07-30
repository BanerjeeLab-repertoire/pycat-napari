"""The single scientific-execution kernel (Method-Widget Spec 6).

One operation, one place: :class:`~pycat.kernel.operation_service.OperationService` runs a single operation's
science given its inputs and reviewed parameters and returns a typed ``AnalysisResult`` — the SAME computation
whether the caller is batch, the Navigator, a generated panel, a manual panel, or headless. Batch handlers keep
only workflow/persistence concerns (paths, output dirs, naming) around a kernel call; they stop being the de
facto scientific API. Migrated one operation FAMILY at a time, each closing a route-equivalence row.
"""
from pycat.kernel.operation_service import OperationService, register_kernel

__all__ = ["OperationService", "register_kernel"]
