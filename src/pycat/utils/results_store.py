"""**Reopen a closed results dock from RETAINED results — never recompute (results_figure_reflow Part 2).**

A workflow computes a payload, then mounts a results dock built from it. Close the dock and the widgets go
stale — but the payload does not, yet without this there was no way back short of re-running the whole
analysis (a closed VPT results panel meant recomputing microrheology over thousands of tracks). This is the
**shared** retain/rebuild registry every workflow uses, so "Show results" is one mechanism, not a bespoke
button per method — VPT, cellular and batch all register the same way:

- ``retain_results(key, rebuild, label=...)`` records how to REBUILD the dock from the payload the workflow
  already produced. Re-registering a key (a fresh run) supersedes the old payload and becomes most-recent.
- ``reopen_results(key)`` rebuilds it — it **never recomputes**; it calls the retained rebuild. The rebuild
  reuses the workflow's own dock-replacement path, so reopening is idempotent (no stacked duplicates).
- ``has_results(key)`` / ``disabled_reason(key)`` gate a "Show results" control: enabled only when there is
  something to show; a *stated* refusal ("run the analysis first") otherwise — never a silent re-run.

Qt-free: it holds only callables and strings, so it is ``core``-testable; the rebuild the workflow registers
is what touches Qt. ``stamp`` optionally carries a dataset/identity token so a consumer can flag a payload
whose source data has since changed as being from an earlier run."""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional


@dataclasses.dataclass
class _RetainedResults:
    key: str
    rebuild: Callable[[], object]
    label: str
    stamp: object = None


_RESULTS: "dict[str, _RetainedResults]" = {}


def retain_results(key, rebuild, *, label, stamp=None):
    """Record that workflow ``key`` has results that ``rebuild()`` can re-mount into a dock, with NO
    recomputation. Re-registering an existing key supersedes it and moves it to most-recent."""
    if not callable(rebuild):
        raise TypeError("rebuild must be callable")
    _RESULTS.pop(key, None)                       # re-insert so a fresh run becomes the most-recent
    _RESULTS[key] = _RetainedResults(key=key, rebuild=rebuild, label=str(label), stamp=stamp)


def has_results(key) -> bool:
    return key in _RESULTS


def results_label(key) -> Optional[str]:
    r = _RESULTS.get(key)
    return r.label if r is not None else None


def results_stamp(key):
    r = _RESULTS.get(key)
    return r.stamp if r is not None else None


def disabled_reason(key) -> Optional[str]:
    """Why a 'Show results' control is disabled for ``key`` — ``None`` when it is enabled (a payload exists)."""
    return None if key in _RESULTS else "Run the analysis first — there are no results to show yet."


def reopen_results(key) -> bool:
    """Rebuild ``key``'s dock from its retained payload. Returns ``True`` if it reopened, ``False`` if there
    is no retained payload — a stated refusal for the caller to surface, **never** a silent recompute."""
    r = _RESULTS.get(key)
    if r is None:
        return False
    r.rebuild()
    return True


def reopen_most_recent() -> bool:
    """Rebuild the most-recently-retained workflow's dock (the common "show me my results" case). ``False``
    when nothing has been retained."""
    if not _RESULTS:
        return False
    last_key = next(reversed(_RESULTS))
    return reopen_results(last_key)


def clear_results(key=None):
    """Forget retained results — one ``key``, or all when ``key`` is ``None`` (e.g. a session reset)."""
    if key is None:
        _RESULTS.clear()
    else:
        _RESULTS.pop(key, None)
