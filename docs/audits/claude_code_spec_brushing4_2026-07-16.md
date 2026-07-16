# Claude Code spec — Brushing increment 4: the scaling fixes

## ✅ STATUS — Parts A/B/D DONE, shipped in 1.6.76. Part C + the LRU DEFERRED, with reasons.
`pytest -m core`: **670 passed, 2 skipped** (was 661). Every claim in this spec was **measured before
being acted on**, and measuring inverted its priorities.

**Part A — CONFIRMED, and worse than stated.** `refs_from_dataframe(100k)` = **6.43 s**, against
**0.02 s** for the scatter itself: the refs cost **~380x the plot they decorate**, and a click uses
one of them. Now **0.006 ms** at wiring (a `LazyRefs` sequence; same signature, same callers), with
the single ref built on the click (0.34 ms).

**Part B — the stated rationale is WRONG, and it is hiding a correctness bug.** `_emphasise` on
100 000 points measures **1.28 ms**. It is not an O(N) performance problem in any sense a user could
feel. But it is **highlighting the wrong points**: a scatter built with a scalar `s=` reports ONE
size, the `np.repeat` guard reads a `state['n']` that never existed (no-op), so `set_sizes` receives
an array of length `index+1` — **shorter than the collection** — and matplotlib **tiles** it.
**Clicking point 5 of 20 enlarges points 5, 11 and 17.** Done, as a correctness fix; the overlay
artist is the right design for the reason the spec didn't give.

**Part D — done for the debounce, NOT for the LRU.** `subscribe_deferred` coalesces the expensive
(pixel-reading) half of a burst to one resolve on a ~30 ms trailing edge; cheap feedback still lands
on every event. The LRU is not done: the lazy readers already hold handles open, a crop is ~0.17
ms/plane (measured in 1.6.71), and the debounce removes the burst the cache was meant to absorb.
Caching decoded frames is the materialization trap this arc exists to avoid.

**Part C — NOT DONE, deliberately.** Its premise is that a Points layer holding every frame is slow.
**Unverified:** napari already slices Points by dims, so the rendering is per-frame already; the
benefit could not be measured here (offscreen Qt has no GL); and the change rewires the *validated*
bead picker's `index → track_id` mapping (`layer.get_value(...)` → `self._bead_picker_tids[idx]`),
which only works while the layer holds every point. After Part B's stated rationale turned out to be
wrong by ~1000x, refactoring a working picker on an unmeasured claim is not a trade worth making.
**It wants a profile on a real long acquisition first** — if the layer genuinely costs, the
`frame → indices` map the spec describes is the right shape.

**One bug of my own, caught by a test:** a `Sequence`'s iteration walks `__getitem__` until
`IndexError`, so `LazyRefs` has to bounds-check **before** its catch-all — otherwise a swallowed
`IndexError` is answered with a blank ref and `for ref in refs` loops forever.

**Date:** 2026-07-16 · **Target tree:** verified against 1.6.70. **PREREQUISITE: increments 1–3
landed** (identity + SelectionService). Re-validate line numbers when you start. These make brushing
FAST on big plots — they plug into the increment-3 service. Touches `utils/brushing.py`,
`utils/object_ref.py`, `vpt_ui.py`; not `file_io.py`.

## The three O(N) / eager problems (verified in the tree)
1. **Eager ref construction** — `refs_from_dataframe` (`object_ref.py:152`) uses `iterrows()` to build
   one `ObjectRef` per row before a plot is wired. For 100k points: 100k Series + 100k objects +
   duplicated strings/tags on the figure. Expensive well before the scatter itself is.
2. **O(N) highlight** — `_emphasise` (`brushing.py`) rewrites the WHOLE marker-size array on every
   click (`np.full(...)`, `set_sizes`), so a click on a 100k-point plot updates 100k sizes and redraws
   the collection.
3. **VPT bead points duplicated per frame** — `_add_pickable_bead_points` (vpt_ui.py) adds one
   Points-layer point per bead per frame; for long acquisitions a huge Points layer, though only the
   current frame is useful for picking.

## Part A — lazy ref construction
Replace `iterrows()` eager build with: the figure retains a compact NumPy array of the increment-2
`_pycat_entity_id`s (strings/ints), NOT a Python object per point. Build the `EntityRef` ONLY on click:
`plot index → entity_id → df row → EntityRef`. `refs_from_dataframe` keeps its signature for
compatibility but returns/attaches the compact id array + a lazy resolver, not a materialized list.
Opening a 100k-point brushable plot must not allocate 100k refs.

## Part B — selection-overlay artist (O(1) highlight)
Replace `_emphasise`'s full-array rewrite with a SECOND one-point overlay artist:
- base scatter: never modified after creation;
- selection artist: a single marker whose (x, y) is set to the selected point.
Each selection updates two coordinates + blits — O(1), not O(N). VPT already uses this philosophy for
lines (its blit highlight); generalize it to the generic scatter. Multi-selection = a small overlay
with the k selected points (still ≪ N).

## Part C — VPT bead points: current frame only
`_add_pickable_bead_points`: keep ALL coordinates in a compact DataFrame; the Points layer holds only
the CURRENT frame's detections. Update the visible subset on `viewer.dims.events.current_step`. For
click resolution keep a `frame → point indices` map (or a per-frame KD-tree for dense detections).
Cuts rendering, hit-testing and property-array overhead on long movies.

## Part D — modest caches + event coalescing (into the increment-3 service)
- LRU: last 2–4 decoded frames, last 32–64 crops, entity-id resolution, per-frame spatial indices.
  NEVER cache full acquisitions.
- Coalesce rapid hover/keyboard selections with a ~20–40 ms trailing debounce for the EXPENSIVE image
  resolve (crop/reveal); update CHEAP feedback (row/point highlight) immediately, then async-resolve
  the image for the most recent selection only. Wire the debounce into `SelectionService` so every
  view benefits.

## Steps
1. `object_ref.py`: lazy `refs_from_dataframe` (compact id array + on-click resolver).
2. `brushing.py`: overlay-artist highlight replacing `_emphasise`'s O(N) rewrite.
3. `vpt_ui.py`: current-frame bead Points + `frame→indices` map + `current_step` update.
4. LRU + debounce in/around `SelectionService`.
5. Tests (`core` where possible): opening an N-point plot allocates O(1) refs not O(N) (assert no
   per-row object build); a selection updates only the overlay artist (assert base scatter sizes
   untouched); the bead Points layer holds one frame's count not all frames; the LRU is bounded;
   the debounce coalesces a burst to one image resolve.
6. Full `pytest -m core` green (esp. VPT brushing + complexity budget).
7. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (increment 4: O(1)
   highlight, lazy refs, current-frame bead points, caches/debounce).

## Definition of done
- A 100k-point brushable plot OPENS without allocating 100k refs and HIGHLIGHTS in O(1).
- VPT bead Points layer holds only the current frame; picking still resolves via the frame index.
- Caches bounded; rapid hover coalesces to one expensive resolve.
- VPT three-way link still correct; full `pytest -m core` green.

## Cautions
- Keys/refs are the increment-2 stable ids and route through the increment-3 service — don't
  reintroduce eager per-row `ObjectRef` or O(N) size rewrites.
- The overlay artist must not change the base scatter's picking (make_pickable still maps click →
  index on the BASE artist; the overlay is display-only).
- Never cache a full acquisition (the materialization trap this whole arc exists to avoid).
- Don't build the linked-selection dock / table adapter (increment 5) here.
