"""**A broad `except` in a scientific module must not silently return a wrong NUMBER.**

The exception-budget ratchet (``test_exception_budget.py``) counts broad handlers; it does not ask what
they *return*. This one does. The engineering audit's #4 rule, made testable:

> A broad ``except`` in a scientific module must not silently return a numerical result, an empty
> DataFrame, a mask, a fit, or a default calibration.

The distinction is the whole point: a broad handler around a Qt close event is fine; a broad handler
around a **fit, a transform, or a calibration lookup** that then returns a plausible default is a silent
wrong-number generator — the worst bug this codebase can ship, hiding in the same syntax as the harmless
ones. So this guard classifies by **what the handler returns on the failure path**: a broad ``except``
whose body directly ``return``s a non-``None`` value (a number / DataFrame / array / dict of
measurements) is flagged, unless it is annotated ``# broad-ok: <reason>``.

**Ratchet-style, like the budget:** the count is pinned at today's value and may only ever go DOWN.
Converting a handler to a typed raise (``ScientificAssumptionError`` & co., ``from exc``, a narrowed
catch) — so the caller decides between an honest NaN and a raised error rather than a fabricated default
— lowers it. A handler that re-raises, returns ``None``, or is a genuine recorded ``DEGRADED`` fallback
does not count. Scoped to the five fit/measure modules whose output IS the published number; widen as
they are cleaned.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"
_MARKER = "# broad-ok:"

# The modules whose return value is the published scientific number. Extensible — add a module here as it
# is cleaned, and lower the budget by what it contributed.
_SCI_MODULES = [
    "toolbox/vpt_tools.py",
    "toolbox/condensate_physics_tools.py",
    "toolbox/frap_tools.py",
    "toolbox/invitro_tools.py",
    "toolbox/partition_enrichment_tools.py",
]

# Un-converted result-swallowing broad handlers, at today's value. A RATCHET — it only ever decreases.
# Convert one to a typed raise (or annotate a genuinely-safe one `# broad-ok:`) and lower this number.
# 15 -> 11 (1.6.210): frap_tools' four handlers classified + annotated.
# 11 -> 0 (1.6.211): the remaining eleven — condensate_physics_tools (5), invitro_tools (3), vpt_tools (3) —
# were classified and annotated. The finding: NONE of the five scientific modules fabricates a plausible
# default on failure. Each flagged handler reports the failure honestly — an all-NaN fit + fit_success flag,
# an explicit verdict string, a fall-back to an already-measured value (equivalent radius / power-law
# retained when the confined model fails), or an optional-backend/optional-check probe. The budget is now
# ZERO: any NEW broad handler that returns a fabricated scientific default is caught.
_RESULT_SWALLOW_BUDGET = 0


def _is_broad(handler):
    t = handler.type
    if isinstance(t, ast.Name):
        return t.id == "Exception"
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == "Exception" for e in t.elts)
    return False


def _is_annotated(lines, lineno):
    header = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
    body = lines[lineno] if 0 <= lineno < len(lines) else ""
    return (_MARKER in header) or (_MARKER in body)


def _returns_non_none(handler):
    """The line numbers of any direct ``return <non-None>`` in the handler body — NOT counting a return
    inside a nested function/lambda (that is the nested function's contract, not the handler's)."""
    hits = []

    def walk(nodes):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(n, ast.Return) and n.value is not None:
                if not (isinstance(n.value, ast.Constant) and n.value.value is None):
                    hits.append(n.lineno)
            walk(list(ast.iter_child_nodes(n)))

    walk(handler.body)
    return hits


def _result_swallowers(source: str):
    """(lineno, return_lines) for every un-annotated broad handler that returns a non-None value."""
    lines = source.split("\n")
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_broad(node) and not _is_annotated(lines, node.lineno):
            rets = _returns_non_none(node)
            if rets:
                out.append((node.lineno, rets))
    return out


def _scan_modules():
    found = []
    for rel in _SCI_MODULES:
        src = (_SRC / rel).read_text(encoding="utf-8", errors="ignore")
        for lineno, rets in _result_swallowers(src):
            found.append(f"{rel}:{lineno} (returns at {rets})")
    return found


def test_scientific_result_swallowers_do_not_grow():
    """The ratchet. A broad handler in a fit/measure module that returns a plausible default on failure
    is a silent wrong-number generator; the count may not rise, and converting one lowers it."""
    offenders = _scan_modules()
    assert len(offenders) <= _RESULT_SWALLOW_BUDGET, (
        f"{len(offenders)} broad handlers in the scientific modules return a value on failure "
        f"(budget {_RESULT_SWALLOW_BUDGET}). A swallowed fit/calibration failure that returns a "
        f"plausible default is a wrong NUMBER, not a caught error.\n\n  "
        + "\n  ".join(offenders)
        + "\n\nConvert it to a typed raise (`ScientificAssumptionError` & co., narrowed catch, "
          "`from exc`) so the caller decides between an honest NaN and a raised error — then lower the "
          "budget. Annotate `# broad-ok: <reason>` only if the return is genuinely safe.")


def test_the_guard_detects_a_result_swallower():
    """The canary: the AST check must FLAG a result-swallowing handler and PASS an annotated / re-raising
    / None-returning one — otherwise the ratchet above is measuring nothing."""
    swallows = (
        "def fit(x):\n"
        "    try:\n"
        "        return real_fit(x)\n"
        "    except Exception:\n"
        "        return 1.0\n")            # <- fabricated default: MUST be flagged
    assert len(_result_swallowers(swallows)) == 1

    safe = (
        "def fit(x):\n"
        "    try:\n"
        "        return real_fit(x)\n"
        "    except Exception:  # broad-ok: logged and re-raised below\n"
        "        return 1.0\n"              # annotated -> not flagged
        "def g(x):\n"
        "    try:\n"
        "        return real(x)\n"
        "    except Exception:\n"
        "        raise\n"                   # re-raises -> not flagged
        "def h(x):\n"
        "    try:\n"
        "        return real(x)\n"
        "    except Exception:\n"
        "        return None\n")            # returns None -> not flagged
    assert _result_swallowers(safe) == []
