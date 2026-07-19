"""
OperationSpec — a **typed, read-only view** over the live operation vocabulary.

OperationSpec increment 1: **validate-first, generate-nothing.**
================================================================
An architecture audit's headline finding: *one operation's identity is separately encoded in the UI,
the batch system, the Navigator op-catalog, the tag system, and the science function* — five parallel
encodings that can drift. The eventual cure is a canonical ``OperationSpec`` the subsystems are
GENERATED from. This module is **increment 1**: it does NOT generate anything. It defines the spec as
a typed view over the ONE place the identity already lives — the ``@tags_layer`` registry
(``utils.tag_registry._OPERATIONS``) — and the accompanying drift guard
(``tests/navigator/test_operation_spec_matches_catalog.py``) makes any divergence between that live
registry and the committed Navigator snapshot (``data/operation_catalog.json``) a **test failure**.

Once the snapshot is provably faithful to the live decorators (zero drift, enforced), a LATER
increment can flip one subsystem at a time from "validate against the spec" to "generate from the
spec" as a proven-safe change. This increment only makes drift *catchable*.

**No new source of truth.** ``iter_operation_specs()`` reads ``_OPERATIONS`` — the same dict the
``@tags_layer`` decorator and ``_register_ui_operations()`` populate. It adds a type and a stable
iteration order; it invents nothing.

**Increment-1 fields only.** ``OperationSpec`` carries exactly what ``@tags_layer`` already declares.
``inputs`` / ``parameters`` / ``batchable`` / ``requirements`` arrive in a LATER increment *with the
validation that makes them real* — an unpopulated field that nothing checks is exactly the drift this
effort exists to prevent.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from dataclasses import dataclass


# The package root (…/src/pycat). operation_spec.py lives at …/src/pycat/navigator/, so parents[1]
# is the `pycat` package directory — the tree we scan for @tags_layer-bearing modules.
_PKG_ROOT = pathlib.Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class OperationSpec:
    """One registered operation, exactly as its ``@tags_layer`` (or UI) declaration states it.

    A frozen view — mutating an operation's identity means editing the decorator, which is the whole
    point: the code stays the single source of truth.
    """

    id: str                      # the @tags_layer op id (== fn.__pycat_op__)
    role: str                    # the KIND of layer it produces (a tag_registry.ROLES value)
    summary: str                 # one line, for the user / tag inspector
    target: str | None           # what it operates on (a tag_registry.TARGETS value), if specific
    produces: str                # output role (defaults to `role`)
    aliases: tuple[str, ...]     # other names that resolve to this op
    registered_by: str | None    # module.qualname of the implementing fn (or the UI-op registrar)
    # increment 2 ADDS `inputs` — the role(s)/target(s) the op consumes — WITH its validation
    # (tests/navigator/test_operation_graph.py). Together with `produces` it makes the vocabulary a
    # directed graph. Default () keeps every existing consumer and the catalog comparison working.
    inputs: tuple[str, ...] = ()
    # Later increments ADD (with THEIR validation — do NOT add speculatively):
    #   parameters, contexts, batchable, requirements


def _discover_tag_modules() -> list[str]:
    """Dotted paths of every module that APPLIES ``@tags_layer``, found by AST.

    Discovered, not hard-coded, so a NEW decorated module is picked up automatically — a hand-kept
    import list is exactly the kind of thing that silently falls out of date and lets a real
    operation go unvalidated. We match an actual ``@tags_layer(...)`` decorator (an ``ast.Call``
    whose ``func`` is the name ``tags_layer``), so modules that merely mention the name in a string
    or that DEFINE the decorator do not match.
    """
    modules: list[str] = []
    for path in sorted(_PKG_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        applies = any(
            isinstance(dec, ast.Call) and getattr(dec.func, "id", "") == "tags_layer"
            for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
            for dec in node.decorator_list
        )
        if applies:
            rel = path.relative_to(_PKG_ROOT).with_suffix("")
            modules.append("pycat." + ".".join(rel.parts))
    return modules


def _populate_registry() -> list[tuple[str, Exception]]:
    """Make ``_OPERATIONS`` reflect the LIVE vocabulary, headlessly.

    Importing ``tag_registry`` runs ``_register_ui_operations()`` (the 16 UI ops — clahe, hand_drawn,
    the merges…). Importing each discovered module runs its ``@tags_layer`` decorators (the toolbox
    ops). Best-effort per module: a decorator-bearing module that needs Qt/napari at import scope
    would be skipped here (and its ops would then read as catalog drift — a deliberate, visible
    signal). Today NONE of the tag-bearing modules import Qt at module scope, so population is
    complete headlessly; the returned skip list exists so the guard can say so if that ever changes.
    """
    import pycat.utils.tag_registry  # noqa: F401  (import side effect: _register_ui_operations())

    skipped: list[tuple[str, Exception]] = []
    for module in _discover_tag_modules():
        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - Qt/optional-dependency import guard
            skipped.append((module, exc))
    return skipped


def iter_operation_specs() -> list[OperationSpec]:
    """Every registered ``@tags_layer``/UI operation as an :class:`OperationSpec`, id-sorted.

    A typed view over the live ``_OPERATIONS`` registry — it triggers registration (imports the
    tag-bearing modules) and then reads what the decorators declared. It introduces NO new source of
    truth. (Measure/interpret ops — ``op_catalog._measure_ops()`` — are NOT included: those are not
    ``@tags_layer`` operations; increment 1 is the decorator/UI set only.)
    """
    from pycat.utils.tag_registry import list_operations

    _populate_registry()

    specs: list[OperationSpec] = []
    for op, entry in list_operations().items():   # list_operations() already returns id-sorted
        specs.append(OperationSpec(
            id=op,
            role=entry["role"],
            summary=entry.get("summary", ""),
            target=entry.get("target"),
            produces=entry.get("produces", entry["role"]),
            aliases=tuple(entry.get("aliases", ())),
            registered_by=entry.get("registered_by"),
            inputs=tuple(entry.get("inputs", ())),
        ))
    return specs
