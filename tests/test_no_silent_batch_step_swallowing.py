"""**A dropped batch item must be VISIBLE — a broad handler around one item may not let it vanish.**

The `exception_context_classification` spec's **batch_step rule** (closed via `close_partial_specs` Part B).
The scientific-result guard catches a broad `except` that returns a wrong *number*; the write guard catches
a save that fails into apparent success. This catches the third silent corruption with no wrong number
anywhere: a batch that processes 100 images and silently drops 7 yields a cohort of **93 that looks
complete**. The failure has to be recorded per item — a `failed`/`skipped` status, a visible `✗`/`⚠` row —
not swallowed.

**The rule:** a broad handler wrapped around a single item of a MULTI-FILE batch loop (`for … in
self.files` / `tiffs` / `image_paths` …) must make the item's failure visible — it must either

- **re-raise**, or
- **record the outcome**: append a per-item result/marker, set a status flag, or produce a
  `BatchStepResult(status=…)` — any of which leaves the drop inspectable in the batch report.

A handler that only logs-and-continues (so the item disappears from the cohort with no trace) is flagged,
unless annotated `# broad-ok: batch_step — <reason>`. Conservative (only handlers directly inside a
batch-file loop) and ratchet-style (the count may only go DOWN).
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"
_MARKER = "# broad-ok:"

# A loop is a MULTI-FILE batch loop when it iterates one of these (the "100 images" pattern the batch_step
# rule is about). Kept to file/image batches on purpose — a generic `for x in items` is out of scope.
_BATCH_ITERABLE_HINTS = ('self.files', 'self.tiffs', 'files', 'tiffs', 'image_paths', 'image_files',
                         'input_files', 'self.image_paths', 'self.records')
# Tokens whose presence in a handler body means the item's failure was RECORDED somewhere visible.
_RECORD_TOKENS = ('append', 'status', 'failed', 'skipped', 'batchstepresult', 'record', '_ok',
                  'errors', 'results')


def _is_broad(handler):
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in ('Exception', 'BaseException')
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ('Exception', 'BaseException') for e in t.elts)
    return False


def _iter_text(for_node, source):
    seg = ast.get_source_segment(source, for_node.iter) if for_node.iter else None
    return seg or ""


def _handler_records(handler, source) -> bool:
    """True if the handler re-raises or records the item's outcome somewhere visible."""
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return True
        # An assignment INTO a structure — `row['mp4'] = 'mp4 error: …'`, `out[i] = failed` — records the
        # item's outcome where the batch report can see it (the returned table row / results dict).
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) for t in n.targets):
            return True
    body = (ast.get_source_segment(source, handler) or "").lower()
    return any(tok in body for tok in _RECORD_TOKENS)


def _annotated(lines, lineno) -> bool:
    header = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
    body = lines[lineno] if 0 <= lineno < len(lines) else ""
    return (_MARKER in header) or (_MARKER in body)


def _silent_batch_swallowers(source: str):
    """Line numbers of broad handlers, directly inside a multi-file batch loop, that neither record the
    item's outcome nor are annotated."""
    lines = source.split("\n")
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not any(h in _iter_text(node, source) for h in _BATCH_ITERABLE_HINTS):
            continue
        # handlers of a Try whose nearest enclosing loop is THIS batch loop
        for t in ast.walk(node):
            if not isinstance(t, ast.Try):
                continue
            for h in t.handlers:
                if _is_broad(h) and not _annotated(lines, h.lineno) and not _handler_records(h, source):
                    out.append(h.lineno)
    return out


# The un-recorded silent batch-item swallow count, pinned at today's value. A RATCHET — only ever decreases.
# 0 (1.6.281): the batch-item loops that exist (BatchWorker.run over self.files; temperature_tools over its
# tiffs) already record every failure — a visible `✗`/`⚠` results row, a `_consolidated_ok` flag, or the
# per-file error written into the returned table row. No item is dropped without a trace, so a NEW silent
# batch-step swallow anywhere in a file/image batch loop is what this catches.
_SILENT_BATCH_BUDGET = 0


def _scan():
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
            for ln in _silent_batch_swallowers(src):
                found.append(f"{path.relative_to(_SRC)}:{ln}")
        except SyntaxError:
            continue
    return found


def test_no_silent_batch_step_swallowers_are_added():
    """The ratchet. A broad handler around one item of a multi-file batch that swallows the failure drops
    that item silently — a 93-of-100 cohort that looks complete; the count may not rise."""
    offenders = _scan()
    assert len(offenders) <= _SILENT_BATCH_BUDGET, (
        f"{len(offenders)} broad handlers around a batch item neither record its failure nor are annotated "
        f"(budget {_SILENT_BATCH_BUDGET}). A silently dropped item makes a partial cohort look complete.\n\n  "
        + "\n  ".join(offenders)
        + "\n\nRecord the item's outcome — a failed/skipped status, a visible result row, or re-raise — then "
          "lower the budget. Annotate `# broad-ok: batch_step — <reason>` only if the drop is genuinely "
          "visible another way.")


def test_the_guard_detects_a_silent_batch_drop():
    """The canary: the AST check must FLAG a silent drop and PASS a recording / re-raising one."""
    silent = (
        "def run(self):\n"
        "    for path in self.files:\n"
        "        try:\n"
        "            process(path)\n"
        "        except Exception as e:\n"
        "            log(e)\n")                              # <- item vanishes: MUST be flagged
    assert len(_silent_batch_swallowers(silent)) == 1

    records = (
        "def run(self):\n"
        "    results = []\n"
        "    for path in self.files:\n"
        "        try:\n"
        "            process(path)\n"
        "        except Exception as e:\n"
        "            results.append(f'FAILED {path}: {e}')\n")   # records the drop -> not flagged
    assert _silent_batch_swallowers(records) == []

    reraises = (
        "def run(self):\n"
        "    for path in self.files:\n"
        "        try:\n"
        "            process(path)\n"
        "        except Exception as e:\n"
        "            raise\n")                               # re-raises -> not flagged
    assert _silent_batch_swallowers(reraises) == []

    annotated = (
        "def run(self):\n"
        "    for path in self.files:\n"
        "        try:\n"
        "            process(path)\n"
        "        except Exception as e:  # broad-ok: batch_step — recorded in the sidecar log\n"
        "            log(e)\n")                              # annotated -> not flagged
    assert _silent_batch_swallowers(annotated) == []

    not_a_batch = (
        "def f(self):\n"
        "    for x in widgets:\n"
        "        try:\n"
        "            x.close()\n"
        "        except Exception as e:\n"
        "            log(e)\n")                              # not a file/image batch -> out of scope
    assert _silent_batch_swallowers(not_a_batch) == []
