# Claude Code spec — Results docks: reflow on resize, reopen after close, and page to the selected track

> **✅ STATUS — DONE (Parts 1–3). Part 1 reflow (1.6.311); Parts 3 page-to-selection + 2 reopen-from-payload
> (1.6.312).**
> **Part 3 (1.6.312).** `results_dock._vpt_page_to_selected_track` + `_vpt_drawn_tids`, hooked into
> `table_adapter._select_track`: an off-page selection moves the pager to the bucket containing the track
> (index recomputed from `all_tids` and the current bucket size, never cached) and re-renders through the
> existing re-highlight path; an on-page selection (incl. a page-0 ensemble track) does not move the view.
> `tests/test_vpt_page_to_selection.py` (`core`, 8).
> **Part 2 (1.6.312).** Shared `utils/results_store.py` (retain/reopen/has/disabled_reason/reopen_most_recent)
> + `dock_space.install_show_results_action` ('📊 Show results' menu action, wired in central_manager). VPT
> retains its payload (its `_show_vpt_results` args) and a reopen restores page/bucket; reopen reuses the
> dock-replacement path (idempotent, no recompute). `tests/test_results_store.py` (`core`, 8).
> **Deliberately deferred (flagged):** (a) the stale-vs-current LABEL on a reopened payload whose source data
> changed — the `stamp` field carries the token but VPT does not yet compare/annotate it; (b) cellular/batch
> ADOPTING the shared mechanism — they only need to call `retain_results` in their mount, which is a small
> per-workflow addition, not new mechanism.
> **Part 1 (1.6.311).** `toolbox/vpt/results_dock.py`: new `_new_results_figure()` returns a
> `Figure(layout='constrained')` (reflows subplot geometry on every resize) instead of the fixed
> `figsize=(11, 8.5)` laid out once with `tight_layout` at draw — the reported "squashed plots that won't
> stretch". The canvas now has an Expanding size policy + a minimum size (`_RESULTS_CANVAS_MIN_W/H`), and the
> results dock is explicitly floatable with a tooltip pointing to floating as the full-size escape hatch. The
> one-shot `tight_layout` is removed (constrained layout also accounts for the suptitle). `tests/test_vpt_results_reflow.py`
> (`base`, 3): constrained layout is enabled, the axes MOVE on resize (the core regression — tests the resize,
> not the initial draw), and the fixed print size is gone. Draw content is unchanged (geometry only).

**Date:** 2026-07-23 · **Target tree:** 1.6.297 · Verified against the 1.6.297 tree. Reported from the
GUI by Shamli Manasvi: the VPT results plots are squashed to hairlines and **dragging the dock wider
does not fix them** — axis labels overlap into unreadable strings, the trajectory panel is a vertical
sliver, and the Van Hove histogram is a single column. This is a layout bug, not a user error.

## Verified cause
`toolbox/vpt/results_dock.py:145-146`:
```python
fig = Figure(figsize=(11.0, 8.5))     # fixed 11×8.5 inch figure
axes = fig.subplots(2, 2)
canvas = FigureCanvasQTAgg(fig)
```
and the only layout call is a **one-shot** `fig.tight_layout(...)` at draw time (line ~281).

Two consequences:
1. **The figure is authored at 11×8.5 in** but mounted in a right-hand dock that is a fraction of that
   width. Matplotlib scales the canvas to the widget, so every axis, tick and label is compressed
   horizontally — producing the overlapping `10⁻⁰¹` labels visible in the report.
2. **`tight_layout` runs once, at draw**, not on resize. So when the user drags the dock wider the
   canvas stretches but the **axes keep their previous geometry** — exactly the "I keep changing the
   shape of the box but it won't stretch" symptom.

A 2×2 grid in a narrow panel compounds it: each subplot gets a quarter of an already-constrained width.

## The fix

### 1. Layout that reflows
Replace the one-shot `tight_layout` with **`constrained_layout`** on the figure
(`Figure(layout='constrained')`), which recomputes on every resize event rather than once at draw. If
constrained layout conflicts with any of the existing draw helpers, the alternative is to connect
`tight_layout` to the canvas `resize_event` — but constrained is preferred: it is the supported
mechanism and needs no event plumbing.

Keep the existing `try/except` tolerance — a degenerate axis must not break the draw (the current
`# broad-ok: tight_layout can fail on degenerate axes` reasoning still applies).

### 2. Don't author at a fixed print size
`figsize=(11.0, 8.5)` is a print-figure size, not a widget size. Either drop it (let the canvas drive)
or set a modest default with an **expanding size policy** so the canvas grows with the dock. The figure
size should follow the widget, not the other way round.

### 3. A usable minimum, and an escape hatch
- Give the canvas a sensible **minimum width** below which the 2×2 grid is not readable, so the dock
  cannot be dragged into an unusable state silently.
- **Make the results dock floatable/undockable** and say so — a floating window escapes the right-hand
  panel's width constraint entirely. This is the immediate workaround for users today and remains the
  right answer for detailed inspection.

### 4. Consider the grid at narrow widths *(design question, flag rather than decide)*
At panel widths a 2×2 grid may never be readable. Options: reflow to 1×4 (vertical stack, scrollable)
below a width threshold, or keep 2×2 and rely on floating. **Do not implement a responsive grid in this
increment** — fix the reflow first and see whether it is sufficient; a width-dependent layout is a
larger change with its own failure modes.

---

## Part 2 — A results dock that is closed cannot be reopened (add "Show results")

**A second, independent defect in the same dock**, reported alongside the reflow bug: if the user closes
the results panel, **there is no way to get it back short of re-running the entire analysis**.

### Verified
`_show_vpt_results(...)` is invoked from exactly **one** place — `vpt_ui.py:1093`, inside the compute
path. Nothing else calls it, and no menu entry, button, or command-palette action reaches it.

But the data is not lost. `results_dock.py:186` retains everything a redraw needs:
```python
self._vpt_results = {
    'ptc': ptc, 'msd_df': msd_df, 'fit': fit, 'mod': mod, 'tracks': tracks,
    'frame_dt': frame_dt, 'van_hove_lag': van_hove_lag,
    'fig': fig, 'axes': axes, 'canvas': canvas, 'all_tids': all_tids,
    'bucket_size': ..., 'page': ..., 'label': ..., 'prev_btn': ..., ...
}
```
The first seven keys are the **analysis results**; the rest are **widget references** that go stale when
the dock closes. So after a close, PyCAT is holding a complete set of computed results and no way to
display them — the user must recompute an analysis that has already run.

### The change
1. **Separate the results from the widgets.** Keep the computed payload (`ptc`, `msd_df`, `fit`, `mod`,
   `tracks`, `frame_dt`, `van_hove_lag`, `all_tids`) in its own retained record, distinct from the
   transient widget refs. Closing the dock invalidates the widgets; it must not touch the results.
2. **Add a "Show results" action** that rebuilds the dock from the retained payload, with no
   recomputation. It should:
   - be **enabled only when a payload exists** (greyed with a reason otherwise — "run the analysis
     first"), consistent with the analysis-preset and quality-gate patterns;
   - **restore the view state** where cheap — the bucket page and bucket size are already retained, so a
     reopened dock should land where the user left it rather than resetting to page 0;
   - reuse the existing dock-replacement path (`_show_vpt_results` already removes a stale dock before
     adding a fresh one), so reopening is idempotent and never stacks duplicates.
3. **Put it where a user will find it** — a button on the VPT method panel next to Run (the natural
   place: "you already ran this, show me the plots") and/or a menu entry. A command-palette entry comes
   free once it is a named action.
4. **Generalise it.** VPT is the reported case, but the same gap exists wherever a workflow mounts a
   results dock — the cellular and batch brushable workspaces have the same shape. Implement the
   retain-payload / rebuild-from-payload pattern in the **shared results-mount layer**
   (`ui/brushable_workspace.py` + `utils/dock_space.py`) so every workflow inherits it, rather than
   adding a bespoke button per method. Same "fix the mechanism" discipline as the dock reflow and the
   status markers.

### What must NOT happen
- **Never recompute silently.** "Show results" displays what was computed; it does not re-run the
  analysis. If the payload is absent, say so — do not quietly start a long computation.
- **Never show stale results as current.** If the underlying layers/data have changed since the payload
  was computed, the rebuilt dock should say the results are from the earlier run (the identity and
  dataset-UUID machinery already makes this checkable). Displaying old numbers as if they were fresh is
  worse than an empty panel.
- **Do not retain the figure/canvas across a close** — rebuild them. Holding dead Qt widgets is what the
  plot-lifecycle work removed; reopening should construct a fresh figure from the payload.

### Tests
- After closing the results dock, the retained payload still exists and "Show results" rebuilds the dock
  **without calling any analysis function** (assert the compute path is not invoked).
- The rebuilt dock renders the same content as before the close (same series, same track set).
- Bucket page and size are restored, not reset.
- With no payload, the action is disabled and states why.
- Reopening twice does not stack duplicate docks (idempotent).
- Widget references from the closed dock are not reused (no dead-Qt access).
- A payload whose source data has since changed is labelled as being from the earlier run.

---

## Part 3 — Clicking a bead outside the displayed bucket must take the plots to it

**A third defect in the same dock.** VPT routinely produces thousands of tracks (the reported session
shows *"representative sample of 4433 tracks"*), so the plots page through them in buckets — page 0 is a
representative ensemble, page *k* is the *k*-th bucket. But **every bead in the image is clickable**,
including beads whose track is not in the displayed bucket. Clicking one does not bring the plots to it.

### Verified
There *is* a promote mechanism: `_highlight_track_in_centered` documents itself as *"promotes a track
that was not drawn on the current page/sample so a selection from any view still lands"*, and after a
page turn `_vpt_render_page` re-applies the highlight for the selected track.

What is missing is the page move. `st['page']` changes in exactly two places — `_vpt_page_step` (the
Prev/Next buttons) and `_vpt_set_bucket_size`. **Nothing sets the page from a selection.** So clicking an
off-bucket bead:

- draws that one track promoted onto the current page's axes, and
- leaves the pager showing *"Tracks a–b of 4433 (bucket k / N)"* — a range that does not contain it.

The selected curve therefore appears without its cohort, against a label that contradicts it. For an
outlier-investigation workflow — the thing linked selection exists for — that is the wrong answer: the
user wants to see the picked track *among its neighbours*, not floating alone on someone else's page.

### The change
When a selection arrives from any view (image, table, plot, centered) for a track that is **not on the
current page**:

1. **Compute the bucket that contains it** — `all_tids` is retained and ordered, so the bucket index is
   `position_in_all_tids // bucket_size + 1` (page 0 is the ensemble, buckets are 1-based).
2. **Move `st['page']` to that bucket and re-render**, then apply the highlight — reusing the existing
   `_vpt_render_page` → re-highlight path rather than adding a parallel one.
3. **Update the pager label** so it names the bucket actually shown (the existing
   `_vpt_update_pager_label` already does this once the page is right).
4. **Keep page 0 special.** If the selected track happens to be in the representative ensemble on page 0,
   stay there — do not jump away from the ensemble view for a track that is already visible.
5. **Do not fight the user.** A selection originating from the *plot itself* on the current page must not
   trigger a page move (it is already visible). Only an off-page selection navigates.

### Design question to flag, not decide
Auto-paging moves the view under the user. That is right for "click a bead → show me its track", but
could be disorienting if selections arrive rapidly. Options: always page (simplest, matches the
linked-selection promise), or page only on an explicit reveal gesture. **Recommend always-page for
increment 1** — the existing brushing work already distinguishes a plain click from a navigating
double-click (`test_a_plain_click_does_NOT_yank_the_viewer`), so if this proves jarring the same
distinction can be applied here rather than designing for it up front.

### Tests
- Selecting a track in a bucket other than the current page moves the page to that track's bucket and
  renders it; the pager label names the new bucket.
- The selected track is highlighted after the page move (the existing re-highlight path fires).
- Selecting a track already on the current page does **not** change the page.
- Selecting a track present in the page-0 ensemble keeps the user on page 0.
- The computed bucket index is correct at bucket boundaries (first and last track of a bucket, and the
  final partial bucket).
- With the dock closed, a selection is a clean no-op (existing contract).
- Changing bucket size then selecting still lands on the right bucket (the index is recomputed, not
  cached).

## Interaction with dock space
This is the same dock the `dock_space` reflow work targets. Tabify gives the results dock the **full
panel height**; this bug is about **width** and the axes not reflowing. They are complementary — neither
fixes the other. After both, the remaining constraint is the panel's width, which is what floating
addresses.

## Tests
- Resizing the canvas triggers a layout recompute: after a simulated resize, the axes' positions
  differ from their pre-resize positions (the core regression — this is what "won't stretch" means).
- `constrained_layout` is enabled on the results figure (or a resize handler is connected).
- A degenerate/empty axis does not raise during draw or resize (existing tolerance preserved).
- The canvas has an expanding size policy and a stated minimum width.
- The dock is floatable.
- The drawn content is unchanged — same data, same series, same labels; only geometry reflows.

## Steps
1. Switch the results figure to constrained layout; remove the one-shot `tight_layout` (or keep it as
   the fallback path under the existing guard).
2. Stop authoring at a fixed print figsize; set an expanding size policy + minimum width.
3. Make the results dock floatable; note the workaround in the dock's tooltip or docs.
4. Split the retained results payload from the transient widget refs.
5. Add the "Show results" action (rebuild from payload, restore page/bucket, disabled with a reason when
   there is nothing to show) in the SHARED results-mount layer so every workflow inherits it.
6. Page-to-selection: move the pager to the bucket containing an off-page selected track, then
   re-render and re-highlight through the existing path.
7. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (results plots now reflow when
   the dock is resized; reported by Shamli Manasvi).

## Definition of done
- Dragging the results dock wider reflows the axes; labels stop overlapping.
- The figure follows the widget rather than a fixed 11×8.5 print size.
- A minimum width prevents an unusable state; floating is available for detailed inspection.
- Draw content is unchanged; degenerate axes still tolerated.
- A closed results dock can be reopened from retained results **without recomputing**; the action is
  disabled with a stated reason when there is nothing to show; reopening is idempotent and restores the
  page/bucket state.
- The reopen mechanism lives in the shared layer, so VPT, cellular and batch workspaces all get it.
- Clicking any bead in the image brings the plots to the bucket containing its track, with the pager
  label and highlight consistent; an on-page selection does not move the view.
- Full `pytest -m core` green.

## Cautions
- **The bug is that layout runs once.** Any fix that still computes geometry only at draw time will look
  correct on first render and regress the moment the user resizes — which is precisely the reported
  symptom. Test the *resize*, not the initial draw.
- **Don't change what is plotted** — same series, same labels, same data. Geometry only.
- Keep the degenerate-axis tolerance; a layout failure must not break the results.
- **Don't build a responsive grid yet** — fix the reflow, then reassess whether 2×2 at panel width is
  still unreadable.
- Tell users the immediate workaround (float the dock) rather than leaving them dragging a panel that
  cannot help them.
- **"Show results" must never recompute.** It displays a retained payload; an absent payload is a stated
  refusal, not a silent re-run of a long analysis.
- **Never present stale results as current** — label a payload whose source data has since changed.
- **Rebuild the figure, don't retain it.** Keeping dead Qt widgets across a close reintroduces exactly
  what the plot-lifecycle teardown work removed.
- Build the reopen in the shared results layer, not per method — otherwise the next workflow forgets it,
  which is how this gap appeared in the first place.
- **Promoting a track is not the same as showing it in context.** The existing promote draws an off-page
  curve onto the current page's axes while the pager label still names a range that excludes it — the
  page must actually move, or the plot and its label contradict each other.
- **Only navigate for an OFF-page selection.** Re-paging on a selection that is already visible yanks the
  view for no reason.
- Recompute the bucket index from `all_tids` and the current bucket size on each selection; never cache
  it, or a bucket-size change will send the user to the wrong page.
