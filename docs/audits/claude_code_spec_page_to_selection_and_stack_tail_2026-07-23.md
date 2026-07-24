# Claude Code spec — Page to the selected track, and close the stack-access tail

> **◐ STATUS — Part 1 (page-to-selection) ALREADY DONE (built after the spec's 1.6.324 target). Part 2
> (stack-access tail) remains — a curated per-site sweep, not a blind ratchet.**
>
> **Part 1 — DONE (verified at 1.6.328).** `_vpt_page_to_selected_track` (`toolbox/vpt/results_dock.py:289`)
> does exactly what the spec asks: recomputes the bucket from the ordered `all_tids` and the CURRENT
> `bucket_size` (`index(tid)//size + 1`), moves `st['page']` and re-renders through the existing
> `_vpt_render_page` → re-highlight path, is a no-op for an on-page pick (`_vpt_drawn_tids()`) and for the
> page-0 ensemble, and never caches the index. It is wired at `table_adapter.py:70` (called after
> `service.select` propagates, non-gating), so any selection source pages to the track. `tests/
> test_vpt_page_to_selection.py` (8 tests: off-page moves + labels, on-page no-move, boundaries, bucket-size
> recompute, dock-closed no-op) — all pass. No further work needed.
>
> **Part 2 — REMAINING.** ~26 curated `np.asarray(<layer>.data)` sites on stack-consuming paths should route
> through `materialize_stack`/`iter_frames`. This is explicitly a **per-site judgement** sweep (the spec:
> "not a blind sweep" — most `.data` sites are 2D-only where `asarray` is correct; ~83 `.data` calls exist in
> total, of which only genuine lazy-time-series consumers need the helper), with output-identical
> characterization per site and a differing output reported as a finding. Deferred as its own careful sweep.

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. Two small,
independent follow-ons from earlier specs whose other parts have shipped.

---

## Part 1 — Clicking a bead outside the displayed bucket must page to it

Reflow (1.6.311) and reopen (1.6.312) landed; **this third part of that spec did not**. Verified: no
`page_for_track`-style logic exists in `results_dock.py`.

### The problem
VPT produces thousands of tracks, so the plots page through them in buckets. **Every bead in the image is
clickable**, including beads whose track is not in the displayed bucket. Selecting one promotes its curve
onto the *current* page's axes (`_highlight_track_in_centered` does this deliberately) — but
`st['page']` only ever changes in `_vpt_page_step` and `_vpt_set_bucket_size`. **Nothing sets the page
from a selection.**

So the pager still reads *"Tracks a–b of 4433 (bucket k / N)"* — a range that excludes the highlighted
track. **The plot and its own label contradict each other**, and the selected curve appears without its
cohort. For outlier investigation, seeing the picked track alone on someone else's page is the wrong
answer.

### The change
When a selection arrives for a track **not on the current page**:
1. Compute its bucket from the retained, ordered `all_tids` (line 155) and the current `bucket_size`
   (line 221): `position_in_all_tids // bucket_size + 1` (page 0 is the ensemble; buckets are 1-based).
2. Set `st['page']` to that bucket and re-render, then apply the highlight — **reuse the existing
   `_vpt_render_page` → re-highlight path**, do not add a parallel one.
3. The pager label follows automatically once the page is right.
4. **Stay on page 0** if the track is already in the representative ensemble.
5. **Do not move** for a selection already visible on the current page — re-paging on an on-page click
   yanks the view for no reason.

**Recompute the bucket index on every selection** from `all_tids` and the *current* bucket size; never
cache it, or changing bucket size sends the user to the wrong page.

### Design note (flag, don't decide)
Auto-paging moves the view under the user. Recommended: always page for increment 1. The brushing work
already distinguishes a plain click from a navigating double-click
(`test_a_plain_click_does_NOT_yank_the_viewer`), so if this proves jarring the same distinction applies
here rather than designing for it up front.

### Tests
- Selecting a track in another bucket moves the page there, renders it, and highlights it; the pager
  label names the new bucket.
- A track already on the current page does **not** change the page.
- A track in the page-0 ensemble keeps the user on page 0.
- Bucket index is correct at boundaries (first/last of a bucket, final partial bucket).
- Changing bucket size then selecting lands correctly (index recomputed, not cached).
- With the dock closed, a selection is a clean no-op.

---

## Part 2 — The stack-access consolidation tail

**Verified: 26 `np.asarray(<x>.data)` sites remain.** The pixel-size and background-mechanics axes of the
redundancy spec shipped (1.6.212, 1.6.258); this one did not.

### The work
Route stack-consuming sites through `materialize_stack` (or explicit `iter_frames`) so there is one
stack-access path with one defused behaviour — this is the frame-0-collapse landmine.

- **Per-site judgement, not a blind sweep.** Most sites are 2D-only where `asarray` is correct; only
  genuine lazy-time-series consumers need `materialize_stack`. For a 2D site both return the same array,
  so routing through the helper is output-identical and removes the footgun.
- **Output-identical is the law.** Characterize before and after. If a site's output *differs*, that site
  had a real frame-0 bug — **report it as a finding**, don't silently "fix" it.
- The existing `test_silent_fallbacks::test_the_stack_helpers_have_ONE_implementation` and the
  lazy-stack no-collapse tests are the net.

### Tests
- Each converted site: output identical (2D sites), or the frame-0 bug demonstrably fixed (lazy sites) —
  stated per site.
- The remaining-site count drops by the number converted.
- A guard prevents new bare `np.asarray(<layer>.data)` on a stack-consuming path.
- Existing lazy-stack tests pass unmodified.

---

## Steps
1. Page-to-selection: bucket computation + page move through the existing render/highlight path; tests.
   Ship.
2. Stack-access: per-site conversion with characterization; guard against new sites; tests. Ship.

## Definition of done
- Clicking any bead brings the plots to the bucket containing its track, with label and highlight
  consistent; an on-page selection does not move the view.
- Stack access routes through one helper; no output changes; the footgun is removed from converted sites.
- All existing tests pass unmodified.

## Cautions
- **Promoting a track is not showing it in context** — the page must actually move, or the plot and its
  label contradict each other.
- **Only navigate for an off-page selection.**
- **Recompute the bucket index**, never cache it.
- **Output-identical is the law** for Part 2; a differing output is a finding to report, not to quietly
  resolve.
- Per-site judgement — most `asarray` sites are correct as they are.
