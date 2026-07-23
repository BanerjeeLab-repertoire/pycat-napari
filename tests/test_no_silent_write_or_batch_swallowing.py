"""**A swallowed WRITE is silent data loss — the user believes their file saved, and it did not.**

The `exception_context_classification` spec's **write rule** (its Part 2 guard). The scientific-result guard
(`test_no_scientific_result_swallowing.py`) catches a broad handler that returns a wrong *number*; this one
catches the other silent corruption that carries **no wrong number at all** — a save that fails into apparent
success. The user clicks *Save Report*, the `except` eats the error, and the CSV is absent or truncated;
nothing downstream looks wrong because nothing downstream ran.

**The rule (from the spec's category table):** a broad handler wrapped around a persist call
(`to_csv` / `savefig` / `imwrite` / `json.dump` / `open(..., 'w')` / `write_text` …) must make a failure
**visible** — it must either

- **re-raise** (a typed `PyCATError` write subclass, or bare `raise`), or
- **surface** the failure to the user: a `show_warning` / `napari_show_warning` / `_warn` / `show_error`
  notification (any notifier whose name carries ``warn`` / ``error`` / ``critical``).

A handler that only ``debug_log``s, ``pass``es, or silently returns success is the swallow this guard flags —
**unless** it is annotated ``# broad-ok: <category> — <reason>`` on the ``except`` line (or its first body
line), which is how a genuinely best-effort write (a cache manifest whose absence costs nothing) records that
its silence is deliberate and *why*.

**Conservative + ratchet, like the exception budget.** It looks only at handlers wrapped around a KNOWN write
call, treats any user-facing notification as compliant (so the many correct UI save handlers are not
false-positived), and pins the count of un-annotated silent write-swallows at today's value — it may only go
DOWN. Converting one to surface/raise, or annotating a genuinely-safe one, lowers it.

The sibling **batch_step rule** — a broad handler around one batch item must record a `failed` status rather
than silently drop the item — is guarded for its concrete offender (`BatchWorker.run`) by
`test_batch_step_visibility.py`; a broader batch sweep is a later increment of the same spec.
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"
_MARKER = "# broad-ok:"

# Calls that PERSIST bytes to disk. A failure in one of these, if swallowed, is lost data. Kept tight on
# purpose (conservative): every name here unambiguously writes a file the user expects to exist.
_WRITE_CALLS = {
    'savefig', 'to_csv', 'to_excel', 'to_parquet', 'to_json', 'to_hdf', 'to_pickle',
    'save', 'savez', 'savez_compressed', 'imwrite', 'imsave',
    'write_text', 'write_bytes', 'writerow', 'writerows', 'dump',
}
# A handler is NOT a silent swallow if its body raises or calls a user-facing notifier — a call whose name
# carries one of these tokens (show_warning / napari_show_warning / _warn / show_error / QMessageBox.critical).
_SURFACE_TOKENS = ('warn', 'error', 'critical')


def _is_broad(handler):
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in ('Exception', 'BaseException')
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in ('Exception', 'BaseException') for e in t.elts)
    return False


def _try_body_writes(try_node) -> bool:
    """True if the try body contains a known persist call, or an ``open(..., 'w'/'a'/'x')``."""
    for n in ast.walk(try_node):
        if isinstance(n, ast.Try) and n is not try_node:
            # a write in a NESTED try belongs to that inner handler, not this one
            continue
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name in _WRITE_CALLS:
                return True
            if isinstance(f, ast.Name) and f.id == 'open':
                for a in n.args[1:]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) and \
                            any(m in a.value for m in ('w', 'a', 'x')):
                        return True
    return False


def _handler_surfaces(handler) -> bool:
    """True if the handler makes the failure visible: it re-raises, or it calls a user-facing notifier."""
    for n in ast.walk(handler):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            name = (f.attr if isinstance(f, ast.Attribute) else
                    (f.id if isinstance(f, ast.Name) else '')) or ''
            if any(tok in name.lower() for tok in _SURFACE_TOKENS):
                return True
    return False


def _annotated(lines, lineno) -> bool:
    header = lines[lineno - 1] if 0 <= lineno - 1 < len(lines) else ""
    body = lines[lineno] if 0 <= lineno < len(lines) else ""
    return (_MARKER in header) or (_MARKER in body)


def _silent_write_swallowers(source: str):
    """Line numbers of broad handlers wrapped around a write that neither surface nor are annotated."""
    lines = source.split("\n")
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _try_body_writes(node):
            continue
        for h in node.handlers:
            if _is_broad(h) and not _annotated(lines, h.lineno) and not _handler_surfaces(h):
                out.append(h.lineno)
    return out


# The un-converted silent write-swallow count, pinned at today's value. A RATCHET — it only ever decreases.
# 0 (1.6.259): the initial write sweep. The three cache writes (local_cache _save_manifest / _save_protected,
# ts_cache_manager prune), the two temperature-batch exports (error recorded into the returned row), and
# channel_designations._save (returns False to the caller) are annotated `# broad-ok: <category> — …`; the one
# genuine silent loss — write_session_outputs' metadata-JSON + session-manifest sidecars, which only
# debug_log'd — was converted to surface via notify.show_warning. Every remaining broad write-handler either
# surfaces to the user or is annotated. A NEW silent write-swallow anywhere in the tree is caught.
_SILENT_WRITE_BUDGET = 0


def _scan():
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
            for ln in _silent_write_swallowers(src):
                found.append(f"{path.relative_to(_SRC)}:{ln}")
        except SyntaxError:
            continue
    return found


def test_no_silent_write_swallowers_are_added():
    """The ratchet. A broad handler around a save that swallows the failure into apparent success loses the
    user's data silently; the count may not rise, and converting/annotating one lowers it."""
    offenders = _scan()
    assert len(offenders) <= _SILENT_WRITE_BUDGET, (
        f"{len(offenders)} broad handlers wrap a write and neither surface the failure nor are annotated "
        f"(budget {_SILENT_WRITE_BUDGET}). A swallowed save is silent data loss — the user thinks the file "
        f"is there and it is not.\n\n  " + "\n  ".join(offenders)
        + "\n\nMake the failure visible — re-raise (typed) or surface it (show_warning / _warn) — then lower "
          "the budget. Annotate `# broad-ok: write — <reason>` ONLY if the write is genuinely best-effort "
          "(e.g. a cache whose absence costs nothing) and say why.")


def test_the_guard_detects_a_silent_write_swallow():
    """The canary: the AST check must FLAG a silent write-swallow and PASS a surfacing / annotated one —
    otherwise the ratchet above is measuring nothing."""
    silent = (
        "def save(df, path):\n"
        "    try:\n"
        "        df.to_csv(path)\n"
        "    except Exception as e:\n"
        "        debug_log('save failed', e)\n")          # <- swallowed: MUST be flagged
    assert len(_silent_write_swallowers(silent)) == 1

    surfaces = (
        "def save(df, path):\n"
        "    try:\n"
        "        df.to_csv(path)\n"
        "    except Exception as e:\n"
        "        show_warning(f'Save failed: {e}')\n")     # surfaces -> not flagged
    assert _silent_write_swallowers(surfaces) == []

    reraises = (
        "def save(df, path):\n"
        "    try:\n"
        "        df.to_csv(path)\n"
        "    except Exception as e:\n"
        "        raise IOError(path) from e\n")            # re-raises -> not flagged
    assert _silent_write_swallowers(reraises) == []

    annotated = (
        "def save(df, path):\n"
        "    try:\n"
        "        df.to_csv(path)\n"
        "    except Exception as e:  # broad-ok: write — best-effort cache, absence is fine\n"
        "        debug_log('cache miss', e)\n")            # annotated -> not flagged
    assert _silent_write_swallowers(annotated) == []

    no_write = (
        "def f(x):\n"
        "    try:\n"
        "        return compute(x)\n"
        "    except Exception as e:\n"
        "        debug_log('compute failed', e)\n")        # no write in try -> out of scope
    assert _silent_write_swallowers(no_write) == []
