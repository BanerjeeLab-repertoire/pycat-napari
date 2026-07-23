"""**`except Exception: pass` does not GROW — and the deliberate ones must SAY they are deliberate.**

The codebase's most common failure handler is a broad `except Exception`. Most are legitimate — a Qt
teardown, an optional backend that may be absent, a metadata probe that may find nothing. Some are not:
a handler that swallows a *scientific* failure and returns a plausible number is the worst bug this
codebase can ship, and it hides in the same syntax as the harmless ones.

A wholesale purge is the wrong move (the harmless ones genuinely need the handler). The right move is
the complexity budget's: **ratchet the count per package at today's value, only ever down**, and give
the deliberate handlers an explicit marker so the lazy ones become visible. This stops the growth the
audit measured across revisions, at zero refactoring cost — and it makes converting the dangerous ones
a deliberate, reviewable act rather than a heroic sweep.

**The escape hatch:** annotate a broad handler with ``# broad-ok: <reason>`` (on the ``except`` line or
the first body line) and it drops out of the count. The reason is mandatory — an unexplained marker is
just the swallow with extra characters.
"""

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"
_MARKER = "# broad-ok:"

# The exception_context_classification vocabulary: a broad-ok MAY carry a category as its first token
# (``# broad-ok: <category> — <reason>``) saying WHAT it guards, so a write / batch-step swallow is held to
# a stricter standard than a UI-cleanup one. When a category is present it must be one of these.
_CATEGORIES = {'ui_cleanup', 'optional_probe', 'scientific_result', 'write', 'batch_step'}
_CATEGORY_FORM = re.compile(r'^([a-z_]+)\s+(?:—|--)\s')

# Un-annotated ``except Exception`` handlers allowed per package, at today's values. A RATCHET: it only
# ever decreases. Convert a scientific handler to a typed raise, or annotate a deliberate one with
# ``# broad-ok: <reason>``, and lower the number here. A package not listed has an implicit budget of 0,
# so a NEW file/package that adds a broad handler fails until it is justified or counted.
_BUDGET = {
    'batch_processor.py': 17,
    'batch_step_registry.py': 5,
    'central_manager.py': 2,
    'data': 3,
    'file_io': 237,   # 284 -> 239: 45 broad handlers in the decomposed code (naming/dialogs/openers)
                      # annotated `# broad-ok:` with body-matched reasons (metadata probes, UI
                      # robustness, format-open log-and-continue) during the 1.6.146 decomposition.
                      # 239 -> 237 (1.6.259): local_cache's two best-effort cache writes annotated
                      # `# broad-ok: write` during the write-swallow sweep. See test_no_silent_write_or_batch_swallowing.
    'navigator': 1,
    'run_pycat.py': 30,
    'toolbox': 491,   # 514 -> 509 -> 498: the 15 scientific result-path handlers (frap 4 @1.6.210; then
                      # condensate_physics 5 / invitro 3 / vpt 3 @1.6.211) annotated `# broad-ok:` after
                      # classification found each reports its failure honestly (NaN + flag, verdict, prior-
                      # measurement fallback, or optional-backend probe). See test_no_scientific_result_swallowing.
                      # 498 -> 491 (1.6.259): the write-swallow sweep annotated ts_cache_manager's cache prune +
                      # temperature_tools' two batch-export handlers (error recorded into the returned row), and
                      # tracks four prior unclaimed reductions. See test_no_silent_write_or_batch_swallowing.
    'ui': 252,
    'utils': 113,     # 114 -> 113 (1.6.259): channel_designations._save annotated `# broad-ok: write` (it
                      # returns False so the caller surfaces the failed persist). See test_no_silent_write_or_batch_swallowing.
}


def _is_broad(handler):
    """An ``except Exception`` (bare name or a tuple containing it) — the family this ratchet counts."""
    t = handler.type
    if isinstance(t, ast.Name):
        return t.id == 'Exception'
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == 'Exception' for e in t.elts)
    return False


def _pkg_of(path):
    rel = path.relative_to(_SRC)
    return rel.parts[0] if len(rel.parts) > 1 else rel.name


def _is_annotated(lines, lineno):
    """Annotated if ``# broad-ok:`` sits on the ``except`` header line or the first body line."""
    header = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ''
    body = lines[lineno] if 0 <= lineno < len(lines) else ''
    return (_MARKER in header) or (_MARKER in body)


def _scan():
    """Per package: the count of UN-annotated broad handlers. Also returns every ``# broad-ok:`` marker
    found (file, lineno, reason) so the reason can be checked non-empty."""
    counts = {}
    markers = []
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding='utf-8', errors='ignore')
        lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for h in ast.walk(tree):
            if isinstance(h, ast.ExceptHandler) and _is_broad(h) and not _is_annotated(lines, h.lineno):
                counts[_pkg_of(path)] = counts.get(_pkg_of(path), 0) + 1
        for i, line in enumerate(lines, start=1):
            if _MARKER in line:
                markers.append((str(path.relative_to(_SRC)), i, line.split(_MARKER, 1)[1].strip()))
    return counts, markers


def test_no_package_GROWS_its_broad_exception_count():
    """**The ratchet.** A new broad `except Exception` in a package at its budget fails here — convert it
    to a typed raise (`utils/errors.py`), or annotate it `# broad-ok: <reason>` if it is genuinely a Qt
    teardown / optional-backend probe. The number only goes down."""
    counts, _ = _scan()
    over = []
    for pkg, n in sorted(counts.items()):
        budget = _BUDGET.get(pkg, 0)
        if n > budget:
            over.append(f"{pkg}: {n} un-annotated broad handlers (budget {budget}, +{n - budget})")
    assert not over, (
        "a package grew its broad-`except Exception` count:\n  " + "\n  ".join(over)
        + f"\n\nEither raise a TYPED error (`pycat.utils.errors`) so the failure has a name a caller can "
          f"catch, or annotate the handler `{_MARKER} <reason>` if it is a legitimate GUI-teardown / "
          f"optional-backend / metadata-probe swallow. Do NOT raise the budget — it is a ratchet.")


def test_every_broad_ok_annotation_carries_a_REASON():
    """An unexplained `# broad-ok:` is the swallow with extra characters. The marker must say WHY the
    broad catch is safe here — that reason is what a reviewer reads instead of re-deriving it."""
    _, markers = _scan()
    empty = [f"{f}:{ln}" for f, ln, reason in markers if not reason]
    assert not empty, (
        f"these `{_MARKER}` markers have no reason:\n  " + "\n  ".join(empty)
        + f"\n\n`{_MARKER}` excludes a handler from the ratchet, so it must justify itself — "
          f"`{_MARKER} Qt teardown during close, nothing to recover` — never a bare marker.")


def test_a_categorized_broad_ok_names_a_VALID_category():
    """The exception_context_classification convention: a broad-ok may carry a CATEGORY as its first token
    (``# broad-ok: <category> — <reason>``) classifying WHAT it guards. When it does, the category must be a
    known kind — a typo'd category silently mislabels the code's intent (a `write` that reads as `writes`
    escapes the write standard). Legacy multi-word reasons (``metadata probe — …``) are not category
    claims and are unaffected."""
    _, markers = _scan()
    bad = []
    for f, ln, reason in markers:
        m = _CATEGORY_FORM.match(reason)
        if m and m.group(1) not in _CATEGORIES:
            bad.append(f"{f}:{ln} — unknown category {m.group(1)!r}")
    assert not bad, (
        f"a `{_MARKER}` names an unknown category (must be one of {sorted(_CATEGORIES)}):\n  "
        + "\n  ".join(bad)
        + "\n\nUse `# broad-ok: <category> — <reason>` with a known category, or a plain "
          "`# broad-ok: <reason>` for an as-yet-uncategorized handler.")


def test_the_typed_error_FAMILY_exists():
    """The vocabulary a converted handler raises into. All derive from `PyCATError`, so a caller can
    catch the family or one kind."""
    from pycat.utils import errors
    assert issubclass(errors.PyCATError, Exception)
    for name in ('UnsupportedFormatError', 'MetadataUnavailableError', 'InvalidCalibrationError',
                 'ScientificAssumptionError', 'OptionalDependencyError', 'LayerResolutionError'):
        cls = getattr(errors, name)
        assert issubclass(cls, errors.PyCATError), f"{name} is not a PyCATError"
