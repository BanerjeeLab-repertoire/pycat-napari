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
    # increment 5 ADDS `requirements` — the data/environment preconditions the op needs to be RUNNABLE
    # (a z-stack, a time axis, a pixel size), from the controlled tag_registry.REQUIREMENTS vocabulary
    # — WITH its validation (tests/navigator/test_operation_requirements.py). See `runnability()` below.
    requirements: tuple[str, ...] = ()
    # `module` (dotted, importable) + `function` — the executor coordinates. Populated in BOTH discovery
    # paths (from `registered_by` when live, from the catalog `source`/`function` when read from the
    # JSON), so `resolve_operation()` can import the implementation at CALL time without re-deriving from
    # `registered_by`. `None` only for a UI-op with no real callable. These are a VIEW of the same
    # provenance the catalog already carries — no new source of truth.
    module: str | None = None
    function: str | None = None
    # Later increments ADD (with THEIR validation — do NOT add speculatively):
    #   parameters, contexts, batchable


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


def _module_function_from_registered_by(registered_by):
    """The importable dotted module + function from a live op's ``registered_by``
    (``pkg.mod.qualname``), or the UI-op registrar marker. Mirrors
    ``op_catalog._provenance_from_registered_by`` for the executor's coordinates."""
    rb = registered_by or ""
    if rb.endswith("(UI operation)"):
        return "pycat.utils.tag_registry", "_register_ui_operations"
    parts = rb.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1]), parts[-1]
    return None, (rb or None)


def _dotted_module_from_source(source):
    """The importable dotted module for a catalog entry's ``source`` path
    (``src/pycat/toolbox/foo.py`` → ``pycat.toolbox.foo``). The JSON's ``module`` field
    is the SHORT display name (``foo``), which is not importable — the path is."""
    s = (source or "").replace("\\", "/")
    if s.startswith("src/"):
        s = s[4:]
    if s.endswith(".py"):
        s = s[:-3]
    return s.replace("/", ".") or None


def iter_operation_specs(live: bool = False) -> list[OperationSpec]:
    """Every registered ``@tags_layer``/UI operation as an :class:`OperationSpec`, id-sorted.

    ``live=False`` (default) reads the generated ``operation_catalog.json`` artefact — so the FULL
    operation vocabulary is available **without importing a single science module**. A missing
    optional/specialist dependency (``pywavelets``, a GPU library, …) no longer makes a third of the
    catalog undiscoverable; the operation is *listed*, and becomes *unavailable-with-a-reason* only if it
    is actually run (see :func:`resolve_operation` / :func:`operation_availability`). The generated file
    is kept faithful to the live decorators by the regeneration guard
    (``tests/navigator/test_operation_spec_matches_catalog.py``), which is what makes reading the
    artefact safe.

    ``live=True`` performs the import-and-introspect path: it imports every tag-bearing module to run its
    decorators and reads ``_OPERATIONS`` directly. This is the source of truth the GUARD and the catalog
    GENERATOR (``op_catalog.build_catalog_document``) use — never remove it, they need the import path.

    (Measure/interpret ops — ``op_catalog._measure_ops()`` — are NOT included either way: those are not
    ``@tags_layer`` operations.)
    """
    if not live:
        return _specs_from_catalog()

    from pycat.utils.tag_registry import list_operations

    _populate_registry()

    specs: list[OperationSpec] = []
    for op, entry in list_operations().items():   # list_operations() already returns id-sorted
        module, function = _module_function_from_registered_by(entry.get("registered_by"))
        specs.append(OperationSpec(
            id=op,
            role=entry["role"],
            summary=entry.get("summary", ""),
            target=entry.get("target"),
            produces=entry.get("produces", entry["role"]),
            aliases=tuple(entry.get("aliases", ())),
            registered_by=entry.get("registered_by"),
            inputs=tuple(entry.get("inputs", ())),
            requirements=tuple(entry.get("requirements", ())),
            module=module,
            function=function,
        ))
    return specs


def _specs_from_catalog() -> list[OperationSpec]:
    """Build the specs from the generated catalog JSON — no science imports. The drift guard keeps this
    equal to the ``live=True`` result, so it is a faithful, import-free view of the same vocabulary."""
    from .op_catalog import load_operation_catalog

    specs: list[OperationSpec] = []
    for entry in load_operation_catalog():
        specs.append(OperationSpec(
            id=entry["op"],
            role=entry["role"],
            summary=entry.get("summary", ""),
            target=entry.get("target"),
            produces=entry.get("produces", entry["role"]),
            aliases=tuple(entry.get("aliases", ())),
            registered_by=None,                      # not stored in the JSON; module/function carry it
            inputs=tuple(entry.get("inputs", ())),
            requirements=tuple(entry.get("requirements", ())),
            module=_dotted_module_from_source(entry.get("source")),
            function=entry.get("function"),
        ))
    specs.sort(key=lambda s: s.id)
    return specs


# ── Runnability gating (increment 5) ───────────────────────────────────────────────────────────
# `inputs` are LAYER preconditions, checked against what layers exist (the Capability machinery in
# capabilities.py / contracts.py). `requirements` are DATA/ENVIRONMENT preconditions — is there a time
# axis? a z-stack? a calibrated pixel size? a GPU? — checked against a set of facts the caller knows
# about the current session. These helpers turn that check into a stated reason a UI can show, which is
# the whole point of declaring the field: gate the operation *before* the click, and say why.

def unmet_requirements(spec: "OperationSpec", available) -> tuple[str, ...]:
    """The declared requirements of ``spec`` that ``available`` does not satisfy, in declared order.

    ``available`` is any container of requirement names the session currently provides (e.g.
    ``{'z_stack', 'pixel_size'}``). Empty result ⇒ the operation is runnable.
    """
    have = set(available or ())
    return tuple(req for req in spec.requirements if req not in have)


def runnability(spec: "OperationSpec", available) -> tuple[bool, str]:
    """``(can_run, reason)`` for ``spec`` given the ``available`` facts.

    ``reason`` is empty when runnable; otherwise it names, in human terms, what is missing — e.g.
    *"needs a 3D z-stack"* — so a consumer can grey the operation out **with an explanation** instead
    of failing at run time. The phrasing comes from ``tag_registry.REQUIREMENTS``, the single source of
    the requirement vocabulary and its reasons.
    """
    from pycat.utils.tag_registry import REQUIREMENTS

    missing = unmet_requirements(spec, available)
    if not missing:
        return True, ""
    reasons = [REQUIREMENTS.get(req, req) for req in missing]
    if len(reasons) == 1:
        return False, f"needs {reasons[0]}"
    return False, "needs " + ", ".join(reasons[:-1]) + f" and {reasons[-1]}"


# ── The executor: import lazily, at CALL time, with a precise per-operation error ───────────────
# Finding 1's other half. Discovery is import-free (above); EXECUTION resolves the implementation from
# the spec's own `module`/`function` and imports it only when the op actually runs. A missing optional
# dependency then names itself for THAT operation, instead of silently dropping a third of the catalog
# at discovery time.

def resolve_operation(spec: "OperationSpec"):
    """Import ``spec.module`` **now** and return the callable ``spec.function``.

    Raises :class:`pycat.utils.errors.OptionalDependencyError` with a precise, per-operation message if
    the module (or one of its own imports — e.g. a missing ``pywavelets``) is unavailable, or the
    function is absent. Never a silent gap: the operation was listed at discovery; this is where an
    unmet dependency finally, and specifically, surfaces.
    """
    from pycat.utils.errors import OptionalDependencyError

    if not spec.module or not spec.function:
        raise OptionalDependencyError(
            f"operation '{spec.id}' has no resolvable implementation "
            f"(module={spec.module!r}, function={spec.function!r})")
    try:
        module = importlib.import_module(spec.module)
    except Exception as exc:                          # broad-ok: optional-dependency import probe — re-raised typed and named
        dep = getattr(exc, "name", None) or spec.module
        raise OptionalDependencyError(
            f"operation '{spec.id}' is unavailable: needs '{dep}' "
            f"(optional dependency) — {type(exc).__name__}: {exc}") from exc
    fn = getattr(module, spec.function, None)
    if not callable(fn):
        raise OptionalDependencyError(
            f"operation '{spec.id}': {spec.module}.{spec.function} is not a callable")
    return fn


def module_importable(spec: "OperationSpec") -> bool:
    """Best-effort: can this operation's implementation module be imported right now?

    A REAL import attempt (cached by ``importlib`` after the first success), not ``find_spec`` — because
    the failure that matters here is a module whose OWN imports fail (a missing optional dep), which
    ``find_spec`` reports as present (the file exists). Importing, not just locating, is the only honest
    test. NOT called during discovery — only when a consumer wants the pre-emptive availability fact.
    """
    try:
        resolve_operation(spec)
        return True
    except Exception:                                # broad-ok: any import failure ⇒ not importable
        return False


def operation_availability(spec: "OperationSpec", available, *, check_module: bool = False) -> tuple[bool, str]:
    """``(can_run, reason)`` combining the declared-requirement gate with (optionally) the module-import
    gate — the single call a UI uses to enable/disable an operation with a stated reason.

    ``check_module=False`` (default) stays lightweight: it checks only the declared ``requirements``
    (z-stack, pixel size, …) against ``available`` — no import, so it is safe to call for every menu
    entry. ``check_module=True`` additionally probes importability (see :func:`module_importable`), so a
    missing optional dependency greys the ONE operation out with *"needs the optional dependency …"*
    rather than being discovered only when the user clicks. Requirement reasons take precedence.
    """
    can_run, reason = runnability(spec, available)
    if not can_run:
        return can_run, reason
    if check_module and not module_importable(spec):
        dep = _missing_dependency(spec)
        return False, (f"needs the optional dependency '{dep}'" if dep
                       else "its module cannot be imported")
    return True, ""


def _missing_dependency(spec: "OperationSpec"):
    """The name of the dependency whose absence stops ``spec``'s module importing, or ``None``."""
    if not spec.module:
        return None
    try:
        importlib.import_module(spec.module)
        return None
    except Exception as exc:                          # broad-ok: reading the failing import's name
        return getattr(exc, "name", None) or spec.module
