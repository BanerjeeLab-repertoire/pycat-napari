# Claude Code spec — Decompose `file_io.py` (the best-covered file in the project)

> **◐ STATUS — the progress step DONE, shipped 1.6.337 (consistency verified: file_io.py was still exactly
> 1,662 lines with `_run_with_busy_progress` at :1034). Remaining: dialogs.py, session_actions.py, loading.py.**
>
> **Step (progress.py) — DONE, 1.6.337.** `_run_with_busy_progress` (113 lines of modal off-thread busy-dialog
> plumbing) moved VERBATIM to `file_io/progress.py` as `_ProgressMixin`; `FileIOClass` inherits it, so every
> `self._run_with_busy_progress(...)` call site is unchanged. The lone class self-reference
> (`FileIOClass._orphan_load_threads`) became `type(self)._orphan_load_threads` (mixin-safe, no circular
> import). Qt imports stay function-local. Load path verified (CZI/IMS streaming tests pass); file_io.py →
> 1,548 lines. **PyQt5 check (per the step's note): NOT yet droppable — file_io.py still imports PyQt5 at
> module scope for the dialog-building methods, so the "no GUI import" win needs the `dialogs.py` extraction.**
> **Step (dialogs.py) — DONE, 1.6.338.** `assign_channels_in_dialog` (120 lines) + `_channels_all_confident`
> moved VERBATIM to a `_DialogsMixin` in `file_io/dialogs.py` (co-located with `ChannelAssignmentDialog`);
> `FileIOClass` inherits it. `derive_layer_name` stays in file_io (lazy-imported in the moved method). Load
> path verified (naming/scene/session/CZI tests pass); file_io.py → 1,412 lines. **PyQt5 check: still NOT
> droppable — file_io.py uses Qt pervasively (`QFileDialog` ×14, `QMessageBox` ×10, other dialogs); it is a
> Qt-bound load controller, so the "no GUI import" win needs ALL the Qt dialogs/pickers extracted, not just
> these two.** Recorded, not claimed.
>
> **Step (session_actions.py) — DONE, 1.6.339.** `save_and_clear_all` (182 lines — Save-and-Clear dialog +
> session write + repository clear) moved VERBATIM to a `_SessionActionsMixin` in `file_io/session_actions.py`
> (Qt-bound, so its own module, not the Qt-free session.py); `FileIOClass` inherits it, so
> `file_io.save_and_clear_all(viewer)` is unchanged. Save-path tests pass; file_io.py → 1,231 lines (from
> 1,662 across the three steps). Recorded in `_DELIBERATE`.
>
> **Remaining:** `loading.py` (per-file helpers — the load-contract-critical step: preserve the exact fire
> order of the pixel-size gate / provenance / tags / sidecar / channel identity). The PyQt5 drop remains a
> larger, separate effort (the file-pickers and message-boxes are the bulk of file_io's Qt use).

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. **1,662 lines, 44
functions**, and — notably — **82 test files** reference it. That is the strongest characterization net
of any decomposition target in the codebase, which makes this the lowest-risk large split remaining.

The file_io *package* has already been substantially refactored (readers, lazy sources, sidecar
discovery, metadata extraction, session handling all live in their own modules). `file_io.py` itself is
what remains: the top-level entry points plus a mix of dialogs, progress plumbing, and orchestration.

## Verified structure
```
44 functions, 2 over the 120 ratchet
   228  open_2d_image               ← entry point + orchestration
   182  save_and_clear_all          ← session action, not I/O
   120  assign_channels_in_dialog   ← UI DIALOG
   113  _run_with_busy_progress     ← Qt progress plumbing
    86  _open_image_auto_single
    83  _add_image_or_mask_single
```

Three concerns are tangled: **loading orchestration**, **Qt dialogs/progress**, and **session actions**.
The dialog and progress functions are why `file_io.py` still imports PyQt5 — which is precisely the kind
of GUI-in-the-data-layer coupling the architectural guards exist to discourage.

## Target
```
file_io/
    file_io.py        # thin: the public entry points (open_image_auto, open_2d_image, …) as orchestration
    dialogs.py        # assign_channels_in_dialog and any other Qt dialog
    progress.py       # _run_with_busy_progress and the busy/progress plumbing
    session_actions.py# save_and_clear_all (or fold into the existing session module)
    loading.py        # _open_image_auto_single, _add_image_or_mask_single, the per-file helpers
```

## Method — coverage is strong, so the constraint is behavioural not evidential
1. **The 82 test files are the net** — but verify per function that coverage pins *behaviour*, not just
   that the call succeeds. Add characterization tests where it is only structural.
2. **Extracting the Qt pieces is the highest-value move.** Once `dialogs.py` and `progress.py` are out,
   `file_io.py` may no longer need PyQt5 at module scope — which would let the architectural guard
   ("scientific modules must import with no GUI") cover it. **Check this after the move and record the
   result**; if the import can be dropped, that is a real structural win beyond line count.
3. **`save_and_clear_all` is a session action**, not file I/O. Move it to the session module if it fits
   there cleanly; otherwise `session_actions.py`. This is the same misfiling pattern as the physics in
   the masking module.
4. **Preserve the load contract exactly.** Loading is where the pixel-size gate, provenance flags, tag
   hooks, sidecar discovery, and channel identity all fire — a reordered call sequence could change which
   fires first. **Assert the post-load repository state is identical**, not just that a layer appeared.
5. Move, don't rewrite; one concern per commit; re-export shim (file_io is imported everywhere).

## Why now
- **Best-covered target available** — 82 test files.
- Removing Qt from the data layer's top-level module is a structural improvement the guards already
  care about.
- Two functions over the ratchet, one of them 228 lines.
- The rest of the `file_io` package is already decomposed; this finishes the job.

## Tests
- Post-load data-repository state is **identical** after each move — pixel size, provenance flags, tags,
  channel identity, sidecar enrichment (assert the repository dict, not just layer presence).
- `open_2d_image` and `open_image_auto` produce identical layers/scale/metadata.
- The dialog and progress functions behave identically from their new modules.
- **After the Qt extraction, check whether `file_io.py` still imports PyQt5 at module scope** and record
  it; if it does not, extend the no-GUI-import guard to cover it.
- All 82 referencing test files pass unmodified.
- The shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` and the per-file line ratchet.

## Steps
1. Move `dialogs.py` (assign_channels_in_dialog); run.
2. Move `progress.py` (_run_with_busy_progress); run — then check the PyQt5 import status.
3. Move `session_actions.py` / fold `save_and_clear_all` into the session module; run.
4. Move `loading.py` (per-file helpers); run.
5. `file_io.py` retains the public entry points as orchestration; add the re-export shim; lower ratchets.
6. Full `pytest -m core` green after each step.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG, noting whether the GUI import
   could be dropped.

## Definition of done
- `file_io.py` holds public entry points and orchestration; dialogs, progress, session actions and
  per-file loading helpers live in their own modules.
- Post-load repository state is provably identical.
- The PyQt5-at-module-scope question is answered and recorded; the guard extended if it can be.
- Ratchets lowered; all 82 test files pass unmodified.

## Cautions
- **Loading is where every gate fires.** Assert the full post-load repository state, not just that an
  image appeared — a reordered sequence could change which of the pixel-size gate, tag hook, sidecar
  enrichment or identity recall runs first.
- **Do not change the load contract** while moving; this is the path every user hits first.
- Re-export shim is essential — `file_io` is one of the most widely imported modules in the project.
- **Move, don't improve.** No consolidating the entry points, no "simplifying" the progress plumbing.
- One concern per commit; loading changes have caused user-visible regressions before (the 2D
  pixel-size-provenance bug).
