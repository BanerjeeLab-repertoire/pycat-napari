# Claude Code spec — VPT rework: detection speed, MSD default, brushing loop, selection overlay

## ✅ STATUS — DONE. All five problems implemented (1.6.83 + 1.6.85), verified against 1.6.91.
This spec had already been executed across two versions before this closure; it just never carried a
status block. Verified: every problem is implemented, each has a dedicated passing test, and **three
of the five root causes did not survive contact with the tree** — the fixes address what is actually
there, and the deviations are recorded below so they do not read as unmotivated.

- **Problem 1 — equivalence guard + tier default (1.6.83).** The guard is memoised on its true
  invariants ``(gpu build id incl. cupy version, min/max/num sigma, threshold)`` — a hit does not even
  read frame 0; a miss still runs the full double-detect, so a mismatching GPU is never trusted. The
  *"Runs once, cheap"* comment the spec quoted **did not exist** in that form. And the tier default was
  done better than asked: rather than a heuristic, ``_choose_detection_tier`` now **measures** all
  three (GPU / CPU-pool / serial) and takes the minimum — measured, GTX 1080, the 7-worker pool beats
  the GPU (the GPU is only ~2-3× one core), and the pool's spawn cost is Amdahl-modelled so a
  20-frame stack is not handed a 5-second pool for 0.27 s of work. Tests: ``test_vpt_equivalence_guard_memo``,
  ``test_vpt_pool_gate``.
- **Problem 2 — MSD default + fragmentation diagnostic (1.6.85).** ``MIN_TRACK_LENGTH_FRAMES = 200``
  (spec's floor), *derived* from the committed lag-window reasoning (30 honest lags × the n/4 rule =
  120-frame minimum; 200 gives 50 lags with headroom) and pinned to the fit gate by a test so they
  cannot drift. Short tracks are **reported, not silently dropped** — two fragmentation signatures
  (co-located tracks in non-overlapping frame windows; gappy tracks), reported, not re-linked. Tests:
  ``test_msd_min_track_length``, ``test_msd_drift``.
- **Problem 3 — plot-click loop (1.6.83).** ``_reveal_track_in_viewer`` is wrapped in a ``_revealing``
  re-entrancy flag released on the next event-loop tick, and the camera/frame move is gated on the
  shared ``follow_selection`` preference (OFF by default, from brushing increment 5) — so the default
  case does not move the view at all and cannot loop. The already-selected click is a no-op. Test:
  ``test_vpt_reveal_loop``.
- **Problem 4 — ring offset (1.6.85).** The root cause was **not padding** — ``pad_px`` never reaches
  the overlay marker (it is only consumed by the offline crop; now pinned by a test). The ring sat at
  the frame-0 position because ``add_points(path[:1])`` had no frame axis, so a ``(T,Y,X)`` viewer drew
  it on every frame at the start point. Fixed with one point per frame at ``(frame, y, x)``, plus a
  ~1.5 Hz display-only pulse (one QTimer per pick, self-cancelling). Test: ``test_picked_bead_ring``.
- **Problem 5 — shifted-ghost trajectory (1.6.85).** There is **no shifted ghost**: the "Picked track"
  overlay and the Tracks layer both take their scale from the image layer, so they align exactly. The
  duplicate overlay is kept (rather than recolouring the Tracks layer, which would borrow display
  state to mean "selected" — the ``selected_label`` mistake ``selection_overlay`` exists to undo) and
  demoted to secondary emphasis, since the ring now pulses on the bead.

**Closed here (1.6.91-era):** ``tests/test_vpt_parallel_equivalence.py`` had been RED — a pre-existing
stale test (added 1.5.293, broken by a later tifffile upgrade, invisible because it is not
``core``-marked). Its fixture wrote a ≤4-frame stack with a bare ``imwrite``, which tifffile now reads
back as an **RGB image**, so the by-descriptor disk read diverged from the in-memory serial path. Fixed
with ``photometric='minisblack'`` (tifffile's own documented cure); real long acquisitions never hit
this. The parallel/serial detection equivalence contract is now genuinely green.


**Date:** 2026-07-16 · **Target tree:** 1.6.80 · Verified against the 1.6.80 tree. Five distinct,
independently-root-caused problems from a real VPT workflow run. Each is separable — ship as one
version or several, but fix them by cause, not symptom. Touches `vpt_tools.py`, `vpt_ui.py`,
`analysis_plots.py`, `selection_overlay.py`; not `file_io.py`.

---

## Problem 1 — detection got SLOWER; the equivalence guard is unmemoised overhead on the hot path
**Root cause (verified).** `detect_beads_stack` (`vpt_tools.py:1580`) tier selection runs a GPU/CPU
**equivalence guard** (`:1685–1707`): before trusting GPU, it detects frame 0 on BOTH the CPU
(skimage) and the GPU and compares the coordinate sets. The comment says *"Runs once, cheap"* — but
there is **no caching whatsoever**, and `detect_beads_stack` is called from **4 sites** in `vpt_ui.py`
(:704 preview, :1033 Mode C, :929/933 main). So every detect — including previews and every param
re-run — pays a full CPU-detect + GPU-detect + compare of frame 0 before the real work starts. That is
pure, repeated overhead on the hot path, and a plausible cause of the "GPU felt slower than CPU-parallel
in the real workflow" observation.

**The insight (Gable):** GPU/CPU equivalence is a property of the **machine + cupy/driver build + the
LoG params**, NOT of the data. It cannot change between two calls in one session. So it must run **once
per process, memoised** — never per call.

**Fix:**
1. Memoise the equivalence verdict at module/process scope, keyed by the true invariants:
   `(gpu_available(), cupy_version, min_sigma, max_sigma, num_sigma, threshold)` — NOT by the stack.
   First call computes it; every subsequent call in the session reads the cached bool for free.
   (A module-level dict or `functools.lru_cache` on a small helper that takes those params.)
2. The guard's frame-0 double-detect only runs on a cache MISS. A session with the same params never
   pays it twice.
3. **Reconsider the default tier.** Gable's real-world run had CPU-parallel BEATING GPU (marginal in
   the audit, worse in practice — GPU setup/transfer + the guard overhead). Options, pick the honest
   one: (a) benchmark GPU vs CPU-parallel on the FIRST real stack once per session and pick the winner
   (cache that too); OR (b) expose the tier as a user choice (Auto / GPU / CPU-parallel / Serial) with
   Auto defaulting to CPU-parallel when the GPU margin is within ~20% — since the guard tax + transfer
   often erase a small GPU win. Do NOT hard-prefer GPU when it isn't reliably faster here.
4. Fix the misleading comment ("Runs once" → actually memoised now).

**Test** (`core` where GPU-free): the equivalence helper is called once for N repeated
`detect_beads_stack` invocations with the same params (assert the double-detect runs once, not N
times — patch/spy the frame-0 detect). The tier selection is deterministic and cached.

---

## Problem 2 — MSD default `min_track_length=5` is far too short (scientific)
**Root cause (verified):** `min_track_length: int = 5` (`vpt_tools.py:2446`), wired to
`self._min_track.value()` in the UI (`vpt_ui.py:2029/2115/2134`). Five-frame tracks cannot support a
meaningful MSD/α fit — they're dominated by localization noise and short-lag artefacts.

**Fix:**
1. Raise the default to **≥200 frames**, with a scientific justification in the docstring + CHANGELOG:
   a reliable α/D fit needs enough independent lag samples that the MSD's diffusive slope is separable
   from the localization-noise floor (`N = 4σ_loc²`) and the finite-track-length bias; ~200 frames
   gives a lag window broad enough for the log-log slope to be estimated with a usable confidence
   interval. Tie it to the framerate/duration reasoning already in the lag-window fit gate. (Pick the
   defensible number — 200 is the floor Gable specified; justify whatever you set.)
2. **The short tracks are a LINKING failure, not just short data** (Gable: "stable beads, still
   fragmented"). So don't silently drop them — when a track is short DESPITE its bead being stable
   across many frames, that's the dropout/fragmentation signature. Surface a count/flag: "N tracks
   rejected as too short; M of them cover beads present >K frames → likely linking fragmentation, not
   absence." This turns a silent filter into a diagnostic (consistent with the no-silent-gates
   contract). Do NOT try to FIX the linking here — just make the fragmentation visible so the number
   isn't quietly trusted.

**Test:** the default is ≥200; a synthetic set of long clean tracks + short fragments recovers the
right D from the long ones and REPORTS the fragment count rather than silently excluding it.

---

## Problem 3 — the MSD plot click loops forever (force-close territory)
**Root cause (verified):** the plot's `_on_pick` (`analysis_plots.py:680`) → `on_pick_track(tid)` →
`_select_track(tid, source='plot')` (`vpt_ui.py:150`). `_select_track` IS guarded (SelectionService +
`source_view='vpt.plot'` suppression). BUT the selection propagates to `_reveal_track_in_viewer`
(`vpt_ui.py:1650+`), which sets `viewer.dims.current_step` (:1702) and `viewer.camera.center` (:1710).
**Moving the camera/frame fires a `draw_event`**, which re-runs the plot's blit-capture (`_pcapture`,
:672) and can re-enter selection/redraw — a continuous jump loop until force-close. The guard stops
selection ECHO, but not the camera-move → draw → re-selection re-entrancy.

**Fix:**
1. Gate the camera/frame move behind re-entrancy protection: while `_reveal_track_in_viewer` is
   applying a camera/step change, set a `_revealing` flag; the `draw_event`/`current_step` handlers
   that could re-emit must no-op while it's set (release on the next event-loop tick, the same delayed
   pattern the SelectionService uses).
2. Respect the **"Follow selection in viewer" preference from brushing increment 5** — camera-follow
   should be OPT-IN and OFF by default. With follow off, a plot click highlights (overlay + row) but
   does NOT move the camera/frame at all — which removes the loop entirely for the default case.
   (Gable's note that the circle is enough emphasis when properly zoomed reinforces: don't auto-move
   the view on every click.)
3. Ensure a click on the ALREADY-selected track is a no-op (it partly is at :688 — verify it doesn't
   re-trigger the reveal).

**Test:** simulate a plot pick that triggers a reveal; assert the camera-move does not re-enter the
pick/selection handler (spy the selection count — one selection per click, not unbounded); with
follow-off, assert no camera/step mutation occurs.

---

## Problem 4 — the orange circle is offset even when empty; make it a pulsing glow at the true centre
**Root cause (verified):** the highlight uses `pad_px=8` (`object_ref.py:351/459`,
`resolve_in_viewer`/`crop_slice`) so the bbox rectangle/marker sits padded OUTWARD from the object.
For an empty or near-empty bbox that reads as "off to the side of where my eye expects." `_rect_for`
(`selection_overlay.py:43`) itself is at true coords — the offset is the padding.

**Fix:**
1. For the selection MARKER (the circle), drop the outward pad — place it at the true centroid
   (`_centre_for` already computes the exact centre; use that, unpadded). Padding is fine for the
   CROP window (you want context around the object), but the highlight marker should sit ON the
   object, not offset from it.
2. Replace the static circle with a **pulsing glow** to draw the eye without obscuring the bead:
   animate the marker's size/alpha (a napari Points layer's `size`/`opacity`, or a small
   `QTimer`-driven oscillation on the overlay artist) — a gentle 1–2 Hz pulse. If per-frame animation
   is too costly, a one-shot expand-and-fade "ping" on selection is an acceptable simpler version.
   Keep it a display overlay (never touches the data layer).

**Test:** the selection marker's coordinate equals the object centroid (not centroid+pad); the pulse
is display-only (asserting the overlay layer, not the labels/image layer, carries it).

---

## Problem 5 — the shifted-outline trajectory looks terrible; bold the real one (or rely on the circle)
**Root cause:** the selection draws a SECOND, offset copy of the trajectory outline (tied to the same
`pad_px` shift as Problem 4), so the highlighted path is a displaced ghost of the real track — visually
wrong.

**Fix:**
1. Do NOT draw a shifted/duplicate trajectory. Emphasise the ACTUAL track line in place: bold it
   (increase linewidth + full alpha + raise zorder) — the same in-place blit emphasis the MSD plot
   already uses for its curves (`_pblit_highlight`), applied to the trajectory overlay.
2. Gable's observation: when the view is properly zoomed to the selected bead, the pulsing circle
   (Problem 4) may be sufficient emphasis on its own. So make the bolded-trajectory a
   secondary/optional emphasis, with the circle as the primary. Don't stack a heavy trajectory
   redraw on top of an already-clear zoomed circle.
3. Whatever emphasis is drawn must be at the track's TRUE coordinates — no offset.

**Test:** the highlighted trajectory shares coordinates with the base track (no offset copy); emphasis
is a style change on the real artist, not a second artist at shifted coords.

---

## Steps
1. Problem 1: memoise the equivalence guard; reconsider/expose the tier default; fix the comment; test.
2. Problem 2: raise `min_track_length` default (justified) + the fragmentation-diagnostic count; test.
3. Problem 3: `_revealing` re-entrancy guard + opt-in camera-follow default-off; test.
4. Problem 4: unpadded centre marker + pulsing glow; test.
5. Problem 5: in-place bold emphasis, no shifted duplicate; test.
6. Full `pytest -m core` green (esp. VPT viscosity chain, brushing, complexity budget — extract
   helpers rather than growing `_reveal_track_in_viewer`/`detect_beads_stack` past 120 lines).
7. Ship: own version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (VPT: equivalence guard
   memoised, MSD default raised with justification + fragmentation diagnostic, plot-click loop fixed,
   selection overlay centred + pulsing, trajectory emphasis in-place).

## Definition of done
- Repeated detects in one session pay the GPU/CPU equivalence check ONCE; the tier default reflects
  real measured performance (CPU-parallel not force-lost to a slower GPU).
- MSD default ≥200 with a scientific justification; short-but-stable tracks are reported as likely
  linking fragmentation, not silently dropped.
- A plot click never loops; camera-follow is opt-in/off by default; one selection per click.
- The selection circle sits ON the bead (no offset) and pulses; no shifted ghost trajectory — the real
  track is bolded in place (or the circle alone suffices when zoomed).
- Full `pytest -m core` green.

## Cautions
- The equivalence guard's PURPOSE (never trust a mismatching GPU) must be preserved — memoise the
  VERDICT, don't remove the check. A cache miss still runs it once.
- Don't remove camera-follow entirely — make it opt-in. Some users want it; the loop is the bug, not
  the feature.
- Overlay changes are display-only — never mutate the data/labels layers.
- The short-track diagnostic REPORTS fragmentation; it does NOT attempt to fix linking here (that's
  the separate linker-gap work — the VPT detection/linking baseline is validated at 8.325, don't
  regress it).
- Keep the MSD default change consistent with the lag-window fit gate's framerate/duration reasoning —
  the two must agree on what a "usable" track length means.
