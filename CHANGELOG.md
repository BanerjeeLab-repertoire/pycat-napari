## [1.6.118] - 2026-07-18
### Added — **Interaction layer 1: selection is now a hover / selected / pinned STATE.**
First increment of the interaction-layer spec. Selection was a single object — no multi-select, no
pinning while exploring, no independent hover. `SelectionService` now holds a `SelectionState`
(`selected: frozenset`, `primary`, `hovered`, `pinned: frozenset`, `generation`) and publishes the
whole state per change, with commands that produce a new one:

- `toggle(entity, source)` — ctrl-click to build a comparison set; `select_entity` — single select;
  `hover(entity, source)` — independent of selection; `pin`/`unpin` — survive a clear;
  `clear_selection(source)` — clears selected + hovered but **keeps pins** (Escape's semantics).
- **Back-compat is total.** `SelectionState` quacks like the old `Selection` (`entity_ids`,
  `primary_id`, `source_view`, `is_empty`), so every existing subscriber (the dock, the VPT views, the
  plots) and every existing test keeps working unchanged — the dispatch core (busy-guard, delayed
  release, deferred-debounce) is untouched, just extracted into `_publish` and shared by the old
  `select(Selection)` entry and the new commands.
### Notes
- Headless-tested: toggle add/remove, clear keeps pins, hover doesn't disturb selection, one command =
  one generation = one publish, a command reaches old subscribers via the back-compat interface, and
  the source view is skipped. All 72 existing selection/brushing tests still green.
- This is the keystone the pyqtgraph plot backend should be built against (its adapter must speak this
  state, not the old bare callback). Remaining interaction-layer increments (honest hit-testing —
  largely done in 1.6.100 via click-cycling; non-sampled track promotion; `LineCollection` background;
  the `SelectionView` adapter contract) are separate, additive passes.

## [1.6.117] - 2026-07-18
### Fixed — **CZI exit hang: force the exit from `atexit`, not `aboutToQuit` (which never fired).**
1.6.116's `QApplication.aboutToQuit` hook did not fix the hang — it is installed from the CZI-open
**worker thread**, where a cross-thread Qt `connect` is unreliable, and the hang is at Python
interpreter shutdown *after* `napari.run()` returns. Moved the guarantee to an **`atexit`** handler:
it runs on the main thread right before Python joins the JVM's non-daemon threads (exactly where it
hangs), so `os._exit(0)` there terminates cleanly. `aboutToQuit` is kept as a best-effort earlier
trigger.
- The handler prints `[PyCAT CZI] BioFormats JVM was open — forcing a clean process exit…` when it
  runs, so it is visible whether the fix engaged. Verified at the process level: the standalone reader
  fires the handler and exits `0`.
- Only the welcome-logo temp-file cleanup atexit is pre-empted (harmless), and only in a CZI session.
### Notes
- **Needs a viewer:** open the streaming `.czi`, close PyCAT — you should see that force-exit line and
  get the prompt back. If you close and do NOT see the line, `napari.run()` isn't returning on close
  and I'll hook the viewer's window-close event instead.

## [1.6.116] - 2026-07-18
### Fixed — **Closing PyCAT after a CZI now returns the terminal (force a clean exit).**
Headless mode (1.6.115) was not enough: something in the napari/Qt + BioFormats-JVM combination still
keeps the process alive at teardown — the window closes but the terminal never comes back. It cannot
be reproduced outside the GUI (a plain script exits fine), so rather than keep chasing which Java/Qt
thread refuses to die, PyCAT now forces a clean termination at the app's quit point: **once a CZI has
started the JVM**, `QApplication.aboutToQuit` flushes the streams and calls `os._exit(0)`. This only
arms in a CZI session (the JVM-start path) — every other session exits normally — and it runs after
other quit handlers, so it is the last thing before the process would otherwise hang.
### Notes
- **Needs a viewer:** confirm closing PyCAT after opening the streaming `.czi` returns the prompt.
- Keeps the 1.6.115 headless start (good practice regardless) and the scrubbing findings from
  1.6.113–115 (the stutters are inherent BioFormats seek latency).

## [1.6.115] - 2026-07-18
### Fixed — **Closing PyCAT after opening a CZI no longer hangs the terminal.**
Long-standing: after opening a streaming CZI, closing PyCAT left the process alive — the window shut
but the terminal never returned. Reading a CZI can make BioFormats touch Java AWT (colour models /
thumbnails), which spawns a **non-daemon AWT thread** that keeps the JVM — and the whole Python
process — running at shutdown. (A plain script exits fine because it never triggers AWT the way the Qt
app does, which is why it only bit inside `run-pycat`.) The JVM is now started **headless**
(`scyjava.config.enable_headless_mode()` → `-Djava.awt.headless=true`), so no AWT thread is ever
created; BioFormats reads pixels and metadata without it (verified: the reader still opens/reads the
real 8 GB file and the process exits cleanly).
### Diagnostics
- The `PYCAT_CZI_TRACE=1` readout now breaks latency into **worst lock-wait** and **worst openBytes**.
  On the real file this settled the scrubbing question: worst lock-wait **1–2 ms** (the prefetcher is
  not blocking foreground reads) and worst openBytes **~400 ms** — i.e. the intermittent stutters are
  BioFormats **seeking to distant frames**, an inherent random-access cost of this streaming CZI that
  caching cannot remove. The prefetch (1.6.114) is correct and harmless but only helps when scrubbing
  pauses or revisits cached frames; it cannot get ahead of a continuous drag through new frames.
### Notes
- **Needs a viewer:** confirm that opening the streaming `.czi` and then closing PyCAT returns the
  terminal prompt (no more reopening the terminal).

## [1.6.114] - 2026-07-18
### Changed — **CZI prefetch: foreground-priority + direction-aware (fixes back-and-forth scrubbing).**
An audit of 1.6.113's prefetch found it structurally wrong for anything but forward playback: it
published the current frame to the prefetcher only AFTER reading it, prefetched forward-only, and could
hold the reader's lock on obsolete frames while the UI waited on the one frame the user actually moved
to. Redesigned per that audit:

- **Foreground priority.** A read now publishes its request (target + a monotonic generation) and
  raises `_fg_pending` *before* it reads, so the background thread never starts a read while the UI is
  waiting, and abandons an obsolete read-ahead pass the moment a newer request arrives.
- **Direction-aware read-ahead.** The prefetcher follows the scrub: forward for a forward scrub,
  **backward for a backward scrub** (previously all-misses), a symmetric neighbourhood when direction
  is unknown or the frame is held, and a shallow ±2 on a large jump (no far speculation).
- **Buffer-layout guard.** `_read_plane_raw` now asserts the BioFormats plane byte-count matches
  `H·W·itemsize` and reports series/RGB/interleaved on mismatch — a wrong series or layout can be the
  wrong size in a way that still reshapes to a shifted image; this fails loudly instead.
### Notes
- Correctness investigation (the reported "seam"): BioFormats reports the streaming file as a **single
  series, single resolution, 500×500 uint16, non-RGB, non-interleaved**, and plane 0's buffer is
  **exactly** 500·500·2 bytes — so the reader selects the right series and the byte→array reshape is
  correct. The residual ~1.5% column-12 step and the anomalous row 0 are constant across frames and
  are in the acquisition, not the decode. No pixel-decoding defect.
- Benchmarked on the real 8 GB file at 25 fps: forward **40/40**, backward **40/40**, oscillate
  **36/36** frames served from cache (0 ms). **Needs a viewer:** confirm scrubbing actually feels
  smooth — set `PYCAT_CZI_TRACE=1` before `run-pycat` to print the real per-scrub cache hit-rate and
  read latency. If the trace shows high hit-rate but the viewer still lags, the bottleneck is napari's
  render path, not the reader.
- Deferred (audit #3): caching native uint16 for display and normalising to float32 only for analysis
  — would roughly double the cached temporal span, but it splits the display/analysis representation
  and cuts against PyCAT's uniform `[0,1]` loader contract, so it wants its own pass.

## [1.6.113] - 2026-07-18
### Changed — **Streaming CZI scrubbing is smooth: an LRU cache + background read-ahead.**
The direct BioFormats reader decodes ~5 ms/plane, which showed as intermittent stalls scrubbing the
15,766-frame movie frame by frame. The reader now caches planes and reads AHEAD.

- **Byte-budgeted LRU cache** (256 MB → 268 planes at 500², 16 at 2048²) so repeats and small back-and-
  forth scrubs are instant.
- **Background read-ahead**: a single worker thread decodes the next few frames (`_PREFETCH_AHEAD = 8`)
  ahead of the frame last accessed, and bails the moment the user moves on, so a forward scrub lands on
  already-decoded planes. Measured on the real 8 GB file: a 25 fps forward scrub was served **30/30
  from cache (0 ms)**, versus ~5 ms/plane cold.
- Every read (foreground + prefetch) is serialised on one lock — a loci `ImageReader` is not safe for
  concurrent `openBytes` — held per plane (~5 ms), so a foreground miss never waits long.
- **The prefetch thread detaches from the JVM whenever it goes idle.** A JNI thread that attached (via
  `openBytes`) and never detached blocks `DestroyJavaVM`, hanging the whole process at exit — found and
  fixed here; the process now exits cleanly.
### Notes
- Headless-tested: cache hit on repeat, read-ahead caches the frames ahead, the cache is byte-budgeted,
  and close stops the prefetcher. The reader was also run end-to-end on the real 8 GB file (opens,
  reads, prefetches frame 101 after frame 100, exits cleanly). **Needs a viewer:** confirm scrubbing
  the streaming `.czi` is now smooth with no intermittent stalls.
- The `@integration` real-file test hit an intermittent jpype `startJVM` access violation in this
  session's harness (unrelated to this change — the prefetch thread starts *after* JVM init, and it is
  deselected from the core suite); the reader is verified via the standalone benchmark above.

## [1.6.112] - 2026-07-18
### Fixed — **The CZI open no longer cancels itself.**
A regression in 1.6.111 (unreleased): opening the streaming CZI reported "CZI open cancelled" and
aborted on its own, with no user interaction. `QProgressDialog.close()` **emits `canceled`**, so when
the dialog closed on *normal completion*, the cancel handler fired and marked the load cancelled.

- The finish handler now marks completion (`done`), and the cancel handler ignores the
  `canceled` that `close()` emits once the work is done — only a real "Give up" click (or Escape/X)
  *before* completion cancels. Regression-tested (`test_busy_progress.py`, real Qt loop): a successful
  call returns its value instead of raising the cancellation.
### Notes
- Rolls up with 1.6.110 (dedupe + off-thread libCZI probe) and 1.6.111 (dialog auto-closes + "Give
  up"). **Needs a viewer:** confirm the streaming `.czi` now opens to completion on its own, and "Give
  up" still cancels cleanly.

## [1.6.111] - 2026-07-18
### Fixed — **The CZI "indexing" dialog now closes itself, and "Give up" actually works.**
From the viewer, on the streaming-CZI open dialog: it stayed open with the elapsed counter frozen, and
only advanced when the user X'd it out; there was no cancel button; and X-ing out early hung the UI.
All three are the same worker-dialog helper (`_run_with_busy_progress`), which had the exact bug the
newer `qt_worker` was built to avoid.

- **It closes when the work finishes.** `worker.finished` is emitted from the worker thread, and the
  old finish handler was a plain function — so Qt ran it *on the worker*, and `dlg.reset()` from there
  never ended the main thread's modal loop. The dialog hung open (frozen elapsed = work already done)
  until the user dismissed it. The handler is now a `QObject` slot that runs on the main thread (queued
  delivery), ending a `QEventLoop` with `loop.quit()`.
- **A "Give up" button that frees the UI.** The BioFormats index parse is a single uninterruptible JVM
  call, so cancel **detaches**: it stops waiting and lets the orphaned worker finish in the background
  (result dropped), instead of `thread.wait()` blocking the UI until the parse completes — which was
  the hang when X-ing out. The detached thread is retained until it finishes so it can't crash by being
  garbage-collected mid-run. Both CZI open sites report "CZI open cancelled." and abort cleanly.
### Notes
- Same fix benefits both CZI busy dialogs (the libCZI index probe and the BioFormats reader open).
  **Needs a viewer:** confirm the indexing dialog now closes on its own and the layer appears without
  X-ing out, and that "Give up" dismisses it and frees the window immediately.
- Still open, deliberately (secondary): the occasional scrubbing latency on the streaming movie —
  that's the prefetch/cache task (read T±k around the current frame), separate from this dialog fix.

## [1.6.110] - 2026-07-18
### Changed — **Opening a big streaming CZI no longer freezes the UI on the libCZI probe.**
The streaming-CZI reader (BioFormats, shipped 1.6.61) already opened its Java reader off-thread — but
the libCZI **metadata** open that routes to it ran on the Qt main thread, and for a 15,766-frame movie
parsing every subblock offset is ~11 s. Worse, it ran **twice**: once to decide the file needs
BioFormats, then again inside the streaming loader for pixel size / channel names. So ~20 s of "Not
Responding" preceded the (already responsive) BioFormats indexing dialog.

- **The two libCZI opens are deduplicated.** The routing probe (`probe_libczi`) now returns the libCZI
  image alongside its can-read verdict, and the streaming loader reuses it instead of re-opening —
  the multi-second subblock parse is paid once.
- **For a large CZI the probe runs off the Qt thread** behind the existing busy dialog, so even the
  first parse stays responsive. A small confocal/widefield CZI (a few MB, parses in milliseconds) still
  probes inline — a worker dialog would only flash. The gate is file size (`_CZI_OFFTHREAD_BYTES`,
  256 MB), which sits far below any streaming movie and far above any normal CZI.
### Notes
- No change to the reader itself or to normal-CZI behaviour (still libCZI, fast, no JVM). Verified: the
  streaming reader still opens and reads the real 8 GB file (integration test), and `probe_libczi`
  returns the image even when the pixel read fails (headless tests). **Needs a viewer:** confirm
  opening the streaming `.czi` shows the responsive indexing dialog from the start, with no initial
  freeze.
- Housekeeping: the CZI reader was fully built and shipped in 1.6.61 but never got a CHANGELOG entry;
  this documents the format is supported (confocal/widefield via libCZI; Zeiss fast-streaming via the
  opt-in `[bioformats]` extra).

## [1.6.109] - 2026-07-18
### Fixed — **QC on a long movie no longer OOMs; it assesses a bounded sample.**
With the IMS decode fixed (1.6.108), QC got further and then hit a *second* out-of-memory: `run_full_qc`
upcast the whole stack to **float64** (18.8 GiB for a 600×2048² movie), and even at float32 the
per-metric transients (`qc_snr`'s `np.diff` over every frame) are multi-GiB. Both were pre-existing and
independent of the off-thread work. Three parts:

- **QC now assesses an evenly-spaced sample of a long time series**, capped at `QC_MAX_FRAMES` (64).
  The UI reads **only those frames** off disk (`materialize_stack(max_frames=…)` indexes them via
  `__getitem__`), so a 600-frame movie costs ~1 GiB instead of ~18 GiB. QC is a health check, so an
  evenly-spaced sample across the acquisition answers it — and the report now carries a **"Frames
  assessed: N of M"** row that says so, and flags that the sampling lowers the rate the vibration check
  sees (drift, bleaching and focus are sampled across the whole run and unaffected).
- **`_to_float` casts to float32, not float64** — ample precision for every QC metric, half the memory,
  and a no-op (no copy) for a stack already decoded as float32.
- **The 3-D check reads the SHAPE, not a `.ndim` attribute.** The IMS readers advertise a `(T, Y, X)`
  shape but no `ndim`, so `getattr(_layer_data, 'ndim', 2)` read them as 2-D and fell to
  `np.asarray(wrapper)` — the lazy-guard refusal, i.e. the original crash. QC now derives 3-D from the
  shape and takes the decode path.
### Notes
- Headless-tested: `materialize_stack(max_frames=…)` returns evenly-spaced frames and reads ONLY those
  (endpoints included); `_to_float` is float32 and copy-free; the QC report adds the sampling note only
  when it actually subsampled. **Needs a viewer:** confirm QC now completes on the 600-frame .ims with
  the report showing "64 of 600 frames".
- **Judgment call worth your eye:** the sample is *strided* (spans the whole acquisition), which keeps
  drift/bleaching/focus honest but lowers the vibration check's frequency range. If you'd rather QC use
  a contiguous native-rate window (vibration correct, drift only over the window) or raise the 64-frame
  cap, say so — both are one-line changes.

## [1.6.108] - 2026-07-18
### Fixed — **`materialize_stack` could not read the IMS readers (QC crashed on an .ims stack).**
Running QC (or any full-stack analysis) on a lazy `.ims` movie raised
`RuntimeError: An implicit full-stack read was attempted on _ImsReaderTYX`. **A pre-existing bug the
1.6.107 off-thread change surfaced** by re-raising it cleanly instead of swallowing it: the old QC
code called the same `materialize_stack`.

- `materialize_stack` is the *sanctioned* full-read path, but for a lazy wrapper without
  `as_full_array` it fell through to `np.asarray(stack_like)` — and the IMS readers' `__array__` now
  **refuses** an implicit full read (`lazy_guard.refuse_implicit_full_read`) rather than truncating to
  one frame, so the blessed reader raised the very error it exists to prevent. It now reads any 3-D
  indexable wrapper **frame by frame via `__getitem__`** (guard-safe, the same access the guard's own
  message points to), keyed on shape before it ever touches `np.asarray`. Plain numpy / dask /
  `as_full_array` wrappers are unchanged. Regression-tested with a wrapper that refuses `__array__`.
### Changed — **Data Quality Control moved to the top level of Analysis Methods.**
It was tucked inside **Toolbox → Data Visualization**, which is both hard to find and conceptually
wrong — QC is the first thing you do to a dataset, not a plot. It is now a top-level item in the
**Analysis Methods** menu, next to Exploratory Analysis. (Per-frame **Frame Quality / Focus QC** stays
under Data Visualization; that is the different, per-frame scorer.)
### Notes
- Headless-tested: `materialize_stack` reads a guard-refusing 3-D wrapper frame-by-frame, preserves
  label dtype, and drives the progress callback. **Needs a viewer:** confirm QC now runs on the .ims
  stack (with the modal decode dialog), and that Data Quality Control appears at the top of Analysis
  Methods.

## [1.6.107] - 2026-07-18
### Changed — **Every widget's stack decode runs off the Qt thread now.**
The other half of 1.6.106. Fourteen sites across eight widgets decoded a lazy stack with
`materialize_stack` on the Qt main thread — the 1.6.81/82 progress bars made that wait visible (a
synchronous `repaint()` advances the bar) without making it shorter, so the window could still say
"Not Responding" while the bar moved. All fourteen now decode through a worker.

- **New `qt_worker.materialize_off_thread(layer.data, viewer=…, **kw)`** wraps `materialize_stack` in
  `run_with_progress`: the decode runs on a `QThread` behind a modal dialog, and the array comes back
  on the caller's thread — safe to hand straight to analysis, exactly as before. `dtype=` and any other
  kwargs pass through unchanged.
- **Converted:** FRAP (recovery + pre-bleach), condensate-physics (fusion + QC), data-QC,
  brightfield (dynamics + focus-QC), in-vitro fluorescence (dynamics + intensity + QC), in-vitro
  brightfield (dynamics + focus-QC), fusion (image mode), and the temperature module's shared cached
  `_get_stack` (which froze once, on whichever section was clicked first). The inline `PhasedProgress`
  bars for the decode phase are retired in favour of the modal dialog.
- **Not converted:** FRAP's 2-D per-candidate scan (`_offer_stack_2d_images`) — it decodes single 2-D
  frames in a loop, where an off-thread dialog would flash once per candidate. It stays synchronous and
  is the one excused entry.
### Notes
- The progress-rollout ratchet (`test_progress_rollout.py`) is rewritten for the new contract: a
  `*_ui.py` that decodes a stack **directly** (synchronously, on the Qt thread — bar or no bar) now
  fails; the way to pass is to route it through `materialize_off_thread`. The countdown is at zero.
- Headless-tested: the helper decodes via `materialize_stack` on the worker, passes kwargs and a
  callable progress callback, and survives a viewer with no Qt window; plus the five real-thread
  integration tests (work off-main, value back on-main, progress crosses to main, errors re-raise,
  threads cleaned up). **The per-widget feel needs a viewer** — confirm a dynamics/QC/FRAP run on a
  long stack shows the modal dialog and no longer says "Not Responding".

## [1.6.106] - 2026-07-18
### Changed — **Session load runs off the Qt thread — no more "Not Responding".**
Loading a session lagged the UI (you reported it; Windows shows "Python is not responding" on a longer
one) because `load_session` did its slow work — `tifffile.imread` per derived layer, `pd.read_csv` per
table — on the Qt main thread. The 1.6.81/82 progress bars made that wait *visible* without making it
*shorter*. This is the other half: the read moves to a worker thread while a modal dialog keeps the
window painting.

- **`load_session` is split into a read half and an apply half.** `_read_session_payload` does the
  decode and the CSV reads and touches **no viewer** (structurally — it has no viewer parameter), so it
  is safe to run on a `QThread`. `_apply_session_payload` creates the napari layers and writes the data
  repository, always on the caller's thread — because `viewer.add_*` off the main thread is a crash,
  not a freeze. `load_session` orchestrates the two via `pycat.utils.qt_worker.run_with_progress`.
- **The UI wiring** (`_open_session_loader`, and the quick "restore latest" path) passes
  `use_worker=True`. The worker owns a modal `QProgressDialog`; the old in-dialog progress bar is
  retired so there aren't two bars for one operation. Headless callers and tests default to
  `use_worker=False` (synchronous) and are unaffected — `run_with_progress` also falls back to
  synchronous when there is no running Qt app.
- **`qt_worker.run_with_progress`** (new, `pycat/utils/qt_worker.py`) runs a function on a `QThread` and
  returns its value **on the caller's thread**, re-raising exceptions there so existing `try/except`
  still works. It deliberately refuses a callback/future API so nobody is tempted to create a layer in
  the worker. Two subtle bugs in the pattern it replaces are fixed in it: a fast worker finishing before
  `exec_()` is entered (deadlock), and a signal-to-plain-function running the slot on the worker thread
  (off-main widget touch). Both were headlessly tested.
### Notes
- Headless-tested: the read half takes no viewer and decodes into a payload; the apply half is the only
  half that calls `viewer.add_*`; the synchronous round trip is unchanged; the worker helper's deadlock
  and thread-affinity fixes. **The off-thread feel needs a viewer** — confirm a real multi-file session
  restore no longer says "Not Responding" and the modal progress dialog advances while the window stays
  responsive.
- This also stages `load_session` (was 149 lines, over the complexity ceiling) into per-phase helpers —
  the prerequisite the roadmap called out either way. The same `qt_worker` helper now exists to move the
  per-widget `materialize_stack` freezes off-thread next (the other half of the same fix).

## [1.6.105] - 2026-07-18
### Changed — **The picked-track highlight is a Tracks layer at 2× the base width.**
From the viewer: after zooming to the bead, the picked-track line was still too thick to read the
trajectory's detail. The cause was a unit mismatch — the highlight was a Shapes path whose width is in
**data units**, so it ballooned as the new zoom-to-bead magnified the view, while the base "Bead
Trajectories" layer (a napari Tracks layer) has its width in **screen pixels** and stays constant.

- **The picked track is now a Tracks layer**, the same type as the base, so its `tail_width` is in
  screen pixels and no longer fattens at deep zoom. The width is exactly **2× the base**
  (`_PICKED_TRACK_TAIL_WIDTH = 2 · _BASE_TRACK_TAIL_WIDTH`, both new constants) — bold enough to stand
  out, thin enough to read the detail — which is what the user asked for by eye.
- **Still orange, still a separate overlay.** It colours via a registered flat-orange colormap
  (`#ff8c00`) rather than recolouring the base layer, so a user's own track colouring is never
  clobbered by a pick. `tail_length`/`head_length` span the whole track so it draws fully at any
  frame, including the bead's first frame. Falls back to a thin Shapes path only if `add_tracks` is
  unavailable.
### Notes
- Headless-tested: the picked track is a Tracks layer at 2× the base width, orange, and spans its full
  frame range. **The zoom-stable feel is UI-coupled** — confirm the line reads well at the zoom-to-bead.

## [1.6.104] - 2026-07-18
### Changed — **A VPT plot click now goes to the bead; the pulse is gone.**
From the viewer, on the picked track: the opacity slider oscillated continuously with no visible glow,
the highlight line was too bold to see detail through, and a click should take the stack to the bead's
z-slice and zoom in. Three fixes.

- **A plot click navigates to the bead — on by default.** `_navigate_to_bead` steps to the bead's
  frame, centres on it, and **zooms** so a small window (`_BEAD_ZOOM_WINDOW_PX = 80 px`) around it fills
  the view. Navigation was gated off while the plot-click loop existed; with one `button_press` per
  click (1.6.100) and the `_revealing` re-entrancy guard, the camera move is safe, so going to the bead
  — what the user asked a click to do — is the default now. VPT's now-unused `_follow_enabled` wrapper
  was removed; the generic brushing path keeps its own for the `follow_selection`/double-click case.
- **The pulsing ring was removed.** `_pulse_layer` armed a QTimer that oscillated the ring's
  size/opacity. But the ring is per-frame — present only on the bead's own frame — so scrubbing away
  left nothing to pulse while the opacity slider churned on for nothing. The ring is a static hollow
  marker now (`size=12, opacity=0.9`); the zoom-to-bead navigation is what draws the eye.
- **The picked-track highlight was thinned**, `_PICKED_TRACK_WIDTH_PX` 1.0 → 0.4, so the trace no
  longer obscures the trajectory detail underneath it.
### Notes
- Headless-tested: the pick navigates (steps + centres) and marks the track, the reveal stays
  re-entrant-guarded so navigating cannot loop, the ring is static with no timer armed, and the removed
  symbols are recorded in `_DELIBERATE`. **The zoom-to-bead feel is UI-coupled and needs a viewer** —
  confirm a plot click lands on the bead at a sensible zoom and the thinner line reads well.

## [1.6.103] - 2026-07-18
### Added — **Session auto-restore: a load reopens the analysis method and rebuilds its view.**
Loading a session restored the dataframes into the repository but left an empty panel — the user had
to reopen the method and re-Compute by hand. Now a load lands back at the working state.

- **The active method is recorded on save.** The manifest gains `active_method` (the open analysis
  UI's class name), written by `write_session_outputs`.
- **The loader surfaces it**, and `_on_load` reopens that method via its `_switch_to_*` handler.
  Switching methods **preserves the data repository**, so the reopened method sees the restored data.
  A session saved before this was recorded has no `active_method`; the method is then inferred from a
  signature dataframe (`vpt_tracks` → VPT), so existing sessions restore too.
- **The reopened method rebuilds its view.** `VideoParticleTrackingUI.restore_session_view` rebuilds
  the trajectory + pickable layers and calls `_on_rheology` — the exact handler the **Compute MSD &
  Viscosity** button runs, which reads `vpt_tracks` from the repository — so the MSD/moduli plots come
  back through the one real render path, not a divergent copy. The slow part of VPT (detection +
  linking) is not redone; recomputing the MSD from the restored tracks is seconds.
### Notes
- Headless-tested: the manifest records/surfaces `active_method`, back-compat returns None (inferred
  from data), the method registry wires VPT correctly, and the restore hook exists. **The end-to-end
  reopen → rebuild → plots is UI-coupled and needs a viewer** — this is the part to confirm: load the
  session and check the VPT method reopens with its tracks clickable and its plots drawn.
- Parameters return at their defaults (frame interval auto-fills from the source metadata); a user who
  needs the session's exact bead radius/temperature sets them and re-Computes. Restoring the exact
  recorded parameters is a later refinement.
- Only VPT has a `restore_session_view` so far; other methods reopen (data preserved) and show a
  "reopen to rebuild" toast until they gain the same hook — additive, method by method.

