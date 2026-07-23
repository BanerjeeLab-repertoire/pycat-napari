# Claude Code spec — The cache-cleanup prompt must not gate startup

> **● STATUS — DONE, shipped 1.6.300.** `clear_cache_on_startup` (immediate modal) is replaced by two entry
> points in `file_io/local_cache.py`: `offer_cache_cleanup(viewer)` — the non-blocking startup offer (a napari
> notification with the cached amount pointing to *File ▸ Manage local cache…*, empty cache silent, never
> raises) — and `open_cache_manager()` — the on-demand modal that shows the SAME `_show_dialog`/`_apply`
> unchanged. `run_pycat` schedules the offer via `QTimer.singleShot(600, …)` after branding/maximize (the idle
> claim re-verified: nothing between viewer construction and the loop opens a cached acquisition — recorded in
> the new comment). The 'Manage local cache…' File-menu action is installed from `central_manager` (NOT the
> line-capped `menu_manager` god-file — so the menu-contract snapshot is untouched; the reachability is
> covered by an integration test on the action instead). Deletion/`KEEP_DAYS`/reporting guarantees and the
> launch `try/except` are all unchanged. `test_cache_cleanup_startup.py` (core + one integration) +
> `test_cache_menu_action.py` (`integration`, 2). Also fixed a latent collection gap: `pycat.file_io`-importing
> core test modules (these + the 1.6.294–296 metadata tests) were silently skipped in the full `-m core` dir
> scan (module-scope gui-bound import + aicsimageio absent); moved those imports off module top-level so they
> actually run. Full core green (1803 — up from 1776, the +27 being the previously-skipped tests now running).

**Date:** 2026-07-23 · **Target tree:** 1.6.297 · Verified against the 1.6.297 tree. Reported from the
GUI: the local-cache cleanup dialog appears **over** the napari splash/branding while the window is
still assembling, so the first thing a user meets is a modal asking them to make a file-deletion
decision about an application that has not finished presenting itself.

## Verified state

`run_pycat.py:463-475` calls `clear_cache_on_startup()` **immediately** after
`viewer = napari.Viewer(title="PyCAT")` — before the layer-tag hook, the coordinate readout, the menus,
the docks, the window icon, and `napari.run()` at line 763. The dialog is a blocking modal:
`local_cache.py:431` → `dlg.exec_()`.

The placement comment reads:

> NOW — before this session opens anything — is the one moment they are provably idle, so it is the
> safe moment to offer to clear them.

**The safety reasoning is correct; the conclusion about placement is not.** The cached files remain
idle for the *entire* launch sequence — nothing between viewer construction and `napari.run()` opens a
cached acquisition. So the "provably idle" window is far wider than one instant, and the dialog has
been pinned to the earliest point in that window rather than the most appropriate one.

Good news: `clear_cache_on_startup` already early-returns when `_scan_cache()` is empty, so a genuine
first run shows nothing. The defect is placement and modality, not triggering.

## The change

### 1. Defer to the end of launch
Move the `clear_cache_on_startup()` call from line ~470 to **after** the rest of the launch sequence
(tag hook, coordinate readout, menus/docks, window icon/maximise) and immediately before or just after
`napari.run()` — whichever the Qt event loop allows for showing a non-blocking widget. The user sees a
fully-formed, branded application first, and the housekeeping offer arrives afterwards.

**Verify the idleness claim still holds at the new position:** confirm nothing between the old and new
call sites opens a cached acquisition. Note this in the code comment, replacing the "one moment"
justification with the accurate one — *the cache is idle for the whole launch; we ask at the end so the
prompt does not compete with startup.*

### 2. Make it non-modal
Replace `dlg.exec_()` with a **non-blocking** presentation. Reclaiming temp-folder disk space is
housekeeping; it should never block the user's first interaction. Two acceptable shapes:

- **Preferred:** a napari notification (`show_info`, already used throughout `menu_manager.py`) saying
  how much is cached and offering to open the manager — e.g. *"PyCAT has 2.0 MB of cached copies from
  a previous session. Manage…"* — with the existing dialog opened on demand.
- **Acceptable:** the existing dialog shown non-modally (`dlg.show()`), so the app is usable behind it.

Either way the **existing dialog is reused**, not rewritten — the grouped-by-source list, per-file and
per-folder Keep, the `KEEP_DAYS` protection, and the "never deletes silently / deletion reported in the
terminal" guarantees all stay exactly as they are.

### 3. Always reachable
Because it no longer forces itself on the user, add a **menu entry** (e.g. File → *Manage local
cache…*) that opens the same dialog on demand. A prompt that can be dismissed must have a way back, or
the cache silently grows with no user-visible control.

## What must NOT change
- **Never delete silently.** Every existing guarantee holds: nothing is removed without the user
  choosing it, deletions are reported in the terminal, `KEEP_DAYS` protection is honoured.
- **Never crash launch.** The `try/except` around the call stays — the module's own docstring is right
  that *"a cleanup that crashes the app it is cleaning up for has done more harm than the disk it was
  reclaiming."*
- **Empty cache stays silent.** The existing early-return must be preserved; a first-run user sees
  nothing at all.

## Tests
- `clear_cache_on_startup` is invoked **after** the launch-sequence steps (assert call ordering in
  `run_pycat_func`, e.g. by patching and recording the sequence).
- The presentation is non-blocking — the call returns without waiting for user input (no `exec_()` on
  the startup path).
- An empty cache produces **no** dialog and **no** notification (the first-run silence test).
- A non-empty cache surfaces the offer, and opening it shows the same grouped list as before.
- The menu entry opens the same dialog (contract test — the menu-contract snapshot must be updated
  deliberately, not incidentally).
- Deletion semantics unchanged: nothing is removed without an explicit choice; `KEEP_DAYS` protection
  still applies; a cleanup failure does not propagate into launch.

## Steps
1. Move the `clear_cache_on_startup()` call to the end of the launch sequence; correct the placement
   comment to state the real reason.
2. Swap `dlg.exec_()` for the non-blocking presentation (notification-first, dialog on demand).
3. Add the File → *Manage local cache…* menu entry; update the menu contract.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (cache cleanup no longer
   interrupts startup; non-blocking, reachable from the File menu).

## Definition of done
- Startup completes and the application is fully presented before any cache prompt appears.
- The prompt is non-blocking; the app is usable without answering it.
- The same dialog remains available on demand from the File menu.
- An empty cache is silent; deletion guarantees and `KEEP_DAYS` protection are unchanged.
- A cleanup failure still cannot crash launch.
- Full `pytest -m core` green.

## Cautions
- **Verify the cache is still idle at the new call site.** The original comment's safety claim is the
  one thing worth re-checking when moving it — confirm nothing in the intervening steps opens a cached
  acquisition, and record that in the comment.
- **Do not rewrite the dialog.** Its grouping, Keep semantics, and reporting are careful work; this
  change is about *when* and *how* it is presented.
- **A dismissible prompt needs a way back** — without the menu entry, the cache becomes unmanageable.
- **Keep the empty-cache early return.** A first-run user must see nothing.
- Keep the `try/except` — a failed cleanup must never take the app down with it.
